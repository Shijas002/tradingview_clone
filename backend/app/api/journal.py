"""
Trade journal API (Phase 5, prefix /api/journal).

GET    /api/journal                    list entries (filters: symbol, status, side, limit)
POST   /api/journal                    create an entry (optional chart screenshot as a data: URL)
GET    /api/journal/stats              win rate, net P&L, profit factor, average R, per-symbol totals
GET    /api/journal/export.csv         all entries as CSV (opens in Excel)
GET    /api/journal/notion             Notion sync status
POST   /api/journal/notion/retry       retry every entry whose sync failed
GET    /api/journal/{id}               one entry
PUT    /api/journal/{id}               update (send only the fields that change)
DELETE /api/journal/{id}               delete (also moves the Notion page to the trash)
GET    /api/journal/{id}/screenshot    the attached chart image
POST   /api/journal/{id}/sync          push this entry to Notion again
"""

from __future__ import annotations

import base64
import csv
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.db.database import Database
from app.db.models import JournalEntry, now_ms
from app.services.notion_journal import NotionJournal, trade_metrics

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/journal", tags=["journal"])

MAX_SHOT_BYTES = 8 * 1024 * 1024
_DATA_URL = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", re.S)


def _db(request: Request) -> Database:
    return request.app.state.db


def _notion(request: Request) -> NotionJournal:
    return request.app.state.notion


# ------------------------------------------------------------------ schemas
class JournalBase(BaseModel):
    provider: str = Field(default="", max_length=40)
    symbol: str = Field(..., min_length=1, max_length=80)
    timeframe: str = Field(default="", max_length=8)
    side: Literal["long", "short"]
    status: Literal["open", "closed"] = "open"
    entry_time: Optional[int] = Field(default=None, ge=0)
    entry_price: float = Field(..., gt=0)
    quantity: float = Field(default=1.0, gt=0)
    exit_time: Optional[int] = Field(default=None, ge=0)
    exit_price: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    fees: float = Field(default=0.0, ge=0)
    setup: str = Field(default="", max_length=120)
    tags: List[str] = Field(default_factory=list, max_length=30)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    notes: str = Field(default="", max_length=50_000)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: List[str]) -> List[str]:
        out = []
        for t in v:
            t = str(t).strip()[:60]
            if t and t not in out:
                out.append(t)
        return out

    @model_validator(mode="after")
    def _closed_needs_exit(self) -> "JournalBase":
        if self.status == "closed" and self.exit_price is None:
            raise ValueError("A closed trade needs an exit price")
        return self


class JournalIn(JournalBase):
    screenshot: Optional[str] = Field(default=None, description="data:image/png;base64,... chart image")


class JournalPatch(BaseModel):
    provider: Optional[str] = Field(default=None, max_length=40)
    symbol: Optional[str] = Field(default=None, min_length=1, max_length=80)
    timeframe: Optional[str] = Field(default=None, max_length=8)
    side: Optional[Literal["long", "short"]] = None
    status: Optional[Literal["open", "closed"]] = None
    entry_time: Optional[int] = Field(default=None, ge=0)
    entry_price: Optional[float] = Field(default=None, gt=0)
    quantity: Optional[float] = Field(default=None, gt=0)
    exit_time: Optional[int] = Field(default=None, ge=0)
    exit_price: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    fees: Optional[float] = Field(default=None, ge=0)
    setup: Optional[str] = Field(default=None, max_length=120)
    tags: Optional[List[str]] = Field(default=None, max_length=30)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = Field(default=None, max_length=50_000)
    screenshot: Optional[str] = None  # new image as data: URL
    remove_screenshot: bool = False
    clear: List[str] = Field(default_factory=list, description="optional fields to set back to empty")


class JournalOut(JournalBase):
    id: str
    created_at: int
    updated_at: int
    pnl: Optional[float] = None
    r_multiple: Optional[float] = None
    has_screenshot: bool = False
    screenshot_version: int = 0
    notion_status: str
    notion_error: str = ""
    notion_url: Optional[str] = None
    notion_synced_at: Optional[int] = None

    @model_validator(mode="after")
    def _closed_needs_exit(self) -> "JournalOut":  # stored rows are already valid
        return self


_CLEARABLE = {"exit_time", "exit_price", "stop_loss", "take_profit", "rating", "entry_time"}


def _out(e: JournalEntry, notion: NotionJournal) -> JournalOut:
    pnl, r = trade_metrics(e)
    return JournalOut(
        id=e.id, created_at=e.created_at, updated_at=e.updated_at,
        provider=e.provider or "", symbol=e.symbol, timeframe=e.timeframe or "", side=e.side, status=e.status,
        entry_time=e.entry_time, entry_price=e.entry_price, quantity=e.quantity,
        exit_time=e.exit_time, exit_price=e.exit_price, stop_loss=e.stop_loss, take_profit=e.take_profit,
        fees=e.fees or 0.0, setup=e.setup or "", tags=list(e.tags or []), rating=e.rating, notes=e.notes or "",
        pnl=pnl, r_multiple=r, has_screenshot=notion.shot_path(e) is not None, screenshot_version=e.screenshot_version or 0,
        notion_status=e.notion_status, notion_error=e.notion_error or "", notion_url=e.notion_url,
        notion_synced_at=e.notion_synced_at,
    )


