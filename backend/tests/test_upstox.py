"""
Tests for the Upstox provider (Phase 4).

Upstox is replaced by an in-memory fake (httpx.MockTransport for REST + the
instrument files, and a fake WebSocket), so no network, account or token is needed.
The protobuf fixtures were produced with Upstox's official MarketDataFeedV3.proto.
"""

import asyncio
import gzip
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, ProviderUnavailableError, SymbolNotFoundError
from app.main import create_app
from app.models.market import Tick, Timeframe, bucket_start
from app.providers.upstox_auth import IST, UpstoxAuth, next_token_expiry
from app.providers.upstox_feed import decode_feed_response
from app.providers.upstox_instruments import parse_instruments
from app.providers.upstox_provider import UpstoxProvider, rebucket
from app.services.candle_aggregator import CandleAggregator

# --- protobuf fixtures (serialized with the official schema) -------------------------
FULL_HEX = (
    "080112240a0c4e53455f464f7c313233343512140a1209000000000050594010a8f19bcea233184b12350a124e53455f494e4445"
    "587c4e69667479203530121f121d121b0a1909cdcccccc9c48d9401090f99bcea23321000000000032d9401286010a134e53455f"
    "45517c494e45303032413031303138126f126d0a6b0a1b09333333333362964010f8809ccea2331819216666666666309640122c"
    "0a16086411000000000062964018322166666666666296400a12119a9999999961964021cdcccccccc62964022110a0f0a024931"
    "11000000000000f03f38052933333333335196403087ad4b18db899ccea233"
)
INFO_HEX = "080218c0fb95cea23322240a0a0a064253455f455110030a0a0a064d43585f464f10000a0a0a064e53455f45511002"
GREEKS_HEX = (
    "12440a0a4e53455f464f7c39393912361a340a10090000000000c04b4010c0c6fdcda2331212113333333333b34b4021cdcccccccc"
    "cc4b401a0909000000000000e03f20a846"
)

RELIANCE_KEY = "NSE_EQ|INE002A01018"
INSTRUMENT_ROWS = {
    "NSE": [
        {"segment": "NSE_EQ", "name": "RELIANCE INDUSTRIES LTD", "exchange": "NSE", "isin": "INE002A01018",
         "instrument_type": "EQ", "instrument_key": RELIANCE_KEY, "lot_size": 1, "tick_size": 5.0,
         "trading_symbol": "RELIANCE", "exchange_token": "2885"},
        {"segment": "NSE_INDEX", "name": "Nifty 50", "exchange": "NSE", "instrument_type": "INDEX",
         "instrument_key": "NSE_INDEX|Nifty 50", "trading_symbol": "NIFTY", "exchange_token": "Nifty 50"},
        {"segment": "NSE_FO", "name": "NIFTY", "exchange": "NSE", "instrument_type": "FUT", "expiry": 1764201599000,
         "instrument_key": "NSE_FO|12345", "lot_size": 75, "tick_size": 10.0, "trading_symbol": "NIFTY FUT 27 NOV 25"},
        {"segment": "NSE_FO", "name": "NIFTY", "exchange": "NSE", "instrument_type": "CE", "expiry": 1764201599000,
         "instrument_key": "NSE_FO|55555", "lot_size": 75, "tick_size": 5.0, "trading_symbol": "NIFTY 26000 CE 27 NOV 25"},
        {"segment": "NSE_FO", "name": "NIFTY", "exchange": "NSE", "instrument_type": "PE", "expiry": 1764201599000,
         "instrument_key": "NSE_FO|999", "lot_size": 75, "tick_size": 5.0, "trading_symbol": "NIFTY 25000 PE 27 NOV 25"},
    ],
    "BSE": [
        {"segment": "BSE_EQ", "name": "RELIANCE INDUSTRIES LTD", "exchange": "BSE", "instrument_type": "A",
         "instrument_key": "BSE_EQ|INE002A01018", "lot_size": 1, "tick_size": 5.0, "trading_symbol": "RELIANCE"},
        {"segment": "BSE_INDEX", "name": "SENSEX", "exchange": "BSE", "instrument_type": "INDEX",
         "instrument_key": "BSE_INDEX|SENSEX", "trading_symbol": "SENSEX"},
    ],
    "MCX": [
        {"segment": "MCX_FO", "name": "CRUDE OIL", "exchange": "MCX", "instrument_type": "FUTCOM", "expiry": 1763560799000,
         "instrument_key": "MCX_FO|440000", "lot_size": 100, "tick_size": 100.0, "trading_symbol": "CRUDEOIL FUT 19 NOV 25"},
    ],
}


