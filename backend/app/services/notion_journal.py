"""
Notion mirror of the trade journal (Phase 5).

The journal always lives in SQLite first (``journal_entries``). When
``NOTION_API_KEY`` is set, this service copies every entry into a Notion
database and keeps it updated:

* **Setup** - checks the token (``GET /v1/users/me``), finds the database's
  *data source* (Notion API 2025-09+ splits databases into data sources), and adds
  any journal property the database is missing (Side, Entry, P&L, Screenshot...).
  Existing properties are never changed or removed. With ``NOTION_PARENT_PAGE_ID``
  instead of a database id, a "Trading Journal" database is created on that page.
* **Sync** - the database rows are the queue: entries with
  ``notion_status = pending`` are created/updated in Notion one by one, entries
  marked ``deleting`` are moved to the Notion trash and then removed locally.
  Chart screenshots are uploaded with the File Upload API, stored in the
  "Screenshot" property and shown as an image inside the page.
* **Limits** - requests are spaced ~0.35 s apart (Notion allows ~3/s), 429
  responses honour ``Retry-After``, network/5xx errors retry with back-off, and
  validation errors are stored on the entry so the UI can show them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from app.core.config import Settings
from app.db.database import Database
from app.db.models import JournalEntry, now_ms

log = logging.getLogger("app.services.notion")

NOTION_API = "https://api.notion.com"
NOTION_VERSION = "2026-03-11"
MIN_INTERVAL_S = 0.35
DATABASE_TITLE = "Trading Journal"
TITLE_PROPERTY = "Trade"

# Journal property -> Notion property definition (added when missing)
SCHEMA: Dict[str, Dict[str, Any]] = {
    "Symbol": {"type": "select", "select": {}},
    "Side": {"type": "select", "select": {"options": [{"name": "Long", "color": "green"}, {"name": "Short", "color": "red"}]}},
    "Status": {"type": "select", "select": {"options": [{"name": "Open", "color": "blue"}, {"name": "Closed", "color": "gray"}]}},
    "Result": {"type": "select", "select": {"options": [
        {"name": "Win", "color": "green"}, {"name": "Loss", "color": "red"}, {"name": "Breakeven", "color": "gray"}]}},
    "Entry Time": {"type": "date", "date": {}},
    "Exit Time": {"type": "date", "date": {}},
    "Entry": {"type": "number", "number": {"format": "number"}},
    "Exit": {"type": "number", "number": {"format": "number"}},
    "Quantity": {"type": "number", "number": {"format": "number"}},
    "Stop Loss": {"type": "number", "number": {"format": "number"}},
    "Take Profit": {"type": "number", "number": {"format": "number"}},
    "Fees": {"type": "number", "number": {"format": "number"}},
    "P&L": {"type": "number", "number": {"format": "number"}},
    "R Multiple": {"type": "number", "number": {"format": "number"}},
    "Setup": {"type": "select", "select": {}},
    "Tags": {"type": "multi_select", "multi_select": {}},
    "Rating": {"type": "number", "number": {"format": "number"}},
    "Timeframe": {"type": "select", "select": {}},
    "Provider": {"type": "select", "select": {}},
    "Notes": {"type": "rich_text", "rich_text": {}},
    "Screenshot": {"type": "files", "files": {}},
    "Journal ID": {"type": "rich_text", "rich_text": {}},
}

Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]


class NotionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class NotionAuthError(NotionError):
    """Token rejected / integration has no access - fixing .env is required."""


class NotionTransientError(NotionError):
    """Network trouble, 5xx or rate limiting that outlasted our retries."""


def parse_notion_id(value: str) -> str:
    """Accept a raw id (with or without dashes) or a Notion URL; return the dashed UUID."""
    raw = (value or "").strip()
    if not raw:
        return ""
    candidate = raw
    if "notion.so" in raw or "notion.site" in raw or "/" in raw:
        path = raw.split("?")[0].rstrip("/")
        candidate = path.rsplit("/", 1)[-1]
    hexes = re.findall(r"[0-9a-fA-F]{32}", candidate.replace("-", ""))
    if not hexes:
        return raw  # let Notion return a clear "not found"
    h = hexes[-1].lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _clean_option(text: str) -> str:
    # Notion select option names cannot contain commas and are limited in length
    return re.sub(r"\s+", " ", str(text).replace(",", " ")).strip()[:100]


def _rich_text(text: str) -> List[Dict[str, Any]]:
    text = text or ""
    chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)][:100]
    return [{"type": "text", "text": {"content": c}} for c in chunks]


def _iso(ts: Optional[int]) -> Optional[Dict[str, Any]]:
    if not ts:
        return None
    return {"start": datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")}


def trade_metrics(e: JournalEntry) -> Tuple[Optional[float], Optional[float]]:
    """(P&L, R multiple) of a closed trade; (None, None) while it is open."""
    if e.status != "closed" or e.exit_price is None:
        return None, None
    direction = 1.0 if e.side == "long" else -1.0
    move = (e.exit_price - e.entry_price) * direction
    pnl = move * (e.quantity or 0.0) - (e.fees or 0.0)
    r = None
    if e.stop_loss is not None and abs(e.entry_price - e.stop_loss) > 0:
        r = move / abs(e.entry_price - e.stop_loss)
    return round(pnl, 10), (round(r, 4) if r is not None else None)


def trade_title(e: JournalEntry) -> str:
    return f"{'Long' if e.side == 'long' else 'Short'} {e.symbol} @ {e.entry_price:g}"


class NotionJournal:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        broadcast: Optional[Broadcast] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._settings = settings
        self._db = db
        self._broadcast = broadcast
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._last_request = 0.0
        self._state_file: Path = settings.data_path / "notion_journal.json"
        self.shots_dir: Path = settings.data_path / "journal"
        self._ready = False
        self._ds_id: Optional[str] = None
        self._title_prop = TITLE_PROPERTY
        self._props: Dict[str, str] = {}  # usable property name -> type
        self._skipped: List[str] = []
        self._retry_delay = 2.0
        self.state: Dict[str, Any] = {
            "configured": settings.notion_configured,
            "connected": False,
            "message": "Not configured - add NOTION_API_KEY and NOTION_DATABASE_ID to backend/.env"
            if not settings.notion_configured
            else "Starting…",
            "database_title": None,
            "database_url": None,
            "skipped_properties": [],
        }

    # ============================================================ lifecycle
    @property
    def enabled(self) -> bool:
        return self._settings.notion_configured

    async def start(self) -> None:
        if not self.enabled:
            log.info("Notion journal sync disabled (NOTION_API_KEY / NOTION_DATABASE_ID not set)")
            return
        with self._db.session() as s:  # entries written before Notion was set up are synced too
            for row in s.query(JournalEntry).filter(JournalEntry.notion_status == "local").all():
                row.notion_status = "pending"
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="notion-journal")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def wake(self) -> None:
        """Something changed locally - sync soon."""
        self._wake.set()

    def status(self) -> Dict[str, Any]:
        out = dict(self.state)
        with self._db.session() as s:
            q = s.query(JournalEntry.notion_status)
            statuses = [row[0] for row in q.all()]
        out["pending"] = sum(1 for x in statuses if x in ("pending", "deleting"))
        out["errors"] = sum(1 for x in statuses if x == "error")
        out["synced"] = sum(1 for x in statuses if x == "synced")
        return out

    async def _set_state(self, **changes: Any) -> None:
        before = dict(self.state)
        self.state.update(changes)
        if self.state != before:
            level = logging.INFO if self.state.get("connected") else logging.WARNING
            log.log(level, "Notion: %s", self.state.get("message"))
            await self._emit({"type": "journal_status", "data": self.status()})

    async def _emit(self, message: Dict[str, Any]) -> None:
        if self._broadcast is not None:
            try:
                await self._broadcast(message)
            except Exception:
                log.debug("journal broadcast failed", exc_info=True)

    # ================================================================= HTTP
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=NOTION_API,
                timeout=self._settings.notion_request_timeout_s,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {self._settings.notion_api_key}",
                    "Notion-Version": NOTION_VERSION,
                },
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        attempts = 0
        while True:
            attempts += 1
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = await self._http().request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                if attempts < 3:
                    await asyncio.sleep(attempts * 1.5)
                    continue
                raise NotionTransientError(0, "network", f"cannot reach Notion: {type(exc).__name__}") from exc
            if resp.status_code in (429, 529) or resp.status_code >= 500:
                body = _json(resp)
                if body.get("code") == "public_api_request_blocked":
                    raise NotionAuthError(resp.status_code, body.get("code", ""), body.get("message", "API access blocked"))
                if attempts < 4:
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else attempts * 2.0
                    await asyncio.sleep(min(delay, 60.0))
                    continue
                raise NotionTransientError(resp.status_code, body.get("code", ""), body.get("message", f"HTTP {resp.status_code}"))
            if resp.status_code >= 400:
                body = _json(resp)
                code, message = body.get("code", ""), body.get("message", f"HTTP {resp.status_code}")
                if resp.status_code == 401:
                    raise NotionAuthError(401, code, "Notion rejected NOTION_API_KEY (check the integration secret)")
                raise NotionError(resp.status_code, code, message)
            return _json(resp)

    # ================================================================ setup
    async def _setup(self) -> None:
        me = await self._request("GET", "/v1/users/me")
        bot_name = me.get("name") or "integration"
        db_id, ds_id = self._configured_ids()
        title = None
        url = None
        if ds_id is None and db_id:
            try:
                db = await self._request("GET", f"/v1/databases/{db_id}")
                sources = db.get("data_sources") or []
                if not sources:
                    raise NotionError(400, "no_data_source", "This Notion database has no data source")
                ds_id = sources[0]["id"]
                title = _plain(db.get("title"))
                url = db.get("url")
            except NotionError as exc:
                if exc.status != 404:
                    raise
                try:  # maybe the user pasted a *data source* id
                    ds = await self._request("GET", f"/v1/data_sources/{db_id}")
                    ds_id = ds["id"]
                except NotionError as exc2:
                    if exc2.status == 404:
                        raise NotionAuthError(
                            404, "object_not_found",
                            f"Notion database {db_id} not found or not shared with '{bot_name}'. In Notion open the "
                            f"database → ••• → Connections → add your integration.",
                        ) from exc2
                    raise
        elif ds_id is None:
            ds_id, db_id, title, url = await self._create_database()

        ds = await self._request("GET", f"/v1/data_sources/{ds_id}")
        title = title or _plain(ds.get("title")) or DATABASE_TITLE
        url = url or ds.get("url")
        props: Dict[str, Dict[str, Any]] = ds.get("properties") or {}
        title_prop = next((name for name, p in props.items() if p.get("type") == "title"), TITLE_PROPERTY)
        missing = {name: spec for name, spec in SCHEMA.items() if name not in props}
        skipped = [name for name, spec in SCHEMA.items() if name in props and props[name].get("type") != spec["type"]]
        if missing:
            await self._request("PATCH", f"/v1/data_sources/{ds_id}", json={"properties": missing})
            log.info("Added %s journal properties to the Notion database: %s", len(missing), ", ".join(missing))
        if skipped:
            log.warning("Notion properties with a different type were left alone (not synced): %s", ", ".join(skipped))
        self._ds_id = ds_id
        self._title_prop = title_prop
        self._props = {name: spec["type"] for name, spec in SCHEMA.items() if name not in skipped}
        self._skipped = skipped
        self._ready = True
        await self._set_state(
            connected=True,
            message=f"Connected to Notion database '{title}'",
            database_title=title,
            database_url=url,
            skipped_properties=skipped,
        )

    def _configured_ids(self) -> Tuple[Optional[str], Optional[str]]:
        """(database_id, data_source_id) from .env, or from the database we created earlier."""
        if self._settings.notion_database_id:
            return parse_notion_id(self._settings.notion_database_id), None
        try:
            saved = json.loads(self._state_file.read_text(encoding="utf-8"))
            if saved.get("parent_page_id") == parse_notion_id(self._settings.notion_parent_page_id):
                return saved.get("database_id"), saved.get("data_source_id")
        except (FileNotFoundError, ValueError):
            pass
        return None, None

    async def _create_database(self) -> Tuple[str, str, str, Optional[str]]:
        parent = parse_notion_id(self._settings.notion_parent_page_id)
        properties: Dict[str, Any] = {TITLE_PROPERTY: {"type": "title", "title": {}}, **SCHEMA}
        try:
            db = await self._request(
                "POST",
                "/v1/databases",
                json={
                    "parent": {"type": "page_id", "page_id": parent},
                    "title": [{"type": "text", "text": {"content": DATABASE_TITLE}}],
                    "icon": {"type": "emoji", "emoji": "📓"},
                    "initial_data_source": {"properties": properties},
                },
            )
        except NotionError as exc:
            if exc.status == 404:
                raise NotionAuthError(
                    404, exc.code,
                    "NOTION_PARENT_PAGE_ID not found or not shared with the integration "
                    "(open the page → ••• → Connections → add your integration)",
                ) from exc
            raise
        sources = db.get("data_sources") or []
        if not sources:
            raise NotionError(500, "no_data_source", "Notion created the database but returned no data source")
        info = {"parent_page_id": parent, "database_id": db["id"], "data_source_id": sources[0]["id"], "url": db.get("url")}
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
        log.info("Created Notion database '%s' (%s). Tip: put NOTION_DATABASE_ID=%s in .env", DATABASE_TITLE, db.get("url"), db["id"])
        return sources[0]["id"], db["id"], DATABASE_TITLE, db.get("url")

    # ============================================================ main loop
    async def _run(self) -> None:
        try:
            while True:
                if not self._ready:
                    try:
                        await self._setup()
                        self._retry_delay = 2.0
                    except NotionAuthError as exc:
                        await self._set_state(connected=False, message=exc.message)
                        await self._sleep(300)  # wait for a restart with a fixed .env (or a manual retry)
                        continue
                    except Exception as exc:
                        msg = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
                        await self._set_state(connected=False, message=f"{msg} - retrying in {self._retry_delay:.0f}s")
                        await self._sleep(self._retry_delay)
                        self._retry_delay = min(self._retry_delay * 2, 300.0)
                        continue

                ids = self._pending_ids()
                if not ids:
                    await self._sleep(60)
                    continue
                for entry_id in ids:
                    try:
                        await self._sync_one(entry_id)
                    except NotionAuthError as exc:
                        self._ready = False
                        await self._set_state(connected=False, message=exc.message)
                        break
                    except NotionTransientError as exc:
                        await self._set_state(connected=False, message=f"Notion unreachable ({exc.message}) - will retry")
                        self._ready = False
                        await self._sleep(self._retry_delay)
                        self._retry_delay = min(self._retry_delay * 2, 300.0)
                        break
                    except NotionError as exc:
                        self._mark_error(entry_id, f"{exc.message} ({exc.code or exc.status})")
                    except Exception as exc:  # never let one entry stop the loop
                        log.exception("Notion sync of %s crashed", entry_id)
                        self._mark_error(entry_id, f"{type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            raise

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _pending_ids(self) -> List[str]:
        with self._db.session() as s:
            rows = (
                s.query(JournalEntry.id)
                .filter(JournalEntry.notion_status.in_(("pending", "deleting")))
                .order_by(JournalEntry.updated_at)
                .limit(50)
                .all()
            )
        return [r[0] for r in rows]

    def _mark_error(self, entry_id: str, message: str) -> None:
        log.warning("Notion sync failed for journal entry %s: %s", entry_id, message)
        with self._db.session() as s:
            e = s.get(JournalEntry, entry_id)
            if e is not None:
                e.notion_status = "error"
                e.notion_error = message[:1000]
        asyncio.create_task(self._emit({"type": "journal_changed", "id": entry_id}))

    # ============================================================ one entry
    def _properties(self, e: JournalEntry) -> Dict[str, Any]:
        pnl, r = trade_metrics(e)
        result = None
        if pnl is not None:
            result = "Win" if pnl > 0 else "Loss" if pnl < 0 else "Breakeven"
        values: Dict[str, Any] = {
            "Symbol": {"select": {"name": _clean_option(e.symbol)}} if e.symbol else {"select": None},
            "Side": {"select": {"name": "Long" if e.side == "long" else "Short"}},
            "Status": {"select": {"name": "Closed" if e.status == "closed" else "Open"}},
            "Result": {"select": {"name": result} if result else None},
            "Entry Time": {"date": _iso(e.entry_time)},
            "Exit Time": {"date": _iso(e.exit_time)},
            "Entry": {"number": e.entry_price},
            "Exit": {"number": e.exit_price},
            "Quantity": {"number": e.quantity},
            "Stop Loss": {"number": e.stop_loss},
            "Take Profit": {"number": e.take_profit},
            "Fees": {"number": e.fees or 0},
            "P&L": {"number": pnl},
            "R Multiple": {"number": r},
            "Setup": {"select": {"name": _clean_option(e.setup)} if e.setup and _clean_option(e.setup) else None},
            "Tags": {"multi_select": [{"name": t} for t in dict.fromkeys(_clean_option(x) for x in (e.tags or [])) if t][:100]},
            "Rating": {"number": e.rating},
            "Timeframe": {"select": {"name": _clean_option(e.timeframe)} if e.timeframe else None},
            "Provider": {"select": {"name": _clean_option(e.provider)} if e.provider else None},
            "Notes": {"rich_text": _rich_text(e.notes)},
            "Journal ID": {"rich_text": _rich_text(e.id)},
        }
        props = {name: value for name, value in values.items() if name in self._props}
        props[self._title_prop] = {"title": _rich_text(trade_title(e))}
        return props

    async def _sync_one(self, entry_id: str) -> None:
        with self._db.session() as s:
            e = s.get(JournalEntry, entry_id)
            if e is None:
                return
            s.expunge(e)
        loaded_version = e.updated_at

        if e.notion_status == "deleting":
            if e.notion_page_id:
                try:
                    await self._request("PATCH", f"/v1/pages/{e.notion_page_id}", json={"in_trash": True})
                except NotionError as exc:
                    if exc.status not in (404, 400):
                        raise
            with self._db.session() as s:
                row = s.get(JournalEntry, entry_id)
                if row is not None and row.notion_status == "deleting":
                    s.delete(row)
            self._delete_shot(e)
            log.info("Journal entry %s removed (moved to Notion trash)", entry_id)
            await self._emit({"type": "journal_changed", "id": entry_id})
            return

        props = self._properties(e)
        page_id, page_url = e.notion_page_id, e.notion_url
        image_block = e.notion_image_block_id
        shot_version = e.notion_screenshot_version
        if page_id:
            try:
                page = await self._request("PATCH", f"/v1/pages/{page_id}", json={"properties": props})
                page_url = page.get("url") or page_url
            except NotionError as exc:
                gone = exc.status == 404 or (exc.status == 400 and re.search(r"archiv|trash", exc.message, re.I))
                if not gone:
                    raise
                log.info("Notion page for %s was deleted in Notion - creating it again", entry_id)
                page_id, image_block, shot_version = None, None, 0
        if not page_id:
            page = await self._request(
                "POST",
                "/v1/pages",
                json={
                    "parent": {"type": "data_source_id", "data_source_id": self._ds_id},
                    "icon": {"type": "emoji", "emoji": "📈" if e.side == "long" else "📉"},
                    "properties": props,
                },
            )
            page_id, page_url = page["id"], page.get("url")
            with self._db.session() as s:  # remember the page at once (no duplicates if a later step fails)
                row = s.get(JournalEntry, entry_id)
                if row is not None:
                    row.notion_page_id, row.notion_url = page_id, page_url

        # screenshot: upload + "Screenshot" property + image block in the page
        shot = self.shot_path(e)
        if shot is not None and e.screenshot_version != shot_version:
            upload_id = await self._upload(shot)
            if "Screenshot" in self._props:
                await self._request(
                    "PATCH",
                    f"/v1/pages/{page_id}",
                    json={"properties": {"Screenshot": {"files": [
                        {"type": "file_upload", "file_upload": {"id": upload_id}, "name": shot.name}]}}},
                )
            if image_block:
                await self._delete_block(image_block)
            appended = await self._request(
                "PATCH",
                f"/v1/blocks/{page_id}/children",
                json={"children": [{"object": "block", "type": "image",
                                    "image": {"type": "file_upload", "file_upload": {"id": upload_id}}}]},
            )
            results = appended.get("results") or []
            image_block = results[0]["id"] if results else None
            shot_version = e.screenshot_version
        elif shot is None and (image_block or shot_version):
            if image_block:
                await self._delete_block(image_block)
            if "Screenshot" in self._props:
                await self._request("PATCH", f"/v1/pages/{page_id}", json={"properties": {"Screenshot": {"files": []}}})
            image_block, shot_version = None, 0

        with self._db.session() as s:
            row = s.get(JournalEntry, entry_id)
            if row is None:
                return
            row.notion_page_id = page_id
            row.notion_url = page_url
            row.notion_image_block_id = image_block
            row.notion_screenshot_version = shot_version
            row.notion_synced_at = now_ms()
            row.notion_error = ""
            if row.notion_status == "pending" and row.updated_at == loaded_version:
                row.notion_status = "synced"  # (edited meanwhile -> stays pending for another pass)
        log.info("Journal entry %s synced to Notion", entry_id)
        await self._emit({"type": "journal_changed", "id": entry_id})

    async def _delete_block(self, block_id: str) -> None:
        try:
            await self._request("DELETE", f"/v1/blocks/{block_id}")
        except NotionError as exc:
            if exc.status not in (404, 400):
                raise

    async def _upload(self, path: Path) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "image/png"
        created = await self._request("POST", "/v1/file_uploads", json={"filename": path.name, "content_type": content_type})
        upload_id = created["id"]
        await self._request(
            "POST",
            f"/v1/file_uploads/{upload_id}/send",
            files={"file": (path.name, path.read_bytes(), content_type)},
        )
        return upload_id

    # =========================================================== files
    def shot_path(self, e: JournalEntry) -> Optional[Path]:
        if not e.screenshot:
            return None
        path = self.shots_dir / e.screenshot
        return path if path.exists() else None

    def _delete_shot(self, e: JournalEntry) -> None:
        path = self.shot_path(e)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass

    async def retry(self) -> int:
        """Mark every entry in 'error' as pending again and wake the worker. Returns how many."""
        with self._db.session() as s:
            rows = s.query(JournalEntry).filter(JournalEntry.notion_status == "error").all()
            for row in rows:
                row.notion_status = "pending"
                row.notion_error = ""
            count = len(rows)
        if not self._ready:
            self._retry_delay = 2.0
        self.wake()
        return count


def _json(resp: httpx.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _plain(rich: Any) -> str:
    if isinstance(rich, list):
        return "".join(str((x or {}).get("plain_text") or ((x or {}).get("text") or {}).get("content") or "") for x in rich)
    return ""
