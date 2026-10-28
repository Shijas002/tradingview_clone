"""
Tests for the trade journal (Phase 5): local CRUD/stats/CSV/screenshots and the
Notion mirror against an in-memory fake of the Notion API (no account needed).
"""

import base64
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.models import JournalEntry
from app.main import create_app
from app.services import notion_journal as nj
from app.services.notion_journal import parse_notion_id, trade_metrics

# 1x1 transparent PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
ORIGINAL_INIT = nj.NotionJournal.__init__
DB_ID = "1f2e3d4c5b6a49788796a5b4c3d2e1f0"
DS_ID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"


class FakeNotion:
    """Just enough of the Notion API (2026-03-11) for the journal sync."""

    def __init__(self, token: str = "ntn_test") -> None:
        self.token = token
        self.calls: List[str] = []
        self.versions: set = set()
        self.ds_props: Dict[str, Dict[str, Any]] = {
            "Name": {"id": "title", "name": "Name", "type": "title", "title": {}},
            "Side": {"id": "x1", "name": "Side", "type": "rich_text", "rich_text": {}},  # user's own, different type
        }
        self.databases = {DB_ID: {"object": "database", "id": DB_ID, "title": [{"plain_text": "My Trades"}],
                                  "url": "https://www.notion.so/My-Trades", "data_sources": [{"id": DS_ID, "name": "My Trades"}]}}
        self.pages: Dict[str, Dict[str, Any]] = {}
        self.blocks: Dict[str, Dict[str, Any]] = {}
        self.uploads: Dict[str, Dict[str, Any]] = {}
        self.rate_limit_once = True

    @staticmethod
    def _norm(i: str) -> str:
        return i.replace("-", "")

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        self.versions.add(request.headers.get("Notion-Version"))
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"object": "error", "status": 401, "code": "unauthorized", "message": "API token is invalid."})
        if method == "POST" and path == "/v1/pages" and self.rate_limit_once:
            self.rate_limit_once = False
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": "rate_limited", "message": "slow down"})
        body = json.loads(request.content or b"{}") if request.headers.get("content-type", "").startswith("application/json") else {}

        if path == "/v1/users/me":
            return httpx.Response(200, json={"object": "user", "type": "bot", "name": "TV Journal"})
        m = re.fullmatch(r"/v1/databases/([\w-]+)", path)
        if m and method == "GET":
            db = self.databases.get(self._norm(m.group(1)))
            return httpx.Response(200, json=db) if db else self._404()
        if path == "/v1/databases" and method == "POST":
            new_id = uuid.uuid4().hex
            for name, spec in body["initial_data_source"]["properties"].items():
                self.ds_props[name] = {"name": name, **spec}
            self.ds_props.pop("Name", None)
            db = {"object": "database", "id": new_id, "url": "https://www.notion.so/created", "data_sources": [{"id": DS_ID, "name": "Trading Journal"}]}
            self.databases[new_id] = db
            self.created_db_body = body
            return httpx.Response(200, json=db)
        m = re.fullmatch(r"/v1/data_sources/([\w-]+)", path)
        if m:
            if self._norm(m.group(1)) != self._norm(DS_ID):
                return self._404()
            if method == "PATCH":
                for name, spec in body["properties"].items():
                    self.ds_props[name] = {"name": name, **spec}
            return httpx.Response(200, json={"object": "data_source", "id": DS_ID, "title": [{"plain_text": "My Trades"}], "properties": self.ds_props})
        if path == "/v1/pages" and method == "POST":
            assert body["parent"] == {"type": "data_source_id", "data_source_id": DS_ID}
            err = self._validate(body["properties"])
            if err:
                return err
            pid = str(uuid.uuid4())
            self.pages[pid] = {"id": pid, "properties": body["properties"], "in_trash": False, "children": []}
            return httpx.Response(200, json={"object": "page", "id": pid, "url": f"https://www.notion.so/{pid.replace('-', '')}"})
        m = re.fullmatch(r"/v1/pages/([\w-]+)", path)
        if m and method == "PATCH":
            page = self.pages.get(m.group(1))
            if page is None:
                return self._404()
            if "in_trash" in body:
                page["in_trash"] = body["in_trash"]
            if "properties" in body:
                err = self._validate(body["properties"])
                if err:
                    return err
                page["properties"].update(body["properties"])
            return httpx.Response(200, json={"object": "page", "id": page["id"], "url": f"https://www.notion.so/{page['id'].replace('-', '')}"})
        if path == "/v1/file_uploads" and method == "POST":
            fid = str(uuid.uuid4())
            self.uploads[fid] = {"status": "pending", "filename": body.get("filename")}
            return httpx.Response(200, json={"object": "file_upload", "id": fid, "status": "pending",
                                             "upload_url": f"https://api.notion.com/v1/file_uploads/{fid}/send"})
        m = re.fullmatch(r"/v1/file_uploads/([\w-]+)/send", path)
        if m and method == "POST":
            up = self.uploads[m.group(1)]
            assert request.headers["content-type"].startswith("multipart/form-data")
            assert b"\x89PNG" in request.content
            up["status"] = "uploaded"
            return httpx.Response(200, json={"object": "file_upload", "id": m.group(1), "status": "uploaded"})
        m = re.fullmatch(r"/v1/blocks/([\w-]+)/children", path)
        if m and method == "PATCH":
            page = self.pages[m.group(1)]
            results = []
            for child in body["children"]:
                fid = child["image"]["file_upload"]["id"]
                assert self.uploads[fid]["status"] == "uploaded"
                bid = str(uuid.uuid4())
                self.blocks[bid] = {"page": page["id"], "upload": fid}
                page["children"].append(bid)
                results.append({"object": "block", "id": bid, "type": "image"})
            return httpx.Response(200, json={"object": "list", "results": results})
        m = re.fullmatch(r"/v1/blocks/([\w-]+)", path)
        if m and method == "DELETE":
            blk = self.blocks.pop(m.group(1), None)
            if blk is None:
                return self._404()
            self.pages[blk["page"]]["children"].remove(m.group(1))
            return httpx.Response(200, json={"object": "block", "id": m.group(1), "in_trash": True})
        return self._404()

    def _validate(self, props: Dict[str, Any]) -> Optional[httpx.Response]:
        for name, value in props.items():
            if name not in self.ds_props:
                return httpx.Response(400, json={"code": "validation_error", "message": f"{name} is not a property that exists."})
            sel = value.get("select")
            if sel and "," in sel.get("name", ""):
                return httpx.Response(400, json={"code": "validation_error", "message": "Select option names cannot contain commas."})
        return None

    @staticmethod
    def _404() -> httpx.Response:
        return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "Could not find object."})


