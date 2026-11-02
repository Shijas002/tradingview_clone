"""
Candle cache (v1.1): history is served from SQLite whenever possible.

Every REST history request goes through ``HistoryService.get``. Bars received
from a provider are stored in ``candle_cache`` together with a *coverage* range
(``candle_coverage``): "every bar of this series between start and end is
cached". With that knowledge:

* **Latest bars** (chart opens): only the bars since the newest cached bar are
  requested (a handful instead of 1000), merged, and the answer comes from SQLite.
* **Older bars** (scrolling back): served straight from the cache when the range
  is covered; otherwise only the missing older part is requested.
* **Start of history**: when a provider returns fewer bars than asked, the series
  is marked as complete at the start (trusted for 24 h), so scrolling further back
  costs no API call.

Cached providers: mt5, oanda, upstox and auto (auto is cached per active source
and time shift, so MT5 and OANDA bars never mix). The simulator is not cached.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import Settings
from app.db.database import Database
from app.db.models import CandleBar, CandleCoverage, now_ms
from app.models.market import Candle, Timeframe
from app.providers.base import MarketDataProvider
from app.providers.registry import ProviderRegistry

log = logging.getLogger(__name__)

CACHEABLE = {"mt5", "oanda", "upstox"}
AT_START_TRUST_MS = 24 * 3600 * 1000
MAX_FETCH = 5000

SeriesKey = Tuple[str, str, str]  # (cache provider key, symbol, timeframe)


@dataclass
class _Cov:
    id: int
    start: int
    end: int
    at_start: bool
    updated_at: int

    @property
    def trusted_start(self) -> bool:
        return self.at_start and now_ms() - self.updated_at < AT_START_TRUST_MS


class HistoryService:
    def __init__(self, db: Database, registry: ProviderRegistry, settings: Settings) -> None:
        self.db = db
        self.registry = registry
        self.enabled = settings.candle_cache_enabled
        self.max_bars = settings.candle_cache_max_bars
        self._locks: Dict[SeriesKey, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0
        self.provider_bars = 0

    # ================================================================ public
    async def get(self, provider: MarketDataProvider, symbol: str, timeframe: Timeframe, count: int, to: Optional[datetime] = None) -> List[Candle]:
        key = self._key(provider, symbol, timeframe)
        if key is None:
            return await provider.get_history(symbol, timeframe, count, to=to)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            try:
                if to is None:
                    return await self._latest(provider, key, symbol, timeframe, count)
                return await self._older(provider, key, symbol, timeframe, count, int(to.timestamp()))
            except Exception as exc:
                if getattr(exc, "status_code", None) is not None:
                    raise  # provider errors (not connected, bad symbol...) go to the client unchanged
                log.exception("Candle cache failed for %s - asking the provider directly", key)
                return await provider.get_history(symbol, timeframe, count, to=to)

    def _key(self, provider: MarketDataProvider, symbol: str, timeframe: Timeframe) -> Optional[SeriesKey]:
        if not self.enabled:
            return None
        if provider.name in CACHEABLE:
            return (provider.name, symbol, timeframe.value)
        if provider.name == "auto":
            details = provider.status().details
            source = details.get("active_source")
            if not source:
                return None
            shift = details.get("time_shift_hours") or 0
            return (f"auto:{source}:{shift:g}", symbol, timeframe.value)
        return None

    # ============================================================== latest
    async def _latest(self, provider: MarketDataProvider, key: SeriesKey, symbol: str, tf: Timeframe, count: int) -> List[Candle]:
        covs = await asyncio.to_thread(self._coverages, key)
        newest = max(covs, key=lambda c: c.end) if covs else None
        if newest is not None:
            period = tf.seconds if tf is not Timeframe.MN1 else 31 * 86400
            missing = int((time.time() - newest.end) / period) + 2
            if missing <= min(count + 2, MAX_FETCH):
                fresh = await self._fetch(provider, symbol, tf, max(missing, 2))
                if not fresh:
                    self.hits += 1
                    return await self._from_cache(provider, key, symbol, tf, count, newest.end)
                if fresh[0].time <= newest.end:  # overlaps the cache -> contiguous
                    await asyncio.to_thread(self._store, key, fresh, fresh[0].time, fresh[-1].time, False)
                    self.hits += 1
                    return await self._from_cache(provider, key, symbol, tf, count, fresh[-1].time)
        # nothing usable cached: one full request
        self.misses += 1
        fresh = await self._fetch(provider, symbol, tf, count)
        if fresh:
            await asyncio.to_thread(self._store, key, fresh, fresh[0].time, fresh[-1].time, len(fresh) < count)
        return fresh

    async def _from_cache(self, provider: MarketDataProvider, key: SeriesKey, symbol: str, tf: Timeframe, count: int, end_time: int) -> List[Candle]:
        """The ``count`` bars up to ``end_time`` from the covered range; fetch older ones if it is too short."""
        cov = await asyncio.to_thread(self._coverage_at, key, end_time)
        if cov is None:
            return await self._fetch(provider, symbol, tf, count)
        bars = await asyncio.to_thread(self._bars_before, key, end_time + 1, count, cov.start)
        if len(bars) >= count or cov.trusted_start:
            return bars[-count:]
        older = await self._fetch_older(provider, key, symbol, tf, count - len(bars), cov.start)
        return (older + bars)[-count:]

    # =============================================================== older
    async def _older(self, provider: MarketDataProvider, key: SeriesKey, symbol: str, tf: Timeframe, count: int, to: int) -> List[Candle]:
        cov = await asyncio.to_thread(self._coverage_at, key, to - 1)
        if cov is not None:
            bars = await asyncio.to_thread(self._bars_before, key, to, count, cov.start)
            if len(bars) >= count or cov.trusted_start:
                self.hits += 1
                return bars[-count:]
            older = await self._fetch_older(provider, key, symbol, tf, count - len(bars), cov.start)
            self.hits += 1
            return (older + bars)[-count:]
        self.misses += 1
        fresh = await self._fetch(provider, symbol, tf, count, to)
        if fresh:
            await asyncio.to_thread(self._store, key, fresh, fresh[0].time, to - 1, len(fresh) < count)
        return fresh

    async def _fetch_older(self, provider: MarketDataProvider, key: SeriesKey, symbol: str, tf: Timeframe, need: int, boundary: int) -> List[Candle]:
        need = min(max(need, 1), MAX_FETCH)
        older = await self._fetch(provider, symbol, tf, need, boundary)
        at_start = len(older) < need
        start = older[0].time if older else boundary - 1
        await asyncio.to_thread(self._store, key, older, start, boundary - 1, at_start)
        return older

    async def _fetch(self, provider: MarketDataProvider, symbol: str, tf: Timeframe, count: int, to: Optional[int] = None) -> List[Candle]:
        to_dt = datetime.fromtimestamp(to, tz=timezone.utc) if to is not None else None
        bars = await provider.get_history(symbol, tf, count, to=to_dt)
        self.provider_bars += len(bars)
        return bars

    # ============================================================ database
    def _coverages(self, key: SeriesKey) -> List[_Cov]:
        p, s, tf = key
        with self.db.session() as session:
            rows = session.execute(
                select(CandleCoverage).where(CandleCoverage.provider == p, CandleCoverage.symbol == s, CandleCoverage.timeframe == tf)
            ).scalars().all()
            return [_Cov(r.id, r.start, r.end, bool(r.at_start), r.updated_at or 0) for r in rows]

    def _coverage_at(self, key: SeriesKey, t: int) -> Optional[_Cov]:
        for c in self._coverages(key):
            if c.start <= t <= c.end:
                return c
        return None

    def _bars_before(self, key: SeriesKey, before: int, limit: int, not_before: Optional[int] = None) -> List[Candle]:
        p, s, tf = key
        conds = [CandleBar.provider == p, CandleBar.symbol == s, CandleBar.timeframe == tf, CandleBar.time < before]
        if not_before is not None:
            conds.append(CandleBar.time >= not_before)
        with self.db.session() as session:
            rows = session.execute(select(CandleBar).where(and_(*conds)).order_by(CandleBar.time.desc()).limit(limit)).scalars().all()
            return [Candle(time=r.time, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume or 0.0) for r in reversed(rows)]

    def _store(self, key: SeriesKey, candles: List[Candle], start: int, end: int, at_start: bool) -> None:
        p, s, tf = key
        with self.db.session() as session:
            if candles:
                rows = [
                    {"provider": p, "symbol": s, "timeframe": tf, "time": c.time, "open": c.open, "high": c.high,
                     "low": c.low, "close": c.close, "volume": c.volume}
                    for c in candles
                ]
                if self.db.engine.dialect.name == "sqlite":
                    for i in range(0, len(rows), 500):
                        stmt = sqlite_insert(CandleBar).values(rows[i:i + 500])
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["provider", "symbol", "timeframe", "time"],
                            set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "volume")},
                        )
                        session.execute(stmt)
                else:  # pragma: no cover - non-SQLite databases
                    for r in rows:
                        session.merge(CandleBar(**r))
            if end < start:
                return
            # merge the new range with every overlapping / touching range
            existing = session.execute(
                select(CandleCoverage).where(CandleCoverage.provider == p, CandleCoverage.symbol == s, CandleCoverage.timeframe == tf)
            ).scalars().all()
            new_start, new_end, new_at_start = start, end, at_start
            for c in existing:
                if c.start <= new_end + 1 and c.end >= new_start - 1:
                    if c.start < new_start:
                        new_start, new_at_start = c.start, bool(c.at_start)
                    elif c.start == new_start:
                        new_at_start = new_at_start or bool(c.at_start)
                    new_end = max(new_end, c.end)
                    session.delete(c)
            session.add(CandleCoverage(provider=p, symbol=s, timeframe=tf, start=new_start, end=new_end,
                                       at_start=new_at_start, updated_at=now_ms()))
        self._prune(key)

    def _prune(self, key: SeriesKey) -> None:
        """Keep at most CANDLE_CACHE_MAX_BARS bars per series (drops the oldest and its coverage)."""
        p, s, tf = key
        with self.db.session() as session:
            total = session.execute(
                select(func.count()).select_from(CandleBar).where(CandleBar.provider == p, CandleBar.symbol == s, CandleBar.timeframe == tf)
            ).scalar_one()
            if total <= self.max_bars:
                return
            cutoff = session.execute(
                select(CandleBar.time).where(CandleBar.provider == p, CandleBar.symbol == s, CandleBar.timeframe == tf)
                .order_by(CandleBar.time.desc()).offset(self.max_bars).limit(1)
            ).scalar_one()
            session.execute(delete(CandleBar).where(CandleBar.provider == p, CandleBar.symbol == s, CandleBar.timeframe == tf, CandleBar.time <= cutoff))
            for c in session.execute(
                select(CandleCoverage).where(CandleCoverage.provider == p, CandleCoverage.symbol == s, CandleCoverage.timeframe == tf)
            ).scalars().all():
                if c.end <= cutoff:
                    session.delete(c)
                elif c.start <= cutoff:
                    c.start, c.at_start = cutoff + 1, False

    # =============================================================== admin
    def stats(self) -> Dict[str, object]:
        with self.db.session() as session:
            bars = session.execute(select(func.count()).select_from(CandleBar)).scalar_one()
            series = session.execute(
                select(CandleBar.provider, CandleBar.symbol, CandleBar.timeframe, func.count(), func.min(CandleBar.time), func.max(CandleBar.time))
                .group_by(CandleBar.provider, CandleBar.symbol, CandleBar.timeframe)
            ).all()
        return {
            "enabled": self.enabled,
            "bars": bars,
            "series": [{"provider": p, "symbol": s, "timeframe": tf, "bars": n, "from": lo, "to": hi} for p, s, tf, n, lo, hi in series],
            "hits": self.hits,
            "misses": self.misses,
            "bars_from_providers": self.provider_bars,
        }

    def clear(self, provider: Optional[str] = None, symbol: Optional[str] = None) -> int:
        with self.db.session() as session:
            q_bars = delete(CandleBar)
            q_cov = delete(CandleCoverage)
            if provider:
                q_bars = q_bars.where(CandleBar.provider.like(f"{provider}%"))
                q_cov = q_cov.where(CandleCoverage.provider.like(f"{provider}%"))
            if symbol:
                q_bars = q_bars.where(CandleBar.symbol == symbol)
                q_cov = q_cov.where(CandleCoverage.symbol == symbol)
            n = session.execute(q_bars).rowcount or 0
            session.execute(q_cov)
        log.info("Candle cache cleared (%s bars)", n)
        return n
