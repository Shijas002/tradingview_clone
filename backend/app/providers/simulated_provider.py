"""
Simulated market-data provider.

Generates realistic random-walk prices so you can develop and test the whole
stack (backend + chart UI) before MetaTrader 5 or a broker account is set up.
It behaves exactly like a real provider: it has symbols, history and a live
tick stream.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.core.exceptions import ProviderUnavailableError, SymbolNotFoundError
from app.models.market import Candle, SymbolInfo, Tick, Timeframe, bucket_start
from app.providers.base import MarketDataProvider


@dataclass(frozen=True)
class _SimSymbol:
    symbol: str
    description: str
    category: str
    start_price: float
    digits: int
    spread: float
    # annualised-ish volatility expressed as fraction of price per sqrt(second)
    vol_per_sqrt_s: float


_SYMBOLS: Dict[str, _SimSymbol] = {
    s.symbol: s
    for s in [
        _SimSymbol("EURUSD", "Euro vs US Dollar", "Forex\\Majors", 1.0850, 5, 0.00008, 0.00004),
        _SimSymbol("GBPUSD", "British Pound vs US Dollar", "Forex\\Majors", 1.2700, 5, 0.00010, 0.00005),
        _SimSymbol("USDJPY", "US Dollar vs Japanese Yen", "Forex\\Majors", 151.20, 3, 0.010, 0.00005),
        _SimSymbol("XAUUSD", "Gold vs US Dollar", "Metals", 2350.00, 2, 0.25, 0.00008),
        _SimSymbol("BTCUSD", "Bitcoin vs US Dollar", "Crypto", 65000.0, 2, 15.0, 0.00015),
        _SimSymbol("NIFTY50", "NIFTY 50 Index (simulated)", "India\\Indices", 24500.0, 2, 0.0, 0.00006),
        _SimSymbol("RELIANCE", "Reliance Industries (simulated)", "India\\Equities", 2950.0, 2, 0.0, 0.00009),
    ]
}


class SimulatedProvider(MarketDataProvider):
    name = "simulated"

    def __init__(self, tick_interval_ms: int = 250) -> None:
        super().__init__()
        self._interval_s = tick_interval_ms / 1000.0
        self._prices: Dict[str, float] = {s: spec.start_price for s, spec in _SYMBOLS.items()}
        self._rng = random.Random()
        # (symbol, timeframe) -> {bar_time: open}; lets older pages join without gaps
        self._open_cache: Dict[Tuple[str, str], Dict[int, float]] = {}
        self._task: Optional[asyncio.Task] = None

    # ----------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="simulated-feed")
        self._details = {"symbols": len(_SYMBOLS), "tick_interval_ms": int(self._interval_s * 1000)}
        await self._set_status(True, "Simulator running")

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._set_status(False, "Simulator stopped")

    # ------------------------------------------------------------- queries
    def normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper()

    def _spec(self, symbol: str) -> _SimSymbol:
        spec = _SYMBOLS.get(symbol.upper())
        if spec is None:
            raise SymbolNotFoundError(
                f"Symbol '{symbol}' not available in simulator. Available: {', '.join(_SYMBOLS)}"
            )
        return spec

    async def get_symbols(self, query: Optional[str] = None, limit: int = 200) -> List[SymbolInfo]:
        q = (query or "").strip().upper()
        out = [
            SymbolInfo(
                provider=self.name,
                symbol=spec.symbol,
                description=spec.description,
                category=spec.category,
                digits=spec.digits,
            )
            for spec in _SYMBOLS.values()
            if not q or q in spec.symbol or q in spec.description.upper()
        ]
        return out[:limit]

    async def get_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        to: Optional[datetime] = None,
    ) -> List[Candle]:
        if not self._connected:
            raise ProviderUnavailableError("Simulator is not running")
        spec = self._spec(symbol)
        now_s = int(time.time())
        current_bucket = bucket_start(now_s, timeframe)

        cache = self._open_cache.setdefault((spec.symbol, timeframe.value), {})
        if to is not None:
            to_s = int(to.timestamp())
            end_bucket = bucket_start(to_s - 1, timeframe)
            # Continue seamlessly from the oldest bar the client already has: its open
            # is the close of the bar just before it.
            anchor_price = cache.get(bucket_start(to_s, timeframe), self._prices[spec.symbol])
        else:
            end_bucket = current_bucket
            anchor_price = self._prices[spec.symbol]

        # Build bar open times walking backwards so W1/MN1 stay calendar-correct.
        times: List[int] = []
        t = end_bucket
        for _ in range(count):
            times.append(t)
            t = bucket_start(t - 1, timeframe)

        # Random walk generated BACKWARDS from the anchor (newest close) so every page
        # joins the next one without gaps. Seeded per window so reloads look stable.
        rng = random.Random(f"{spec.symbol}:{timeframe.value}:{times[-1]}:{times[0]}")
        step_vol = spec.vol_per_sqrt_s * (timeframe.seconds ** 0.5)
        floor_price = spec.start_price * 0.05
        price = anchor_price
        candles: List[Candle] = []
        for bar_time in times:  # newest -> oldest
            c = price
            o = max(c / (1 + rng.gauss(0, step_vol)), floor_price)
            h = max(o, c) * (1 + abs(rng.gauss(0, step_vol * 0.6)))
            l = min(o, c) * (1 - abs(rng.gauss(0, step_vol * 0.6)))
            vol = float(int(abs(rng.gauss(1, 0.5)) * timeframe.seconds / 2) + 1)
            candles.append(Candle(time=bar_time, open=o, high=h, low=l, close=c, volume=vol))
            price = o
        candles.reverse()

        if len(cache) > 200_000:
            cache.clear()
        d = spec.digits
        for cd in candles:
            cd.open, cd.high, cd.low, cd.close = (
                round(cd.open, d), round(cd.high, d), round(cd.low, d), round(cd.close, d)
            )
            cache[cd.time] = cd.open
        return candles

    # -------------------------------------------------------- subscriptions
    async def subscribe(self, symbol: str) -> None:
        spec = self._spec(symbol)
        self._subscribed.add(spec.symbol)
        self.log.info("Subscribed %s", spec.symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self._subscribed.discard(symbol.upper())
        self.log.info("Unsubscribed %s", symbol.upper())

    # ---------------------------------------------------------- feed loop
    async def _run(self) -> None:
        self.log.info("Simulator feed loop started (interval=%.3fs)", self._interval_s)
        try:
            while True:
                started = time.monotonic()
                now_ms = int(time.time() * 1000)
                for sym in list(self._subscribed):
                    tick = self._next_tick(sym, now_ms)
                    await self._emit_tick(tick)
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, self._interval_s - elapsed))
        except asyncio.CancelledError:
            self.log.info("Simulator feed loop stopped")
            raise
        except Exception:
            self.log.exception("Simulator feed loop crashed")
            await self._set_status(False, "Simulator crashed - see logs")

    def _next_tick(self, symbol: str, now_ms: int) -> Tick:
        spec = _SYMBOLS[symbol]
        step_vol = spec.vol_per_sqrt_s * (self._interval_s ** 0.5)
        price = self._prices[symbol] * (1 + self._rng.gauss(0, step_vol))
        price = round(max(price, spec.start_price * 0.05), spec.digits)
        self._prices[symbol] = price
        is_fx_like = spec.spread > 0
        bid = price
        ask = round(price + spec.spread, spec.digits) if is_fx_like else price
        return Tick(
            provider=self.name,
            symbol=symbol,
            time_ms=now_ms,
            bid=bid,
            ask=ask,
            last=0.0 if is_fx_like else price,
            volume=0.0 if is_fx_like else float(self._rng.randint(1, 500)),
            price=price,
        )
