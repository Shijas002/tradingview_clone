"""
Outgoing notifications (v1.1): Telegram bot messages and the JSON webhook.

* **Telegram** - set ``TELEGRAM_BOT_TOKEN`` (from @BotFather) and
  ``TELEGRAM_CHAT_ID``. Don't know your chat id? Send any message to your bot,
  then open ``/api/notify/telegram/chats`` - it lists the chats that wrote to it.
* **Webhook** - ``ALERT_WEBHOOK_URL`` receives every event as JSON (Discord,
  Slack, n8n, your own bot...).

``TELEGRAM_EVENTS`` chooses what is sent to Telegram: any of
``alerts`` (price + indicator alerts), ``paper`` (paper-trading fills),
``journal`` (trades imported from MT5 / Upstox).
Sending never blocks or breaks the caller - failures are only logged.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Dict, List, Optional, Set

import httpx

from app.core.config import Settings

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class Notifier:
    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._tasks: Set[asyncio.Task] = set()
        self.sent = 0
        self.failed = 0
        self.last_error: Optional[str] = None

    @property
    def telegram_enabled(self) -> bool:
        return bool(self._settings.telegram_bot_token and self._settings.telegram_chat_id)

    def telegram_wants(self, event: str) -> bool:
        return self.telegram_enabled and event in self._settings.telegram_event_set

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0, transport=self._transport)
        return self._client

    async def close(self) -> None:
        for t in list(self._tasks):
            try:
                await asyncio.wait_for(t, timeout=3)
            except Exception:
                t.cancel()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- send
    def notify(self, event: str, title: str, lines: List[str], payload: Optional[Dict[str, Any]] = None, telegram: bool = True) -> None:
        """Fire-and-forget: Telegram (if enabled for ``event``) + webhook (alerts only)."""
        if telegram and self.telegram_wants(event):
            self._spawn(self.send_telegram(self.format(title, lines)))
        if event == "alerts" and self._settings.alert_webhook_url and payload is not None:
            self._spawn(self._post_webhook(payload))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    def format(title: str, lines: List[str]) -> str:
        body = "\n".join(html.escape(str(x)) for x in lines if x)
        return f"<b>{html.escape(title)}</b>" + (f"\n{body}" if body else "")

    async def send_telegram(self, text: str, chat_id: Optional[str] = None) -> bool:
        token = self._settings.telegram_bot_token
        chat = chat_id or self._settings.telegram_chat_id
        if not token or not chat:
            return False
        try:
            resp = await self._http().post(
                f"{TELEGRAM_API}/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 429:
                retry = int((data.get("parameters") or {}).get("retry_after", 2))
                await asyncio.sleep(min(retry, 30))
                resp = await self._http().post(
                    f"{TELEGRAM_API}/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True},
                )
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code != 200 or not data.get("ok"):
                self.failed += 1
                self.last_error = f"Telegram HTTP {resp.status_code}: {data.get('description') or resp.text[:200]}"
                log.warning(self.last_error)
                return False
            self.sent += 1
            self.last_error = None
            return True
        except httpx.HTTPError as exc:
            self.failed += 1
            self.last_error = f"Telegram unreachable: {type(exc).__name__}"
            log.warning(self.last_error)
            return False

    async def _post_webhook(self, payload: Dict[str, Any]) -> None:
        try:
            r = await self._http().post(self._settings.alert_webhook_url, json=payload)
            if r.status_code >= 300:
                log.warning("Alert webhook returned HTTP %s: %s", r.status_code, r.text[:200])
        except httpx.HTTPError as exc:
            log.warning("Alert webhook failed: %s", exc)

    # ------------------------------------------------------------- helpers
    async def telegram_chats(self) -> List[Dict[str, Any]]:
        """Chats that recently messaged the bot (to find TELEGRAM_CHAT_ID)."""
        token = self._settings.telegram_bot_token
        if not token:
            return []
        resp = await self._http().get(f"{TELEGRAM_API}/bot{token}/getUpdates", params={"limit": 100})
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or f"HTTP {resp.status_code}")
        chats: Dict[str, Dict[str, Any]] = {}
        for upd in data.get("result") or []:
            msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if "id" in chat:
                name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
                chats[str(chat["id"])] = {"chat_id": str(chat["id"]), "type": chat.get("type"), "name": name}
        return list(chats.values())

    def status(self) -> Dict[str, Any]:
        return {
            "telegram": self.telegram_enabled,
            "telegram_events": sorted(self._settings.telegram_event_set),
            "webhook": bool(self._settings.alert_webhook_url),
            "sent": self.sent,
            "failed": self.failed,
            "last_error": self.last_error,
        }
