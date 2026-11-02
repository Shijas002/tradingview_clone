"""
v1.1 features: paper trading, indicator / Pine alerts (real embedded PineTS),
Telegram notifications and the MT5 / Upstox journal import mapping.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import Database
from app.db.models import Alert, AlertEvent, JournalEntry, PaperAccount
from app.main import create_app
from app.models.market import Candle, Tick, Timeframe
from app.providers.base import MarketDataProvider
from app.services.history_service import HistoryService
from app.services.indicator_alerts import IndicatorAlertEngine, check_target
from app.services.journal_import import mt5_round_trips, upstox_round_trips
from app.services.notifier import Notifier
from app.services.paper_broker import PaperBroker
from app.services.pine_runtime import PineRuntime, alertcondition_titles

try:
    import py_mini_racer  # noqa: F401

    HAVE_V8 = True
except Exception:  # pragma: no cover
    HAVE_V8 = False


class TickProvider(MarketDataProvider):
    """A provider whose ticks the test pushes by hand; history is a flat-ish series."""

    name = "oanda"

    def __init__(self, base: float = 1.1) -> None:
        super().__init__()
        self._connected = True
        self.base = base
        self.history_calls = 0

    async def connect(self): ...
    async def disconnect(self): ...
    async def get_symbols(self, query=None, limit=200): return []
    async def subscribe(self, symbol): self._subscribed.add(symbol)
    async def unsubscribe(self, symbol): self._subscribed.discard(symbol)

    async def get_history(self, symbol, timeframe, count, to=None):
        self.history_calls += 1
        now = int(time.time()) // timeframe.seconds * timeframe.seconds
        end = now if to is None else int(to.timestamp()) - timeframe.seconds
        return [Candle(time=t, open=self.base, high=self.base + 0.001, low=self.base - 0.001, close=self.base, volume=1)
                for t in range(end - (count - 1) * timeframe.seconds, end + 1, timeframe.seconds)]

    async def push(self, price: float, bid: Optional[float] = None, ask: Optional[float] = None, t_ms: Optional[int] = None) -> None:
        await self._emit_tick(Tick(provider=self.name, symbol="EUR_USD", time_ms=t_ms or int(time.time() * 1000),
                                   bid=bid or price, ask=ask or price, last=price, price=price))


class FakeRegistry:
    def __init__(self, provider: MarketDataProvider) -> None:
        self.p = provider

    def get(self, name: str) -> MarketDataProvider:
        from app.core.exceptions import ProviderNotFoundError

        if name != self.p.name:
            raise ProviderNotFoundError(f"no provider {name}")
        return self.p

    def all(self):
        return [self.p]


def make_db(tmp_path) -> Database:
    db = Database(f"sqlite:///{(tmp_path / 'v11.db').as_posix()}")
    db.create_all()
    return db


async def settle(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0.01)


# ================================================================== paper trading
def test_paper_broker_orders_positions_journal(tmp_path):
    async def scenario():
        db = make_db(tmp_path)
        prov = TickProvider()
        events: List[Dict[str, Any]] = []

        async def broadcast(m):
            events.append(m)

        settings = Settings(_env_file=None, data_dir=str(tmp_path / "data"), paper_initial_balance=10_000, paper_commission_pct=0.0)
        broker = PaperBroker(db, FakeRegistry(prov), broadcast, None, settings)
        await broker.start()
        await prov.push(1.1000, bid=1.0999, ask=1.1001)

        # market buy fills at the ask, opens a long, creates an open journal entry
        o = await broker.place_order("oanda", "EUR_USD", "buy", "market", 10_000, None, 1.0950, 1.1100)
        assert o["status"] == "filled" and o["fill_price"] == 1.1001
        acct = await broker.account()
        assert len(acct["positions"]) == 1 and acct["positions"][0]["side"] == "long"
        with db.session() as s:
            j = s.query(JournalEntry).one()
            assert (j.status, j.source, j.stop_loss) == ("open", "paper", 1.0950)

        # bad SL side is rejected
        with pytest.raises(Exception, match="Stop loss"):
            await broker.place_order("oanda", "EUR_USD", "buy", "market", 1, None, 1.2, None)

        # a working limit order fills when the price comes down to it
        lim = await broker.place_order("oanda", "EUR_USD", "buy", "limit", 10_000, 1.0980, None, None)
        assert lim["status"] == "working"
        lim = await broker.modify_order(lim["id"], None, 1.0990, None, None, [])
        assert lim["price"] == 1.0990
        await prov.push(1.0990, bid=1.0989, ask=1.0990)
        await settle()
        acct = await broker.account()
        pos = acct["positions"][0]
        assert pos["qty"] == 20_000 and abs(pos["avg_price"] - (1.1001 + 1.0990) / 2) < 1e-9
        assert not acct["orders"]

        # take profit hits -> position closed with realized P&L; journal closed with exact broker_pnl
        await prov.push(1.1100, bid=1.1100, ask=1.1102)
        await settle()
        acct = await broker.account()
        assert not acct["positions"]
        expected = (1.1100 - (1.1001 + 1.0990) / 2) * 20_000
        assert abs(acct["realized_pnl"] - expected) < 1e-6 and abs(acct["balance"] - 10_000 - expected) < 1e-6
        assert acct["stats"]["closed"] == 1 and acct["stats"]["wins"] == 1
        with db.session() as s:
            j = s.query(JournalEntry).one()
            assert j.status == "closed" and abs(j.broker_pnl - expected) < 1e-6 and j.exit_price == 1.1100

        # short, partial close, flip via a bigger buy, stop order, cancel, reset
        await broker.place_order("oanda", "EUR_USD", "sell", "market", 1000, None, None, None)
        acct = await broker.account()
        short = acct["positions"][0]
        assert short["side"] == "short" and short["avg_price"] == 1.1100
        await broker.close_position(short["id"], 400)
        acct = await broker.account()
        assert acct["positions"][0]["qty"] == 600
        await broker.place_order("oanda", "EUR_USD", "buy", "market", 1000, None, None, None)  # flips to long 400
        acct = await broker.account()
        assert acct["positions"][0]["side"] == "long" and abs(acct["positions"][0]["qty"] - 400) < 1e-9
        stop = await broker.place_order("oanda", "EUR_USD", "sell", "stop", 400, 1.0, None, None)
        assert (await broker.cancel_order(stop["id"]))["status"] == "cancelled"
        hist = await broker.history()
        assert any(x["status"] == "cancelled" for x in hist["orders"]) and len(hist["positions"]) == 2
        acct = await broker.reset(5000, "EUR", 0.01)
        assert acct["balance"] == 5000 and acct["currency"] == "EUR" and not acct["positions"]
        assert any(m["type"] == "paper_changed" for m in events)
        await broker.stop()

    asyncio.run(scenario())


def test_paper_api_endpoints(tmp_path):
    settings = Settings(_env_file=None, enabled_providers="simulated", default_provider="simulated", sim_tick_interval_ms=20,
                        log_dir=str(tmp_path / "logs"), log_level="WARNING", data_dir=str(tmp_path / "data"),
                        database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    with TestClient(create_app(settings)) as c:
        acct = c.get("/api/paper/account").json()
        assert acct["balance"] == 100_000 and acct["positions"] == []
        r = c.post("/api/paper/orders", json={"provider": "simulated", "symbol": "EURUSD", "side": "buy", "type": "market", "qty": 1000})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "filled"
        assert c.post("/api/paper/orders", json={"provider": "simulated", "symbol": "EURUSD", "side": "buy", "type": "limit", "qty": 1}).status_code == 400
        pos = c.get("/api/paper/account").json()["positions"][0]
        r = c.put(f"/api/paper/positions/{pos['id']}", json={"stop_loss": round(pos["avg_price"] * 0.9, 5)})
        assert r.json()["stop_loss"] == round(pos["avg_price"] * 0.9, 5)
        assert c.post("/api/paper/close-all").json()["closed"] == 1
        journal = c.get("/api/journal").json()
        assert journal[0]["source"] == "paper" and journal[0]["status"] == "closed" and journal[0]["pnl"] is not None


# ================================================================ indicator alerts
RSI_SCRIPT = """//@version=6
indicator("Close alert test")
plot(close, "Close")
plot(ta.sma(close, 3), "SMA3")
alertcondition(close > 1.2, title="Above 1.2", message="close is above 1.2")
"""


def test_alertcondition_title_parsing_and_check_target():
    src = 'alertcondition(ta.crossover(a, b), "Cross up", "msg")\nalertcondition(x, title = "Down, fast", message="m")\nalertcondition(y)'
    assert alertcondition_titles(src) == ["Cross up", "Down, fast", "alertcondition #3"]

    class A:
        target, target2, condition, price, price2 = "plot:RSI", None, "crossing_up", 70.0, None

    res = {"plots": {"RSI": [[1, 65.0], [2, 69.0], [3, 71.0]], "Sig": [[1, 1], [2, 72.0], [3, 70.0]]}, "alerts": []}
    assert check_target(A, res) == (True, {"value": 71.0, "level": 70.0})
    A.target2, A.condition = "plot:Sig", "crossing_up"
    assert check_target(A, res)[0] is True  # 69 < 72 and 71 >= 70
    A.target, A.target2 = "alertcondition:Up", None
    assert check_target(A, {"alerts": [{"type": "alertcondition", "title": "Up", "message": "hi"}]}) == (True, {"message": "hi"})
    A.target = "alert"
    assert check_target(A, {"alerts": [{"type": "alertcondition", "title": "Up"}]})[0] is False


@pytest.mark.skipif(not HAVE_V8, reason="mini-racer not installed")
def test_indicator_alert_engine_fires_on_bar_close(tmp_path):
    async def scenario():
        db = make_db(tmp_path)
        prov = TickProvider(base=1.1)
        events: List[Dict[str, Any]] = []

        async def broadcast(m):
            events.append(m)

        settings = Settings(_env_file=None, pine_alert_bars=100, pine_alert_interval_s=1.0)
        history = HistoryService(db, FakeRegistry(prov), settings)
        runtime = PineRuntime(lambda *a: history.get(prov, a[1], a[2], a[3]))
        sent: List[str] = []

        class N:
            def notify(self, event, title, lines, payload=None, telegram=True):
                sent.append(title + " | " + " ".join(lines))

        engine = IndicatorAlertEngine(db, FakeRegistry(prov), history, runtime, broadcast, N(), settings)
        with db.session() as s:
            s.add(Alert(id="ac", name="AC", provider="oanda", symbol="EUR_USD", condition="signal", kind="indicator", timeframe="M1",
                        script_name="Close alert test", script_source=RSI_SCRIPT, script_inputs={}, target="alertcondition:Above 1.2",
                        frequency="once_per_bar_close", active=True, status="active", created_at=0, trigger_count=0))
            s.add(Alert(id="pl", name="PL", provider="oanda", symbol="EUR_USD", condition="crossing_up", price=1.15, kind="indicator",
                        timeframe="M1", script_name="Close alert test", script_source=RSI_SCRIPT, script_inputs={}, target="plot:Close",
                        frequency="once", active=True, status="active", created_at=0, trigger_count=0))
            s.add(Alert(id="bad", name="Bad", provider="oanda", symbol="EUR_USD", condition="signal", kind="indicator", timeframe="M1",
                        script_name="Broken", script_source="//@version=6\nindicator('x')\nplot(nope)", target="alert",
                        frequency="once_per_bar", active=True, status="active", created_at=0, trigger_count=0))
        await engine.start()
        await asyncio.sleep(1.3)  # history loaded
        series = next(iter(engine.series.values()))
        assert series.loaded and len(series.bars) == 100
        now_bucket = series.bars[-1].time
        # forming bar jumps to 1.25 -> "once" plot alert (crossing up 1.15) fires on the forming bar
        await prov.push(1.25, t_ms=(now_bucket + 10) * 1000)
        await asyncio.sleep(1.5)
        # next minute starts -> previous bar (close 1.25) closes -> alertcondition fires once
        await prov.push(1.26, t_ms=(now_bucket + 70) * 1000)
        await asyncio.sleep(1.5)
        await prov.push(1.27, t_ms=(now_bucket + 80) * 1000)  # same bar: must not fire again
        await asyncio.sleep(1.2)
        with db.session() as s:
            ev = {e.alert_id: e for e in s.query(AlertEvent).all()}
            counts = {a.id: a.trigger_count for a in s.query(Alert).all()}
            pl = s.get(Alert, "pl")
            bad = s.get(Alert, "bad")
            assert counts["ac"] == 1 and counts["pl"] == 1, counts
            assert pl.active is False and pl.status == "triggered"
            assert "close is above 1.2" in ev["ac"].message
            assert "Undeclared identifier" in (bad.last_error or "")
        assert any(m.get("type") == "alert" for m in events)
        assert any("close is above 1.2" in x for x in sent)
        await engine.stop()
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.skipif(not HAVE_V8, reason="mini-racer not installed")
def test_indicator_alert_api_validation_and_check(tmp_path):
    settings = Settings(_env_file=None, enabled_providers="simulated", default_provider="simulated", log_dir=str(tmp_path / "logs"),
                        log_level="WARNING", data_dir=str(tmp_path / "data"), database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}")
    with TestClient(create_app(settings)) as c:
        base = {"provider": "simulated", "symbol": "EURUSD", "kind": "indicator", "timeframe": "M5", "script_name": "T",
                "script_source": RSI_SCRIPT}
        assert c.post("/api/alerts", json={**base, "target": "plot:Close", "condition": "crossing_up"}).status_code == 422
        r = c.post("/api/alerts", json={**base, "target": "plot:Close", "condition": "crossing_up", "price": 1.2, "frequency": "once_per_bar"})
        assert r.status_code == 201, r.text
        assert r.json()["kind"] == "indicator" and r.json()["timeframe"] == "M5"
        r = c.post("/api/alerts", json={**base, "target": "alertcondition:Above 1.2"})
        assert r.json()["condition"] == "signal"
        chk = c.post("/api/alerts/pine/check", json={"provider": "simulated", "symbol": "EURUSD", "timeframe": "M5", "source": RSI_SCRIPT}).json()
        assert [p["title"] for p in chk["plots"]] == ["Close", "SMA3"] and chk["alertconditions"] == ["Above 1.2"]
        assert isinstance(chk["plots"][0]["value"], float)
        bad = c.post("/api/alerts/pine/check", json={"provider": "simulated", "symbol": "EURUSD", "timeframe": "M5", "source": "plot(x)"})
        assert bad.status_code == 400 and "Pine error" in bad.json()["error"]
        eng = c.get("/api/alerts/engine").json()
        assert eng["indicator"]["runtime"]["available"] is True


# ====================================================================== Telegram
def test_notifier_telegram_and_chat_discovery():
    calls: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 12345, "type": "private", "first_name": "Ravi"}}},
                {"update_id": 2, "message": {"chat": {"id": -100777, "type": "group", "title": "Trading"}}}]})
        body = json.loads(request.content)
        if body["chat_id"] == "bad":
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
        return httpx.Response(200, json={"ok": True, "result": {}})

    async def scenario():
        settings = Settings(_env_file=None, telegram_bot_token="123:abc", telegram_chat_id="12345", telegram_events="alerts,paper")
        n = Notifier(settings, transport=httpx.MockTransport(handler))
        assert n.telegram_wants("alerts") and not n.telegram_wants("journal")
        n.notify("alerts", "🔔 EURUSD <test>", ["crossed 1.1 & more"])
        n.notify("journal", "ignored", ["x"])
        await asyncio.sleep(0.05)
        sends = [json.loads(r.content) for r in calls if r.url.path.endswith("/sendMessage")]
        assert len(sends) == 1 and sends[0]["parse_mode"] == "HTML"
        assert "&lt;test&gt;" in sends[0]["text"] and "&amp;" in sends[0]["text"]
        assert calls[0].url.path == "/bot123:abc/sendMessage"
        assert not await n.send_telegram("x", chat_id="bad") and "chat not found" in n.last_error
        chats = await n.telegram_chats()
        assert {c["chat_id"] for c in chats} == {"12345", "-100777"}
        assert n.status()["sent"] == 1 and n.status()["failed"] == 1
        await n.close()

    asyncio.run(scenario())


# ================================================================ journal import
def test_mt5_round_trips_mapping():
    data = {
        "currency": "USD",
        "contract": {"EURUSD": 100000.0},
        "sltp": {11: (1.09, 1.12)},
        "deals": [
            {"ticket": 1, "position_id": 11, "type": 0, "entry": 0, "symbol": "EURUSD", "volume": 0.1, "price": 1.1000, "time": 1_700_000_000 + 7200,
             "profit": 0.0, "commission": -0.35, "swap": 0.0, "fee": 0.0},
            {"ticket": 2, "position_id": 11, "type": 0, "entry": 0, "symbol": "EURUSD", "volume": 0.1, "price": 1.1010, "time": 1_700_000_600 + 7200,
             "profit": 0.0, "commission": -0.35, "swap": 0.0, "fee": 0.0},
            {"ticket": 3, "position_id": 11, "type": 1, "entry": 1, "symbol": "EURUSD", "volume": 0.2, "price": 1.1050, "time": 1_700_003_600 + 7200,
             "profit": 90.0, "commission": -0.7, "swap": -1.2, "fee": 0.0},
            {"ticket": 4, "position_id": 22, "type": 1, "entry": 0, "symbol": "EURUSD", "volume": 0.5, "price": 1.1060, "time": 1_700_004_000 + 7200,
             "profit": 0.0, "commission": 0.0, "swap": 0.0, "fee": 0.0},
            {"ticket": 5, "position_id": 0, "type": 2, "entry": 0, "symbol": "", "volume": 0, "price": 0, "time": 0, "profit": 1000},
        ],
        "positions": [{"ticket": 22, "symbol": "EURUSD", "type": 1, "volume": 0.5, "price_open": 1.1060, "time": 1_700_004_000 + 7200, "sl": 1.11, "tp": 0.0}],
    }
    trades = {t["external_id"]: t for t in mt5_round_trips(data, offset_s=7200)}
    closed = trades["mt5:11"]
    assert closed["status"] == "closed" and closed["side"] == "long"
    assert closed["entry_price"] == pytest.approx(1.1005) and closed["exit_price"] == pytest.approx(1.1050)
    assert closed["quantity"] == pytest.approx(20000) and closed["entry_time"] == 1_700_000_000
    assert closed["broker_pnl"] == pytest.approx(90.0 - 1.4 - 1.2) and closed["fees"] == pytest.approx(1.4 + 1.2)
    assert (closed["stop_loss"], closed["take_profit"]) == (1.09, 1.12)
    still_open = trades["mt5:22"]
    assert still_open["status"] == "open" and still_open["side"] == "short" and still_open["broker_pnl"] is None


def test_upstox_round_trips_fifo_and_flip():
    rows = [
        {"trade_id": "t1", "transaction_type": "BUY", "quantity": 10, "average_price": 100.0, "exchange_timestamp": "2026-10-28 09:20:00",
         "instrument_token": "NSE_EQ|X"},
        {"trade_id": "t2", "transaction_type": "BUY", "quantity": 10, "average_price": 102.0, "exchange_timestamp": "2026-10-28 09:30:00",
         "instrument_token": "NSE_EQ|X"},
        {"trade_id": "t3", "transaction_type": "SELL", "quantity": 25, "average_price": 105.0, "exchange_timestamp": "2026-10-28 10:00:00",
         "instrument_token": "NSE_EQ|X"},  # closes 20, opens short 5
        {"trade_id": "t3", "transaction_type": "SELL", "quantity": 25, "average_price": 105.0, "exchange_timestamp": "2026-10-28 10:00:00",
         "instrument_token": "NSE_EQ|X"},  # duplicate from the other endpoint
        {"trade_id": "h1", "transaction_type": "SELL", "quantity": 3, "price": 50.0, "trade_date": "2026-10-20", "instrument_token": "NSE_EQ|Y"},
    ]
    trips = upstox_round_trips(rows, lambda r: (r["instrument_token"], "NSE:" + r["instrument_token"][-1]))
    by_id = {t["external_id"]: t for t in trips}
    long_trip = by_id["upstox:NSE_EQ|X:t1"]
    assert long_trip["status"] == "closed" and long_trip["side"] == "long" and long_trip["quantity"] == 20
    assert long_trip["entry_price"] == 101.0 and long_trip["exit_price"] == 105.0
    assert long_trip["entry_time"] == int(datetime(2026, 10, 28, 3, 50, tzinfo=timezone.utc).timestamp())
    flip = by_id["upstox:NSE_EQ|X:t3"]
    assert flip["status"] == "open" and flip["side"] == "short" and flip["quantity"] == 5
    assert by_id["upstox:NSE_EQ|Y:h1"]["side"] == "short"


def test_journal_import_endpoint_upserts(tmp_path, monkeypatch):
    from app.services import journal_import as ji

    settings = Settings(_env_file=None, enabled_providers="simulated", default_provider="simulated", log_dir=str(tmp_path / "logs"),
                        log_level="WARNING", data_dir=str(tmp_path / "data"), database_url=f"sqlite:///{(tmp_path / 'j.db').as_posix()}")
    fake = [{"external_id": "mt5:1", "source": "mt5", "provider": "mt5", "symbol": "EURUSD", "side": "long", "status": "open",
             "entry_time": 1_700_000_000, "entry_price": 1.1, "quantity": 10000, "exit_time": None, "exit_price": None,
             "stop_loss": 1.09, "take_profit": None, "fees": 0.0, "broker_pnl": None, "notes": "MT5 position #1"}]

    async def fetch(self, days):
        return [dict(t) for t in fake]

    monkeypatch.setattr(ji.JournalImporter, "_fetch_mt5", fetch)
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/journal/import", json={"source": "mt5", "days": 7}).json()
        assert (r["created"], r["updated"]) == (1, 0)
        entry = c.get("/api/journal").json()[0]
        c.put(f"/api/journal/{entry['id']}", json={"setup": "Breakout", "tags": ["mt5", "A+"]})
        fake[0].update({"status": "closed", "exit_price": 1.105, "exit_time": 1_700_003_600, "broker_pnl": 49.3})
        r = c.post("/api/journal/import", json={"source": "mt5", "days": 7}).json()
        assert (r["created"], r["updated"], r["closed_imported"]) == (0, 1, 1)
        entry = c.get("/api/journal").json()[0]
        assert entry["status"] == "closed" and entry["pnl"] == 49.3 and entry["setup"] == "Breakout" and entry["source"] == "mt5"
        assert c.post("/api/journal/import", json={"source": "mt5", "days": 7}).json()["unchanged"] == 1
        st = c.get("/api/journal/import").json()
        assert st["last"]["mt5"]["created"] == 0 and st["sources"]["mt5"]["enabled"] is False
