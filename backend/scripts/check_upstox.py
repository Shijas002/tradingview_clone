"""
Upstox connection diagnostic - run after setting up Upstox in backend/.env.

    python scripts/check_upstox.py                  # checks login, instruments, NSE:RELIANCE candles and live prices
    python scripts/check_upstox.py NSE:NIFTY        # test another symbol (index, stock, future...)
    python scripts/check_upstox.py "MCX:CRUDEOIL"   # partial names work too (first search match is used)

Exit code 0 = everything works, 1 = something needs fixing (message explains what).
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ENV_FILE, get_settings  # noqa: E402
from app.core.exceptions import DataBridgeError  # noqa: E402
from app.models.market import Tick, Timeframe  # noqa: E402
from app.providers.upstox_provider import IST, UpstoxProvider  # noqa: E402


def fail(message: str) -> int:
    print(f"\n[FAIL] {message}")
    return 1


def masked(value: str) -> str:
    return f"set ({value[:4]}…{value[-4:]})" if len(value) > 10 else ("set" if value else "EMPTY")


async def run(symbol: str) -> int:
    print("=" * 64)
    print(" Upstox diagnostic")
    print("=" * 64)
    print(f".env file       : {ENV_FILE} ({'found' if ENV_FILE.exists() else 'NOT FOUND'})")
    try:
        settings = get_settings()
    except Exception as exc:
        return fail(f"Your .env file has an invalid value: {exc}")
    print(f"Analytics token : {masked(settings.upstox_analytics_token)}")
    print(f"API key/secret  : {masked(settings.upstox_api_key)} / {masked(settings.upstox_api_secret)}")
    print(f"Exchanges       : {settings.upstox_exchanges}   feed mode: {settings.upstox_feed_mode}")
    if "upstox" not in settings.enabled_provider_list:
        print("[WARN] 'upstox' is not in ENABLED_PROVIDERS - the app will not show it until you add it.")

    provider = UpstoxProvider(settings)
    mode = provider.auth.mode
    if mode == "none":
        return fail("Upstox is not configured. Set UPSTOX_ANALYTICS_TOKEN (or UPSTOX_API_KEY + UPSTOX_API_SECRET). See TUTORIAL.md, Step 15.")
    print(f"Login mode      : {mode}")
    if provider.auth.active_token() is None:
        return fail(
            "No valid login yet. Start the backend (python run.py), open the app and click "
            "'Log in to Upstox' in the status bar (needed once a day), then run this check again."
        )

    ticks: List[Tick] = []

    async def on_tick(t: Tick) -> None:
        ticks.append(t)

    provider.add_tick_listener(on_tick)
    try:
        print("Loading instrument lists (first run downloads them, ~10-30 s)...")
        try:
            await provider.instruments.load()
        except DataBridgeError as exc:
            return fail(exc.message)
        print(f"[OK] Instruments: {len(provider.instruments)} chartable symbols")

        try:
            canonical = provider.normalize_symbol(symbol)
        except DataBridgeError:
            matches = provider.instruments.search(symbol, 1)
            if not matches:
                return fail(f"No Upstox symbol matches '{symbol}'")
            canonical = matches[0].symbol
        print(f"[OK] Symbol     : {symbol} -> {canonical} ({provider.instruments.resolve(canonical).key})")

        try:
            candles = await provider.get_history(canonical, Timeframe.D1, 5)
            intraday = await provider.get_history(canonical, Timeframe.M5, 5)
        except DataBridgeError as exc:
            return fail(f"History request failed: {exc.message}")
        if not candles:
            return fail(f"No daily candles returned for {canonical}")
        print(f"[OK] Last {len(candles)} daily candles:")
        for c in candles:
            print(f"     {datetime.fromtimestamp(c.time, tz=timezone.utc).strftime('%Y-%m-%d')}  O={c.open}  H={c.high}  L={c.low}  C={c.close}  V={int(c.volume)}")
        print(f"[OK] Last {len(intraday)} 5-minute candles (IST):")
        for c in intraday:
            print(f"     {datetime.fromtimestamp(c.time, tz=IST).strftime('%Y-%m-%d %H:%M')}  C={c.close}  V={int(c.volume)}")

        await provider.connect()
        await provider.acquire(canonical)
        print("Waiting up to 20 s for the live stream (prices only move while the market is open)...")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and len(ticks) < 3:
            await asyncio.sleep(0.25)
        status = provider.status()
        print(f"Stream status   : {status.message}")
        if ticks:
            last = ticks[-1]
            print(f"[OK] Live       : {len(ticks)} updates, last price={last.price} bid={last.bid} ask={last.ask}")
        elif provider.status().details.get("stream") == "live":
            print("[OK] Live stream connected (no price change yet - normal when the market is closed).")
        else:
            print("[WARN] Live stream not connected yet - see the status message above.")
    finally:
        await provider.disconnect()

    print("\nUpstox is ready. In the app choose the 'upstox' provider chip in symbol search.")
    return 0


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NSE:RELIANCE"
    try:
        return asyncio.run(run(symbol))
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