class FakeUpstox:
    """Upstox REST API + instrument CDN, serving deterministic minute/day candles."""

    def __init__(self, token: str = "good-token") -> None:
        self.token = token
        self.history_calls: List[Dict[str, Any]] = []
        self.token_calls: List[Dict[str, Any]] = []
        self.downloads: List[str] = []

    # deterministic synthetic market: 375 one-minute bars per weekday from 09:15 IST
    @staticmethod
    def _minute_rows(day: date, interval: int) -> List[List[Any]]:
        if day.weekday() >= 5:
            return []
        rows = []
        start = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
        for i in range(0, 375, interval):
            t = start + timedelta(minutes=i)
            base = 1000 + day.toordinal() % 50 + i / 100
            rows.append([t.isoformat(), base, base + 1, base - 1, base + 0.5, 100 * interval, 0])
        return rows

    @staticmethod
    def _day_row(day: date) -> List[Any]:
        t = datetime(day.year, day.month, day.day, tzinfo=IST)
        base = 1000 + day.toordinal() % 50
        return [t.isoformat(), base, base + 10, base - 10, base + 5, 37500, 0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "assets.upstox.com":
            exchange = url.path.rsplit("/", 1)[-1].split(".")[0]
            self.downloads.append(exchange)
            return httpx.Response(200, content=gzip.compress(json.dumps(INSTRUMENT_ROWS.get(exchange, [])).encode()))
        if url.path == "/v2/login/authorization/token":
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            if form.get("code") != ["good-code"]:
                return httpx.Response(400, json={"status": "error", "errors": [{"errorCode": "UDAPI100057", "message": "Invalid Auth code"}]})
            return httpx.Response(200, json={"email": "someone@example.com", "user_name": "Some One", "user_id": "AB1234",
                                             "access_token": "oauth-token", "extended_token": None, "poa": False})
        auth = request.headers.get("Authorization")
        if auth != f"Bearer {self.token}":
            return httpx.Response(401, json={"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token used to access API"}]})
        if url.path == "/v3/feed/market-data-feed/authorize":
            return httpx.Response(200, json={"status": "success", "data": {"authorizedRedirectUri": "wss://fake.upstox/feed?code=x"}})
        raw = request.url.raw_path.decode().split("?")[0]
        m = re.match(r"^/v3/historical-candle/intraday/([^/]+)/(\w+)/(\d+)$", raw)
        if m:
            unit, interval = m.group(2), int(m.group(3))
            today = datetime.now(IST).date()
            rows = self._day_rows([today]) if unit == "days" else self._minute_rows(today, interval)[:30]
            return httpx.Response(200, json={"status": "success", "data": {"candles": list(reversed(rows))}})
        m = re.match(r"^/v3/historical-candle/([^/]+)/(\w+)/(\d+)/(\d{4}-\d\d-\d\d)/(\d{4}-\d\d-\d\d)$", raw)
        if m:
            key = unquote(m.group(1))
            unit, interval = m.group(2), int(m.group(3))
            to_d, from_d = date.fromisoformat(m.group(4)), date.fromisoformat(m.group(5))
            self.history_calls.append({"key": key, "unit": unit, "interval": interval, "to": to_d, "from": from_d})
            span = (to_d - from_d).days + 1
            if from_d > to_d or (unit == "minutes" and interval <= 15 and span > 31) or (unit == "minutes" and span > 92):
                return httpx.Response(400, json={"status": "error", "errors": [{"errorCode": "UDAPI1148", "message": "Invalid date range"}]})
            today = datetime.now(IST).date()
            days = [from_d + timedelta(days=i) for i in range(span) if from_d + timedelta(days=i) < today]
            if unit == "days":
                rows = self._day_rows(days)
            else:
                rows = [r for d in days for r in self._minute_rows(d, interval)]
            return httpx.Response(200, json={"status": "success", "data": {"candles": list(reversed(rows))}})
        return httpx.Response(404, json={"status": "error", "errors": [{"message": f"no route {raw}"}]})

    def _day_rows(self, days: List[date]) -> List[List[Any]]:
        return [self._day_row(d) for d in days if d.weekday() < 5]


