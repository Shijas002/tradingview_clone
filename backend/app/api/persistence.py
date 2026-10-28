"""
Persistence API (Phase 3) - the chart app's workspace lives in SQLite.

GET    /api/workspace                 layout, panes, watchlist, settings (+version)
PUT    /api/workspace                 save it (last write wins, version increments)
GET    /api/drawings                  {symbol_key: [drawings]} for every symbol
PUT    /api/drawings/{symbol_key}     replace the drawings of one symbol
DELETE /api/drawings/{symbol_key}
GET    /api/scripts                   user Pine scripts
PUT    /api/scripts/{id}              create or update a script (client-generated id)
DELETE /api/scripts/{id}
GET    /api/export                    everything as one JSON document (backup, incl. journal)
POST   /api/import                    restore a backup (replaces current data)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.db.database import Database
from app.db.models import Alert, DrawingSet, JournalEntry, Script, Workspace, now_ms

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["persistence"])

WORKSPACE_ID = "default"
MAX_JSON_BYTES = 8 * 1024 * 1024


def _db(request: Request) -> Database:
    return request.app.state.db


# ------------------------------------------------------------------ schemas
class WorkspaceIn(BaseModel):
    data: Dict[str, Any]
    version: Optional[int] = None


class WorkspaceOut(BaseModel):
    data: Dict[str, Any]
    version: int
    updated_at: Optional[int] = None


class DrawingsIn(BaseModel):
    drawings: List[Dict[str, Any]] = Field(default_factory=list)


class ScriptIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source: str = Field(..., max_length=500_000)


class ScriptOut(BaseModel):
    id: str
    name: str
    source: str
    created_at: int
    updated_at: int


# ---------------------------------------------------------------- workspace
@router.get("/workspace", response_model=WorkspaceOut)
def get_workspace(request: Request) -> WorkspaceOut:
    with _db(request).session() as s:
        ws = s.get(Workspace, WORKSPACE_ID)
        if ws is None:
            return WorkspaceOut(data={}, version=0, updated_at=None)
        return WorkspaceOut(data=ws.data or {}, version=ws.version, updated_at=ws.updated_at)


@router.put("/workspace", response_model=WorkspaceOut)
def put_workspace(body: WorkspaceIn, request: Request) -> WorkspaceOut:
    if len(str(body.data)) > MAX_JSON_BYTES:
        raise InvalidRequestError("Workspace too large")
    with _db(request).session() as s:
        ws = s.get(Workspace, WORKSPACE_ID)
        if ws is None:
            ws = Workspace(id=WORKSPACE_ID, data=body.data, version=1, updated_at=now_ms())
            s.add(ws)
        else:
            ws.data = body.data
            ws.version = (ws.version or 0) + 1
            ws.updated_at = now_ms()
        s.flush()
        return WorkspaceOut(data=ws.data, version=ws.version, updated_at=ws.updated_at)


# ----------------------------------------------------------------- drawings
@router.get("/drawings")
def get_drawings(request: Request) -> Dict[str, List[Dict[str, Any]]]:
    with _db(request).session() as s:
        return {d.symbol_key: d.drawings or [] for d in s.query(DrawingSet).all()}


@router.put("/drawings/{symbol_key:path}")
def put_drawings(symbol_key: str, body: DrawingsIn, request: Request) -> Dict[str, Any]:
    key = symbol_key.strip().lower()
    if not key or len(key) > 160:
        raise InvalidRequestError("Invalid symbol key")
    with _db(request).session() as s:
        row = s.get(DrawingSet, key)
        if not body.drawings:
            if row is not None:
                s.delete(row)
            return {"symbol_key": key, "count": 0}
        if row is None:
            s.add(DrawingSet(symbol_key=key, drawings=body.drawings, updated_at=now_ms()))
        else:
            row.drawings = body.drawings
            row.updated_at = now_ms()
        return {"symbol_key": key, "count": len(body.drawings)}


@router.delete("/drawings/{symbol_key:path}", status_code=204)
def delete_drawings(symbol_key: str, request: Request) -> Response:
    with _db(request).session() as s:
        row = s.get(DrawingSet, symbol_key.strip().lower())
        if row is not None:
            s.delete(row)
    return Response(status_code=204)


# ------------------------------------------------------------------ scripts
def _script_out(r: Script) -> ScriptOut:
    return ScriptOut(id=r.id, name=r.name, source=r.source, created_at=r.created_at, updated_at=r.updated_at)


@router.get("/scripts", response_model=List[ScriptOut])
def list_scripts(request: Request) -> List[ScriptOut]:
    with _db(request).session() as s:
        return [_script_out(r) for r in s.query(Script).order_by(Script.name).all()]


@router.put("/scripts/{script_id}", response_model=ScriptOut)
def upsert_script(script_id: str, body: ScriptIn, request: Request) -> ScriptOut:
    if not script_id or len(script_id) > 64:
        raise InvalidRequestError("Invalid script id")
    with _db(request).session() as s:
        row = s.get(Script, script_id)
        if row is None:
            row = Script(id=script_id, name=body.name, source=body.source, created_at=now_ms(), updated_at=now_ms())
            s.add(row)
        else:
            row.name = body.name
            row.source = body.source
            row.updated_at = now_ms()
        s.flush()
        return _script_out(row)


@router.delete("/scripts/{script_id}", status_code=204)
def delete_script(script_id: str, request: Request) -> Response:
    with _db(request).session() as s:
        row = s.get(Script, script_id)
        if row is None:
            raise NotFoundError(f"Script {script_id} not found")
        s.delete(row)
    return Response(status_code=204)


# ------------------------------------------------------------ backup/restore
def _alert_dict(a: Alert) -> Dict[str, Any]:
    return {c.name: getattr(a, c.name) for c in Alert.__table__.columns}


_JOURNAL_SKIP = {"screenshot", "screenshot_version", "notion_image_block_id", "notion_screenshot_version"}


def _journal_dict(j: JournalEntry) -> Dict[str, Any]:
    return {c.name: getattr(j, c.name) for c in JournalEntry.__table__.columns if c.name not in _JOURNAL_SKIP}


@router.get("/export")
def export_all(request: Request) -> Dict[str, Any]:
    with _db(request).session() as s:
        ws = s.get(Workspace, WORKSPACE_ID)
        return {
            "format": "tvclone-backup",
            "version": 1,
            "exported_at": now_ms(),
            "workspace": ws.data if ws else {},
            "drawings": {d.symbol_key: d.drawings for d in s.query(DrawingSet).all()},
            "scripts": [_script_out(r).model_dump() for r in s.query(Script).all()],
            "alerts": [_alert_dict(a) for a in s.query(Alert).all()],
            # journal entries without their screenshot files
            "journal": [_journal_dict(j) for j in s.query(JournalEntry).filter(JournalEntry.notion_status != "deleting").all()],
        }


class ImportIn(BaseModel):
    format: str
    workspace: Dict[str, Any] = Field(default_factory=dict)
    drawings: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    scripts: List[Dict[str, Any]] = Field(default_factory=list)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    journal: Optional[List[Dict[str, Any]]] = None  # absent in Phase 3 backups -> journal left untouched


@router.post("/import")
async def import_all(body: ImportIn, request: Request) -> Dict[str, Any]:
    if body.format != "tvclone-backup":
        raise InvalidRequestError("Not a TradingView Clone backup file")
    alert_cols = {c.name for c in Alert.__table__.columns}
    journal_cols = {c.name for c in JournalEntry.__table__.columns} - _JOURNAL_SKIP
    notion = getattr(request.app.state, "notion", None)
    notion_on = bool(notion is not None and notion.enabled)
    with _db(request).session() as s:
        s.query(DrawingSet).delete()
        s.query(Script).delete()
        s.query(Alert).delete()
        ws = s.get(Workspace, WORKSPACE_ID)
        if ws is None:
            s.add(Workspace(id=WORKSPACE_ID, data=body.workspace, version=1, updated_at=now_ms()))
        else:
            ws.data = body.workspace
            ws.version = (ws.version or 0) + 1
            ws.updated_at = now_ms()
        for key, drawings in body.drawings.items():
            if drawings:
                s.add(DrawingSet(symbol_key=key.lower(), drawings=drawings, updated_at=now_ms()))
        for sc in body.scripts:
            if sc.get("id") and sc.get("name") and isinstance(sc.get("source"), str):
                s.add(Script(id=sc["id"], name=sc["name"], source=sc["source"], created_at=sc.get("created_at") or now_ms(), updated_at=sc.get("updated_at") or now_ms()))
        for a in body.alerts:
            clean = {k: v for k, v in a.items() if k in alert_cols}
            if clean.get("id") and clean.get("provider") and clean.get("symbol") and clean.get("condition"):
                s.add(Alert(**clean))
        if body.journal is not None:
            s.query(JournalEntry).delete()
            for j in body.journal:
                clean = {k: v for k, v in j.items() if k in journal_cols}
                if clean.get("id") and clean.get("symbol") and clean.get("side") in ("long", "short") and clean.get("entry_price"):
                    clean["notion_status"] = "pending" if notion_on else "local"
                    s.add(JournalEntry(**clean))
    engine = getattr(request.app.state, "alert_engine", None)
    if engine is not None:
        await engine.reload()
    if notion is not None:
        notion.wake()
    log.info("Backup imported: %s drawings sets, %s scripts, %s alerts", len(body.drawings), len(body.scripts), len(body.alerts))
    return {"ok": True, "drawings": len(body.drawings), "scripts": len(body.scripts), "alerts": len(body.alerts),
            "journal": len(body.journal or [])}
