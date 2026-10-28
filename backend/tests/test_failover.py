"""
Tests for the OANDA provider and the "auto" failover provider.

OANDA is replaced by an in-memory fake (httpx.MockTransport), and MT5 by a
controllable stub, so these tests need no network, no MT5 and no API token.
"""

import asyncio
import json
import time
from typing import List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.exceptions import SymbolNotFoundError
from app.models.market import Candle, SymbolInfo, Tick, Timeframe
from app.providers.base import MarketDataProvider
from app.providers.failover_provider import FailoverProvider
from app.providers.oanda_provider import OandaProvider

ACCOUNT = "101-001-0000000-001"
INSTRUMENTS = [
    {"name": "EUR_USD", "displayName": "EUR/USD", "type": "CURRENCY", "displayPrecision": 5},
    {"name": "XAU_USD", "displayName": "Gold", "type": "METAL", "displayPrecision": 3},
    {"name": "NAS100_USD", "displayName": "US Nas 100", "type": "CFD", "displayPrecision": 1},
    {"name": "DE30_EUR", "displayName": "Germany 30", "type": "CFD", "displayPrecision": 1},
]


class FakeOanda:
    """Minimal OANDA v20 API: accounts, instruments, candles and a price stream."""

    def __init__(self) -> None:
        self.candle_params: List[dict] = []
        self.stream_requests: List[str] = []
        self.price = 1.1000

    async def _stream(self, instruments: List[str]):
        for i in range(400):
            self.price += 0.0001
            now = time.time()
            for inst in instruments:
                line = {
                    "type": "PRICE", "instrument": inst, "time": f"{now:.6f}",
                    "bids": [{"price": f"{self.price:.5f}"}], "asks": [{"price": f"{self.price + 0.0001:.5f}"}],
                }
                yield (json.dumps(line) + "\n").encode()
            if i % 10 == 0:
                yield (json.dumps({"type": "HEARTBEAT", "time": f"{now:.6f}"}) + "\n").encode()
            await asyncio.sleep(0.02)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("Authorization") != "Bearer test-token":
            return httpx.Response(401, json={"errorMessage": "Insufficient authorization"})
        if path == "/v3/accounts":
            return httpx.Response(200, json={"accounts": [{"id": ACCOUNT}]})
        if path == f"/v3/accounts/{ACCOUNT}/instruments":
            return httpx.Response(200, json={"instruments": INSTRUMENTS})
        if path.startswith("/v3/instruments/") and path.endswith("/candles"):
            inst = path.split("/")[3]
            if inst not in {i["name"] for i in INSTRUMENTS}:
                return httpx.Response(400, json={"errorMessage": "Invalid value specified for 'instrument'"})
            params = dict(request.url.params)
            self.candle_params.append(params)
            step = {"M1": 60, "M5": 300, "H1": 3600, "D": 86400}[params["granularity"]]
            count = int(params["count"])
            end = int(float(params.get("to", time.time())))
            end -= end % step
            candles = []
            for k in range(count):
                t = end - (count - k) * step
                candles.append({"time": f"{t}.000000000", "volume": 10, "complete": True,
                                "bid": {"o": "1.10000", "h": "1.10100", "l": "1.09900", "c": "1.10050"}})
            return httpx.Response(200, json={"instrument": inst, "candles": candles})
        if path == f"/v3/accounts/{ACCOUNT}/pricing/stream":
            instruments = request.url.params["instruments"].split(",")
            self.stream_requests.append(",".join(instruments))
            return httpx.Response(200, content=self._stream(instruments))
        return httpx.Response(404, json={"errorMessage": f"no route {path}"})


def oanda_settings(**kw) -> Settings:
    base = dict(_env_file=None, oanda_api_token="test-token", oanda_reconnect_max_delay_s=1.0,
                oanda_symbol_map="USTEC=NAS100_USD")
    base.update(kw)
    return Settings(**base)