class FakeWS:
    """Async-context-manager WebSocket that the test can push binary frames into."""

    def __init__(self) -> None:
        self.sent: List[Any] = []
        self.inbox: "asyncio.Queue[Any]" = asyncio.Queue()
        self.closed = False

    async def __aenter__(self) -> "FakeWS":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> Any:
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("closed by server")
        return item

    async def close(self) -> None:
        await self.inbox.put(None)


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(
        _env_file=None,
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        log_level="WARNING",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        upstox_exchanges="NSE,BSE,MCX",
    )
    base.update(kw)
    return Settings(**base)


def make_provider(tmp_path, fake: Optional[FakeUpstox] = None, ws: Optional[FakeWS] = None, **kw):
    fake = fake or FakeUpstox()
    ws = ws or FakeWS()
    settings = make_settings(tmp_path, **kw)
    provider = UpstoxProvider(settings, transport=httpx.MockTransport(fake.handler), ws_connect=lambda uri: ws)
    return provider, fake, ws


async def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


# =============================================================== feed decoding
def test_feed_decoder_matches_official_protobuf():
    msg = decode_feed_response(bytes.fromhex(FULL_HEX))
    assert msg.type == "live_feed" and msg.current_ts == 1761637500123
    rel = msg.feeds[RELIANCE_KEY]
    assert (rel.ltp, rel.ltt, rel.ltq, rel.cp) == (1432.55, 1761637499000, 25, 1420.1)
    assert (rel.bid, rel.ask, rel.vtt) == (1432.5, 1432.6, 1234567)
    nifty = msg.feeds["NSE_INDEX|Nifty 50"]
    assert nifty.ltp == 25890.45 and nifty.vtt is None
    assert msg.feeds["NSE_FO|12345"].ltq == 75

    info = decode_feed_response(bytes.fromhex(INFO_HEX))
    assert info.type == "market_info"
    assert info.segment_status == {"NSE_EQ": "NORMAL_OPEN", "MCX_FO": "PRE_OPEN_START", "BSE_EQ": "NORMAL_CLOSE"}

    greeks = decode_feed_response(bytes.fromhex(GREEKS_HEX))
    assert greeks.type == "initial_feed"
    q = greeks.feeds["NSE_FO|999"]
    assert (q.ltp, q.bid, q.ask, q.vtt) == (55.5, 55.4, 55.6, 9000)


# ============================================================ session alignment
def test_bucket_start_session_anchor_matches_nse_bars():
    anchor = 3 * 3600 + 45 * 60  # 09:15 IST
    t = int(datetime(2025, 11, 3, 10, 7, tzinfo=IST).timestamp())
    as_ist = lambda ts: datetime.fromtimestamp(ts, tz=IST).strftime("%H:%M")
    assert as_ist(bucket_start(t, Timeframe.H1, anchor)) == "09:15"
    assert as_ist(bucket_start(t, Timeframe.M30, anchor)) == "09:45"
    assert as_ist(bucket_start(t, Timeframe.M15, anchor)) == "10:00"
    t2 = int(datetime(2025, 11, 3, 14, 0, tzinfo=IST).timestamp())
    assert as_ist(bucket_start(t2, Timeframe.H4, anchor)) == "13:15"
    assert bucket_start(t, Timeframe.D1, anchor) == bucket_start(t, Timeframe.D1)
    assert bucket_start(t, Timeframe.H1) % 3600 == 0  # no anchor -> clock aligned

    agg = CandleAggregator(Timeframe.H1, anchor_s=anchor, real_volume=True)
    c = agg.update(100.0, t * 1000, 7)
    assert as_ist(c.time) == "09:15" and c.volume == 7
    assert CandleAggregator(Timeframe.H1).update(100.0, t * 1000, 7).volume == 1  # tick volume by default


