"""
Upstox market-data provider (Indian markets: NSE, BSE, MCX).

* Auth:     ``UpstoxAuth`` - Analytics Token from .env, or the daily OAuth login
            started from the app (``/api/broker/upstox/login``).
* Symbols:  ``InstrumentStore`` - TradingView-style names like ``NSE:RELIANCE``
            mapped to Upstox instrument keys (``NSE_EQ|INE002A01018``).
* History:  REST ``GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}``
            plus ``/v3/historical-candle/intraday/...`` for today's bars. Upstox
            limits the date range per request (1 month for 1-15 minute bars,
            1 quarter for 30m/1h, 1 decade for daily), so long requests are split
            into several calls and stitched together.
* Live:     Market Data Feed V3 - one WebSocket (``GET /v3/feed/market-data-feed/authorize``
            returns the URL), binary JSON subscribe requests, protobuf responses
            decoded by ``upstox_feed``.

Time handling: Upstox timestamps are IST (+05:30); candles are stored as real UTC
like every other provider. Intraday bars are aligned to the session open
(09:15 IST for NSE/BSE, 09:00 for MCX) so 1h/4h bars match TradingView, and
daily/weekly/monthly bars are stamped 00:00 UTC of their Indian trading date.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, ProviderUnavailableError
from app.models.market import Candle, SymbolInfo, Tick, Timeframe, bucket_start
from app.providers.base import MarketDataProvider
from app.providers.upstox_auth import API_BASE, UpstoxAuth, error_text
from app.providers.upstox_feed import FeedDecodeError, FeedMessage, decode_feed_response
from app.providers.upstox_instruments import Instrument, InstrumentStore

IST = timezone(timedelta(hours=5, minutes=30))
AUTHORIZE_PATH = "/v3/feed/market-data-feed/authorize"

# timeframe -> (unit, interval, max calendar days per request, minutes per bar)
_FETCH: Dict[Timeframe, Tuple[str, int, int, float]] = {
    Timeframe.M1: ("minutes", 1, 28, 1),
    Timeframe.M5: ("minutes", 5, 28, 5),
    Timeframe.M15: ("minutes", 15, 28, 15),
    Timeframe.M30: ("minutes", 30, 88, 30),
    Timeframe.H1: ("minutes", 60, 88, 60),
    Timeframe.H4: ("minutes", 60, 88, 240),  # built from 1h bars so 4h bars start at the session open
    Timeframe.D1: ("days", 1, 3600, 0),
    Timeframe.W1: ("weeks", 1, 3600 * 3, 0),
    Timeframe.MN1: ("months", 1, 3600 * 3, 0),
}
_DAILY_PLUS = (Timeframe.D1, Timeframe.W1, Timeframe.MN1)
_INTRADAY_SINCE = date(2022, 1, 1)  # Upstox minute/hour data starts January 2022
_DAILY_SINCE = date(2000, 1, 1)
_MAX_REQUESTS = 12
_STATUS_TEXT = {
    "NORMAL_OPEN": "open",
    "NORMAL_CLOSE": "closed",
    "PRE_OPEN_START": "pre-open",
    "PRE_OPEN_END": "pre-open ended",
    "CLOSING_START": "closing session",
    "CLOSING_END": "closed",
}

WsConnect = Callable[[str], Any]


class _AuthRejected(Exception):
    """Upstox answered 401 - the token is expired or revoked."""


def _default_ws_connect(uri: str) -> Any:
    try:
        from websockets.asyncio.client import connect  # websockets >= 13
    except ImportError:  # pragma: no cover - older websockets
        from websockets import connect  # type: ignore[no-redef]
    return connect(uri, max_size=8 * 1024 * 1024, open_timeout=20, ping_interval=20, ping_timeout=20)


def rebucket(rows: List[List[Any]], timeframe: Timeframe, anchor_s: int) -> List[Candle]:
    """
    Convert Upstox candle rows ``[iso_time, o, h, l, c, volume, oi]`` into Candles
    on this app's bar grid (merging rows that fall into the same bar).
    """
    merged: Dict[int, Candle] = {}
    parsed: List[Tuple[int, List[Any]]] = []
    for row in rows:
        try:
            dt = datetime.fromisoformat(str(row[0]))
        except (ValueError, IndexError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        if timeframe in _DAILY_PLUS:
            d = dt.astimezone(IST).date()
            t = calendar.timegm(d.timetuple())
        else:
            t = int(dt.timestamp())
        parsed.append((t, row))
    parsed.sort(key=lambda p: p[0])
    for t, row in parsed:
        try:
            o, h, l, c = (float(row[i]) for i in range(1, 5))
            v = float(row[5]) if len(row) > 5 and row[5] is not None else 0.0
        except (ValueError, TypeError, IndexError):
            continue
        bt = bucket_start(t, timeframe, anchor_s)
        cur = merged.get(bt)
        if cur is None:
            merged[bt] = Candle(time=bt, open=o, high=h, low=l, close=c, volume=v)
        else:
            cur.high = max(cur.high, h)
            cur.low = min(cur.low, l)
            cur.close = c
            cur.volume += v
    return [merged[k] for k in sorted(merged)]


def calendar_days_for(count: int, timeframe: Timeframe, session_minutes: int) -> int:
    """Rough number of calendar days that contain ``count`` bars (weekends + holidays included)."""
    _, _, _, bar_minutes = _FETCH[timeframe]
    if timeframe is Timeframe.D1:
        trading_days = count
    elif timeframe is Timeframe.W1:
        trading_days = count * 5
    elif timeframe is Timeframe.MN1:
        trading_days = count * 21
    else:
        trading_days = count / max(1.0, session_minutes / bar_minutes)
    return int(math.ceil(trading_days * 7 / 5 * 1.1)) + 4


class UpstoxProvider(MarketDataProvider):
    name = "upstox"
    real_volume = True

    def __init__(
        self,
        settings: Settings,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        ws_connect: Optional[WsConnect] = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._transport = transport
        self._ws_connect: WsConnect = ws_connect or _default_ws_connect
        self.auth = UpstoxAuth(settings, transport)
        self.instruments = InstrumentStore(settings, transport)
        self._rest: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._ws: Any = None
        self._wake = asyncio.Event()
        self._retry_delay = 1.0
        self._mode = "full" if settings.upstox_feed_mode == "full" else "ltpc"
        self._last_vtt: Dict[str, int] = {}
        self._last_ltt: Dict[str, int] = {}
        self._segments: Dict[str, str] = {}
        self._stream_state = "idle"
        self._stream_error: Optional[str] = None
        self._history_sem = asyncio.Semaphore(3)

    # ============================================================ helpers
    def _client(self) -> httpx.AsyncClient:
        if self._rest is None:
            self._rest = httpx.AsyncClient(
                base_url=API_BASE, timeout=self._settings.upstox_request_timeout_s, transport=self._transport
            )
        return self._rest

    def _require_token(self) -> str:
        token = self.auth.active_token()
        if token is None:
            raise ProviderUnavailableError(f"Upstox: {self.auth.describe()['message']}")
        return token

    async def _api_get(self, path: str) -> Dict[str, Any]:
        token = self._require_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        for attempt in range(3):
            try:
                resp = await self._client().get(path, headers=headers)
            except httpx.HTTPError as exc:
                raise ProviderUnavailableError(f"Upstox request failed: {type(exc).__name__}: {exc}") from exc
            if resp.status_code == 429 and attempt < 2:
                await asyncio.sleep(1.0 + attempt)
                continue
            break
        if resp.status_code == 401:
            self._token_rejected(token)
            raise ProviderUnavailableError("Upstox rejected the access token (expired or revoked) - log in again")
        if resp.status_code in (400, 404, 422):
            raise InvalidRequestError(f"Upstox: {error_text(resp)}")
        if resp.status_code == 403:
            raise ProviderUnavailableError(f"Upstox refused access (403): {error_text(resp)}")
        if resp.status_code >= 300:
            raise ProviderUnavailableError(f"Upstox HTTP {resp.status_code}: {error_text(resp)}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderUnavailableError("Upstox returned invalid JSON") from exc

    def _token_rejected(self, token: str) -> None:
        self.log.warning("Upstox rejected the access token (HTTP 401)")
        self.auth.mark_rejected(token)
        self.on_auth_changed()

    def _refresh_details(self) -> None:
        details: Dict[str, Any] = {
            "auth_mode": self.auth.mode,
            "authenticated": self.auth.active_token() is not None,
            "instruments": len(self.instruments),
            "stream": self._stream_state,
            "feed_mode": self._mode,
        }
        expires = self.auth.expires_at()
        if expires:
            details["token_expires"] = datetime.fromtimestamp(expires, tz=IST).strftime("%Y-%m-%d %H:%M IST")
        for seg in ("NSE_EQ", "BSE_EQ", "NSE_FO", "MCX_FO"):
            if seg in self._segments:
                details[f"market_{seg}"] = self._segments[seg]
        self._details = details

    def _market_summary(self) -> str:
        parts = []
        for seg, label in (("NSE_EQ", "NSE"), ("BSE_EQ", "BSE"), ("MCX_FO", "MCX")):
            status = self._segments.get(seg)
            if status:
                parts.append(f"{label} {_STATUS_TEXT.get(status, status.lower().replace('_', ' '))}")
        return ", ".join(parts)

    async def _status(self, connected: bool, message: str) -> None:
        self._refresh_details()
        await self._set_status(connected, message, force=True)

    def session_anchor_s(self, symbol: str) -> int:
        try:
            return self.instruments.resolve(symbol).anchor_s
        except Exception:
            return 0

    # ========================================================== lifecycle
    async def connect(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="upstox-supervisor")
        if self.auth.mode == "none":
            await self._status(False, self.auth.describe()["message"])
        else:
            await self._status(False, "Starting - loading the instrument list…")

    async def disconnect(self) -> None:
        for task in (self._task, self._refresh_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = None
        self._refresh_task = None
        if self._rest is not None:
            try:
                await self._rest.aclose()
            except Exception:
                self.log.debug("Error closing Upstox client", exc_info=True)
            self._rest = None
        self._stream_state = "idle"
        await self._status(False, "Disconnected")

    def on_auth_changed(self) -> None:
        """Called after a login/logout: drop the current stream and restart with the new token."""
        self._wake.set()
        ws = self._ws
        if ws is not None:
            asyncio.create_task(self._close_ws(ws))

    @staticmethod
    async def _close_ws(ws: Any) -> None:
        try:
            await ws.close()
        except Exception:
            pass

    # ========================================================= supervisor
    async def _run(self) -> None:
        max_delay = self._settings.upstox_reconnect_max_delay_s
        try:
            if self.auth.mode == "none":
                self.log.info("Upstox not configured - provider idle")
                return
            while True:
                if not self.instruments.loaded:
                    try:
                        await self.instruments.load()
                        self._retry_delay = 1.0
                    except Exception as exc:
                        msg = getattr(exc, "message", str(exc))
                        await self._status(False, f"{msg} - retrying in {self._retry_delay:.0f}s")
                        await asyncio.sleep(self._retry_delay)
                        self._retry_delay = min(self._retry_delay * 2, max_delay)
                        continue
                self._maybe_refresh_instruments()

                token = self.auth.active_token()
                if token is None:
                    self._stream_state = "logged_out"
                    await self._status(False, self.auth.describe()["message"])
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=60)
                    except asyncio.TimeoutError:
                        pass
                    continue

                self._wake.clear()
                try:
                    await self._stream_session(token)
                    self._retry_delay = 1.0
                    continue  # token changed or server closed cleanly -> reconnect at once
                except _AuthRejected:
                    self._token_rejected(token)
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    reason = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
                    self._stream_error = reason
                    self._stream_state = "reconnecting"
                    still_valid = self.auth.active_token() is not None
                    await self._status(
                        still_valid,
                        f"Logged in - live stream down ({reason}); retrying in {self._retry_delay:.0f}s",
                    )
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=self._retry_delay)
                    except asyncio.TimeoutError:
                        pass
                    self._retry_delay = min(self._retry_delay * 2, max_delay)
        except asyncio.CancelledError:
            self.log.info("Upstox supervisor stopped")
            raise

    def _maybe_refresh_instruments(self) -> None:
        if self.instruments.stale() and (self._refresh_task is None or self._refresh_task.done()):
            self._refresh_task = asyncio.create_task(self._refresh_instruments(), name="upstox-instruments")

    async def _refresh_instruments(self) -> None:
        try:
            await self.instruments.load()
            self._refresh_details()
        except Exception as exc:
            self.log.warning("Daily Upstox instrument refresh failed: %s", getattr(exc, "message", exc))

    async def _authorize(self, token: str) -> str:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            resp = await self._client().get(AUTHORIZE_PATH, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"could not reach Upstox: {type(exc).__name__}") from exc
        if resp.status_code == 401:
            raise _AuthRejected()
        if resp.status_code == 403:
            raise ProviderUnavailableError(
                f"WebSocket access refused (403: {error_text(resp)}). Make sure your Upstox app has market data access"
            )
        if resp.status_code != 200:
            raise ProviderUnavailableError(f"authorize HTTP {resp.status_code}: {error_text(resp)}")
        try:
            data = resp.json().get("data") or {}
        except ValueError as exc:
            raise ProviderUnavailableError("authorize returned invalid JSON") from exc
        uri = data.get("authorizedRedirectUri") or data.get("authorized_redirect_uri")
        if not uri:
            raise ProviderUnavailableError("authorize response had no WebSocket URL")
        return str(uri)

    async def _stream_session(self, token: str) -> None:
        uri = await self._authorize(token)
        self._stream_state = "connecting"
        async with self._ws_connect(uri) as ws:
            self._ws = ws
            try:
                self._retry_delay = 1.0
                self._stream_state = "live"
                self._stream_error = None
                self._last_vtt.clear()
                self._last_ltt.clear()
                await self._status(True, "Live")
                keys = self._subscribed_keys()
                if keys:
                    await self._send(ws, "sub", keys)
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        raw = None
                    if self.auth.active_token() != token or self._wake.is_set():
                        self.log.info("Upstox login changed - restarting the live stream")
                        return
                    if raw is None:
                        self._maybe_refresh_instruments()
                        continue
                    await self._handle_raw(raw)
            finally:
                self._ws = None
                if self._stream_state == "live":
                    self._stream_state = "reconnecting"

    def _subscribed_keys(self) -> List[str]:
        keys = []
        for symbol in sorted(self._subscribed):
            try:
                keys.append(self.instruments.resolve(symbol).key)
            except Exception:
                self.log.warning("Dropping unknown Upstox subscription %s", symbol)
        return keys

    async def _send(self, ws: Any, method: str, keys: List[str]) -> None:
        payload: Dict[str, Any] = {"guid": uuid.uuid4().hex, "method": method, "data": {"instrumentKeys": keys}}
        if method == "sub":
            payload["data"]["mode"] = self._mode
        await ws.send(json.dumps(payload).encode("utf-8"))  # Upstox requires a *binary* frame
        self.log.info("Upstox %s %s", method, ", ".join(keys))

    # ============================================================ feed
    async def _handle_raw(self, raw: Any) -> None:
        if isinstance(raw, str):
            self.log.warning("Upstox sent a text frame: %s", raw[:200])
            return
        try:
            msg = decode_feed_response(raw)
        except FeedDecodeError as exc:
            self.log.warning("Could not decode Upstox feed message (%s bytes): %s", len(raw), exc)
            return
        await self.handle_feed(msg)

    async def handle_feed(self, msg: FeedMessage) -> None:
        if msg.segment_status:
            self._segments.update(msg.segment_status)
            summary = self._market_summary()
            await self._status(True, f"Live - {summary}" if summary else "Live")
        for key, q in msg.feeds.items():
            inst = self.instruments.by_key(key)
            if inst is None or q.ltp <= 0:
                continue
            volume = 0.0
            if q.vtt is not None:
                prev = self._last_vtt.get(key)
                self._last_vtt[key] = q.vtt
                if prev is not None and q.vtt > prev:
                    volume = float(q.vtt - prev)
            else:
                prev_ltt = self._last_ltt.get(key)
                self._last_ltt[key] = q.ltt
                if prev_ltt is not None and q.ltt and q.ltt != prev_ltt:
                    volume = float(q.ltq)
            time_ms = q.ltt or msg.current_ts or int(time.time() * 1000)
            tick = Tick(
                provider=self.name,
                symbol=inst.symbol,
                time_ms=int(time_ms),
                bid=q.bid or q.ltp,
                ask=q.ask or q.ltp,
                last=q.ltp,
                volume=volume,
                price=q.ltp,
            )
            await self._emit_tick(tick)

    # ====================================================== subscriptions
    def normalize_symbol(self, symbol: str) -> str:
        return self.instruments.resolve(symbol).symbol

    async def subscribe(self, symbol: str) -> None:
        inst = self.instruments.resolve(symbol)
        if inst.symbol in self._subscribed:
            return
        self._subscribed.add(inst.symbol)
        ws = self._ws
        if ws is not None:
            try:
                await self._send(ws, "sub", [inst.key])
            except Exception as exc:  # the reconnect will subscribe everything again
                self.log.warning("Upstox subscribe for %s failed: %s", inst.symbol, exc)

    async def unsubscribe(self, symbol: str) -> None:
        try:
            inst = self.instruments.resolve(symbol)
        except Exception:
            self._subscribed.discard(symbol)
            return
        self._subscribed.discard(inst.symbol)
        self._last_vtt.pop(inst.key, None)
        self._last_ltt.pop(inst.key, None)
        ws = self._ws
        if ws is not None:
            try:
                await self._send(ws, "unsub", [inst.key])
            except Exception as exc:
                self.log.debug("Upstox unsubscribe for %s failed: %s", inst.symbol, exc)

    # ============================================================ queries
    async def get_symbols(self, query: Optional[str] = None, limit: int = 200) -> List[SymbolInfo]:
        if not self.instruments.loaded:
            raise ProviderUnavailableError(f"Upstox: {self._status_message}")
        return [
            SymbolInfo(
                provider=self.name,
                symbol=inst.symbol,
                description=inst.name if inst.kind != "FUT" else f"{inst.name} futures (lot {inst.lot_size})",
                category=f"{inst.exchange} {inst.kind}",
                digits=inst.digits,
                currency_base=inst.trading_symbol,
                currency_profit="INR",
            )
            for inst in self.instruments.search(query, limit)
        ]

    async def get_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        to: Optional[datetime] = None,
    ) -> List[Candle]:
        inst = self.instruments.resolve(symbol)
        self._require_token()
        unit, interval, chunk_days, _ = _FETCH[timeframe]
        key = quote(inst.key, safe="")
        to_ts: Optional[int] = int(to.timestamp()) if to is not None else None
        rows: Dict[str, List[Any]] = {}

        async with self._history_sem:
            if to_ts is None:
                intraday_unit, intraday_interval = ("days", 1) if timeframe in _DAILY_PLUS else ("minutes", interval)
                data = await self._api_get(f"/v3/historical-candle/intraday/{key}/{intraday_unit}/{intraday_interval}")
                for row in (data.get("data") or {}).get("candles") or []:
                    if row:
                        rows[f"i{row[0]}"] = row
                cursor = datetime.now(IST).date()
            else:
                cursor = datetime.fromtimestamp(to_ts - 1, tz=IST).date()

            earliest = _DAILY_SINCE if timeframe in _DAILY_PLUS else _INTRADAY_SINCE
            remaining_days = calendar_days_for(count, timeframe, inst.session_minutes)
            empty_streak = 0
            for _ in range(_MAX_REQUESTS):
                if cursor < earliest:
                    break
                span = max(7, min(chunk_days, remaining_days))
                start = max(cursor - timedelta(days=span - 1), earliest)
                data = await self._api_get(
                    f"/v3/historical-candle/{key}/{unit}/{interval}/{cursor.isoformat()}/{start.isoformat()}"
                )
                chunk = (data.get("data") or {}).get("candles") or []
                for row in chunk:
                    if row:
                        rows[str(row[0])] = row
                candles = self._finish(rows, timeframe, inst, to_ts)
                if len(candles) >= count:
                    break
                empty_streak = 0 if chunk else empty_streak + 1
                if empty_streak >= 3:
                    break  # nothing older (newly listed symbol, or data start reached)
                remaining_days = calendar_days_for(count - len(candles), timeframe, inst.session_minutes)
                cursor = start - timedelta(days=1)
            candles = self._finish(rows, timeframe, inst, to_ts)
        return candles[-count:]

    @staticmethod
    def _finish(rows: Dict[str, List[Any]], timeframe: Timeframe, inst: Instrument, to_ts: Optional[int]) -> List[Candle]:
        # Intraday rows ("i" prefix) win over historical rows with the same timestamp.
        by_time: Dict[str, List[Any]] = {}
        for k, row in rows.items():
            if k.startswith("i"):
                continue
            by_time[str(row[0])] = row
        for k, row in rows.items():
            if k.startswith("i"):
                by_time[str(row[0])] = row
        candles = rebucket(list(by_time.values()), timeframe, inst.anchor_s)
        if to_ts is not None:
            candles = [c for c in candles if c.time < to_ts]
        return candles
