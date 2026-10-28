"""
"auto" provider - automatic failover between a primary and a fallback source.

Default wiring (see .env): primary = MetaTrader 5, fallback = OANDA.

Rules
-----
* While the primary is connected, all data comes from it.
* The moment the primary disconnects, every subscribed symbol is moved to the
  fallback (OANDA) and a ``status`` message tells clients the source changed.
* When the primary comes back it must stay healthy for
  FAILOVER_SWITCH_BACK_DELAY_S seconds before data switches back (prevents
  flapping on an unstable connection).
* If both sources are down the provider reports ``connected: false`` and keeps
  its subscriptions, so data resumes automatically when either recovers.

Symbols & time
--------------
Clients always use the primary's (MT5) symbol names, e.g. "EURUSD.m". The
fallback translates them (EURUSD.m -> EUR_USD). OANDA timestamps are UTC, MT5
uses broker server time, so fallback data is shifted by the MT5 server offset
(auto-detected or MT5_SERVER_UTC_OFFSET_HOURS) to keep one consistent timeline.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from app.core.exceptions import DataBridgeError, ProviderUnavailableError
from app.models.market import Candle, ProviderStatus, SymbolInfo, Tick, Timeframe
from app.providers.base import MarketDataProvider

PRIMARY = "primary"
FALLBACK = "fallback"


class FailoverProvider(MarketDataProvider):
    name = "auto"

    def __init__(
        self,
        primary: MarketDataProvider,
        fallback: MarketDataProvider,
        switch_back_delay_s: float = 15.0,
        check_interval_s: float = 1.0,
    ) -> None:
        super().__init__()
        self.primary = primary
        self.fallback = fallback
        self._switch_back_delay_s = switch_back_delay_s
        self._check_interval_s = check_interval_s
        self._active: Optional[str] = None
        self._acquired: Dict[str, Tuple[str, str]] = {}  # requested -> (role, source symbol)
        self._reverse: Dict[Tuple[str, str], Set[str]] = {}  # (role, source symbol) -> requested
        self._unmapped: Set[str] = set()
        self._primary_ok_since: Optional[float] = None
        self._lock = asyncio.Lock()
        self._eval_lock = asyncio.Lock()
        self._monitor: Optional[asyncio.Task] = None
        self._last_shift: Optional[int] = None
        self._switches = 0

    # ============================================================ helpers
    def _src(self, role: Optional[str]) -> Optional[MarketDataProvider]:
        if role == PRIMARY:
            return self.primary
        if role == FALLBACK:
            return self.fallback
        return None

    def _shift_s(self) -> int:
        offset = getattr(self.primary, "time_offset_s", None)
        return int(offset) if offset is not None else 0

    @property
    def active_source(self) -> Optional[str]:
        src = self._src(self._active)
        return src.name if src is not None else None

    # ========================================================== lifecycle
    async def connect(self) -> None:
        self.primary.add_tick_listener(self._on_primary_tick)
        self.fallback.add_tick_listener(self._on_fallback_tick)
        self.primary.add_status_listener(self._on_source_status)
        self.fallback.add_status_listener(self._on_source_status)
        await self._evaluate()
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.create_task(self._monitor_loop(), name="failover-monitor")

    async def disconnect(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            try:
                await self._monitor
            except asyncio.CancelledError:
                pass
            self._monitor = None
        async with self._lock:
            await self._release_all()
        await self._set_status(False, "Stopped")

    async def _monitor_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._check_interval_s)
                try:
                    await self._evaluate()
                except Exception:
                    self.log.exception("Failover evaluation failed")
        except asyncio.CancelledError:
            raise

    async def _on_source_status(self, _status: ProviderStatus) -> None:
        await self._evaluate()

    # ========================================================== switching
    def _desired_role(self) -> Optional[str]:
        now = time.monotonic()
        p_ok, f_ok = self.primary.is_connected, self.fallback.is_connected
        if p_ok:
            if self._primary_ok_since is None:
                self._primary_ok_since = now
        else:
            self._primary_ok_since = None

        if p_ok:
            stable = (now - (self._primary_ok_since or now)) >= self._switch_back_delay_s
            if self._active in (None, PRIMARY) or not f_ok or stable:
                return PRIMARY
            return FALLBACK  # primary is back but not yet stable
        if f_ok:
            return FALLBACK
        return self._active  # both down: keep current subscriptions so they resume

    async def _evaluate(self) -> None:
        async with self._eval_lock:
            desired = self._desired_role()
            if desired != self._active:
                await self._switch(desired)
            await self._publish_status()

    async def _switch(self, new_role: Optional[str]) -> None:
        async with self._lock:
            old_name = self.active_source
            await self._release_all()
            self._active = new_role
            self._switches += 1
            for requested in sorted(self._subscribed):
                try:
                    await self._acquire_for(requested)
                except DataBridgeError:
                    pass  # logged in _acquire_for and reported via status "unmapped_symbols"
            new_name = self.active_source
        if new_role == FALLBACK:
            self.log.warning("Switched data source %s -> %s (primary unavailable)", old_name, new_name)
        else:
            self.log.info("Switched data source %s -> %s", old_name, new_name)

    async def _release_all(self) -> None:
        for requested, (role, symbol) in list(self._acquired.items()):
            src = self._src(role)
            try:
                if src is not None:
                    await src.release(symbol)
            except Exception:
                self.log.exception("Release of %s on %s failed", symbol, role)
        self._acquired.clear()
        self._reverse.clear()

    async def _acquire_for(self, requested: str) -> None:
        """Subscribe ``requested`` on the active source (caller holds the lock)."""
        src = self._src(self._active)
        if src is None or requested in self._acquired:
            return
        try:
            symbol = await src.acquire(requested)
        except DataBridgeError as exc:
            self._unmapped.add(requested)
            self.log.warning("Cannot stream %s from %s: %s", requested, src.name, exc.message)
            raise
        self._unmapped.discard(requested)
        self._acquired[requested] = (self._active, symbol)  # type: ignore[arg-type]
        self._reverse.setdefault((self._active, symbol), set()).add(requested)  # type: ignore[arg-type]

    async def _publish_status(self) -> None:
        src = self._src(self._active)
        shift = self._shift_s()
        connected = src is not None and src.is_connected
        if connected and self._active == PRIMARY:
            message = f"Live from {self.primary.name}"
        elif connected:
            message = f"{self.primary.name} offline - using {self.fallback.name} fallback"
        elif src is not None:
            message = f"{src.name} reconnecting - no data source available right now"
        else:
            message = "No data source available"
        self._details = {
            "active_source": self.active_source,
            f"{self.primary.name}_connected": self.primary.is_connected,
            f"{self.fallback.name}_connected": self.fallback.is_connected,
            "time_shift_hours": shift / 3600.0,
            "unmapped_symbols": ", ".join(sorted(self._unmapped)) or None,
        }
        shift_changed = self._last_shift is not None and shift != self._last_shift
        self._last_shift = shift
        await self._set_status(connected, message, force=shift_changed)

    # ============================================================== ticks
    async def _on_primary_tick(self, tick: Tick) -> None:
        if self._active == PRIMARY:
            await self._forward(PRIMARY, tick, 0)

    async def _on_fallback_tick(self, tick: Tick) -> None:
        if self._active == FALLBACK:
            await self._forward(FALLBACK, tick, self._shift_s())

    async def _forward(self, role: str, tick: Tick, shift_s: int) -> None:
        for requested in list(self._reverse.get((role, tick.symbol), ())):
            await self._emit_tick(
                tick.model_copy(update={"provider": self.name, "symbol": requested,
                                        "time_ms": tick.time_ms + shift_s * 1000})
            )

    # ============================================================ queries
    async def get_symbols(self, query: Optional[str] = None, limit: int = 200) -> List[SymbolInfo]:
        if self._active == FALLBACK and self.fallback.is_connected:
            items = await self.fallback.get_symbols(query=query, limit=limit)
            return [
                s.model_copy(update={"provider": self.name, "symbol": s.symbol.replace("_", ""),
                                     "description": f"{s.description} ({self.fallback.name})"})
                for s in items
            ]
        if self.primary.is_connected:
            items = await self.primary.get_symbols(query=query, limit=limit)
            return [s.model_copy(update={"provider": self.name}) for s in items]
        raise ProviderUnavailableError("No data source available")

    def _query_order(self) -> List[str]:
        order = [self._active] if self._active else []
        order += [r for r in (PRIMARY, FALLBACK) if r not in order]
        return [r for r in order if self._src(r).is_connected]  # type: ignore[union-attr]

    async def get_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        to: Optional[datetime] = None,
    ) -> List[Candle]:
        last_error: Optional[DataBridgeError] = None
        for role in self._query_order():
            try:
                if role == PRIMARY:
                    return await self.primary.get_history(symbol, timeframe, count, to=to)
                if getattr(self.fallback, "supports_time_shift", False):
                    return await self.fallback.get_history(  # type: ignore[call-arg]
                        symbol, timeframe, count, to=to, time_shift_s=self._shift_s()
                    )
                return await self.fallback.get_history(symbol, timeframe, count, to=to)
            except ProviderUnavailableError as exc:
                last_error = exc
                self.log.warning("History from %s failed, trying next source: %s", role, exc.message)
        if last_error is not None:
            raise last_error
        raise ProviderUnavailableError("No data source available for history")

    # ====================================================== subscriptions
    async def subscribe(self, symbol: str) -> None:
        if self._active is None:
            raise ProviderUnavailableError("No data source available (MT5 and fallback are both offline)")
        async with self._lock:
            self._subscribed.add(symbol)
            try:
                await self._acquire_for(symbol)
            except DataBridgeError:
                self._subscribed.discard(symbol)
                self._unmapped.discard(symbol)
                raise
        self.log.info("Subscribed %s via %s", symbol, self.active_source)

    async def unsubscribe(self, symbol: str) -> None:
        async with self._lock:
            self._subscribed.discard(symbol)
            self._unmapped.discard(symbol)
            entry = self._acquired.pop(symbol, None)
            if entry is not None:
                role, source_symbol = entry
                refs = self._reverse.get(entry)
                if refs is not None:
                    refs.discard(symbol)
                    if not refs:
                        self._reverse.pop(entry, None)
                src = self._src(role)
                if src is not None:
                    try:
                        await src.release(source_symbol)
                    except Exception:
                        self.log.exception("Release of %s failed", source_symbol)
        self.log.info("Unsubscribed %s", symbol)