def test_rebucket_daily_uses_indian_trading_date():
    rows = [["2025-11-03T00:00:00+05:30", 1, 2, 0.5, 1.5, 10, 0], ["2025-11-04T00:00:00+05:30", 1.5, 3, 1, 2, 20, 0]]
    candles = rebucket(rows, Timeframe.D1, 13500)
    assert [datetime.fromtimestamp(c.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") for c in candles] == [
        "2025-11-03 00:00", "2025-11-04 00:00"]
    week = rebucket(rows, Timeframe.W1, 13500)
    assert len(week) == 1 and week[0].high == 3 and week[0].volume == 30
    assert datetime.fromtimestamp(week[0].time, tz=timezone.utc).weekday() == 6  # Sunday, like MT5 weeks


# ================================================================= instruments
def test_instrument_parsing_resolution_and_search(tmp_path):
    parsed = parse_instruments(INSTRUMENT_ROWS["NSE"] + INSTRUMENT_ROWS["MCX"], include_options=False)
    names = {i.symbol for i in parsed}
    assert names == {"NSE:RELIANCE", "NSE:NIFTY", "NSE:NIFTY FUT 27 NOV 25", "MCX:CRUDEOIL FUT 19 NOV 25"}
    by = {i.symbol: i for i in parsed}
    assert by["NSE:RELIANCE"].digits == 2 and by["NSE:NIFTY FUT 27 NOV 25"].digits == 1
    assert by["MCX:CRUDEOIL FUT 19 NOV 25"].digits == 0
    assert by["MCX:CRUDEOIL FUT 19 NOV 25"].anchor_s == 12600 and by["NSE:RELIANCE"].anchor_s == 13500
    assert len(parse_instruments(INSTRUMENT_ROWS["NSE"], include_options=True)) == 5

    async def scenario():
        provider, fake, _ = make_provider(tmp_path, upstox_analytics_token="good-token")
        await provider.instruments.load()
        assert sorted(fake.downloads) == ["BSE", "MCX", "NSE"]
        assert provider.normalize_symbol("RELIANCE") == "NSE:RELIANCE"  # NSE preferred
        assert provider.normalize_symbol("bse:reliance") == "BSE:RELIANCE"
        assert provider.normalize_symbol(RELIANCE_KEY) == "NSE:RELIANCE"
        assert provider.normalize_symbol("NSE_EQ:RELIANCE") == "NSE:RELIANCE"
        assert provider.normalize_symbol("SENSEX") == "BSE:SENSEX"
        with pytest.raises(SymbolNotFoundError):
            provider.normalize_symbol("NOPE")
        found = [s.symbol for s in await provider.get_symbols("nifty", 10)]
        assert found[0] == "NSE:NIFTY"  # exact match + index first
        assert "NSE:NIFTY FUT 27 NOV 25" in found
        assert [s.symbol for s in await provider.get_symbols("bse:rel", 10)] == ["BSE:RELIANCE"]
        assert [s.symbol for s in await provider.get_symbols("rel", 10)] == ["NSE:RELIANCE", "BSE:RELIANCE"]
        info = (await provider.get_symbols("RELIANCE", 1))[0]
        assert info.currency_profit == "INR" and info.digits == 2

        # second load uses the cache (no new downloads)
        provider2, fake2, _ = make_provider(tmp_path, upstox_analytics_token="good-token")
        await provider2.instruments.load()
        assert fake2.downloads == []

    asyncio.run(scenario())


# ===================================================================== history
def test_history_chunks_aligns_and_merges_today(tmp_path):
    async def scenario():
        provider, fake, _ = make_provider(tmp_path, upstox_analytics_token="good-token")
        await provider.instruments.load()

        m1 = await provider.get_history("NSE:RELIANCE", Timeframe.M1, 1000)
        assert len(m1) == 1000
        times = [c.time for c in m1]
        assert times == sorted(times) and len(set(times)) == len(times)
        today = datetime.now(IST).date()
        assert datetime.fromtimestamp(m1[-1].time, tz=IST).date() == today  # intraday endpoint merged
        assert all((c["to"] - c["from"]).days + 1 <= 28 for c in fake.history_calls)

        h1 = await provider.get_history("NSE:RELIANCE", Timeframe.H1, 50)
        assert len(h1) == 50
        assert {datetime.fromtimestamp(c.time, tz=IST).minute for c in h1} == {15}
        assert datetime.fromtimestamp(h1[-2].time, tz=IST).hour <= 15

        h4 = await provider.get_history("NSE:RELIANCE", Timeframe.H4, 20)
        assert {datetime.fromtimestamp(c.time, tz=IST).strftime("%H:%M") for c in h4} <= {"09:15", "13:15"}

        fake.history_calls.clear()
        d1 = await provider.get_history("NSE:RELIANCE", Timeframe.D1, 300)
        assert len(d1) == 300 and all(c.time % 86400 == 0 for c in d1)
        assert len(fake.history_calls) == 1  # one request covers ~1.2 years of daily bars

        # lazy-load older bars
        older = await provider.get_history("NSE:RELIANCE", Timeframe.M5, 200, to=datetime.fromtimestamp(m1[0].time, tz=timezone.utc))
        assert len(older) == 200 and older[-1].time < m1[0].time

    asyncio.run(scenario())


def test_history_requires_login_and_reports_rejected_token(tmp_path):
    async def scenario():
        provider, _, _ = make_provider(tmp_path, upstox_api_key="key", upstox_api_secret="secret")
        await provider.instruments.load()
        with pytest.raises(ProviderUnavailableError, match="Log in to Upstox"):
            await provider.get_history("NSE:RELIANCE", Timeframe.M1, 10)

        bad, _, _ = make_provider(tmp_path, upstox_analytics_token="expired-token")
        await bad.instruments.load()
        with pytest.raises(ProviderUnavailableError, match="rejected"):
            await bad.get_history("NSE:RELIANCE", Timeframe.M1, 10)
        assert bad.auth.active_token() is None  # not retried until a new token arrives

    asyncio.run(scenario())


# ===================================================================== live
def test_stream_subscribes_and_emits_ticks_with_real_volume(tmp_path):
    async def scenario():
        provider, _, ws = make_provider(tmp_path, upstox_analytics_token="good-token")
        ticks: List[Tick] = []

        async def on_tick(t: Tick) -> None:
            ticks.append(t)

        provider.add_tick_listener(on_tick)
        await provider.connect()
        await wait_for(lambda: provider.is_connected and provider._stream_state == "live")

        canonical = await provider.acquire("RELIANCE")
        assert canonical == "NSE:RELIANCE"
        await provider.acquire("NIFTY")
        sub = json.loads(ws.sent[-1].decode())
        assert isinstance(ws.sent[-1], bytes)  # Upstox needs binary frames
        assert sub["method"] == "sub" and sub["data"] == {"instrumentKeys": ["NSE_INDEX|Nifty 50"], "mode": "full"}

        await ws.inbox.put(bytes.fromhex(INFO_HEX))
        await ws.inbox.put(bytes.fromhex(FULL_HEX))
        await wait_for(lambda: len(ticks) >= 2)
        by_symbol = {t.symbol: t for t in ticks}
        rel = by_symbol["NSE:RELIANCE"]
        assert (rel.price, rel.bid, rel.ask, rel.volume, rel.time_ms) == (1432.55, 1432.5, 1432.6, 0.0, 1761637499000)
        assert by_symbol["NSE:NIFTY"].price == 25890.45
        assert provider.status().message == "Live - NSE open, BSE closed, MCX pre-open"
        assert provider.status().details["market_NSE_EQ"] == "NORMAL_OPEN"

        # second update: vtt grew by 500 -> tick volume 500
        msg = bytearray(bytes.fromhex(FULL_HEX))
        old_vtt = bytes.fromhex("3087ad4b")  # field 6 varint 1234567
        idx = msg.find(old_vtt)
        msg[idx:idx + 4] = bytes.fromhex("30fbb04b")  # 1235067
        ticks.clear()
        await ws.inbox.put(bytes(msg))
        await wait_for(lambda: any(t.symbol == "NSE:RELIANCE" for t in ticks))
        assert [t.volume for t in ticks if t.symbol == "NSE:RELIANCE"] == [500.0]

        await provider.release("NIFTY")
        unsub = json.loads(ws.sent[-1].decode())
        assert unsub["method"] == "unsub" and unsub["data"]["instrumentKeys"] == ["NSE_INDEX|Nifty 50"]
        await provider.disconnect()

    asyncio.run(scenario())


def test_unconfigured_provider_is_idle_and_explains(tmp_path):
    async def scenario():
        provider, fake, _ = make_provider(tmp_path)
        await provider.connect()
        await asyncio.sleep(0.05)
        assert not provider.is_connected
        assert "UPSTOX_ANALYTICS_TOKEN" in provider.status().message
        assert fake.downloads == []  # nothing downloaded when Upstox isn't set up
        with pytest.raises(ProviderUnavailableError):
            await provider.get_symbols("rel")
        await provider.disconnect()

    asyncio.run(scenario())


# ======================================================================= auth
def test_token_expiry_is_next_0330_ist():
    issued = datetime(2025, 11, 3, 20, 0, tzinfo=IST)
    assert next_token_expiry(issued) == datetime(2025, 11, 4, 3, 30, tzinfo=IST)
    early = datetime(2025, 11, 3, 2, 0, tzinfo=IST)
    assert next_token_expiry(early) == datetime(2025, 11, 3, 3, 30, tzinfo=IST)


def test_oauth_login_flow_stores_only_what_is_needed(tmp_path):
    async def scenario():
        fake = FakeUpstox(token="oauth-token")
        settings = make_settings(tmp_path, upstox_api_key="my-key", upstox_api_secret="my-secret")
        auth = UpstoxAuth(settings, transport=httpx.MockTransport(fake.handler))
        assert auth.mode == "oauth" and auth.active_token() is None
        url = urlparse(auth.login_url())
        q = parse_qs(url.query)
        assert url.netloc == "api.upstox.com" and url.path == "/v2/login/authorization/dialog"
        assert q["client_id"] == ["my-key"] and q["response_type"] == ["code"]
        assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/broker/upstox/callback"]
        state = q["state"][0]

        with pytest.raises(InvalidRequestError, match="expired"):
            await auth.exchange_code("good-code", "forged-state")
        with pytest.raises(InvalidRequestError, match="Invalid Auth code"):
            await auth.exchange_code("bad-code", state)
        state = parse_qs(urlparse(auth.login_url()).query)["state"][0]
        token = await auth.exchange_code("good-code", state)
        assert token.access_token == "oauth-token" and token.user_id == "AB1234"
        assert fake.token_calls[-1]["grant_type"] == ["authorization_code"]
        assert fake.token_calls[-1]["client_secret"] == ["my-secret"]
        saved = json.loads((tmp_path / "data" / "upstox_token.json").read_text())
        assert set(saved) == {"access_token", "expires_at", "user_id", "obtained_at"}  # no e-mail / name
        assert auth.active_token() == "oauth-token"

        # restart: the stored login is reloaded; rejection forgets it
        again = UpstoxAuth(settings)
        assert again.active_token() == "oauth-token"
        again.mark_rejected("oauth-token")
        assert again.active_token() is None
        assert not (tmp_path / "data" / "upstox_token.json").exists()

    asyncio.run(scenario())


def test_broker_api_endpoints(tmp_path, monkeypatch):
    async def idle(self) -> None:  # keep the test offline (no instrument download)
        return None

    monkeypatch.setattr(UpstoxProvider, "_run", idle)
    settings = make_settings(tmp_path, enabled_providers="simulated,upstox", default_provider="simulated")
    with TestClient(create_app(settings)) as client:
        r = client.get("/api/broker/status")
        body = r.json()
        assert body["enabled"] is True and body["mode"] == "none" and body["authenticated"] is False
        r = client.get("/api/broker/upstox/login", follow_redirects=False)
        assert r.status_code == 400 and "not configured" in r.text
        r = client.get("/api/broker/upstox/callback", params={"code": "x", "state": "y"})
        assert r.status_code == 400 and "Start again" in r.text

    settings = make_settings(tmp_path / "b", enabled_providers="simulated,upstox", default_provider="simulated",
                             upstox_api_key="k", upstox_api_secret="s")
    with TestClient(create_app(settings)) as client:
        r = client.get("/api/broker/upstox/login", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("https://api.upstox.com/v2/login/authorization/dialog?")
        assert client.get("/api/broker/status").json()["login_available"] is True
        assert client.post("/api/broker/upstox/logout").json()["authenticated"] is False

    settings = make_settings(tmp_path / "c", enabled_providers="simulated", default_provider="simulated")
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/broker/status").json()["enabled"] is False
