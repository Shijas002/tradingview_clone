"""Candle cache (HistoryService) and the additive database migration (v1.1)."""

import asyncio
import time
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.db.database import Database
from app.models.market import Candle, Timeframe
from app.providers.base import MarketDataProvider
from app.services.history_service import HistoryService


class FakeProvider(MarketDataProvider):
    """24h market with one bar per minute since START; counts every request."""

    name = "oanda"

    def __init__(self, start: int) -> None:
        super().__init__()
        self.start = start
        self.calls: List[tuple] = []
        self.bars_served = 0
        self.now = int(time.time()) // 60 * 60

    async def connect(self): ...
    async def disconnect(self): ...
    async def get_symbols(self, query=None, limit=200): return []
    async def subscribe(self, symbol): ...
    async def unsubscribe(self, symbol): ...

    async def get_history(self, symbol, timeframe, count, to: Optional[datetime] = None):
        end = self.now if to is None else (int(to.timestamp()) - 1) // 60 * 60
        self.calls.append((count, None if to is None else int(to.timestamp())))
        first = max(self.start, end - (count - 1) * 60)
        out = [Candle(time=t, open=t % 97, high=t % 97 + 1, low=t % 97 - 1, close=t % 97 + 0.5, volume=1) for t in range(first, end + 1, 60)]
        self.bars_served += len(out)
        return out


class FakeRegistry:
    def all(self):
        return []


def make(tmp_path, max_bars=300_000):
    db = Database(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    db.create_all()
    settings = Settings(_env_file=None, candle_cache_max_bars=max_bars)
    return db, HistoryService(db, FakeRegistry(), settings)


def test_cache_latest_older_and_start(tmp_path):
    async def scenario():
        db, svc = make(tmp_path)
        p = FakeProvider(start=int(time.time()) // 60 * 60 - 3000 * 60)
        first = await svc.get(p, "EUR_USD", Timeframe.M1, 1000)
        assert len(first) == 1000 and p.calls == [(1000, None)]

        # reopen: only the newest couple of bars are requested, the rest comes from SQLite
        again = await svc.get(p, "EUR_USD", Timeframe.M1, 1000)
        assert [c.time for c in again] == [c.time for c in first]
        assert p.calls[-1][0] <= 3

        # a new bar appears -> merged
        p.now += 60
        newer = await svc.get(p, "EUR_USD", Timeframe.M1, 1000)
        assert newer[-1].time == p.now and newer[0].time == first[1].time

        # scroll back: provider asked once, second time served from cache
        to = datetime.fromtimestamp(newer[0].time, tz=timezone.utc)
        older = await svc.get(p, "EUR_USD", Timeframe.M1, 1000, to=to)
        assert len(older) == 1000 and older[-1].time == newer[0].time - 60
        n_calls = len(p.calls)
        older2 = await svc.get(p, "EUR_USD", Timeframe.M1, 1000, to=to)
        assert [c.time for c in older2] == [c.time for c in older] and len(p.calls) == n_calls

        # bigger request than cached range -> only the missing part is fetched
        big = await svc.get(p, "EUR_USD", Timeframe.M1, 2500)
        assert len(big) == 2500 and p.calls[-1][1] is not None and p.calls[-1][0] == 500

        # reaching the start of history: fewer bars -> marked, later requests cost nothing
        start_page = await svc.get(p, "EUR_USD", Timeframe.M1, 1000, to=datetime.fromtimestamp(big[0].time, tz=timezone.utc))
        assert len(start_page) == 502 and start_page[0].time == p.start
        n_calls = len(p.calls)
        assert len(await svc.get(p, "EUR_USD", Timeframe.M1, 1000, to=datetime.fromtimestamp(big[0].time, tz=timezone.utc))) == 502
        assert len(p.calls) == n_calls

        st = svc.stats()
        assert st["bars"] == 3002 and st["hits"] >= 4 and len(st["series"]) == 1
        assert svc.clear("oanda") == 3002 and svc.stats()["bars"] == 0

    asyncio.run(scenario())


def test_cache_prunes_and_skips_simulator(tmp_path):
    async def scenario():
        db, svc = make(tmp_path, max_bars=1000)
        p = FakeProvider(start=int(time.time()) // 60 * 60 - 5000 * 60)
        await svc.get(p, "X", Timeframe.M1, 1500)
        assert svc.stats()["bars"] == 1000
        sim = FakeProvider(start=0)
        sim.name = "simulated"
        await svc.get(sim, "X", Timeframe.M1, 10)
        await svc.get(sim, "X", Timeframe.M1, 10)
        assert len(sim.calls) == 2 and svc.stats()["bars"] == 1000

    asyncio.run(scenario())


def test_additive_migration_adds_new_columns(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:  # a Phase-5 era journal table without the v1.1 columns
        conn.execute(text("CREATE TABLE journal_entries (id VARCHAR(64) PRIMARY KEY, created_at INTEGER, updated_at INTEGER, "
                          "provider VARCHAR(40), symbol VARCHAR(80), timeframe VARCHAR(8), side VARCHAR(8), status VARCHAR(8), "
                          "entry_time INTEGER, entry_price FLOAT, quantity FLOAT, exit_time INTEGER, exit_price FLOAT, stop_loss FLOAT, "
                          "take_profit FLOAT, fees FLOAT, setup VARCHAR(120), tags JSON, rating INTEGER, notes TEXT, screenshot VARCHAR(200), "
                          "screenshot_version INTEGER, notion_page_id VARCHAR(64), notion_status VARCHAR(12), notion_error TEXT, "
                          "notion_synced_at INTEGER, notion_screenshot_version INTEGER, notion_image_block_id VARCHAR(64), notion_url VARCHAR(300))"))
        conn.execute(text("INSERT INTO journal_entries (id, symbol, side, status, entry_price) VALUES ('a', 'EURUSD', 'long', 'open', 1.1)"))
    engine.dispose()
    db = Database(url)
    db.create_all()
    with db.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(journal_entries)"))}
        assert {"source", "external_id", "broker_pnl"} <= cols
        assert conn.execute(text("SELECT source FROM journal_entries WHERE id='a'")).scalar_one() == "manual"
    db.dispose()