def make_client(tmp_path, monkeypatch, fake: Optional[FakeNotion] = None, **kw):
    if fake is not None:

        def patched(self, settings, db, broadcast=None, transport=None):
            ORIGINAL_INIT(self, settings, db, broadcast, transport=httpx.MockTransport(fake.handler))

        monkeypatch.setattr(nj.NotionJournal, "__init__", patched)
        monkeypatch.setattr(nj, "MIN_INTERVAL_S", 0.0)
    settings = Settings(
        _env_file=None,
        enabled_providers="simulated",
        default_provider="simulated",
        log_dir=str(tmp_path / "logs"),
        log_level="WARNING",
        data_dir=str(tmp_path / "data"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        **kw,
    )
    return TestClient(create_app(settings))


def wait_until(fn, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"condition not met, last={last!r}")


TRADE = {"provider": "upstox", "symbol": "NSE:RELIANCE", "timeframe": "M15", "side": "long", "entry_price": 1400.0,
         "quantity": 10, "stop_loss": 1390.0, "take_profit": 1430.0, "setup": "Breakout, retest", "tags": ["trend", "A+"],
         "entry_time": 1761637500, "notes": "Clean break above the range."}


# ======================================================================== local
def test_metrics_and_id_parsing():
    e = JournalEntry(side="short", status="closed", entry_price=100.0, exit_price=95.0, quantity=2, fees=1.0, stop_loss=102.0)
    assert trade_metrics(e) == (9.0, 2.5)
    e.status = "open"
    assert trade_metrics(e) == (None, None)
    assert parse_notion_id("https://www.notion.so/team/My-Trades-1f2e3d4c5b6a49788796a5b4c3d2e1f0?v=0123456789abcdef0123456789abcdef") == \
        "1f2e3d4c-5b6a-4978-8796-a5b4c3d2e1f0"
    assert parse_notion_id("1f2e3d4c-5b6a-4978-8796-a5b4c3d2e1f0") == "1f2e3d4c-5b6a-4978-8796-a5b4c3d2e1f0"


def test_local_journal_crud_stats_csv_and_screenshot(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        assert c.get("/api/journal/notion").json()["configured"] is False
        r = c.post("/api/journal", json={**TRADE, "screenshot": PNG_URL})
        assert r.status_code == 201, r.text
        e = r.json()
        assert e["notion_status"] == "local" and e["has_screenshot"] and e["pnl"] is None
        assert c.get(f"/api/journal/{e['id']}/screenshot").content == PNG

        # closing without an exit price is rejected, with one it computes P&L and R
        r = c.put(f"/api/journal/{e['id']}", json={"status": "closed"})
        assert r.status_code == 400 and "exit price" in r.json()["error"]
        r = c.put(f"/api/journal/{e['id']}", json={"status": "closed", "exit_price": 1425.0, "fees": 5})
        body = r.json()
        assert body["pnl"] == 245.0 and body["r_multiple"] == 2.5 and body["exit_time"]

        c.post("/api/journal", json={**TRADE, "symbol": "NSE:TCS", "side": "short", "status": "closed", "entry_price": 3000,
                                     "exit_price": 3020, "quantity": 1, "stop_loss": 3010})
        c.post("/api/journal", json={**TRADE, "symbol": "EURUSD", "provider": "oanda"})  # still open
        assert c.post("/api/journal", json={**TRADE, "status": "closed"}).status_code == 422
        assert c.post("/api/journal", json={**TRADE, "screenshot": "data:image/png;base64,AAAA"}).status_code == 400

        st = c.get("/api/journal/stats").json()
        assert (st["trades"], st["open"], st["closed"], st["wins"], st["losses"]) == (3, 1, 2, 1, 1)
        assert st["win_rate"] == 50.0 and st["net_pnl"] == 225.0 and st["profit_factor"] == 245 / 20
        assert st["avg_r"] == 0.25  # (2.5 + -2) / 2

        assert len(c.get("/api/journal").json()) == 3
        assert [x["symbol"] for x in c.get("/api/journal", params={"status": "open"}).json()] == ["EURUSD"]
        csv_text = c.get("/api/journal/export.csv").content.decode("utf-8-sig")
        assert csv_text.splitlines()[0].startswith("id,symbol,provider") and "NSE:TCS" in csv_text

        # remove screenshot, then delete the entry (local only -> gone immediately)
        c.put(f"/api/journal/{e['id']}", json={"remove_screenshot": True})
        assert c.get(f"/api/journal/{e['id']}/screenshot").status_code == 404
        assert c.delete(f"/api/journal/{e['id']}").status_code == 204
        assert c.get(f"/api/journal/{e['id']}").status_code == 404

        # backup contains the journal and restores it
        backup = c.get("/api/export").json()
        assert len(backup["journal"]) == 2
        c.post("/api/journal", json=TRADE)
        r = c.post("/api/import", json=backup)
        assert r.json()["journal"] == 2 and len(c.get("/api/journal").json()) == 2


# ======================================================================= Notion
def test_notion_sync_create_update_screenshot_delete(tmp_path, monkeypatch):
    fake = FakeNotion()
    with make_client(tmp_path, monkeypatch, fake, notion_api_key="ntn_test",
                     notion_database_id=f"https://www.notion.so/team/My-Trades-{DB_ID}?v=1") as c:
        st = wait_until(lambda: (s := c.get("/api/journal/notion").json())["connected"] and s)
        assert st["database_title"] == "My Trades" and st["skipped_properties"] == ["Side"]
        assert {"Entry", "P&L", "Screenshot", "Tags", "Journal ID"} <= set(fake.ds_props)
        assert fake.ds_props["Side"]["type"] == "rich_text"  # the user's property was not touched
        assert fake.versions == {nj.NOTION_VERSION}

        e = c.post("/api/journal", json={**TRADE, "screenshot": PNG_URL}).json()
        assert e["notion_status"] == "pending"
        synced = wait_until(lambda: (x := c.get(f"/api/journal/{e['id']}").json())["notion_status"] == "synced" and x)
        assert synced["notion_url"].startswith("https://www.notion.so/")
        page = next(iter(fake.pages.values()))
        props = page["properties"]
        assert props["Name"]["title"][0]["text"]["content"] == "Long NSE:RELIANCE @ 1400"
        assert "Side" not in props  # skipped (type mismatch)
        assert props["Symbol"]["select"]["name"] == "NSE:RELIANCE"
        assert props["Setup"]["select"]["name"] == "Breakout retest"  # commas removed
        assert props["Entry Time"]["date"]["start"] == "2025-10-28T07:45:00Z"
        assert props["Screenshot"]["files"][0]["type"] == "file_upload"
        assert len(page["children"]) == 1  # image block in the page body

        # close the trade + new screenshot -> page updated, old image replaced
        c.put(f"/api/journal/{e['id']}", json={"status": "closed", "exit_price": 1425.0, "screenshot": PNG_URL})
        wait_until(lambda: c.get(f"/api/journal/{e['id']}").json()["notion_status"] == "synced"
                   and page["properties"].get("P&L", {}).get("number") == 250.0)
        assert page["properties"]["Result"]["select"]["name"] == "Win"
        assert page["properties"]["Status"]["select"]["name"] == "Closed"
        assert len(page["children"]) == 1 and len(fake.uploads) == 2

        # delete -> Notion page trashed, entry removed locally
        c.delete(f"/api/journal/{e['id']}")
        wait_until(lambda: page["in_trash"])
        wait_until(lambda: c.get(f"/api/journal/{e['id']}").status_code == 404)
        assert c.get("/api/journal/notion").json()["pending"] == 0


def test_notion_validation_error_is_shown_and_retryable(tmp_path, monkeypatch):
    fake = FakeNotion()
    with make_client(tmp_path, monkeypatch, fake, notion_api_key="ntn_test", notion_database_id=DB_ID) as c:
        wait_until(lambda: c.get("/api/journal/notion").json()["connected"])
        fake.ds_props.pop("Provider")  # property deleted in Notion after setup -> Notion rejects the page
        e = c.post("/api/journal", json=TRADE).json()
        failed = wait_until(lambda: (x := c.get(f"/api/journal/{e['id']}").json())["notion_status"] == "error" and x)
        assert "Provider is not a property" in failed["notion_error"]
        assert c.get("/api/journal/notion").json()["errors"] == 1
        fake.ds_props["Provider"] = {"name": "Provider", "type": "select", "select": {}}
        assert c.post("/api/journal/notion/retry").json()["retried"] == 1
        wait_until(lambda: c.get(f"/api/journal/{e['id']}").json()["notion_status"] == "synced")


def test_notion_creates_database_on_parent_page(tmp_path, monkeypatch):
    fake = FakeNotion()
    fake.ds_props = {}
    parent = "0123456789abcdef0123456789abcdef"
    with make_client(tmp_path, monkeypatch, fake, notion_api_key="ntn_test", notion_parent_page_id=parent) as c:
        st = wait_until(lambda: (s := c.get("/api/journal/notion").json())["connected"] and s)
        assert st["database_title"] == "Trading Journal"
        assert fake.created_db_body["parent"] == {"type": "page_id", "page_id": parse_notion_id(parent)}
        assert fake.ds_props["Trade"]["type"] == "title"
        saved = json.loads((tmp_path / "data" / "notion_journal.json").read_text())
        assert saved["data_source_id"] == DS_ID
        e = c.post("/api/journal", json=TRADE).json()
        wait_until(lambda: c.get(f"/api/journal/{e['id']}").json()["notion_status"] == "synced")
        assert next(iter(fake.pages.values()))["properties"]["Trade"]["title"][0]["text"]["content"].startswith("Long")
    # restart: the created database is reused (not created twice)
    fake2 = FakeNotion()
    fake2.ds_props = dict(fake.ds_props)
    with make_client(tmp_path, monkeypatch, fake2, notion_api_key="ntn_test", notion_parent_page_id=parent) as c:
        wait_until(lambda: c.get("/api/journal/notion").json()["connected"])
        assert "GET /v1/users/me" in fake2.calls and "POST /v1/databases" not in fake2.calls


def test_notion_bad_token_and_unshared_database(tmp_path, monkeypatch):
    fake = FakeNotion(token="ntn_other")
    with make_client(tmp_path, monkeypatch, fake, notion_api_key="ntn_test", notion_database_id=DB_ID) as c:
        st = wait_until(lambda: (s := c.get("/api/journal/notion").json())["message"] != "Starting…" and s)
        assert st["connected"] is False and "rejected NOTION_API_KEY" in st["message"]
        e = c.post("/api/journal", json=TRADE).json()
        assert e["notion_status"] == "pending"  # kept locally, synced once the key is fixed

    fake = FakeNotion()
    with make_client(tmp_path / "b", monkeypatch, fake, notion_api_key="ntn_test", notion_database_id="99999999999999999999999999999999") as c:
        st = wait_until(lambda: (s := c.get("/api/journal/notion").json())["message"] != "Starting…" and s)
        assert "not shared" in st["message"] and "Connections" in st["message"]
