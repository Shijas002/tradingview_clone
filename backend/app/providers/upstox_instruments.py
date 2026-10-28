"""
Upstox instrument master: symbol search and symbol <-> instrument-key mapping.

Upstox identifies instruments by an *instrument key* such as
``NSE_EQ|INE002A01018`` (Reliance) or ``NSE_INDEX|Nifty 50``. The chart uses
readable TradingView-style names instead, ``EXCHANGE:TRADING_SYMBOL``:

    NSE:RELIANCE        BSE:RELIANCE        NSE:NIFTY (index)
    NSE:NIFTY FUT 27 NOV 25                 MCX:CRUDEOIL FUT 19 NOV 25

The lists come from Upstox's public daily files (no login needed):
``https://assets.upstox.com/market-quote/instruments/exchange/<EXCHANGE>.json.gz``.
They are refreshed by Upstox around 06:00 IST, cached in ``backend/data/upstox/``
and re-downloaded once per day (a stale cache is used if the download fails).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from app.core.config import Settings
from app.core.exceptions import ProviderUnavailableError, SymbolNotFoundError

log = logging.getLogger("app.providers.upstox.instruments")

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
IST = timezone(timedelta(hours=5, minutes=30))
REFRESH_AFTER_IST = (6, 30)  # Upstox publishes the day's file around 06:00 IST

# Session opens in seconds after 00:00 UTC: NSE/BSE 09:15 IST, MCX & currency 09:00 IST
ANCHOR_EQUITY = 3 * 3600 + 45 * 60
ANCHOR_COMMODITY = 3 * 3600 + 30 * 60
# Trading minutes per day (used only to estimate how much history to request)
SESSION_MINUTES = {"equity": 375, "commodity": 870, "currency": 480}

_DERIVATIVE_SEGMENTS = {"NSE_FO", "BSE_FO", "MCX_FO", "NCD_FO", "BCD_FO", "NSE_COM"}
_CASH_SEGMENTS = {"NSE_EQ", "BSE_EQ"}
_INDEX_SEGMENTS = {"NSE_INDEX", "BSE_INDEX", "MCX_INDEX"}
_OPTION_TYPES = {"CE", "PE"}
# Lower = shown first in search results, and wins when two instruments share a name
_PRIORITY = {"INDEX": 0, "EQ": 1, "FUT": 2, "OPT": 3}


@dataclass(frozen=True)
class Instrument:
    key: str  # Upstox instrument_key, e.g. "NSE_EQ|INE002A01018"
    symbol: str  # chart symbol, e.g. "NSE:RELIANCE"
    trading_symbol: str
    name: str
    segment: str
    exchange: str
    kind: str  # INDEX | EQ | FUT | OPT
    digits: int
    lot_size: int
    expiry: int  # UNIX ms for derivatives, 0 otherwise

    @property
    def market(self) -> str:
        if self.segment.startswith("MCX") or self.segment == "NSE_COM":
            return "commodity"
        if self.segment in ("NCD_FO", "BCD_FO"):
            return "currency"
        return "equity"

    @property
    def anchor_s(self) -> int:
        return ANCHOR_EQUITY if self.market == "equity" else ANCHOR_COMMODITY

    @property
    def session_minutes(self) -> int:
        return SESSION_MINUTES[self.market]


def _digits(tick_size: Any, kind: str) -> int:
    """Price decimals from Upstox tick_size, which is given in paise (5.0 = Rs 0.05)."""
    if kind == "INDEX":
        return 2
    try:
        tick = float(tick_size) / 100.0
    except (TypeError, ValueError):
        return 2
    if tick <= 0:
        return 2
    text = f"{tick:.6f}".rstrip("0").rstrip(".")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return max(0, min(decimals, 4))


def parse_instruments(rows: Iterable[Dict[str, Any]], include_options: bool) -> List[Instrument]:
    """Turn raw Upstox JSON rows into chartable instruments (filtering what the chart can't use)."""
    out: List[Instrument] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("instrument_key") or "")
        segment = str(row.get("segment") or key.split("|", 1)[0])
        tsym = str(row.get("trading_symbol") or row.get("tradingsymbol") or "").strip()
        if not key or not tsym:
            continue
        itype = str(row.get("instrument_type") or "").upper()
        if segment in _INDEX_SEGMENTS:
            kind = "INDEX"
        elif segment in _CASH_SEGMENTS:
            kind = "EQ"
        elif segment in _DERIVATIVE_SEGMENTS:
            if itype in _OPTION_TYPES:
                if not include_options:
                    continue
                kind = "OPT"
            elif itype.startswith("FUT") or itype == "FUT":
                kind = "FUT"
            else:
                continue
        else:
            continue  # mutual funds, bonds feeds etc. are not chartable here
        exchange = str(row.get("exchange") or segment.split("_", 1)[0]).upper()
        if exchange in ("NCD", "NSE_CD"):
            exchange = "NSE"
        if exchange in ("BCD",):
            exchange = "BSE"
        try:
            lot = int(float(row.get("lot_size") or 1))
        except (TypeError, ValueError):
            lot = 1
        try:
            expiry = int(row.get("expiry") or 0)
        except (TypeError, ValueError):
            expiry = 0
        out.append(
            Instrument(
                key=key,
                symbol=f"{exchange}:{tsym}",
                trading_symbol=tsym,
                name=str(row.get("name") or tsym),
                segment=segment,
                exchange=exchange,
                kind=kind,
                digits=_digits(row.get("tick_size"), kind),
                lot_size=lot,
                expiry=expiry,
            )
        )
    return out


