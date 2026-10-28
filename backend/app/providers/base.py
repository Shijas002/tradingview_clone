"""
Abstract market-data provider.

Every data source (MetaTrader 5, OANDA, the simulator, the "auto" failover
provider and later Upstox / Kite / Angel One) implements this interface. The
rest of the application never talks to a broker SDK directly - it only calls
these methods, which is what makes adding a new broker a single-file change.

Live data is *pushed* out of a provider through listeners:
* tick listeners   - called for every new tick of a subscribed symbol
* status listeners - called when the provider connects / disconnects
Several listeners can be attached (the StreamManager and the failover provider
both listen to MT5, for example).

Subscriptions are reference-counted through ``acquire`` / ``release`` so two
independent consumers of the same symbol never unsubscribe each other.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional, Set, Union

from app.models.market import Candle, ProviderStatus, SymbolInfo, Tick, Timeframe

TickHandler = Callable[[Tick], Awaitable[None]]
StatusHandler = Callable[[ProviderStatus], Awaitable[None]]
DetailValue = Union[str, int, float, bool, None]


class MarketDataProvider(ABC):
    """Base class with shared bookkeeping for subscriptions and listeners."""

    #: unique short name used in URLs and WebSocket messages, e.g. "mt5"
    name: str = "base"

    def __init__(self) -> None:
        self.log = logging.getLogger(f"app.providers.{self.name}")
        self._tick_listeners: List[TickHandler] = []
        self._status_listeners: List[StatusHandler] = []
        self._subscribed: Set[str] = set()
        self._sub_refs: Dict[str, int] = {}
        self._connected: bool = False
        self._status_message: str = "Not connected"
        self._details: Dict[str, DetailValue] = {}

    # ------------------------------------------------------------ listeners
    def add_tick_listener(self, handler: TickHandler) -> None:
        if handler not in self._tick_listeners:
            self._tick_listeners.append(handler)

    def add_status_listener(self, handler: StatusHandler) -> None:
        if handler not in self._status_listeners:
            self._status_listeners.append(handler)

    async def _emit_tick(self, tick: Tick) -> None:
        for listener in list(self._tick_listeners):
            try:
                await listener(tick)
            except Exception:  # never let a consumer bug kill the feed loop
                self.log.exception("Tick listener raised for %s", tick.symbol)

    async def _emit_status(self) -> None:
        status = self.status()
        for listener in list(self._status_listeners):
            try:
                await listener(status)
            except Exception:
                self.log.exception("Status listener raised")

    async def _set_status(self, connected: bool, message: str, force: bool = False) -> None:
        changed = connected != self._connected or message != self._status_message
        self._connected = connected
        self._status_message = message
        if changed or force:
            level = logging.INFO if connected else logging.WARNING
            self.log.log(level, "Status -> connected=%s (%s)", connected, message)
            await self._emit_status()

    # ---------------------------------------------------------------- state
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def subscribed_symbols(self) -> Set[str]:
        return set(self._subscribed)

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            connected=self._connected,
            message=self._status_message,
            details=dict(self._details),
        )

    def normalize_symbol(self, symbol: str) -> str:
        """Return the provider's canonical spelling of ``symbol`` (identity by default)."""
        return symbol.strip()

    # ------------------------------------------------- ref-counted subscribe
    async def acquire(self, symbol: str) -> str:
        """Subscribe on first use; returns the canonical symbol the ticks will carry."""
        canonical = self.normalize_symbol(symbol)
        if self._sub_refs.get(canonical, 0) == 0:
            await self.subscribe(canonical)
        self._sub_refs[canonical] = self._sub_refs.get(canonical, 0) + 1
        return canonical

    async def release(self, symbol: str) -> None:
        """Unsubscribe when the last user lets go."""
        canonical = self.normalize_symbol(symbol)
        refs = self._sub_refs.get(canonical, 0)
        if refs <= 1:
            self._sub_refs.pop(canonical, None)
            if refs == 1:
                await self.unsubscribe(canonical)
        else:
            self._sub_refs[canonical] = refs - 1

    # ------------------------------------------------------- abstract API
    @abstractmethod
    async def connect(self) -> None:
        """Open the connection. Should raise ProviderConnectionError on failure."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop background tasks and release resources."""

    @abstractmethod
    async def get_symbols(self, query: Optional[str] = None, limit: int = 200) -> List[SymbolInfo]:
        """Return tradable symbols, optionally filtered by a case-insensitive query."""

    @abstractmethod
    async def get_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        to: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Return up to ``count`` candles in ascending time order.
        If ``to`` is given, return bars that open strictly before it
        (used by the chart to lazy-load older history).
        """

    @abstractmethod
    async def subscribe(self, symbol: str) -> None:
        """Start pushing ticks for ``symbol`` (use ``acquire`` from outside)."""

    @abstractmethod
    async def unsubscribe(self, symbol: str) -> None:
        """Stop pushing ticks for ``symbol`` (use ``release`` from outside)."""
