"""
AlertEngine - evaluates price and trend-line alerts on every tick, server-side.

Alerts keep working while no browser is open: the engine subscribes to the
provider feeds it needs (reference-counted, so it never disturbs charts), checks
each tick against the active alerts of that symbol and, when one fires:

  1. stores an AlertEvent row,
  2. updates the alert (count, last time; "once" alerts become "triggered"),
  3. broadcasts {"type": "alert"} to every WebSocket client (toast + sound + browser notification),
  4. optionally POSTs the event as JSON to ALERT_WEBHOOK_URL.

Conditions (level = fixed price, or the trend line's price at the tick time):
  crossing, crossing_up, crossing_down, greater_than, less_than,
  entering_channel, exiting_channel (between price and price2)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import httpx

from app.core.exceptions import DataBridgeError
from app.db.database import Database
from app.db.models import Alert, AlertEvent, now_ms
from app.models.market import Tick
from app.providers.registry import ProviderRegistry

log = logging.getLogger(__name__)

CONDITIONS = {"crossing", "crossing_up", "crossing_down", "greater_than", "less_than", "entering_channel", "exiting_channel"}
TRIGGERS = {"once": 0, "once_per_minute": 60_000, "every_time": 1_000}
CONDITION_LABELS = {
    "crossing": "Crossing",
    "crossing_up": "Crossing up",
    "crossing_down": "Crossing down",
    "greater_than": "Greater than",
    "less_than": "Less than",
    "entering_channel": "Entering channel",
    "exiting_channel": "Exiting channel",
}

Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]


def line_level(line: Dict[str, Any], t_seconds: float) -> Optional[float]:
    """Price of a trend line {t1,p1,t2,p2,extendLeft,extendRight} at time t (None outside the segment)."""
    try:
        t1, p1, t2, p2 = float(line["t1"]), float(line["p1"]), float(line["t2"]), float(line["p2"])
    except (KeyError, TypeError, ValueError):
        return None
    if t1 == t2:
        return None
    if t1 > t2:
        t1, p1, t2, p2 = t2, p2, t1, p1
    ext_l = bool(line.get("extendLeft"))
    ext_r = bool(line.get("extendRight"))
    if (t_seconds < t1 and not ext_l) or (t_seconds > t2 and not ext_r):
        return None
    return p1 + (p2 - p1) * (t_seconds - t1) / (t2 - t1)


def evaluate(condition: str, prev: Optional[float], curr: float, level: Optional[float], level2: Optional[float] = None, prev_level: Optional[float] = None) -> bool:
    """Pure condition check (unit-tested). prev_level lets moving trend lines be compared correctly."""
    if level is None:
        return False
    pl = level if prev_level is None else prev_level
    if condition == "greater_than":
        return curr > level
    if condition == "less_than":
        return curr < level
    if prev is None:
        return False
    if condition == "crossing_up":
        return prev < pl and curr >= level
    if condition == "crossing_down":
        return prev > pl and curr <= level
    if condition == "crossing":
        return (prev < pl and curr >= level) or (prev > pl and curr <= level)
    if condition in ("entering_channel", "exiting_channel"):
        if level2 is None:
            return False
        lo, hi = min(level, level2), max(level, level2)
        was_in = lo <= prev <= hi
        is_in = lo <= curr <= hi
        return (not was_in and is_in) if condition == "entering_channel" else (was_in and not is_in)
    return False


@dataclass
class _LiveAlert:
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
    expires_at: Optional[int]
    last_triggered_at: Optional[int]
    trigger_count: int
    feed: Tuple[str, str] = ("", "")
    prev_level: Optional[float] = None
    fired: bool = False  # "once" alerts: guard against double firing before the DB round-trip

    def level_at(self, t_seconds: float) -> Optional[float]:
        if self.line:
            return line_level(self.line, t_seconds)
        return self.price


@dataclass
class _Feed:
    provider: str
    symbol: str  # canonical symbol as carried by ticks
    acquired: bool = False
    last_price: Optional[float] = None
    alerts: List[_LiveAlert] = field(default_factory=list)


class AlertEngine:
    def __init__(self, db: Database, registry: ProviderRegistry, broadcast: Broadcast, webhook_url: str = "") -> None:
        self.db = db
        self.registry = registry
        self.broadcast = broadcast
        self.webhook_url = webhook_url.strip()
        self.feeds: Dict[Tuple[str, str], _Feed] = {}
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._http: Optional[httpx.AsyncClient] = None
        self._background: Set[asyncio.Task] = set()

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        for provider in self.registry.all():
            provider.add_tick_listener(self._on_tick)
        if self.webhook_url:
            self._http = httpx.AsyncClient(timeout=8.0)
        await self.reload()
        self._task = asyncio.create_task(self._maintenance(), name="alert-maintenance")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for t in list(self._background):
            t.cancel()
        async with self._lock:
            for feed in self.feeds.values():
                await self._release(feed)
            self.feeds.clear()
        if self._http:
            await self._http.aclose()

    async def _maintenance(self) -> None:
        """Every 10 s: retry feeds whose provider was offline, expire old alerts."""
        try:
            while True:
                await asyncio.sleep(10)
                try:
                    await self._expire()
                    async with self._lock:
                        for feed in self.feeds.values():
                            if not feed.acquired:
                                await self._acquire(feed)
                except Exception:
                    log.exception("Alert maintenance failed")
        except asyncio.CancelledError:
            raise

    # ----------------------------------------------------------- feeds
    async def _acquire(self, feed: _Feed) -> None:
        try:
            provider = self.registry.get(feed.provider)
            feed.symbol = await provider.acquire(feed.symbol)
            feed.acquired = True
            log.info("Alert feed active: %s:%s (%s alerts)", feed.provider, feed.symbol, len(feed.alerts))
        except DataBridgeError as exc:
            feed.acquired = False
            log.warning("Alert feed %s:%s not available yet: %s", feed.provider, feed.symbol, exc.message)

    async def _release(self, feed: _Feed) -> None:
        if not feed.acquired:
            return
        try:
            await self.registry.get(feed.provider).release(feed.symbol)
        except Exception:
            log.exception("Releasing alert feed %s:%s failed", feed.provider, feed.symbol)
        feed.acquired = False

    def _feed_key(self, provider_name: str, symbol: str) -> Tuple[str, str]:
        try:
            canonical = self.registry.get(provider_name).normalize_symbol(symbol)
        except DataBridgeError:
            canonical = symbol
        return (provider_name, canonical.lower())

    # ----------------------------------------------------------- loading
    def _load_rows(self) -> List[Alert]:
        with self.db.session() as s:
            return list(s.query(Alert).filter(Alert.active.is_(True)).all())

    async def reload(self) -> None:
        """Re-read active alerts from the database and adjust feed subscriptions."""
        rows = await asyncio.to_thread(self._load_rows)
        async with self._lock:
            wanted: Dict[Tuple[str, str], List[_LiveAlert]] = {}
            for r in rows:
                key = self._feed_key(r.provider, r.symbol)
                live = _LiveAlert(
                    id=r.id, name=r.name, provider=r.provider, symbol=r.symbol, condition=r.condition, price=r.price,
                    price2=r.price2, line=r.line, trigger=r.trigger, message=r.message, expires_at=r.expires_at,
                    last_triggered_at=r.last_triggered_at, trigger_count=r.trigger_count, feed=key,
                )
                wanted.setdefault(key, []).append(live)
            for key in list(self.feeds):
                if key not in wanted:
                    await self._release(self.feeds.pop(key))
            for key, alerts in wanted.items():
                feed = self.feeds.get(key)
                if feed is None:
                    feed = _Feed(provider=key[0], symbol=alerts[0].symbol)
                    self.feeds[key] = feed
                    feed.alerts = alerts
                    await self._acquire(feed)
                else:
                    feed.alerts = alerts
        log.info("Alert engine loaded %s active alert(s) on %s feed(s)", len(rows), len(self.feeds))

    # ------------------------------------------------------------ ticks
    async def _on_tick(self, tick: Tick) -> None:
        feed = self.feeds.get((tick.provider, tick.symbol.lower()))
        if feed is None or not feed.alerts:
            return
        prev = feed.last_price
        curr = tick.price
        feed.last_price = curr
        t = tick.time_ms / 1000.0
        now = now_ms()
        for a in list(feed.alerts):
            if a.fired:
                continue
            if a.expires_at and now > a.expires_at:
                continue
            level = a.level_at(t)
            level2 = a.price2
            if evaluate(a.condition, prev, curr, level, level2, a.prev_level if a.line else None):
                cooldown = TRIGGERS.get(a.trigger, 0)
                if a.trigger != "once" and a.last_triggered_at and now - a.last_triggered_at < cooldown:
                    a.prev_level = level
                    continue
                a.last_triggered_at = now
                a.trigger_count += 1
                if a.trigger == "once":
                    a.fired = True
                task = asyncio.create_task(self._fire(a, curr, level, now))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            a.prev_level = level

    def _describe(self, a: _LiveAlert, price: float, level: Optional[float]) -> str:
        if a.message:
            return (
                a.message.replace("{{ticker}}", a.symbol)
                .replace("{{close}}", f"{price}")
                .replace("{{price}}", f"{price}")
                .replace("{{level}}", "" if level is None else f"{level:.6g}")
            )
        target = "trend line" if a.line else (f"{a.price}" if a.price2 is None else f"{a.price} – {a.price2}")
        return f"{a.symbol} {CONDITION_LABELS.get(a.condition, a.condition).lower()} {target} (price {price})"

    def _record(self, a: _LiveAlert, price: float, text: str, ts: int) -> Optional[Dict[str, Any]]:
        with self.db.session() as s:
            row = s.get(Alert, a.id)
            if row is None:
                return None
            row.last_triggered_at = ts
            row.trigger_count = (row.trigger_count or 0) + 1
            if row.trigger == "once":
                row.active = False
                row.status = "triggered"
            s.add(AlertEvent(alert_id=a.id, triggered_at=ts, price=price, message=text))
            return {"id": row.id, "status": row.status}

    async def _fire(self, a: _LiveAlert, price: float, level: Optional[float], ts: int) -> None:
        text = self._describe(a, price, level)
        try:
            saved = await asyncio.to_thread(self._record, a, price, text, ts)
        except Exception:
            log.exception("Could not store alert event %s", a.id)
            saved = None
        if saved is None:
            return
        log.info("ALERT %s: %s", a.name or a.id, text)
        payload = {
            "alert_id": a.id,
            "name": a.name,
            "provider": a.provider,
            "symbol": a.symbol,
            "condition": a.condition,
            "price": price,
            "level": level,
            "message": text,
            "triggered_at": ts,
            "status": saved["status"],
        }
        try:
            await self.broadcast({"type": "alert", "data": payload})
            await self.broadcast({"type": "alerts_changed"})
        except Exception:
            log.exception("Alert broadcast failed")
        if a.trigger == "once":
            async with self._lock:
                feed = self.feeds.get(a.feed)
                if feed:
                    feed.alerts = [x for x in feed.alerts if x.id != a.id]
                    if not feed.alerts:
                        await self._release(self.feeds.pop(a.feed))
        if self._http and self.webhook_url:
            try:
                r = await self._http.post(self.webhook_url, json=payload)
                if r.status_code >= 300:
                    log.warning("Alert webhook returned HTTP %s: %s", r.status_code, r.text[:200])
            except httpx.HTTPError as exc:
                log.warning("Alert webhook failed: %s", exc)

    # ----------------------------------------------------------- expiry
    def _expire_rows(self) -> int:
        now = now_ms()
        with self.db.session() as s:
            rows = s.query(Alert).filter(Alert.active.is_(True), Alert.expires_at.isnot(None), Alert.expires_at < now).all()
            for r in rows:
                r.active = False
                r.status = "expired"
            return len(rows)

    async def _expire(self) -> None:
        n = await asyncio.to_thread(self._expire_rows)
        if n:
            log.info("%s alert(s) expired", n)
            await self.reload()
            await self.broadcast({"type": "alerts_changed"})

    # ------------------------------------------------------------ stats
    def stats(self) -> Dict[str, Any]:
        return {
            "feeds": [
                {"provider": f.provider, "symbol": f.symbol, "subscribed": f.acquired, "alerts": len(f.alerts), "last_price": f.last_price}
                for f in self.feeds.values()
            ],
            "webhook": bool(self.webhook_url),
            "checked_at": int(time.time()),
        }
