"""
End-to-end tests against the real app using only the simulated provider,
so they run on any OS without MetaTrader 5.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        enabled_providers="simulated",
        default_provider="simulated",
        sim_tick_interval_ms=20,
        log_dir=str(tmp_path / "logs"),
        log_level="WARNING",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_health_and_providers(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["providers"] == {"simulated": True}
    r = client.get("/api/providers")
    assert r.json()[0]["name"] == "simulated"


def test_symbols_search(client):
    r = client.get("/api/symbols", params={"query": "usd"})
    names = {s["symbol"] for s in r.json()}
    assert {"EURUSD", "XAUUSD"} <= names
    assert "NIFTY50" not in names


def test_history_and_lazy_load(client):
    r = client.get("/api/history", params={"symbol": "eurusd", "timeframe": "M5", "count": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 100
    times = [c["time"] for c in body["candles"]]
    assert times == sorted(times) and all(t % 300 == 0 for t in times)

    older = client.get("/api/history", params={"symbol": "EURUSD", "timeframe": "M5", "count": 50, "to": times[0]})
    older_times = [c["time"] for c in older.json()["candles"]]
    assert len(older_times) == 50 and older_times[-1] == times[0] - 300
    # simulator pages join without a price gap
    assert older.json()["candles"][-1]["close"] == body["candles"][0]["open"]


def test_errors_are_clean_json(client):
    assert client.get("/api/history", params={"symbol": "NOPE"}).status_code == 404
    assert client.get("/api/history", params={"symbol": "EURUSD", "provider": "mt5"}).status_code == 404
    r = client.get("/api/history", params={"symbol": "EURUSD", "count": 999999})
    assert r.status_code == 400 and "HISTORY_MAX_BARS" in r.json()["error"]


def test_websocket_stream(client):
    with client.websocket_connect("/ws/market") as ws:
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"action": "subscribe", "provider": "simulated", "symbol": "XAUUSD", "timeframe": "M1"})
        sub = ws.receive_json()
        assert sub["type"] == "subscribed" and sub["candle"] is not None

        seen = set()
        for _ in range(20):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if {"tick", "candle"} <= seen:
                break
        assert {"tick", "candle"} <= seen

        ws.send_json({"action": "bogus"})
        while (msg := ws.receive_json())["type"] != "error":
            pass
        ws.send_json({"action": "unsubscribe", "provider": "simulated", "symbol": "XAUUSD", "timeframe": "M1"})
        while (msg := ws.receive_json())["type"] != "unsubscribed":
            pass
    assert client.get("/api/stream/stats").json()["feeds"] == []


def test_viewer_is_served(client):
    r = client.get("/viewer")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "LightweightCharts" in r.text and "/ws/market" in r.text
    assert client.get("/", follow_redirects=False).status_code == 307


def test_frontend_app_is_served_when_built(client):
    from app.main import FRONTEND_DIST

    r = client.get("/app/")
    if (FRONTEND_DIST / "index.html").exists():
        assert r.status_code == 200 and '<div id="root">' in r.text
        assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
    else:
        assert r.status_code == 503