class InstrumentStore:
    """In-memory index of the Upstox instrument files for the configured exchanges."""

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._settings = settings
        self._transport = transport
        self._dir: Path = settings.data_path / "upstox"
        self._by_symbol: Dict[str, Instrument] = {}  # "NSE:RELIANCE" (upper) -> Instrument
        self._by_key: Dict[str, Instrument] = {}
        self._by_tsym: Dict[str, List[Instrument]] = {}  # "RELIANCE" -> [NSE, BSE ...] by priority
        self._sorted: List[Instrument] = []
        self._loaded_at: Optional[float] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ status
    @property
    def loaded(self) -> bool:
        return self._loaded_at is not None

    def __len__(self) -> int:
        return len(self._by_key)

    def stale(self) -> bool:
        """True once a newer daily file should be available from Upstox."""
        return self._loaded_at is not None and self._loaded_at < _last_publish_time()

    # ------------------------------------------------------------- load
    async def load(self) -> None:
        async with self._lock:
            instruments: List[Instrument] = []
            errors: List[str] = []
            for exchange in self._settings.upstox_exchange_list:
                try:
                    rows = await self._rows(exchange)
                except Exception as exc:  # one exchange failing must not hide the others
                    errors.append(f"{exchange}: {exc}")
                    log.warning("Could not load Upstox %s instruments: %s", exchange, exc)
                    continue
                parsed = await asyncio.to_thread(parse_instruments, rows, self._settings.upstox_include_options)
                log.info("Upstox %s: %s chartable instruments", exchange, len(parsed))
                instruments.extend(parsed)
            if not instruments:
                raise ProviderUnavailableError("Upstox instrument list unavailable - " + "; ".join(errors or ["empty file"]))
            self._index(instruments)
            self._loaded_at = time.time()

    async def _rows(self, exchange: str) -> List[Dict[str, Any]]:
        cache = self._dir / f"{exchange}.json.gz"
        fresh = cache.exists() and cache.stat().st_mtime >= _last_publish_time()
        if not fresh:
            try:
                await self._download(exchange, cache)
            except Exception as exc:
                if not cache.exists():
                    raise ProviderUnavailableError(f"download failed ({exc}) and no cached copy") from exc
                log.warning("Upstox %s instrument download failed (%s) - using cached copy", exchange, exc)
        return await asyncio.to_thread(_read_gz_json, cache)

    async def _download(self, exchange: str, target: Path) -> None:
        url = INSTRUMENTS_URL.format(exchange=exchange)
        log.info("Downloading Upstox instrument list %s", url)
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            raise ProviderUnavailableError(f"HTTP {resp.status_code} for {url}")
        content = resp.content
        if content[:2] != b"\x1f\x8b":  # validate before replacing the cache
            raise ProviderUnavailableError(f"{url} did not return a gzip file")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(content)
        tmp.replace(target)

    def _index(self, instruments: List[Instrument]) -> None:
        instruments.sort(key=lambda i: (_PRIORITY[i.kind], i.expiry, i.symbol))
        by_symbol: Dict[str, Instrument] = {}
        by_key: Dict[str, Instrument] = {}
        by_tsym: Dict[str, List[Instrument]] = {}
        final: List[Instrument] = []
        for inst in instruments:
            if inst.key in by_key:
                continue
            upper = inst.symbol.upper()
            if upper in by_symbol:  # rare name clash (e.g. an index and a stock) -> suffix the later one
                inst = Instrument(**{**inst.__dict__, "symbol": f"{inst.symbol}-{inst.kind}"})
                upper = inst.symbol.upper()
                if upper in by_symbol:
                    continue
            by_symbol[upper] = inst
            by_key[inst.key] = inst
            by_tsym.setdefault(inst.trading_symbol.upper(), []).append(inst)
            final.append(inst)
        self._by_symbol, self._by_key, self._by_tsym, self._sorted = by_symbol, by_key, by_tsym, final

    # ----------------------------------------------------------- lookup
    def by_key(self, key: str) -> Optional[Instrument]:
        return self._by_key.get(key)

    def resolve(self, symbol: str) -> Instrument:
        raw = symbol.strip()
        if not raw:
            raise SymbolNotFoundError("Empty symbol")
        if not self.loaded:
            raise ProviderUnavailableError("Upstox instrument list is still loading - try again in a few seconds")
        if "|" in raw:  # raw instrument key
            inst = self._by_key.get(raw) or self._by_key.get(raw.upper())
            if inst is not None:
                return inst
        upper = raw.upper()
        inst = self._by_symbol.get(upper)
        if inst is not None:
            return inst
        if ":" in upper:
            prefix, _, rest = upper.partition(":")
            inst = self._by_symbol.get(f"{prefix.split('_')[0]}:{rest}")  # "NSE_EQ:RELIANCE"
            if inst is not None:
                return inst
            upper = rest
        matches = self._by_tsym.get(upper)
        if matches:
            order = {ex: n for n, ex in enumerate(self._settings.upstox_exchange_list)}
            return sorted(matches, key=lambda i: (_PRIORITY[i.kind], order.get(i.exchange, 9)))[0]
        raise SymbolNotFoundError(
            f"No Upstox instrument named '{symbol}'. Use the symbol search (e.g. NSE:RELIANCE, NSE:NIFTY, "
            f"BSE:SENSEX) or an instrument key like NSE_EQ|INE002A01018."
        )

    def search(self, query: Optional[str], limit: int) -> List[Instrument]:
        q = (query or "").strip().upper()
        if not q:
            return self._sorted[:limit]
        if ":" in q:
            prefix, _, rest = q.partition(":")
            exchange_filter: Optional[str] = prefix.split("_")[0]
            q = rest.strip()
        else:
            exchange_filter = None
        order = {ex: n for n, ex in enumerate(self._settings.upstox_exchange_list)}
        ranked: List[Tuple[int, int, int, int, str, Instrument]] = []
        for inst in self._sorted:
            if exchange_filter and inst.exchange != exchange_filter:
                continue
            tsym = inst.trading_symbol.upper()
            if not q:
                score = 3
            elif tsym == q:
                score = 0
            elif tsym.startswith(q):
                score = 1
            elif q in tsym:
                score = 2
            elif q in inst.name.upper():
                score = 3
            else:
                continue
            ranked.append((score, _PRIORITY[inst.kind], order.get(inst.exchange, 9), inst.expiry, inst.symbol, inst))
        ranked.sort(key=lambda r: r[:5])
        return [r[5] for r in ranked[:limit]]


def _read_gz_json(path: Path) -> List[Dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ProviderUnavailableError(f"{path.name}: expected a JSON list")
    return data


def _last_publish_time() -> float:
    """UNIX time of the most recent 06:30 IST (newer files may exist after this)."""
    now = datetime.now(IST)
    mark = now.replace(hour=REFRESH_AFTER_IST[0], minute=REFRESH_AFTER_IST[1], second=0, microsecond=0)
    if now < mark:
        mark -= timedelta(days=1)
    return mark.timestamp()
