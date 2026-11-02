"""
Indicator & Pine alerts (v1.1) - evaluated on the server, like TradingView.

An indicator alert stores the script (source + inputs), the chart's provider,
symbol and timeframe, and a *target*:

* ``plot:<title>``            a plotted series compared with a value (or with
                              another plot, ``target2 = plot:<title>``) using the
                              usual conditions (crossing, crossing up, greater than...)
* ``alertcondition:<title>``  fires when the script's alertcondition() is true
* ``alert``                   fires on any alert() call in the script

Frequency:
* ``once_per_bar_close`` (default) - checked when a bar closes, fires at most once per bar
* ``once_per_bar``                - checked on the forming bar every PINE_ALERT_INTERVAL_S
* ``once``                        - first time it is true, then the alert stops

For every (provider, symbol, timeframe) the engine keeps the last PINE_ALERT_BARS
candles (from the candle cache) and updates them from live ticks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.core.config import Settings
from app.core.exceptions import DataBridgeError
from app.db.database import Database
from app.db.models import Alert, AlertEvent, now_ms
from app.models.market import Candle, Tick, Timeframe
from app.providers.registry import ProviderRegistry
from app.services.alert_engine import CONDITION_LABELS, evaluate
from app.services.candle_aggregator import CandleAggregator
from app.services.history_service import HistoryService
from app.services.notifier import Notifier
from app.services.pine_runtime import PineError, PineRuntime

log = logging.getLogger(__name__)

FREQUENCIES = {"once", "once_per_bar", "once_per_bar_close"}
PLOT_CONDITIONS = {"crossing", "crossing_up", "crossing_down", "greater_than", "less_than", "entering_channel", "exiting_channel"}
Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]
SeriesKey = Tuple[str, str, str]


@dataclass
class _IAlert:
    id: str
    name: str
    provider: str
    symbol: str
    timeframe: str
    script_name: str
    source: str
    inputs: Dict[str, Any]
    target: str
    target2: Optional[str]
    condition: str
    price: Optional[float]
    price2: Optional[float]
    frequency: str
    message: str
    expires_at: Optional[int]
    last_bar_time: Optional[int]
    notify_telegram: bool
    fired: bool = False


@dataclass
class _Series:
    provider: str
    symbol: str  # canonical
    timeframe: Timeframe
    digits: int = 5
    bars: List[Candle] = field(default_factory=list)
    agg: Optional[CandleAggregator] = None
    acquired: bool = False
    loaded: bool = False
    alerts: List[_IAlert] = field(default_factory=list)
    closed_pending: bool = False
    dirty: bool = False
    last_eval: float = 0.0
    error: Optional[str] = None


class IndicatorAlertEngine:
    def __init__(
        self,
        db: Database,
        registry: ProviderRegistry,
        history: HistoryService,
        runtime: PineRuntime,
        broadcast: Broadcast,
        notifier: Optional[Notifier],
        settings: Settings,
    ) -> None:
        self.db = db
        self.registry = registry
        self.history = history
        self.runtime = runtime
        self.broadcast = broadcast
        self.notifier = notifier
        self.bars_needed = settings.pine_alert_bars
        self.interval = settings.pine_alert_interval_s
        self.series: Dict[SeriesKey, _Series] = {}
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._background: Set[asyncio.Task] = set()
        self.evaluations = 0

    # ============================================================ lifecycle
    async def start(self) -> None:
        for provider in self.registry.all():
            provider.add_tick_listener(self._on_tick)
        await self.reload()
        self._task = asyncio.create_task(self._loop(), name="indicator-alerts")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for s in self.series.values():
                await self._release(s)
            self.series.clear()

    # ============================================================== loading
    def _rows(self) -> List[Alert]:
        with self.db.session() as s:
            return list(s.query(Alert).filter(Alert.active.is_(True), Alert.kind == "indicator").all())

    async def reload(self) -> None:
        rows = await asyncio.to_thread(self._rows)
        async with self._lock:
            wanted: Dict[SeriesKey, List[_IAlert]] = {}
            for r in rows:
                try:
                    tf = Timeframe(r.timeframe or "M1")
                except ValueError:
                    continue
                try:
                    canonical = self.registry.get(r.provider).normalize_symbol(r.symbol)
                except DataBridgeError:
                    canonical = r.symbol
                key = (r.provider, canonical, tf.value)
                wanted.setdefault(key, []).append(
                    _IAlert(
                        id=r.id, name=r.name or "", provider=r.provider, symbol=r.symbol, timeframe=tf.value,
                        script_name=r.script_name or "", source=r.script_source or "", inputs=dict(r.script_inputs or {}),
                        target=r.target or "", target2=r.target2, condition=r.condition, price=r.price, price2=r.price2,
                        frequency=r.frequency or "once_per_bar_close", message=r.message or "", expires_at=r.expires_at,
                        last_bar_time=r.last_bar_time, notify_telegram=r.notify_telegram is not False,
                    )
                )
            for key in list(self.series):
                if key not in wanted:
                    await self._release(self.series.pop(key))
            for key, alerts in wanted.items():
                s = self.series.get(key)
                if s is None:
                    s = _Series(provider=key[0], symbol=key[1], timeframe=Timeframe(key[2]))
                    self.series[key] = s
                s.alerts = alerts
                if not s.acquired:
                    await self._acquire(s)
        if rows:
            log.info("Indicator alerts: %s active on %s series", len(rows), len(self.series))

    async def _acquire(self, s: _Series) -> None:
        try:
            provider = self.registry.get(s.provider)
            s.symbol = await provider.acquire(s.symbol)
            s.acquired = True
            s.error = None
        except DataBridgeError as exc:
            s.acquired = False
            s.error = exc.message

    async def _release(self, s: _Series) -> None:
        if not s.acquired:
            return
        try:
            await self.registry.get(s.provider).release(s.symbol)
        except Exception:
            log.debug("release failed for %s", s.symbol, exc_info=True)
        s.acquired = False

    async def _load(self, s: _Series) -> None:
        provider = self.registry.get(s.provider)
        bars = await self.history.get(provider, s.symbol, s.timeframe, self.bars_needed)
        if not bars:
            raise DataBridgeError(f"No history for {s.provider}:{s.symbol}")
        s.bars = bars
        s.agg = CandleAggregator(
            s.timeframe, bars[-1], anchor_s=provider.session_anchor_s(s.symbol), real_volume=provider.real_volume
        )
        s.digits = _digits_of(bars)
        s.loaded = True
        s.dirty = True

    # ================================================================ ticks
    async def _on_tick(self, tick: Tick) -> None:
        for s in self.series.values():
            if s.provider != tick.provider or s.symbol != tick.symbol or not s.loaded or s.agg is None:
                continue
            candle = s.agg.update(tick.price, tick.time_ms, tick.volume)
            if candle is None:
                continue
            if s.bars and candle.time == s.bars[-1].time:
                s.bars[-1] = candle
            elif not s.bars or candle.time > s.bars[-1].time:
                s.bars.append(candle)
                s.closed_pending = True  # the previous bar just closed
                if len(s.bars) > self.bars_needed + 100:
                    del s.bars[: len(s.bars) - self.bars_needed]
            s.dirty = True

    # ================================================================= loop
    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                for key, s in list(self.series.items()):
                    try:
                        await self._service(s)
                    except Exception:
                        log.exception("Indicator alert evaluation failed for %s", key)
        except asyncio.CancelledError:
            raise

    async def _service(self, s: _Series) -> None:
        if not s.alerts:
            return
        if not s.acquired:
            await self._acquire(s)
        if not s.loaded:
            try:
                await self._load(s)
            except DataBridgeError as exc:
                s.error = exc.message
                return
        now = time.monotonic()
        if s.closed_pending and len(s.bars) > 1:
            s.closed_pending = False
            closed = s.bars[:-1]
            await self._evaluate(s, closed, at_close=True)
        if s.dirty and now - s.last_eval >= self.interval:
            s.dirty = False
            s.last_eval = now
            await self._evaluate(s, s.bars, at_close=False)

    async def _evaluate(self, s: _Series, bars: List[Candle], at_close: bool) -> None:
        now = now_ms()
        due = []
        for a in s.alerts:
            if a.fired or (a.expires_at and now > a.expires_at):
                continue
            if at_close != (a.frequency == "once_per_bar_close"):
                continue
            due.append(a)
        if not due:
            return
        results: Dict[str, Any] = {}
        bar_time = bars[-1].time
        for a in due:
            cache_key = a.source + "\x00" + json.dumps(a.inputs, sort_keys=True)
            if cache_key not in results:
                try:
                    results[cache_key] = await self.runtime.run(a.source, a.inputs, bars, s.provider, s.symbol, s.timeframe, s.digits)
                    self.evaluations += 1
                except PineError as exc:
                    results[cache_key] = exc
            res = results[cache_key]
            if isinstance(res, PineError):
                await self._set_error(a, str(res))
                continue
            hit, detail = check_target(a, res)
            if not hit:
                continue
            if a.frequency != "once" and a.last_bar_time == bar_time:
                continue  # already fired on this bar
            a.last_bar_time = bar_time
            if a.frequency == "once":
                a.fired = True
            await self._fire(s, a, bars[-1], detail)

    # ================================================================ firing
    def _describe(self, s: _Series, a: _IAlert, bar: Candle, detail: Dict[str, Any]) -> str:
        if detail.get("message") and not a.message:
            return str(detail["message"])
        if a.message:
            text = a.message
            for k, v in {"{{ticker}}": s.symbol, "{{close}}": f"{bar.close}", "{{price}}": f"{bar.close}",
                         "{{interval}}": Timeframe(s.timeframe).label, "{{value}}": _fmt(detail.get("value")),
                         "{{plot}}": _fmt(detail.get("value")), "{{level}}": _fmt(detail.get("level"))}.items():
                text = text.replace(k, v)
            return text
        kind, _, name = a.target.partition(":")
        tf = Timeframe(s.timeframe).label
        if kind == "plot":
            other = a.target2.partition(":")[2] if a.target2 else _fmt(a.price)
            return f"{s.symbol} {tf} · {a.script_name} {name} {CONDITION_LABELS.get(a.condition, a.condition).lower()} {other} (value {_fmt(detail.get('value'))})"
        if kind == "alertcondition":
            return f"{s.symbol} {tf} · {a.script_name}: {name}"
        return f"{s.symbol} {tf} · {a.script_name} alert()"

    def _record(self, a: _IAlert, price: float, text: str, ts: int) -> Optional[str]:
        with self.db.session() as session:
            row = session.get(Alert, a.id)
            if row is None:
                return None
            row.last_triggered_at = ts
            row.trigger_count = (row.trigger_count or 0) + 1
            row.last_bar_time = a.last_bar_time
            row.last_error = None
            if a.frequency == "once":
                row.active = False
                row.status = "triggered"
            session.add(AlertEvent(alert_id=a.id, triggered_at=ts, price=price, message=text))
            return row.status

    async def _fire(self, s: _Series, a: _IAlert, bar: Candle, detail: Dict[str, Any]) -> None:
        ts = now_ms()
        text = self._describe(s, a, bar, detail)
        status = await asyncio.to_thread(self._record, a, bar.close, text, ts)
        if status is None:
            return
        log.info("INDICATOR ALERT %s: %s", a.name or a.id, text)
        payload = {
            "alert_id": a.id, "name": a.name, "provider": a.provider, "symbol": a.symbol, "timeframe": a.timeframe,
            "kind": "indicator", "target": a.target, "condition": a.condition, "price": bar.close,
            "level": detail.get("level"), "value": detail.get("value"), "message": text, "triggered_at": ts, "status": status,
        }
        try:
            await self.broadcast({"type": "alert", "data": payload})
            await self.broadcast({"type": "alerts_changed"})
        except Exception:
            log.exception("Indicator alert broadcast failed")
        if self.notifier is not None:
            self.notifier.notify("alerts", f"🔔 {a.name or a.script_name or a.symbol}",
                                 [text, f"{a.provider}:{a.symbol} {Timeframe(a.timeframe).label} · close {bar.close}"],
                                 payload, telegram=a.notify_telegram)
        if a.frequency == "once":
            s.alerts = [x for x in s.alerts if x.id != a.id]

    async def _set_error(self, a: _IAlert, message: str) -> None:
        def save() -> bool:
            with self.db.session() as session:
                row = session.get(Alert, a.id)
                if row is None or row.last_error == message:
                    return False
                row.last_error = message[:500]
                return True

        if await asyncio.to_thread(save):
            log.warning("Indicator alert %s: %s", a.name or a.id, message)
            await self.broadcast({"type": "alerts_changed"})

    # ================================================================ helpers
    async def check_script(self, provider_name: str, symbol: str, timeframe: Timeframe, source: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Run a script once on the latest bars (used by the alert dialog to list plots and values)."""
        provider = self.registry.get(provider_name)
        canonical = provider.normalize_symbol(symbol)
        bars = await self.history.get(provider, canonical, timeframe, self.bars_needed)
        if not bars:
            raise DataBridgeError(f"No history for {provider_name}:{symbol}")
        return await self.runtime.run(source, inputs, bars, provider.name, canonical, timeframe, _digits_of(bars))

    def stats(self) -> Dict[str, Any]:
        return {
            "runtime": {"available": self.runtime.available, "error": self.runtime.error, "runs": self.runtime.runs},
            "evaluations": self.evaluations,
            "series": [
                {"provider": s.provider, "symbol": s.symbol, "timeframe": s.timeframe.value, "bars": len(s.bars),
                 "alerts": len(s.alerts), "subscribed": s.acquired, "error": s.error}
                for s in self.series.values()
            ],
        }


