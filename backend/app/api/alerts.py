"""
Alerts API (Phase 3).

GET    /api/alerts                  all alerts (active first)
POST   /api/alerts                  create
PUT    /api/alerts/{id}             update (also used to pause / resume)
DELETE /api/alerts/{id}
GET    /api/alerts/events?limit=    trigger history (newest first)
DELETE /api/alerts/events           clear the history
GET    /api/alerts/engine           what the engine is watching right now
POST   /api/alerts/{id}/test        fire a test notification for this alert
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.db.database import Database
from app.db.models import Alert, AlertEvent, now_ms
from app.services.alert_engine import CONDITIONS, TRIGGERS

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _db(request: Request) -> Database:
    return request.app.state.db


class TrendLine(BaseModel):
    t1: float
    p1: float
    t2: float
    p2: float
    extendLeft: bool = False
    extendRight: bool = False


class AlertIn(BaseModel):
    name: str = Field(default="", max_length=200)
    provider: str = Field(..., min_length=1, max_length=40)
    symbol: str = Field(..., min_length=1, max_length=80)
    condition: str
    price: Optional[float] = None
    price2: Optional[float] = None
    line: Optional[TrendLine] = None
    trigger: Literal["once", "once_per_minute", "every_time"] = "once"
    message: str = Field(default="", max_length=2000)
    expires_at: Optional[int] = None
    drawing_id: Optional[str] = Field(default=None, max_length=64)
    active: bool = True

    @model_validator(mode="after")
    def _check(self) -> "AlertIn":
        if self.condition not in CONDITIONS:
            raise ValueError(f"condition must be one of {sorted(CONDITIONS)}")
        if self.trigger not in TRIGGERS:
            raise ValueError(f"trigger must be one of {sorted(TRIGGERS)}")
        if self.line is None and self.price is None:
            raise ValueError("price is required (or a trend line)")
        if self.condition in ("entering_channel", "exiting_channel") and self.price2 is None:
            raise ValueError("price2 is required for channel conditions")
        if self.expires_at is not None and self.expires_at < now_ms():
            raise ValueError("expires_at is in the past")
        return self


class AlertOut(BaseModel):
    id: str
    name: str
    provider: str
    symbol: str
    condition: str
    price: Optional[float]
    price2: Optional[float]
    line: Optional[Dict[str, Any]]
    trigger: str
    message: str
    active: bool
    status: str
    expires_at: Optional[int]
    drawing_id: Optional[str]
    created_at: int
    last_triggered_at: Optional[int]
    trigger_count: int


class EventOut(BaseModel):
    id: int
    alert_id: str
    alert_name: str = ""
    symbol: str = ""
    triggered_at: int
    price: float
    message: str


def _out(a: Alert) -> AlertOut:
    return AlertOut(
        id=a.id, name=a.name or "", provider=a.provider, symbol=a.symbol, condition=a.condition, price=a.price, price2=a.price2,
        line=a.line, trigger=a.trigger, message=a.message or "", active=bool(a.active), status=a.status or ("active" if a.active else "paused"),
        expires_at=a.expires_at, drawing_id=a.drawing_id, created_at=a.created_at, last_triggered_at=a.last_triggered_at,
        trigger_count=a.trigger_count or 0,
    )


async def _changed(request: Request) -> None:
    engine = getattr(request.app.state, "alert_engine", None)
    if engine is not None:
        await engine.reload()
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is not None:
        await manager.broadcast({"type": "alerts_changed"})


@router.get("", response_model=List[AlertOut])
def list_alerts(request: Request) -> List[AlertOut]:
    with _db(request).session() as s:
        rows = s.query(Alert).order_by(Alert.active.desc(), Alert.created_at.desc()).all()
        return [_out(a) for a in rows]


@router.post("", response_model=AlertOut, status_code=201)
async def create_alert(body: AlertIn, request: Request) -> AlertOut:
    with _db(request).session() as s:
        row = Alert(
            id=uuid.uuid4().hex[:16], name=body.name, provider=body.provider, symbol=body.symbol, condition=body.condition,
            price=body.price, price2=body.price2, line=body.line.model_dump() if body.line else None, trigger=body.trigger,
            message=body.message, active=body.active, status="active" if body.active else "paused", expires_at=body.expires_at,
            drawing_id=body.drawing_id, created_at=now_ms(), trigger_count=0,
        )
        s.add(row)
        s.flush()
        out = _out(row)
    await _changed(request)
    return out


# NOTE: the static paths below are declared before "/{alert_id}" so they are not shadowed.
@router.get("/events", response_model=List[EventOut])
def list_events(request: Request, limit: int = Query(default=200, ge=1, le=2000)) -> List[EventOut]:
    with _db(request).session() as s:
        rows = (
            s.query(AlertEvent, Alert.name, Alert.symbol)
            .outerjoin(Alert, Alert.id == AlertEvent.alert_id)
            .order_by(AlertEvent.triggered_at.desc())
            .limit(limit)
            .all()
        )
        return [EventOut(id=e.id, alert_id=e.alert_id, alert_name=n or "", symbol=sym or "", triggered_at=e.triggered_at, price=e.price, message=e.message) for e, n, sym in rows]


@router.delete("/events", status_code=204)
async def clear_events(request: Request) -> Response:
    with _db(request).session() as s:
        s.query(AlertEvent).delete()
    return Response(status_code=204)


@router.get("/engine")
def engine_stats(request: Request) -> Dict[str, Any]:
    engine = getattr(request.app.state, "alert_engine", None)
    return engine.stats() if engine is not None else {"feeds": [], "webhook": False}


@router.put("/{alert_id}", response_model=AlertOut)
async def update_alert(alert_id: str, body: AlertIn, request: Request) -> AlertOut:
    with _db(request).session() as s:
        row = s.get(Alert, alert_id)
        if row is None:
            raise NotFoundError(f"Alert {alert_id} not found")
        row.name = body.name
        row.provider = body.provider
        row.symbol = body.symbol
        row.condition = body.condition
        row.price = body.price
        row.price2 = body.price2
        row.line = body.line.model_dump() if body.line else None
        row.trigger = body.trigger
        row.message = body.message
        row.expires_at = body.expires_at
        row.drawing_id = body.drawing_id
        row.active = body.active
        row.status = "active" if body.active else "paused"
        s.flush()
        out = _out(row)
    await _changed(request)
    return out


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(alert_id: str, request: Request) -> Response:
    with _db(request).session() as s:
        row = s.get(Alert, alert_id)
        if row is None:
            raise NotFoundError(f"Alert {alert_id} not found")
        s.query(AlertEvent).filter(AlertEvent.alert_id == alert_id).delete()
        s.delete(row)
    await _changed(request)
    return Response(status_code=204)


@router.post("/{alert_id}/test")
async def test_alert(alert_id: str, request: Request) -> Dict[str, Any]:
    with _db(request).session() as s:
        row = s.get(Alert, alert_id)
        if row is None:
            raise NotFoundError(f"Alert {alert_id} not found")
        payload = {
            "alert_id": row.id, "name": row.name, "provider": row.provider, "symbol": row.symbol, "condition": row.condition,
            "price": row.price, "level": row.price, "message": f"[TEST] {row.message or row.name or row.symbol}", "triggered_at": now_ms(), "status": row.status, "test": True,
        }
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is None:
        raise InvalidRequestError("Stream manager not running")
    await manager.broadcast({"type": "alert", "data": payload})
    return {"ok": True}