def _decode_shot(data_url: str) -> tuple[bytes, str]:
    m = _DATA_URL.match(data_url.strip())
    if not m:
        raise InvalidRequestError("screenshot must be a data:image/png;base64,... URL")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except ValueError as exc:
        raise InvalidRequestError("screenshot is not valid base64") from exc
    if len(raw) > MAX_SHOT_BYTES:
        raise InvalidRequestError("screenshot is larger than 8 MB")
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif raw[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        ext = "webp"
    else:
        raise InvalidRequestError("screenshot must be a PNG, JPEG or WebP image")
    return raw, ext


def _save_shot(notion: NotionJournal, e: JournalEntry, data_url: str) -> None:
    raw, ext = _decode_shot(data_url)
    notion.shots_dir.mkdir(parents=True, exist_ok=True)
    old = notion.shot_path(e)
    version = (e.screenshot_version or 0) + 1
    name = f"{e.id}-{version}.{ext}"
    (notion.shots_dir / name).write_bytes(raw)
    if old is not None and old.name != name:
        try:
            old.unlink()
        except OSError:
            pass
    e.screenshot = name
    e.screenshot_version = version


def _queue(e: JournalEntry, notion: NotionJournal) -> None:
    e.notion_status = "pending" if notion.enabled else "local"
    e.notion_error = ""


async def _changed(request: Request, entry_id: str) -> None:
    _notion(request).wake()
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is not None:
        await manager.broadcast({"type": "journal_changed", "id": entry_id})


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=List[JournalOut])
def list_entries(
    request: Request,
    symbol: Optional[str] = Query(default=None, max_length=80),
    status: Optional[Literal["open", "closed"]] = None,
    side: Optional[Literal["long", "short"]] = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> List[JournalOut]:
    notion = _notion(request)
    with _db(request).session() as s:
        q = s.query(JournalEntry).filter(JournalEntry.notion_status != "deleting")
        if symbol:
            q = q.filter(JournalEntry.symbol == symbol)
        if status:
            q = q.filter(JournalEntry.status == status)
        if side:
            q = q.filter(JournalEntry.side == side)
        rows = q.order_by(JournalEntry.entry_time.desc(), JournalEntry.created_at.desc()).limit(limit).all()
        return [_out(e, notion) for e in rows]


@router.post("", response_model=JournalOut, status_code=201)
async def create_entry(body: JournalIn, request: Request) -> JournalOut:
    notion = _notion(request)
    data = body.model_dump(exclude={"screenshot"})
    now = now_ms()
    e = JournalEntry(id=uuid.uuid4().hex, created_at=now, updated_at=now, **data)
    if e.entry_time is None:
        e.entry_time = now // 1000
    if e.status == "closed" and e.exit_time is None:
        e.exit_time = now // 1000
    if body.screenshot:
        _save_shot(notion, e, body.screenshot)
    _queue(e, notion)
    with _db(request).session() as s:
        s.add(e)
    log.info("Journal: logged %s %s @ %s", e.side, e.symbol, e.entry_price)
    await _changed(request, e.id)
    return _out(e, notion)


@router.get("/stats")
def stats(request: Request) -> Dict[str, Any]:
    with _db(request).session() as s:
        rows = s.query(JournalEntry).filter(JournalEntry.notion_status != "deleting").all()
        closed = [(e, *trade_metrics(e)) for e in rows if e.status == "closed"]
        open_count = sum(1 for e in rows if e.status == "open")
    pnls = [p for _, p, _ in closed if p is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [r for _, _, r in closed if r is not None]
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for e, p, _ in closed:
        b = by_symbol.setdefault(e.symbol, {"symbol": e.symbol, "trades": 0, "wins": 0, "pnl": 0.0})
        b["trades"] += 1
        b["wins"] += 1 if (p or 0) > 0 else 0
        b["pnl"] += p or 0.0
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "trades": len(rows),
        "open": open_count,
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(pnls) * 100.0) if pnls else None,
        "net_pnl": sum(pnls),
        "avg_win": (gross_win / len(wins)) if wins else None,
        "avg_loss": (-gross_loss / len(losses)) if losses else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_r": (sum(rs) / len(rs)) if rs else None,
        "best": max(pnls) if pnls else None,
        "worst": min(pnls) if pnls else None,
        "by_symbol": sorted(by_symbol.values(), key=lambda b: -abs(b["pnl"])),
    }


