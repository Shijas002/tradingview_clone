"""
Pydantic models shared by REST, WebSocket and provider code.

Time conventions (important for lightweight-charts in Phase 2):
* ``Candle.time`` is the bar's open time as a UNIX timestamp in **seconds**.
* ``Tick.time_ms`` is the tick time as a UNIX timestamp in **milliseconds**.
* MT5 timestamps are broker *server time* encoded as if it were UTC. We pass them
  through unchanged so the chart shows exactly the same times as your MT5 terminal.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def seconds(self) -> int:
        """Nominal length in seconds (MN1 uses 30 days, only for estimates)."""
        return _TIMEFRAME_SECONDS[self]

    @property
    def label(self) -> str:
        return _TIMEFRAME_LABELS[self]


_TIMEFRAME_SECONDS: Dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
    Timeframe.MN1: 2592000,
}

_TIMEFRAME_LABELS: Dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
    Timeframe.W1: "1W",
    Timeframe.MN1: "1M",
}


def bucket_start(ts_seconds: int, timeframe: Timeframe, anchor_s: int = 0) -> int:
    """
    Return the open time (seconds) of the bar that contains ``ts_seconds``.

    * Intraday bars: floor on the period. ``anchor_s`` (seconds after 00:00 UTC)
      shifts the grid so bars start at the exchange's session open - e.g. NSE opens
      09:15 IST = 03:45 UTC, so ``anchor_s=13500`` gives 1h bars at 09:15, 10:15 ...
      and 4h bars at 09:15, 13:15 IST, exactly like TradingView. 0 = clock aligned.
    * Daily bars: 00:00 UTC (the anchor is ignored).
    * Weekly bars: start on Sunday 00:00 (MetaTrader 5 convention).
    * Monthly bars: start on the 1st of the month 00:00.
    """
    if timeframe in (Timeframe.W1, Timeframe.MN1):
        dt = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
        if timeframe is Timeframe.W1:
            # Python: Monday=0 ... Sunday=6  -> days since last Sunday
            days_since_sunday = (dt.weekday() + 1) % 7
            start = (dt - timedelta(days=days_since_sunday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return calendar.timegm(start.utctimetuple())
    period = timeframe.seconds
    if timeframe is Timeframe.D1 or not anchor_s:
        return ts_seconds - (ts_seconds % period)
    offset = anchor_s % period
    return ts_seconds - ((ts_seconds - offset) % period)


class Candle(BaseModel):
    time: int = Field(..., description="Bar open time, UNIX seconds")
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Tick(BaseModel):
    provider: str
    symbol: str
    time_ms: int = Field(..., description="Tick time, UNIX milliseconds")
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: float = 0.0
    price: float = Field(..., description="Price used for charting (bid for FX, last for exchange symbols)")


class SymbolInfo(BaseModel):
    provider: str
    symbol: str
    description: str = ""
    category: str = ""
    digits: int = 5
    currency_base: str = ""
    currency_profit: str = ""


class ProviderStatus(BaseModel):
    name: str
    connected: bool
    message: str = ""
    details: Dict[str, Union[str, int, float, bool, None]] = Field(default_factory=dict)


class HistoryResponse(BaseModel):
    provider: str
    symbol: str
    timeframe: Timeframe
    count: int
    candles: List[Candle]


# ---------------------------------------------------------------------------
# WebSocket client -> server messages
# ---------------------------------------------------------------------------
class SubscribeMessage(BaseModel):
    action: Literal["subscribe"]
    provider: str
    symbol: str = Field(..., min_length=1, max_length=64)
    timeframe: Timeframe = Timeframe.M1


class UnsubscribeMessage(BaseModel):
    action: Literal["unsubscribe"]
    provider: str
    symbol: str = Field(..., min_length=1, max_length=64)
    timeframe: Timeframe = Timeframe.M1


class PingMessage(BaseModel):
    action: Literal["ping"]
    ts: Optional[int] = None


class ListSubscriptionsMessage(BaseModel):
    action: Literal["list_subscriptions"]


ClientMessage = Union[SubscribeMessage, UnsubscribeMessage, PingMessage, ListSubscriptionsMessage]
