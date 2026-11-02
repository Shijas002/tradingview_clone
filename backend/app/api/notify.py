"""
Notifications API (v1.1, prefix /api/notify).

GET  /api/notify                   Telegram / webhook configuration + counters
POST /api/notify/test              send a test message to Telegram
GET  /api/notify/telegram/chats    chats that messaged your bot (to find TELEGRAM_CHAT_ID)
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.exceptions import InvalidRequestError, ProviderUnavailableError
from app.services.notifier import Notifier

router = APIRouter(prefix="/api/notify", tags=["notifications"])


def _notifier(request: Request) -> Notifier:
    return request.app.state.notifier


class TestIn(BaseModel):
    text: str = Field(default="✅ Test message from your TradingView Clone", max_length=1000)


@router.get("")
def status(request: Request) -> Dict[str, Any]:
    return _notifier(request).status()


@router.post("/test")
async def test(body: TestIn, request: Request) -> Dict[str, Any]:
    n = _notifier(request)
    if not n.telegram_enabled:
        raise InvalidRequestError("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in backend/.env and restart")
    ok = await n.send_telegram(n.format("TradingView Clone", [body.text]))
    if not ok:
        raise ProviderUnavailableError(n.last_error or "Telegram did not accept the message")
    return {"ok": True}


@router.get("/telegram/chats")
async def telegram_chats(request: Request) -> Dict[str, Any]:
    n = _notifier(request)
    if not request.app.state.settings.telegram_bot_token:
        raise InvalidRequestError("Set TELEGRAM_BOT_TOKEN in backend/.env first (create a bot with @BotFather)")
    try:
        chats = await n.telegram_chats()
    except Exception as exc:
        raise ProviderUnavailableError(f"Telegram getUpdates failed: {exc}") from exc
    hint = "Put one of these chat_id values into TELEGRAM_CHAT_ID" if chats else "No chats yet: open your bot in Telegram, press Start / send any message, then reload this page"
    return {"chats": chats, "hint": hint}