@router.get("/export.csv")
def export_csv(request: Request) -> Response:
    notion = _notion(request)
    cols = ["id", "symbol", "provider", "timeframe", "side", "status", "entry_time", "entry_price", "quantity",
            "exit_time", "exit_price", "stop_loss", "take_profit", "fees", "pnl", "r_multiple", "setup", "tags",
            "rating", "notes", "notion_url"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    with _db(request).session() as s:
        rows = s.query(JournalEntry).filter(JournalEntry.notion_status != "deleting").order_by(JournalEntry.entry_time).all()
        for e in rows:
            o = _out(e, notion).model_dump()
            o["entry_time"] = _fmt_time(o["entry_time"])
            o["exit_time"] = _fmt_time(o["exit_time"])
            o["tags"] = ", ".join(o["tags"])
            w.writerow([o.get(c) if o.get(c) is not None else "" for c in cols])
    name = f"trading-journal-{datetime.now().strftime('%Y-%m-%d')}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/notion")
def notion_status(request: Request) -> Dict[str, Any]:
    return _notion(request).status()


@router.post("/notion/retry")
async def notion_retry(request: Request) -> Dict[str, Any]:
    count = await _notion(request).retry()
    return {"retried": count, **_notion(request).status()}


@router.get("/{entry_id}", response_model=JournalOut)
def get_entry(entry_id: str, request: Request) -> JournalOut:
    with _db(request).session() as s:
        e = s.get(JournalEntry, entry_id)
        if e is None or e.notion_status == "deleting":
            raise NotFoundError(f"Journal entry {entry_id} not found")
        return _out(e, _notion(request))


@router.put("/{entry_id}", response_model=JournalOut)
async def update_entry(entry_id: str, body: JournalPatch, request: Request) -> JournalOut:
    notion = _notion(request)
    patch = body.model_dump(exclude_unset=True, exclude={"screenshot", "remove_screenshot", "clear"})
    patch = {k: v for k, v in patch.items() if v is not None}
    bad = [c for c in body.clear if c not in _CLEARABLE]
    if bad:
        raise InvalidRequestError(f"Cannot clear {bad}; clearable fields: {sorted(_CLEARABLE)}")
    with _db(request).session() as s:
        e = s.get(JournalEntry, entry_id)
        if e is None or e.notion_status == "deleting":
            raise NotFoundError(f"Journal entry {entry_id} not found")
        for k, v in patch.items():
            setattr(e, k, v)
        for c in body.clear:
            setattr(e, c, None)
        # validate the merged result with the same rules as a new entry
        merged = {c: getattr(e, c) for c in JournalBase.model_fields}
        try:
            JournalBase(**merged)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(x) for x in first.get("loc", ()) if x != "__root__")
            msg = str(first.get("msg", "Invalid journal entry")).replace("Value error, ", "")
            raise InvalidRequestError(f"{where}: {msg}" if where else msg) from exc
        if e.status == "closed" and e.exit_time is None:
            e.exit_time = now_ms() // 1000
        if body.remove_screenshot:
            old = notion.shot_path(e)
            if old is not None:
                try:
                    old.unlink()
                except OSError:
                    pass
            e.screenshot = None
        if body.screenshot:
            _save_shot(notion, e, body.screenshot)
        e.updated_at = now_ms()
        _queue(e, notion)
        out = _out(e, notion)
    await _changed(request, entry_id)
    return out


@router.delete("/{entry_id}", status_code=204)
async def delete_entry(entry_id: str, request: Request) -> Response:
    notion = _notion(request)
    with _db(request).session() as s:
        e = s.get(JournalEntry, entry_id)
        if e is None:
            raise NotFoundError(f"Journal entry {entry_id} not found")
        if notion.enabled and e.notion_page_id:
            e.notion_status = "deleting"  # the Notion worker trashes the page, then removes the row
            e.updated_at = now_ms()
        else:
            shot = notion.shot_path(e)
            s.delete(e)
            if shot is not None:
                try:
                    shot.unlink()
                except OSError:
                    pass
    await _changed(request, entry_id)
    return Response(status_code=204)


@router.get("/{entry_id}/screenshot")
def get_screenshot(entry_id: str, request: Request) -> FileResponse:
    with _db(request).session() as s:
        e = s.get(JournalEntry, entry_id)
        path = _notion(request).shot_path(e) if e is not None else None
    if path is None:
        raise NotFoundError("No screenshot for this journal entry")
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@router.post("/{entry_id}/sync", response_model=JournalOut)
async def sync_entry(entry_id: str, request: Request) -> JournalOut:
    notion = _notion(request)
    if not notion.enabled:
        raise InvalidRequestError("Notion is not configured: set NOTION_API_KEY and NOTION_DATABASE_ID in backend/.env")
    with _db(request).session() as s:
        e = s.get(JournalEntry, entry_id)
        if e is None or e.notion_status == "deleting":
            raise NotFoundError(f"Journal entry {entry_id} not found")
        e.notion_status = "pending"
        e.notion_error = ""
        out = _out(e, notion)
    await _changed(request, entry_id)
    return out


def _fmt_time(ts: Optional[int]) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