class StubMT5(MarketDataProvider):
    """Stand-in for MT5: connection state and ticks are driven by the test."""

    name = "mt5"

    def __init__(self, offset_s: int = 7200) -> None:
        super().__init__()
        self.time_offset_s: Optional[int] = offset_s

    async def set_connected(self, value: bool) -> None:
        await self._set_status(value, "up" if value else "down")

    async def push(self, symbol: str, price: float) -> None:
        await self._emit_tick(Tick(provider="mt5", symbol=symbol, time_ms=int(time.time() * 1000),
                                   bid=price, ask=price, price=price))

    async def connect(self) -> None:
        await self.set_connected(True)

    async def disconnect(self) -> None:
        await self.set_connected(False)

    async def get_symbols(self, query=None, limit=200) -> List[SymbolInfo]:
        return [SymbolInfo(provider="mt5", symbol="EURUSD.m", digits=5)]

    async def get_history(self, symbol, timeframe, count, to=None) -> List[Candle]:
        return [Candle(time=1_800_000_000, open=2.0, high=2.0, low=2.0, close=2.0, volume=1)]

    async def subscribe(self, symbol: str) -> None:
        self._subscribed.add(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self._subscribed.discard(symbol)


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


# --------------------------------------------------------------------- OANDA
def test_oanda_symbol_resolution_and_history_shift():
    async def scenario():
        fake = FakeOanda()
        p = OandaProvider(oanda_settings(), transport=httpx.MockTransport(fake.handler))
        await p.connect()
        try:
            assert p.is_connected
            for name in ["EURUSD", "EURUSD.m", "EURUSDm", "eur_usd", "EUR/USD", "EUR_USD"]:
                assert p.normalize_symbol(name) == "EUR_USD", name
            assert p.normalize_symbol("XAUUSD.pro") == "XAU_USD"
            assert p.normalize_symbol("GER40") == "DE30_EUR"      # alias falls back to DE30_EUR
            assert p.normalize_symbol("USTEC") == "NAS100_USD"    # OANDA_SYMBOL_MAP override
            with pytest.raises(SymbolNotFoundError):
                p.normalize_symbol("ZZZZZZ")

            candles = await p.get_history("EURUSD", Timeframe.M1, 10, time_shift_s=7200)
            params = fake.candle_params[-1]
            assert params["price"] == "B" and params["dailyAlignment"] == "22"
            assert params["weeklyAlignment"] == "Saturday"
            assert len(candles) == 10 and all(c.time % 60 == 0 for c in candles)
            now = time.time()
            assert now + 7200 - 15 * 60 < candles[-1].time < now + 7200 + 60  # shifted to server time

            symbols = await p.get_symbols(query="usd")
            assert {s.symbol for s in symbols} >= {"EUR_USD", "XAU_USD"}
        finally:
            await p.disconnect()

    asyncio.run(scenario())


def test_oanda_bad_token_reports_clearly():
    async def scenario():
        p = OandaProvider(oanda_settings(oanda_api_token="wrong"), transport=httpx.MockTransport(FakeOanda().handler))
        with pytest.raises(Exception) as err:
            await p.connect()
        assert "rejected" in str(err.value)
        assert not p.is_connected
        await p.disconnect()

    asyncio.run(scenario())


def test_oanda_stream_emits_ticks():
    async def scenario():
        fake = FakeOanda()
        p = OandaProvider(oanda_settings(), transport=httpx.MockTransport(fake.handler))
        ticks: List[Tick] = []

        async def on_tick(t: Tick) -> None:
            ticks.append(t)

        p.add_tick_listener(on_tick)
        await p.connect()
        try:
            assert await p.acquire("EURUSD.m") == "EUR_USD"
            await _wait_for(lambda: len(ticks) >= 3)
            assert ticks[-1].symbol == "EUR_USD" and ticks[-1].ask > ticks[-1].bid
            await p.acquire("XAUUSD")
            await _wait_for(lambda: any(t.symbol == "XAU_USD" for t in ticks))
            assert fake.stream_requests[-1] == "EUR_USD,XAU_USD"  # stream re-opened with both
        finally:
            await p.disconnect()

    asyncio.run(scenario())


# ------------------------------------------------------------------ failover
def test_failover_switches_and_switches_back():
    async def scenario():
        fake = FakeOanda()
        mt5 = StubMT5(offset_s=7200)
        oanda = OandaProvider(oanda_settings(), transport=httpx.MockTransport(fake.handler))
        auto = FailoverProvider(mt5, oanda, switch_back_delay_s=0.3, check_interval_s=0.05)
        got: List[Tick] = []

        async def on_tick(t: Tick) -> None:
            got.append(t)

        auto.add_tick_listener(on_tick)
        await mt5.connect()
        await oanda.connect()
        await auto.connect()
        try:
            assert auto.active_source == "mt5"
            await auto.acquire("EURUSD.m")
            await mt5.push("EURUSD.m", 1.2345)
            assert got[-1].provider == "auto" and got[-1].symbol == "EURUSD.m" and got[-1].price == 1.2345
            assert (await auto.get_history("EURUSD.m", Timeframe.M1, 5))[0].close == 2.0  # from MT5

            # MT5 drops -> OANDA takes over, same symbol name, times shifted to server time
            await mt5.set_connected(False)
            await _wait_for(lambda: auto.active_source == "oanda")
            got.clear()
            await _wait_for(lambda: len(got) >= 3)
            tick = got[-1]
            assert tick.provider == "auto" and tick.symbol == "EURUSD.m"
            assert abs(tick.time_ms / 1000 - (time.time() + 7200)) < 5
            assert auto.status().details["active_source"] == "oanda"
            hist = await auto.get_history("EURUSD.m", Timeframe.M1, 5)
            assert hist[-1].close == 1.1005  # from OANDA

            # MT5 returns -> stays on OANDA until stable for 0.3 s, then switches back
            await mt5.set_connected(True)
            assert auto.active_source == "oanda"
            await _wait_for(lambda: auto.active_source == "mt5", timeout=2.0)
            await mt5.push("EURUSD.m", 1.5)
            assert got[-1].price == 1.5
            assert "EUR_USD" not in oanda.subscribed_symbols  # OANDA stream released

            # Both down -> not connected, subscriptions kept for recovery
            await mt5.set_connected(False)
            await oanda.disconnect()
            await _wait_for(lambda: not auto.is_connected)
            assert "EURUSD.m" in auto.subscribed_symbols
        finally:
            await auto.disconnect()
            await oanda.disconnect()

    asyncio.run(scenario())


def test_auto_provider_end_to_end_over_websocket(tmp_path, monkeypatch):
    """Full app: MT5 unavailable -> 'auto' serves OANDA data over REST and WebSocket."""
    import app.providers.mt5_provider as mt5_module
    import app.providers.registry as registry_module

    fake = FakeOanda()
    monkeypatch.setattr(mt5_module, "mt5", None)  # behave as if MT5 is not installed
    monkeypatch.setattr(
        registry_module, "OandaProvider",
        lambda settings: OandaProvider(settings, transport=httpx.MockTransport(fake.handler)),
    )
    settings = oanda_settings(
        enabled_providers="mt5,oanda,auto", default_provider="auto",
        mt5_server_utc_offset_hours="2", log_dir=str(tmp_path / "logs"), log_level="WARNING",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    from app.main import create_app

    with TestClient(create_app(settings)) as client:
        status = {p["name"]: p for p in client.get("/api/providers").json()}
        assert status["mt5"]["connected"] is False
        assert status["auto"]["connected"] is True
        assert status["auto"]["details"]["active_source"] == "oanda"

        r = client.get("/api/history", params={"symbol": "EURUSD", "timeframe": "M5", "count": 20})
        assert r.status_code == 200 and r.json()["count"] == 20

        with client.websocket_connect("/ws/market") as ws:
            assert ws.receive_json()["type"] == "welcome"
            ws.send_json({"action": "subscribe", "provider": "auto", "symbol": "EURUSD", "timeframe": "M1"})
            seen = set()
            for _ in range(60):
                msg = ws.receive_json()
                seen.add(msg["type"])
                if msg["type"] == "candle":
                    assert msg["provider"] == "auto" and msg["symbol"] == "EURUSD"
                if {"subscribed", "tick", "candle"} <= seen:
                    break
            assert {"subscribed", "tick", "candle"} <= seen