def check_target(a: Any, res: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """Pure check of one alert target against a script result (unit-tested)."""
    kind, _, name = (a.target or "").partition(":")
    if kind == "alertcondition":
        for x in res.get("alerts") or []:
            if x.get("type") == "alertcondition" and x.get("title") == name:
                return True, {"message": x.get("message")}
        return False, {}
    if kind == "alert":
        for x in res.get("alerts") or []:
            if x.get("type") == "alert":
                return True, {"message": x.get("message")}
        return False, {}
    if kind == "plot":
        series = (res.get("plots") or {}).get(name)
        if not series:
            return False, {}
        curr = _num(series[-1][1])
        prev = _num(series[-2][1]) if len(series) > 1 else None
        if curr is None:
            return False, {}
        level, prev_level = a.price, None
        if a.target2:
            other = (res.get("plots") or {}).get(a.target2.partition(":")[2])
            if not other:
                return False, {}
            level = _num(other[-1][1])
            prev_level = _num(other[-2][1]) if len(other) > 1 else None
            if prev_level is None and a.condition.startswith("crossing"):
                return False, {}
        ok = evaluate(a.condition, prev, curr, level, a.price2, prev_level)
        return ok, {"value": curr, "level": level}
    return False, {}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else None


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _digits_of(bars: List[Candle]) -> int:
    best = 0
    for b in bars[-50:]:
        text = f"{b.close:.8f}".rstrip("0")
        if "." in text:
            best = max(best, len(text.split(".")[1]))
    return min(max(best, 2), 8)
