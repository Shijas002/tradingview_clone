"""
Builds live OHLCV candles from a stream of ticks for one symbol + timeframe.

The aggregator is seeded with the most recent historical bar so the live
(forming) candle continues the real bar instead of starting from the first tick
we happen to see. Volume is *tick volume* (one unit per tick), matching the
``tick_volume`` column MetaTrader 5 returns for historical bars.
"""

from __future__ import annotations

from typing import Optional

from app.models.market import Candle, Timeframe, bucket_start


class CandleAggregator:
    def __init__(self, timeframe: Timeframe, seed: Optional[Candle] = None) -> None:
        self.timeframe = timeframe
        self._current: Optional[Candle] = seed.model_copy() if seed is not None else None

    @property
    def current(self) -> Optional[Candle]:
        return self._current.model_copy() if self._current is not None else None

    def update(self, price: float, time_ms: int, volume: float = 1.0) -> Optional[Candle]:
        """
        Apply one tick. Returns a copy of the updated (or newly opened) candle,
        or ``None`` when the tick is older than the current bar and was ignored.
        """
        if price <= 0:
            return None
        bar_time = bucket_start(time_ms // 1000, self.timeframe)
        cur = self._current

        if cur is None or bar_time > cur.time:
            self._current = Candle(
                time=bar_time, open=price, high=price, low=price, close=price, volume=volume
            )
        elif bar_time == cur.time:
            cur.high = max(cur.high, price)
            cur.low = min(cur.low, price)
            cur.close = price
            cur.volume += volume
        else:
            return None  # late tick belonging to an already-closed bar
        return self._current.model_copy()
