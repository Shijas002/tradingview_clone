"""
Alerts API (Phase 3).

GET    /api/alerts                  all alerts (active first)
POST   /api/alerts                  create
PUT    /api/alerts/{id}             update (also used to pause / resume)
DELETE /api/alerts/{id}
GET    /api/alerts/events?limit=    trigger history (newest first)
DELETE /api/alerts/events           clear the history
GET    /api/alerts/engine           what the engines are watching right now
POST   /api/alerts/pine/check       run a script once on the latest bars (plots, values, alertconditions)
POST   /api/alerts/{id}/test        fire a test notification for this alert

Kinds: "price" (price level, channel, trend line - evaluated on every tick) and
"indicator" (a plot / alertcondition / alert() of a Pine script - evaluated on the
server by IndicatorAlertEngine, see app/services/indicator_alerts.py).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from app.core.exceptions import InvalidRequestError, NotFoundError
from app.db.database import Database
from app.db.models import Alert, AlertEvent, now_ms
from app.models.market import Timeframe
from app.services.alert_engine import CONDITIONS, TRIGGERS
from app.services.indicator_alerts import FREQUENCIES
from app.services.pine_runtime import PineError, alertcondition_titles, uses_alert_function

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
    kind: Literal["price", "indicator"] = "price"
    condition: str = "crossing"
    price: Optional[float] = None
    price2: Optional[float] = None
    line: Optional[TrendLine] = None
    trigger: Literal["once", "once_per_minute", "every_time"] = "once"
    message: str = Field(default="", max_length=2000)
    expires_at: Optional[int] = None
    drawing_id: Optional[str] = Field(default=None, max_length=64)
    active: bool = True
    notify_telegram: bool = True
    # indicator alerts
    timeframe: Optional[Timeframe] = None
    script_name: str = Field(default="", max_length=200)
    script_source: Optional[str] = Field(default=None, max_length=500_000)
    script_inputs: Optional[Dict[str, Any]] = None
    target: Optional[str] = Field(default=None, max_length=240)
    target2: Optional[str] = Field(default=None, max_length=240)
    frequency: str = "once_per_bar_close"

    @model_validator(mode="after")
    def _check(self) -> "AlertIn":
        if self.expires_at is not None and self.expires_at < now_ms():
            raise ValueError("expires_at is in the past")
        if self.kind == "indicator":
            if not self.script_source or not self.script_source.strip():
                raise ValueError("script_source is required for indicator alerts")
            if self.timeframe is None:
                raise ValueError("timeframe is required for indicator alerts")
            if self.frequency not in FREQUENCIES:
                raise ValueError(f"frequency must be one of {sorted(FREQUENCIES)}")
            kind, _, name = (self.target or "").partition(":")
            if kind == "plot":
                if not name:
                    raise ValueError("target plot name is missing")
                if self.condition not in CONDITIONS:
                    raise ValueError(f"condition must be one of {sorted(CONDITIONS)}")
                if self.target2:
                    if not self.target2.startswith("plot:"):
                        raise ValueError("target2 must look like plot:<title>")
                elif self.price is None:
                    raise ValueError("value is required (or choose another plot to compare with)")
                if self.condition in ("entering_channel", "exiting_channel") and (self.price is None or self.price2 is None):
                    raise ValueError("both channel values are required")
            elif kind in ("alertcondition", "alert"):
                if kind == "alertcondition" and not name:
                    raise ValueError("alertcondition title is missing")
                self.condition = "signal"
            else:
                raise ValueError("target must be plot:<title>, alertcondition:<title> or alert")
            return self
        if self.condition not in CONDITIONS:
            raise ValueError(f"condition must be one of {sorted(CONDITIONS)}")
        if self.trigger not in TRIGGERS:
            raise ValueError(f"trigger must be one of {sorted(TRIGGERS)}")
        if self.line is None and self.price is None:
            raise ValueError("price is required (or a trend line)")
        if self.condition in ("entering_channel", "exiting_channel") and self.price2 is None:
            raise ValueError("price2 is required for channel conditions")
        return self

    def indicator_fields(self) -> Dict[str, Any]:
        if self.kind != "indicator":
            return {"kind": "price", "timeframe": None, "script_name": "", "script_source": None, "script_inputs": None,
                    "target": None, "target2": None, "frequency": "once_per_bar_close"}
        return {"kind": "indicator", "timeframe": self.timeframe.value if self.timeframe else None, "script_name": self.script_name,
                "script_source": self.script_source, "script_inputs": self.script_inputs or {}, "target": self.target,
                "target2": self.target2, "frequency": self.frequency}


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
    kind: str = "price"
    notify_telegram: bool = True
    timeframe: Optional[str] = None
    script_name: str = ""
    script_source: Optional[str] = None
    script_inputs: Optional[Dict[str, Any]] = None
    target: Optional[str] = None
    target2: Optional[str] = None
    frequency: str = "once_per_bar_close"
    last_error: Optional[str] = None


class PineCheckIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=40)
    symbol: str = Field(..., min_length=1, max_length=80)
    timeframe: Timeframe
    source: str = Field(..., min_length=1, max_length=500_000)
    inputs: Dict[str, Any] = Field(default_factory=dict)


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
        trigger_count=a.trigger_count or 0, kind=a.kind or "price", notify_telegram=a.notify_telegram is not False,
        timeframe=a.timeframe, script_name=a.script_name or "", script_source=a.script_source, script_inputs=a.script_inputs,
        target=a.target, target2=a.target2, frequency=a.frequency or "once_per_bar_close", last_error=a.last_error,
    )


async def _changed(request: Request) -> None:
    for name in ("alert_engine", "indicator_alerts"):
        engine = getattr(request.app.state, name, None)
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
            drawing_id=body.drawing_id, created_at=now_ms(), trigger_count=0, notify_telegram=body.notify_telegram,
            **body.indicator_fields(),
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
    out = engine.stats() if engine is not None else {"feeds": []}
    ind = getattr(request.app.state, "indicator_alerts", None)
    out["indicator"] = ind.stats() if ind is not None else None
    return out


@router.post("/pine/check")
async def pine_check(body: PineCheckIn, request: Request) -> Dict[str, Any]:
    """Run the script once on the latest bars: lists plots (with last values) and alertconditions."""
    ind = getattr(request.app.state, "indicator_alerts", None)
    if ind is None:
        raise InvalidRequestError("Indicator alert engine not running")
    if not ind.runtime.available:
        raise InvalidRequestError(ind.runtime.error or "Server-side Pine is not available")
    try:
        res = await ind.check_script(body.provider, body.symbol, body.timeframe, body.source, body.inputs)
    except PineError as exc:
        raise InvalidRequestError(f"Pine error: {exc}") from exc
    plots = [{"title": t, "value": (v[-1][1] if v else None)} for t, v in (res.get("plots") or {}).items()]
    return {
        "title": res.get("title"),
        "plots": plots,
        "alertconditions": alertcondition_titles(body.source),
        "uses_alert": uses_alert_function(body.source),
    }


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
        row.notify_telegram = body.notify_telegram
        row.last_error = None
        row.last_bar_time = None
        for k, v in body.indicator_fields().items():
            setattr(row, k, v)
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
        notify_telegram = row.notify_telegram is not False
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is None:
        raise InvalidRequestError("Stream manager not running")
    await manager.broadcast({"type": "alert", "data": payload})
    notifier = getattr(request.app.state, "notifier", None)
    telegram = False
    if notifier is not None and notify_telegram and notifier.telegram_wants("alerts"):
        telegram = await notifier.send_telegram(notifier.format(f"🔔 TEST · {payload['name'] or payload['symbol']}", [payload["message"]]))
    return {"ok": True, "telegram": telegram}
