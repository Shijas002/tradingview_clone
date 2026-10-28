"""Phase 3: workspace/drawings/scripts persistence, backup, and the alert engine."""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.alert_engine import evaluate, line_level


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


# ---------------------------------------------------------------- pure logic
def test_evaluate_conditions():
    assert evaluate("crossing_up", 0.9, 1.1, 1.0)
    assert not evaluate("crossing_up", 1.1, 1.2, 1.0)
    assert evaluate("crossing_down", 1.1, 0.9, 1.0)
    assert evaluate("crossing", 1.1, 0.9, 1.0) and evaluate("crossing", 0.9, 1.0, 1.0)
    assert not evaluate("crossing", None, 1.2, 1.0)  # needs a previous price
    assert evaluate("greater_than", None, 1.2, 1.0) and not evaluate("less_than", None, 1.2, 1.0)
    assert evaluate("entering_channel", 0.5, 1.5, 1.0, 2.0)
    assert evaluate("exiting_channel", 1.5, 2.5, 1.0, 2.0)
    assert not evaluate("exiting_channel", 1.5, 1.6, 1.0, 2.0)
    # moving trend line: price flat at 1.0, line rises from 0.9 to 1.1 -> crossed down
    assert evaluate("crossing_down", 1.0, 1.0, 1.1, prev_level=0.9)


def test_line_level():
    line = {"t1": 100, "p1": 1.0, "t2": 200, "p2": 2.0, "extendLeft": False, "extendRight": True}
    assert line_level(line, 150) == 1.5
    assert line_level(line, 300) == 3.0
    assert line_level(line, 50) is None


# ------------------------------------------------------------------ storage
def test_workspace_roundtrip(client):
    assert client.get("/api/workspace").json() == {"data": {}, "version": 0, "updated_at": None}
    r = client.put("/api/workspace", json={"data": {"layout": "2v", "panes": [{"id": "a"}]}})
    assert r.status_code == 200 and r.json()["version"] == 1
    r = client.put("/api/workspace", json={"data": {"layout": "4"}})
    assert r.json()["version"] == 2
    assert client.get("/api/workspace").json()["data"] == {"layout": "4"}


def test_drawings_and_scripts(client):
    d = [{"id": "d1", "type": "hline", "points": [{"time": 1, "price": 1.1}], "style": {}}]
    assert client.put("/api/drawings/simulated:eurusd", json={"drawings": d}).json()["count"] == 1
    assert client.get("/api/drawings").json() == {"simulated:eurusd": d}
    client.put("/api/drawings/simulated:eurusd", json={"drawings": []})
    assert client.get("/api/drawings").json() == {}

    r = client.put("/api/scripts/scr_1", json={"name": "Mine", "source": "//@version=6\nindicator('x')\nplot(close)"})
    assert r.status_code == 200 and r.json()["name"] == "Mine"
    client.put("/api/scripts/scr_1", json={"name": "Renamed", "source": "plot(open)"})
    scripts = client.get("/api/scripts").json()
    assert len(scripts) == 1 and scripts[0]["name"] == "Renamed"
    assert client.delete("/api/scripts/scr_1").status_code == 204
    assert client.delete("/api/scripts/scr_1").status_code == 404


def test_export_import(client):
    client.put("/api/workspace", json={"data": {"layout": "3"}})
    client.put("/api/scripts/s1", json={"name": "A", "source": "plot(close)"})
    client.put("/api/drawings/simulated:xauusd", json={"drawings": [{"id": "x", "type": "vline", "points": [{"time": 5, "price": 1}], "style": {}}]})
    backup = client.get("/api/export").json()
    assert backup["format"] == "tvclone-backup" and len(backup["scripts"]) == 1
    client.delete("/api/scripts/s1")
    r = client.post("/api/import", json=backup)
    assert r.status_code == 200 and r.json()["scripts"] == 1
    assert client.get("/api/scripts").json()[0]["name"] == "A"
    assert client.post("/api/import", json={"format": "nope"}).status_code == 400


# ------------------------------------------------------------------- alerts
def test_alert_validation_and_crud(client):
    bad = client.post("/api/alerts", json={"provider": "simulated", "symbol": "EURUSD", "condition": "crossing"})
    assert bad.status_code == 422
    bad = client.post("/api/alerts", json={"provider": "simulated", "symbol": "EURUSD", "condition": "entering_channel", "price": 1})
    assert bad.status_code == 422
    r = client.post("/api/alerts", json={"provider": "simulated", "symbol": "EURUSD", "condition": "crossing", "price": 999.0, "name": "far"})
    assert r.status_code == 201
    a = r.json()
    assert a["status"] == "active"
    upd = dict(a, active=False)
    for k in ("id", "status", "created_at", "last_triggered_at", "trigger_count"):
        upd.pop(k)
    r = client.put(f"/api/alerts/{a['id']}", json=upd)
    assert r.json()["status"] == "paused"
    assert client.get("/api/alerts/engine").json()["feeds"] == []  # paused -> nothing watched
    assert client.delete(f"/api/alerts/{a['id']}").status_code == 204
    assert client.get("/api/alerts").json() == []


def test_alert_fires_over_websocket(client):
    with client.websocket_connect("/ws/market") as ws:
        assert ws.receive_json()["type"] == "welcome"
        r = client.post(
            "/api/alerts",
            json={"provider": "simulated", "symbol": "eurusd", "condition": "greater_than", "price": 0.0001, "trigger": "once", "message": "{{ticker}} above {{level}} at {{close}}"},
        )
        alert_id = r.json()["id"]
        got = None
        deadline = time.time() + 10
        while time.time() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "alert":
                got = msg["data"]
                break
        assert got is not None, "alert was not broadcast"
        assert got["alert_id"] == alert_id and got["message"].startswith("eurusd above 0.0001 at")
    time.sleep(0.3)
    alert = client.get("/api/alerts").json()[0]
    assert alert["status"] == "triggered" and alert["trigger_count"] == 1 and alert["active"] is False
    events = client.get("/api/alerts/events").json()
    assert len(events) == 1 and events[0]["alert_id"] == alert_id
    assert client.delete("/api/alerts/events").status_code == 204
    assert client.get("/api/alerts/events").json() == []
