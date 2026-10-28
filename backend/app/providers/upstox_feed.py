"""
Decoder for the Upstox Market Data Feed V3 (binary protobuf messages).

Upstox publishes the schema as MarketDataFeedV3.proto. Rather than shipping
generated protobuf code (which is tied to one version of the ``protobuf``
package), this module reads the protobuf *wire format* directly. That needs no
extra dependency and only decodes the fields the chart uses:

    FeedResponse { Type type = 1; map<string, Feed> feeds = 2;
                   int64 currentTs = 3; MarketInfo marketInfo = 4; }
    Feed         { LTPC ltpc = 1; FullFeed fullFeed = 2;
                   FirstLevelWithGreeks firstLevelWithGreeks = 3; RequestMode requestMode = 4; }
    LTPC         { double ltp = 1; int64 ltt = 2; int64 ltq = 3; double cp = 4; }
    FullFeed     { MarketFullFeed marketFF = 1; IndexFullFeed indexFF = 2; }
    MarketFullFeed { LTPC ltpc = 1; MarketLevel marketLevel = 2; ...; int64 vtt = 6; ... }
    IndexFullFeed  { LTPC ltpc = 1; MarketOHLC marketOHLC = 2; }
    MarketLevel  { repeated Quote bidAskQuote = 1; }
    Quote        { int64 bidQ = 1; double bidP = 2; int64 askQ = 3; double askP = 4; }
    FirstLevelWithGreeks { LTPC ltpc = 1; Quote firstDepth = 2; ...; int64 vtt = 4; }
    MarketInfo   { map<string, MarketStatus> segmentStatus = 1; ... }

Unknown fields are skipped, so new fields added by Upstox never break decoding.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

FEED_TYPES = {0: "initial_feed", 1: "live_feed", 2: "market_info"}
MARKET_STATUS = {
    0: "PRE_OPEN_START",
    1: "PRE_OPEN_END",
    2: "NORMAL_OPEN",
    3: "NORMAL_CLOSE",
    4: "CLOSING_START",
    5: "CLOSING_END",
}


class FeedDecodeError(ValueError):
    """The bytes are not a valid FeedResponse."""


# ---------------------------------------------------------------- wire format
def _varint(buf: bytes, pos: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise FeedDecodeError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise FeedDecodeError("varint too long")


def _fields(buf: bytes) -> List[Tuple[int, int, object]]:
    """Split a message into (field_number, wire_type, raw_value) tuples."""
    out: List[Tuple[int, int, object]] = []
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _varint(buf, pos)
        num, wtype = key >> 3, key & 7
        if wtype == 0:  # varint
            val, pos = _varint(buf, pos)
        elif wtype == 1:  # 64-bit
            if pos + 8 > n:
                raise FeedDecodeError("truncated fixed64")
            val = buf[pos:pos + 8]
            pos += 8
        elif wtype == 2:  # length-delimited
            length, pos = _varint(buf, pos)
            if pos + length > n:
                raise FeedDecodeError("truncated length-delimited field")
            val = buf[pos:pos + length]
            pos += length
        elif wtype == 5:  # 32-bit
            if pos + 4 > n:
                raise FeedDecodeError("truncated fixed32")
            val = buf[pos:pos + 4]
            pos += 4
        else:
            raise FeedDecodeError(f"unsupported wire type {wtype}")
        out.append((num, wtype, val))
    return out


def _double(raw: object) -> float:
    return struct.unpack("<d", raw)[0] if isinstance(raw, (bytes, bytearray)) and len(raw) == 8 else 0.0


def _int64(raw: object) -> int:
    if not isinstance(raw, int):
        return 0
    return raw - (1 << 64) if raw >= (1 << 63) else raw


# --------------------------------------------------------------- data classes
@dataclass
class Quote:
    ltp: float = 0.0
    ltt: int = 0  # last trade time, UNIX ms
    ltq: int = 0  # last trade quantity
    cp: float = 0.0  # previous close
    bid: float = 0.0
    ask: float = 0.0
    vtt: Optional[int] = None  # total traded volume today (full modes only)


@dataclass
class FeedMessage:
    type: str = "initial_feed"  # proto3 omits the default enum value (0)
    current_ts: int = 0
    feeds: Dict[str, Quote] = field(default_factory=dict)
    segment_status: Dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------- decoders
def _ltpc(buf: bytes, q: Quote) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 1:
            q.ltp = _double(val)
        elif num == 2 and wtype == 0:
            q.ltt = _int64(val)
        elif num == 3 and wtype == 0:
            q.ltq = _int64(val)
        elif num == 4 and wtype == 1:
            q.cp = _double(val)


def _quote_level(buf: bytes, q: Quote) -> None:
    """Quote {bidQ=1, bidP=2, askQ=3, askP=4} - best bid/ask."""
    for num, wtype, val in _fields(buf):
        if num == 2 and wtype == 1:
            q.bid = _double(val)
        elif num == 4 and wtype == 1:
            q.ask = _double(val)


def _market_level(buf: bytes, q: Quote) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:  # first entry is the best level
            _quote_level(val, q)
            return


def _market_full(buf: bytes, q: Quote) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:
            _ltpc(val, q)
        elif num == 2 and wtype == 2:
            _market_level(val, q)
        elif num == 6 and wtype == 0:
            q.vtt = _int64(val)


def _index_full(buf: bytes, q: Quote) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:
            _ltpc(val, q)


def _full_feed(buf: bytes, q: Quote) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:
            _market_full(val, q)
        elif num == 2 and wtype == 2:
            _index_full(val, q)


def _first_level_greeks(buf: bytes, q: Quote) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:
            _ltpc(val, q)
        elif num == 2 and wtype == 2:
            _quote_level(val, q)
        elif num == 4 and wtype == 0:
            q.vtt = _int64(val)


def _feed(buf: bytes) -> Quote:
    q = Quote()
    for num, wtype, val in _fields(buf):
        if wtype != 2:
            continue
        if num == 1:
            _ltpc(val, q)
        elif num == 2:
            _full_feed(val, q)
        elif num == 3:
            _first_level_greeks(val, q)
    return q


def _map_entry(buf: bytes) -> Tuple[str, object]:
    key = ""
    value: object = None
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:
            key = bytes(val).decode("utf-8", errors="replace")
        elif num == 2:
            value = val
    return key, value


def _market_info(buf: bytes, msg: FeedMessage) -> None:
    for num, wtype, val in _fields(buf):
        if num == 1 and wtype == 2:
            segment, status = _map_entry(val)
            if segment:
                code = status if isinstance(status, int) else 0
                msg.segment_status[segment] = MARKET_STATUS.get(code, str(code))


def decode_feed_response(data: bytes) -> FeedMessage:
    """Decode one binary WebSocket message from the V3 market data feed."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise FeedDecodeError("feed message must be bytes")
    msg = FeedMessage()
    for num, wtype, val in _fields(bytes(data)):
        if num == 1 and wtype == 0:
            msg.type = FEED_TYPES.get(int(val), str(val))  # type: ignore[arg-type]
        elif num == 2 and wtype == 2:
            key, raw = _map_entry(val)  # type: ignore[arg-type]
            if key and isinstance(raw, (bytes, bytearray)):
                msg.feeds[key] = _feed(raw)
        elif num == 3 and wtype == 0:
            msg.current_ts = _int64(val)
        elif num == 4 and wtype == 2:
            _market_info(val, msg)  # type: ignore[arg-type]
    return msg
