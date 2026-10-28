"""
OANDA v20 market-data provider (REST history + streaming prices).

Used on its own (provider "oanda") or as the automatic fallback behind
"auto" when MetaTrader 5 is offline.

* Auth: a personal API token from a free OANDA practice (demo) account,
  set in OANDA_API_TOKEN. The account id is auto-detected unless OANDA_ACCOUNT_ID
  is set.
* History: GET /v3/instruments/{instrument}/candles (bid prices, max 5000 bars).
* Live: one long-lived HTTP stream GET /v3/accounts/{id}/pricing/stream that
  carries PRICE and HEARTBEAT lines for every subscribed instrument. When the
  subscription set changes, the stream is re-opened with the new instrument list.
* Symbols: OANDA names look like EUR_USD. ``normalize_symbol`` also accepts MT5
  style names (EURUSD, EURUSD.m, EURUSDm, XAUUSD, US30, GER40 ...) so charts keep
  the same symbol when the failover provider switches sources.
* Time shift: ``get_history(..., time_shift_s=N)`` returns candles shifted by N
  seconds and re-bucketed, so OANDA (UTC) bars line up with MT5 broker server time.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import Settings
from app.core.exceptions import (
    InvalidRequestError,
    ProviderConnectionError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from app.models.market import Candle, SymbolInfo, Tick, Timeframe, bucket_start
from app.providers.base import MarketDataProvider

_URLS = {
    "practice": ("https://api-fxpractice.oanda.com", "https://stream-fxpractice.oanda.com"),
    "live": ("https://api-fxtrade.oanda.com", "https://stream-fxtrade.oanda.com"),
}

_GRANULARITY = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.M30: "M30",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
    Timeframe.MN1: "M",
}

# Common MT5 / CFD names -> OANDA candidates (first one that exists on the account wins)
_ALIASES: Dict[str, List[str]] = {
    "US30": ["US30_USD"], "DJ30": ["US30_USD"], "WS30": ["US30_USD"], "DOW": ["US30_USD"],
    "US500": ["SPX500_USD"], "SPX500": ["SPX500_USD"], "SP500": ["SPX500_USD"], "SPX": ["SPX500_USD"],
    "USTEC": ["NAS100_USD"], "NAS100": ["NAS100_USD"], "NDX100": ["NAS100_USD"], "NDX": ["NAS100_USD"],
    "US2000": ["US2000_USD"], "RUSSELL": ["US2000_USD"],
    "GER40": ["DE40_EUR", "DE30_EUR"], "GER30": ["DE30_EUR", "DE40_EUR"], "DE40": ["DE40_EUR", "DE30_EUR"],
    "DE30": ["DE30_EUR", "DE40_EUR"], "DAX": ["DE40_EUR", "DE30_EUR"], "DAX40": ["DE40_EUR", "DE30_EUR"],
    "UK100": ["UK100_GBP"], "FTSE100": ["UK100_GBP"], "FTSE": ["UK100_GBP"],
    "JP225": ["JP225_USD"], "JPN225": ["JP225_USD"], "NIKKEI": ["JP225_USD"],
    "FRA40": ["FR40_EUR"], "FR40": ["FR40_EUR"], "EU50": ["EU50_EUR"], "STOXX50": ["EU50_EUR"],
    "AUS200": ["AU200_AUD"], "HK50": ["HK33_HKD"], "CN50": ["CN50_USD"],
    "XTIUSD": ["WTICO_USD"], "USOIL": ["WTICO_USD"], "WTI": ["WTICO_USD"], "CL": ["WTICO_USD"],
    "XBRUSD": ["BCO_USD"], "UKOIL": ["BCO_USD"], "BRENT": ["BCO_USD"],
    "XNGUSD": ["NATGAS_USD"], "NATGAS": ["NATGAS_USD"], "NGAS": ["NATGAS_USD"],
    "XCUUSD": ["XCU_USD"], "COPPER": ["XCU_USD"],
    "GOLD": ["XAU_USD"], "SILVER": ["XAG_USD"],
}


class _FatalAuthError(Exception):
    """Token rejected - retrying cannot help until the user fixes .env."""


class OandaProvider(MarketDataProvider):
    name = "oanda"
    supports_time_shift = True

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        super().__init__()
        self._settings = settings
        self._transport = transport
        self._rest_url, self._stream_url = _URLS[settings.oanda_environment]
        self._account_id: str = settings.oanda_account_id.strip()
        self._overrides = settings.oanda_symbol_overrides
        self._instruments: Dict[str, Dict[str, Any]] = {}  # "EUR_USD" -> instrument json
        self._by_key: Dict[str, str] = {}  # "EURUSD" -> "EUR_USD"
        self._rest: Optional[httpx.AsyncClient] = None
        self._stream: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._ready = False
        self._subs_changed = asyncio.Event()
        self._retry_delay = 1.0
        self._last_price_at: Optional[float] = None

    # ============================================================ clients
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.oanda_api_token.strip()}",
            "Accept-Datetime-Format": "UNIX",
            "Content-Type": "application/json",
        }

    def _make_clients(self) -> None:
        timeout = self._settings.oanda_request_timeout_s
        if self._rest is None:
            self._rest = httpx.AsyncClient(
                base_url=self._rest_url, headers=self._headers(), timeout=timeout, transport=self._transport
            )
        if self._stream is None:
            # Heartbeats arrive every ~5 s, so 30 s without data means the stream is dead.
            self._stream = httpx.AsyncClient(
                base_url=self._stream_url,
                headers=self._headers(),
                timeout=httpx.Timeout(connect=timeout, read=30.0, write=timeout, pool=timeout),
                transport=self._transport,
            )

    async def _close_clients(self) -> None:
        for client in (self._rest, self._stream):
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    self.log.debug("Error closing OANDA client", exc_info=True)
        self._rest = None
        self._stream = None

    @staticmethod
    def _error_text(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            return str(body.get("errorMessage") or body)
        except Exception:
            return resp.text[:300] or f"HTTP {resp.status_code}"

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._rest is None:
            raise ProviderUnavailableError("OANDA client not started")
        try:
            resp = await self._rest.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"OANDA request failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code in (401, 403):
            raise _FatalAuthError(f"OANDA rejected the API token ({resp.status_code}): {self._error_text(resp)}")
        if resp.status_code == 400 or resp.status_code == 404:
            raise InvalidRequestError(f"OANDA: {self._error_text(resp)}")
        if resp.status_code >= 300:
            raise ProviderUnavailableError(f"OANDA HTTP {resp.status_code}: {self._error_text(resp)}")
        return resp.json()

    # ========================================================== lifecycle
    async def connect(self) -> None:
        if not self._settings.oanda_api_token.strip():
            message = "OANDA_API_TOKEN is empty - add your OANDA practice token to backend/.env"
            await self._set_status(False, message)
            raise ProviderConnectionError(message)
        self._make_clients()
        try:
            await self._setup()
        except _FatalAuthError as exc:
            await self._set_status(False, str(exc))
            raise ProviderConnectionError(str(exc)) from exc
        except (ProviderUnavailableError, InvalidRequestError) as exc:
            await self._set_status(False, f"{exc.message} - will keep retrying in the background")
            raise ProviderConnectionError(exc.message) from exc
        finally:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="oanda-supervisor")
        await self._set_status(True, self._ready_message())

    async def disconnect(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._close_clients()
        self._ready = False
        await self._set_status(False, "Disconnected")

    def _ready_message(self) -> str:
        return f"Connected to OANDA {self._settings.oanda_environment} account {self._account_id}"

    async def _setup(self) -> None:
        """Resolve the account id and load the instrument list."""
        if not self._account_id:
            data = await self._get("/v3/accounts")
            accounts = data.get("accounts") or []
            if not accounts:
                raise ProviderUnavailableError("OANDA token works but has no accounts")
            self._account_id = str(accounts[0]["id"])
            self.log.info("Using OANDA account %s", self._account_id)
        data = await self._get(f"/v3/accounts/{self._account_id}/instruments")
        instruments = data.get("instruments") or []
        if not instruments:
            raise ProviderUnavailableError("OANDA returned no instruments for this account")
        self._instruments = {i["name"]: i for i in instruments}
        self._by_key = {name.replace("_", ""): name for name in self._instruments}
        self._details = {
            "environment": self._settings.oanda_environment,
            "account_id": self._account_id,
            "instruments": len(self._instruments),
        }
        self._ready = True
        self.log.info("OANDA ready: %s instruments", len(self._instruments))

    # ================================================== symbol resolution
    def normalize_symbol(self, symbol: str) -> str:
        raw = symbol.strip()
        if not raw:
            raise SymbolNotFoundError("Empty symbol")
        upper = raw.upper()
        if upper in self._instruments:
            return upper
        if upper in self._overrides:
            return self._overrides[upper]

        joined = re.sub(r"[^A-Z0-9]", "", upper)  # "EUR/USD", "eur-usd" -> "EURUSD"
        if joined in self._by_key:
            return self._by_key[joined]

        cleaned = re.split(r"[.#\-_/ ]", raw)[0]
        cleaned = re.sub(r"(?<=[A-Z0-9])[a-z]+$", "", cleaned)  # broker suffix like "m", "pro"
        key = re.sub(r"[^A-Z0-9]", "", cleaned.upper())
        if key in self._overrides:
            return self._overrides[key]
        if not self._instruments:
            raise ProviderUnavailableError("OANDA instrument list not loaded yet")
        if key in self._by_key:
            return self._by_key[key]
        for candidate in _ALIASES.get(key, []):
            if candidate in self._instruments:
                return candidate
        raise SymbolNotFoundError(
            f"No OANDA instrument matches '{symbol}'. Add a mapping in backend/.env, e.g. "
            f"OANDA_SYMBOL_MAP={key or upper}=EUR_USD (see /api/symbols?provider=oanda for names)."
        )

    # ============================================================ queries
    async def get_symbols(self, query: Optional[str] = None, limit: int = 200) -> List[SymbolInfo]:
        if not self._ready:
            raise ProviderUnavailableError(f"OANDA is not connected: {self._status_message}")
        q = (query or "").strip().upper().replace("_", "")
        out: List[SymbolInfo] = []
        for name, inst in sorted(self._instruments.items()):
            display = inst.get("displayName", name)
            if q and q not in name.replace("_", "") and q not in display.upper().replace("/", ""):
                continue
            base, _, quote = name.partition("_")
            out.append(
                SymbolInfo(
                    provider=self.name,
                    symbol=name,
                    description=display,
                    category=inst.get("type", ""),
                    digits=int(inst.get("displayPrecision", 5)),
                    currency_base=base,
                    currency_profit=quote,
                )
            )
            if len(out) >= limit:
                break
        return out

    def digits_for(self, instrument: str) -> int:
        return int(self._instruments.get(instrument, {}).get("displayPrecision", 5))

    async def get_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        to: Optional[datetime] = None,
        time_shift_s: int = 0,
    ) -> List[Candle]:
        if not self._ready:
            raise ProviderUnavailableError(f"OANDA is not connected: {self._status_message}")
        instrument = self.normalize_symbol(symbol)
        shift_h = int(round(time_shift_s / 3600))
        params: Dict[str, Any] = {
            "price": "B",
            "granularity": _GRANULARITY[timeframe],
            "count": max(1, min(count, 5000)),
            # Align D/W/M (and H4) bars to the target clock's midnight (broker server time).
            "alignmentTimezone": "UTC",
            "dailyAlignment": (-shift_h) % 24,
            "weeklyAlignment": "Saturday" if shift_h > 0 else "Sunday",
        }
        to_shifted: Optional[int] = None
        if to is not None:
            to_shifted = int(to.timestamp())
            params["to"] = str(to_shifted - time_shift_s)
        try:
            data = await self._get(f"/v3/instruments/{instrument}/candles", params=params)
        except _FatalAuthError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

        merged: Dict[int, Candle] = {}
        for raw in data.get("candles") or []:
            bid = raw.get("bid")
            if not bid:
                continue
            t = int(float(raw["time"])) + time_shift_s
            bar_time = bucket_start(t, timeframe)
            o, h, l, c = float(bid["o"]), float(bid["h"]), float(bid["l"]), float(bid["c"])
            vol = float(raw.get("volume", 0))
            existing = merged.get(bar_time)
            if existing is None:
                merged[bar_time] = Candle(time=bar_time, open=o, high=h, low=l, close=c, volume=vol)
            else:  # OANDA bar fell into an already-started bucket (alignment differences)
                existing.high = max(existing.high, h)
                existing.low = min(existing.low, l)
                existing.close = c
                existing.volume += vol
        candles = [merged[k] for k in sorted(merged)]
        if to_shifted is not None:
            candles = [cd for cd in candles if cd.time < bucket_start(to_shifted, timeframe)]
        return candles

    # ====================================================== subscriptions
    async def subscribe(self, symbol: str) -> None:
        if not self._ready:
            raise ProviderUnavailableError(f"OANDA is not connected: {self._status_message}")
        instrument = self.normalize_symbol(symbol)
        if instrument not in self._subscribed:
            self._subscribed.add(instrument)
            self._subs_changed.set()
        self.log.info("Subscribed %s", instrument)

    async def unsubscribe(self, symbol: str) -> None:
        try:
            instrument = self.normalize_symbol(symbol)
        except Exception:
            instrument = symbol.upper()
        if instrument in self._subscribed:
            self._subscribed.discard(instrument)
            self._subs_changed.set()
        self.log.info("Unsubscribed %s", instrument)

    # ========================================================= supervisor
    async def _run(self) -> None:
        max_delay = self._settings.oanda_reconnect_max_delay_s
        self.log.info("OANDA supervisor started")
        try:
            while True:
                if not self._ready:
                    try:
                        self._make_clients()
                        await self._setup()
                        self._retry_delay = 1.0
                        await self._set_status(True, self._ready_message())
                    except _FatalAuthError as exc:
                        await self._set_status(False, f"{exc} - fix OANDA_API_TOKEN and restart")
                        return
                    except Exception as exc:
                        msg = getattr(exc, "message", str(exc))
                        await self._set_status(False, f"{msg} - retrying in {self._retry_delay:.0f}s")
                        await asyncio.sleep(self._retry_delay)
                        self._retry_delay = min(self._retry_delay * 2, max_delay)
                        continue

                if not self._subscribed:
                    self._subs_changed.clear()
                    await self._subs_changed.wait()
                    continue

                instruments = sorted(self._subscribed)
                self._subs_changed.clear()
                consume = asyncio.create_task(self._consume(instruments), name="oanda-stream")
                waiter = asyncio.create_task(self._subs_changed.wait(), name="oanda-subs-wait")
                try:
                    done, _ = await asyncio.wait({consume, waiter}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in (consume, waiter):
                        if not t.done():
                            t.cancel()
                    for t in (consume, waiter):
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                if waiter in done:
                    continue  # instrument list changed -> reopen the stream immediately

                exc = consume.exception() if not consume.cancelled() else None
                if isinstance(exc, _FatalAuthError):
                    await self._set_status(False, f"{exc} - fix OANDA_API_TOKEN and restart")
                    return
                reason = f"{type(exc).__name__}: {exc}" if exc else "stream closed by server"
                await self._set_status(False, f"Price stream dropped ({reason}) - retrying in {self._retry_delay:.0f}s")
                await asyncio.sleep(self._retry_delay)
                self._retry_delay = min(self._retry_delay * 2, max_delay)
        except asyncio.CancelledError:
            self.log.info("OANDA supervisor stopped")
            raise

    async def _consume(self, instruments: List[str]) -> None:
        assert self._stream is not None
        path = f"/v3/accounts/{self._account_id}/pricing/stream"
        async with self._stream.stream("GET", path, params={"instruments": ",".join(instruments)}) as resp:
            if resp.status_code in (401, 403):
                await resp.aread()
                raise _FatalAuthError(f"OANDA rejected the API token ({resp.status_code}): {self._error_text(resp)}")
            if resp.status_code != 200:
                await resp.aread()
                raise ProviderUnavailableError(f"stream HTTP {resp.status_code}: {self._error_text(resp)}")
            self._retry_delay = 1.0
            await self._set_status(True, self._ready_message())
            self.log.info("Price stream open for %s", ", ".join(instruments))
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.log.debug("Skipping non-JSON stream line: %s", line[:120])
                    continue
                if msg.get("type") != "PRICE":
                    continue  # HEARTBEAT
                tick = self._parse_price(msg)
                if tick is not None:
                    self._last_price_at = time.time()
                    await self._emit_tick(tick)

    def _parse_price(self, msg: Dict[str, Any]) -> Optional[Tick]:
        try:
            bids, asks = msg.get("bids") or [], msg.get("asks") or []
            if not bids:
                return None
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"]) if asks else bid
            return Tick(
                provider=self.name,
                symbol=msg["instrument"],
                time_ms=int(float(msg["time"]) * 1000),
                bid=bid,
                ask=ask,
                last=0.0,
                volume=0.0,
                price=bid,
            )
        except (KeyError, ValueError, TypeError) as exc:
            self.log.debug("Bad PRICE message %s: %s", msg, exc)
            return None
