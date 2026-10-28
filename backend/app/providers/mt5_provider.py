"""
MetaTrader 5 market-data provider (Windows only).

How it works
------------
* The official ``MetaTrader5`` Python package talks to a running MT5 terminal
  over local IPC. It is synchronous and keeps global state, so **every** MT5 call
  is executed on one dedicated worker thread (``ThreadPoolExecutor(max_workers=1)``).
  That keeps calls serialised and the asyncio event loop responsive.
* MT5 has no push API for Python, so a background *supervisor* task polls
  ``symbol_info_tick`` for every subscribed symbol every MT5_POLL_INTERVAL_MS,
  de-duplicates unchanged ticks and pushes new ones to the StreamManager.
* The supervisor also owns reconnection: if the terminal is closed or the IPC
  link breaks, it shuts the session down and re-initialises with exponential
  back-off (capped by MT5_RECONNECT_MAX_DELAY_S), re-selecting all subscribed
  symbols when it comes back.
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from app.core.config import Settings
from app.core.exceptions import (
    InvalidRequestError,
    ProviderConnectionError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from app.models.market import Candle, SymbolInfo, Tick, Timeframe
from app.providers.base import MarketDataProvider

try:  # The package only exists on Windows.
    import MetaTrader5 as mt5  # type: ignore[import-not-found]

    MT5_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # ImportError, or DLL load errors on broken installs
    mt5 = None  # type: ignore[assignment]
    MT5_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

T = TypeVar("T")

# health states returned by the blocking poll
_HEALTH_OK = "ok"
_HEALTH_OFFLINE = "offline"  # terminal alive but not connected to the broker server
_HEALTH_LOST = "lost"  # IPC link to the terminal is gone


class _ConnectionLost(Exception):
    """Internal signal: the terminal disappeared, a full re-initialise is needed."""


def estimate_server_offset(tick_epoch_s: float, now_utc_s: float, tolerance_s: float = 90.0) -> Optional[int]:
    """
    Estimate the broker's server-time offset from a *fresh* tick.

    MT5 tick timestamps are server wall-clock time encoded as epoch seconds, so
    ``tick - now`` is the offset plus a little latency. Offsets are rounded to the
    nearest 30 minutes; returns None when the difference is not close to such a
    multiple (i.e. the tick was not fresh).
    """
    diff = tick_epoch_s - now_utc_s
    rounded = round(diff / 1800.0) * 1800
    if abs(diff - rounded) > tolerance_s or abs(rounded) > 14 * 3600:
        return None
    return int(rounded)


class MT5Provider(MarketDataProvider):
    name = "mt5"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
        self._poll_interval_s = settings.mt5_poll_interval_ms / 1000.0
        self._health_every = max(1, int(2.0 / self._poll_interval_s))  # ~every 2 s
        self._supervisor: Optional[asyncio.Task] = None
        self._initialised = False  # mt5.initialize() succeeded and not shut down
        self._last_seen: Dict[str, Tuple[int, float, float, float, float]] = {}
        self._poll_counter = 0
        self._offset_file = settings.data_path / "mt5_server_offset.json"
        self._offset_configured = settings.mt5_server_offset_seconds is not None
        self._offset_s: Optional[int] = (
            settings.mt5_server_offset_seconds if self._offset_configured else self._load_offset()
        )
        self._offset_votes: List[int] = []
        self._details["server_utc_offset_hours"] = self._offset_hours()
        self._timeframe_map: Dict[Timeframe, int] = {}
        if mt5 is not None:
            self._timeframe_map = {
                Timeframe.M1: mt5.TIMEFRAME_M1,
                Timeframe.M5: mt5.TIMEFRAME_M5,
                Timeframe.M15: mt5.TIMEFRAME_M15,
                Timeframe.M30: mt5.TIMEFRAME_M30,
                Timeframe.H1: mt5.TIMEFRAME_H1,
                Timeframe.H4: mt5.TIMEFRAME_H4,
                Timeframe.D1: mt5.TIMEFRAME_D1,
                Timeframe.W1: mt5.TIMEFRAME_W1,
                Timeframe.MN1: mt5.TIMEFRAME_MN1,
            }

    # ===================================================== server offset
    @property
    def time_offset_s(self) -> Optional[int]:
        """Seconds to add to UTC to get this broker's server time (None = unknown yet)."""
        return self._offset_s

    def _offset_hours(self) -> Optional[float]:
        return None if self._offset_s is None else self._offset_s / 3600.0

    def _load_offset(self) -> Optional[int]:
        try:
            data = json.loads(self._offset_file.read_text(encoding="utf-8"))
            value = int(data["offset_seconds"])
            self.log.info("Loaded last known MT5 server offset: %+.1f h", value / 3600)
            return value
        except FileNotFoundError:
            return None
        except Exception as exc:
            self.log.warning("Ignoring unreadable %s: %s", self._offset_file, exc)
            return None

    def _save_offset(self, value: int) -> None:
        try:
            self._offset_file.parent.mkdir(parents=True, exist_ok=True)
            self._offset_file.write_text(
                json.dumps({"offset_seconds": value, "detected_at": int(time.time())}), encoding="utf-8"
            )
        except Exception as exc:
            self.log.warning("Could not save MT5 server offset to %s: %s", self._offset_file, exc)

    def _vote_offset(self, tick_epoch_s: float) -> bool:
        """Feed one fresh tick into offset detection. Returns True when the offset changed."""
        if self._offset_configured:
            return False
        estimate = estimate_server_offset(tick_epoch_s, time.time())
        if estimate is None:
            return False
        self._offset_votes.append(estimate)
        self._offset_votes = self._offset_votes[-5:]
        # require 3 agreeing votes before trusting / changing the value
        if len(self._offset_votes) >= 3 and len(set(self._offset_votes[-3:])) == 1:
            agreed = self._offset_votes[-1]
            if agreed != self._offset_s:
                self.log.info("Detected MT5 server time offset: UTC%+.1f h", agreed / 3600)
                self._offset_s = agreed
                self._details["server_utc_offset_hours"] = self._offset_hours()
                self._save_offset(agreed)
                return True
        return False

    # ============================================================ helpers
    async def _call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a blocking MT5 function on the dedicated MT5 thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(fn, *args, **kwargs))

    @staticmethod
    def _last_error() -> str:
        if mt5 is None:
            return "MetaTrader5 package unavailable"
        try:
            code, description = mt5.last_error()
            return f"[{code}] {description}"
        except Exception:
            return "unknown MT5 error"

    def _require_ready(self) -> None:
        if mt5 is None:
            raise ProviderUnavailableError(
                "MetaTrader5 Python package is not installed (it only works on Windows). "
                f"Import error: {MT5_IMPORT_ERROR}"
            )
        if not self._initialised:
            raise ProviderUnavailableError(f"MT5 is not connected: {self._status_message}")

    # ========================================================== lifecycle
    async def connect(self) -> None:
        if mt5 is None:
            message = (
                "MetaTrader5 package not available - install it on Windows with "
                f"'pip install MetaTrader5' ({MT5_IMPORT_ERROR})"
            )
            await self._set_status(False, message)
            raise ProviderConnectionError(message)

        try:
            await self._call(self._initialise_blocking)
        except ProviderConnectionError as exc:
            await self._set_status(False, f"{exc.message} - will keep retrying in the background")
            raise
        finally:
            # The supervisor polls ticks and keeps retrying if this first attempt failed.
            if self._supervisor is None or self._supervisor.done():
                self._supervisor = asyncio.create_task(self._supervise(), name="mt5-supervisor")
        await self._set_status(True, self._connected_message())

    async def disconnect(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except asyncio.CancelledError:
                pass
            self._supervisor = None
        if mt5 is not None and self._initialised:
            try:
                await self._call(self._shutdown_blocking)
            except Exception:
                self.log.exception("Error during MT5 shutdown")
        self._executor.shutdown(wait=False, cancel_futures=True)
        await self._set_status(False, "Disconnected")

    async def reconnect(self) -> None:
        """Force a fresh initialise (exposed through the REST API)."""
        if mt5 is None:
            raise ProviderConnectionError(f"MetaTrader5 package not available ({MT5_IMPORT_ERROR})")
        if self._initialised:
            await self._call(self._shutdown_blocking)
        await self.connect()

    def _connected_message(self) -> str:
        account = self._details.get("account_login")
        server = self._details.get("account_server")
        if account:
            return f"Connected to account {account} on {server}"
        return "Connected to MT5 terminal"

    # ------------------------------------------------ blocking (MT5 thread)
    def _initialise_blocking(self) -> None:
        if self._initialised:  # another caller already (re)connected
            return
        s = self._settings
        kwargs: Dict[str, Any] = {"timeout": s.mt5_timeout_ms, "portable": s.mt5_portable}
        if s.mt5_credentials_configured:
            kwargs.update(login=int(s.mt5_login), password=s.mt5_password, server=s.mt5_server)
        elif s.mt5_login or s.mt5_password or s.mt5_server:
            self.log.warning(
                "MT5_LOGIN / MT5_PASSWORD / MT5_SERVER are only partially set - ignoring them "
                "and using the account already logged in inside the terminal."
            )

        self.log.info(
            "Initialising MT5 (path=%s, explicit_login=%s)",
            s.mt5_terminal_path or "<auto-detect>",
            s.mt5_credentials_configured,
        )
        if s.mt5_terminal_path:
            ok = mt5.initialize(s.mt5_terminal_path, **kwargs)
        else:
            ok = mt5.initialize(**kwargs)
        if not ok:
            error = self._last_error()
            mt5.shutdown()
            raise ProviderConnectionError(f"mt5.initialize() failed: {error}")

        terminal = mt5.terminal_info()
        if terminal is None:
            error = self._last_error()
            mt5.shutdown()
            raise ProviderConnectionError(f"Terminal info unavailable after initialise: {error}")

        account = mt5.account_info()
        version = mt5.version()
        self._details = {
            "server_utc_offset_hours": self._offset_hours(),
            "terminal_name": terminal.name,
            "terminal_company": terminal.company,
            "terminal_path": terminal.path,
            "terminal_build": int(terminal.build),
            "terminal_connected": bool(terminal.connected),
            "account_login": int(account.login) if account else None,
            "account_server": account.server if account else None,
            "account_currency": account.currency if account else None,
            "mt5_package_version": ".".join(str(v) for v in version[:2]) if version else None,
        }
        self._initialised = True

        # Re-select symbols that were subscribed before a reconnect.
        for symbol in list(self._subscribed):
            if not mt5.symbol_select(symbol, True):
                self.log.warning("Could not re-select %s after reconnect: %s", symbol, self._last_error())
        self._last_seen.clear()
        self.log.info("MT5 initialised: %s", self._details)

    def _shutdown_blocking(self) -> None:
        try:
            mt5.shutdown()
        finally:
            self._initialised = False

    def _poll_blocking(self, symbols: List[str]) -> Tuple[List[Tick], str, bool]:
        """Read the latest tick of each symbol; return new ticks, a health flag, offset-changed."""
        if not self._initialised:  # shut down concurrently (e.g. manual reconnect)
            return [], _HEALTH_OK, False
        offset_changed = False
        self._poll_counter += 1
        health = _HEALTH_OK
        if self._poll_counter % self._health_every == 0:
            terminal = mt5.terminal_info()
            if terminal is None:
                raise _ConnectionLost(f"terminal_info() returned None {self._last_error()}")
            health = _HEALTH_OK if terminal.connected else _HEALTH_OFFLINE

        ticks: List[Tick] = []
        missing = 0
        for symbol in symbols:
            raw = mt5.symbol_info_tick(symbol)
            if raw is None:
                missing += 1
                continue
            key = (int(raw.time_msc), float(raw.bid), float(raw.ask), float(raw.last), float(raw.volume_real))
            previous = self._last_seen.get(symbol)
            if previous == key:
                continue
            self._last_seen[symbol] = key
            # A tick that changed since the last poll is fresh -> usable for offset detection.
            if previous is not None and key[0] != previous[0]:
                offset_changed = self._vote_offset(key[0] / 1000.0) or offset_changed
            bid, ask, last = float(raw.bid), float(raw.ask), float(raw.last)
            price = bid if bid > 0 else last
            if price <= 0:
                continue
            ticks.append(
                Tick(
                    provider=self.name,
                    symbol=symbol,
                    time_ms=int(raw.time_msc),
                    bid=bid,
                    ask=ask,
                    last=last,
                    volume=float(raw.volume_real or raw.volume),
                    price=price,
                )
            )

        if symbols and missing == len(symbols):
            # Every symbol failed - verify the terminal is still there.
            if mt5.terminal_info() is None:
                raise _ConnectionLost(f"symbol_info_tick failed for all symbols {self._last_error()}")
        return ticks, health, offset_changed

    # ------------------------------------------------- supervisor (async)
    async def _supervise(self) -> None:
        delay = 1.0
        max_delay = self._settings.mt5_reconnect_max_delay_s
        self.log.info("MT5 supervisor started (poll=%.3fs)", self._poll_interval_s)
        try:
            while True:
                if not self._initialised:
                    await asyncio.sleep(delay)
                    try:
                        await self._call(self._initialise_blocking)
                        delay = 1.0
                        await self._set_status(True, self._connected_message())
                    except ProviderConnectionError as exc:
                        delay = min(delay * 2, max_delay)
                        await self._set_status(False, f"{exc.message} - retrying in {delay:.0f}s")
                        continue
                    except Exception as exc:
                        self.log.exception("Unexpected error during MT5 initialise")
                        delay = min(delay * 2, max_delay)
                        await self._set_status(False, f"Unexpected error: {exc} - retrying in {delay:.0f}s")
                        continue

                started = time.monotonic()
                symbols = list(self._subscribed)
                try:
                    ticks, health, offset_changed = await self._call(self._poll_blocking, symbols)
                except _ConnectionLost as exc:
                    self.log.warning("Lost connection to MT5 terminal: %s", exc)
                    try:
                        await self._call(self._shutdown_blocking)
                    except Exception:
                        self.log.exception("Error while shutting down broken MT5 session")
                    await self._set_status(False, "Lost connection to terminal - reconnecting")
                    continue
                except Exception:
                    self.log.exception("Unexpected error while polling MT5")
                    await asyncio.sleep(1.0)
                    continue

                if health == _HEALTH_OFFLINE:
                    await self._set_status(False, "Terminal is running but not connected to the broker server")
                elif health == _HEALTH_OK and not self._connected:
                    await self._set_status(True, self._connected_message())

                if offset_changed:
                    await self._emit_status()  # lets the failover provider re-align OANDA times

                for tick in ticks:
                    await self._emit_tick(tick)

                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.0, self._poll_interval_s - elapsed))
        except asyncio.CancelledError:
            self.log.info("MT5 supervisor stopped")
            raise

    # ============================================================ queries
    async def get_symbols(self, query: Optional[str] = None, limit: int = 200) -> List[SymbolInfo]:
        self._require_ready()
        raw = await self._call(mt5.symbols_get)
        if raw is None:
            raise ProviderUnavailableError(f"symbols_get() failed: {self._last_error()}")
        q = (query or "").strip().upper()
        out: List[SymbolInfo] = []
        for s in raw:
            if q and q not in s.name.upper() and q not in (s.description or "").upper():
                continue
            out.append(
                SymbolInfo(
                    provider=self.name,
                    symbol=s.name,
                    description=s.description or "",
                    category=s.path or "",
                    digits=int(s.digits),
                    currency_base=s.currency_base or "",
                    currency_profit=s.currency_profit or "",
                )
            )
            if len(out) >= limit:
                break
        return out

    def _ensure_symbol_blocking(self, symbol: str) -> None:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise SymbolNotFoundError(
                f"Symbol '{symbol}' not found in MT5. Names are case-sensitive and may carry a "
                "broker suffix (e.g. 'EURUSD.m'). Use /api/symbols?provider=mt5 to list them."
            )
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise SymbolNotFoundError(f"Could not add '{symbol}' to Market Watch: {self._last_error()}")

    def _history_blocking(
        self, symbol: str, mt5_tf: int, count: int, to: Optional[datetime]
    ) -> List[Candle]:
        self._ensure_symbol_blocking(symbol)
        if to is None:
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
        else:
            # copy_rates_from returns bars opened at or before date_from; step back one
            # second so the bar at ``to`` itself (already on the chart) is excluded.
            date_from = datetime.fromtimestamp(int(to.timestamp()) - 1, tz=timezone.utc)
            rates = mt5.copy_rates_from(symbol, mt5_tf, date_from, count)
        if rates is None:
            raise ProviderUnavailableError(f"No history returned for {symbol}: {self._last_error()}")
        return [
            Candle(
                time=int(r["time"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["tick_volume"]),
            )
            for r in rates
        ]

    async def get_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
        to: Optional[datetime] = None,
    ) -> List[Candle]:
        self._require_ready()
        mt5_tf = self._timeframe_map.get(timeframe)
        if mt5_tf is None:
            raise InvalidRequestError(f"Unsupported timeframe {timeframe}")
        return await self._call(self._history_blocking, symbol, mt5_tf, count, to)

    # ====================================================== subscriptions
    async def subscribe(self, symbol: str) -> None:
        self._require_ready()
        await self._call(self._ensure_symbol_blocking, symbol)
        self._subscribed.add(symbol)
        self.log.info("Subscribed %s", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self._subscribed.discard(symbol)
        self._last_seen.pop(symbol, None)
        self.log.info("Unsubscribed %s", symbol)
