"""
MT5 connection diagnostic - run this BEFORE starting the server.

    python scripts/check_mt5.py            # uses backend/.env
    python scripts/check_mt5.py XAUUSD     # also test a specific symbol

It initialises MetaTrader 5 with the values from your .env, prints terminal and
account information, then reads a live tick and the last 5 M1 candles.
Exit code 0 = everything works, 1 = something needs fixing (message explains what).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Make "app" importable when running from backend/ or backend/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ENV_FILE, get_settings  # noqa: E402


def fail(message: str) -> int:
    print(f"\n[FAIL] {message}")
    return 1


def main() -> int:
    test_symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    print("=" * 64)
    print(" MetaTrader 5 diagnostic")
    print("=" * 64)
    print(f".env file          : {ENV_FILE} ({'found' if ENV_FILE.exists() else 'NOT FOUND - using defaults'})")

    if sys.platform != "win32":
        return fail("The MetaTrader5 Python package only works on Windows.")
    if sys.maxsize <= 2**32:
        return fail("You are running 32-bit Python. Install 64-bit Python 3.12 (MT5 is 64-bit only).")

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        return fail(f"MetaTrader5 package not installed ({exc}). Run: pip install MetaTrader5")

    try:
        settings = get_settings()
    except Exception as exc:
        return fail(f"Your .env file has an invalid value: {exc}")

    path = settings.mt5_terminal_path
    print(f"MT5 package        : {mt5.__version__}")
    print(f"MT5_TERMINAL_PATH  : {path or '<empty - auto-detect>'}")
    if path and not Path(path).exists():
        return fail(f"MT5_TERMINAL_PATH does not exist: {path}")
    print(f"Explicit login     : {'yes (' + settings.mt5_login + ' @ ' + settings.mt5_server + ')' if settings.mt5_credentials_configured else 'no - using account logged in inside the terminal'}")

    kwargs = {"timeout": settings.mt5_timeout_ms, "portable": settings.mt5_portable}
    if settings.mt5_credentials_configured:
        kwargs.update(login=int(settings.mt5_login), password=settings.mt5_password, server=settings.mt5_server)

    print("\nInitialising terminal (this can take up to a minute the first time)...")
    ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
    if not ok:
        code, desc = mt5.last_error()
        mt5.shutdown()
        hints = {
            -10005: "Timeout - start the MT5 terminal manually once, log in, then retry.",
            -10004: "No IPC connection - is the terminal installed at the configured path?",
            -6: "Authorization failed - check MT5_LOGIN / MT5_PASSWORD / MT5_SERVER.",
        }
        return fail(f"mt5.initialize() failed: [{code}] {desc}\n       Hint: {hints.get(code, 'See TUTORIAL.md -> Troubleshooting.')}")

    try:
        term = mt5.terminal_info()
        acc = mt5.account_info()
        print("\n[OK] Terminal")
        print(f"     name / build   : {term.name} / {term.build}")
        print(f"     path           : {term.path}")
        print(f"     connected      : {term.connected}")
        if acc:
            print("[OK] Account")
            print(f"     login / server : {acc.login} / {acc.server}")
            print(f"     currency       : {acc.currency}")
        else:
            print("[WARN] No account logged in. Log in inside the terminal (File -> Login to Trade Account).")

        if not term.connected:
            print("[WARN] Terminal is not connected to the broker server - check internet / login.")

        info = mt5.symbol_info(test_symbol)
        if info is None:
            matches = [s.name for s in (mt5.symbols_get() or []) if test_symbol.upper() in s.name.upper()][:10]
            return fail(
                f"Symbol '{test_symbol}' not found. Similar names on your broker: {matches or 'none'}"
            )
        if not info.visible:
            mt5.symbol_select(test_symbol, True)

        tick = mt5.symbol_info_tick(test_symbol)
        if tick is None:
            return fail(f"No tick for {test_symbol}: {mt5.last_error()} (market closed or not subscribed?)")
        tick_time = datetime.fromtimestamp(tick.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[OK] Tick {test_symbol}: bid={tick.bid} ask={tick.ask} time(server)={tick_time}")

        rates = mt5.copy_rates_from_pos(test_symbol, mt5.TIMEFRAME_M1, 0, 5)
        if rates is None or len(rates) == 0:
            return fail(f"No M1 history for {test_symbol}: {mt5.last_error()}")
        print(f"[OK] Last {len(rates)} M1 candles:")
        for r in rates:
            t = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"     {t}  O={r['open']}  H={r['high']}  L={r['low']}  C={r['close']}  V={r['tick_volume']}")
    finally:
        mt5.shutdown()

    print("\nAll checks passed - MT5 is ready for the data bridge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
