"""
OANDA connection diagnostic - run after putting OANDA_API_TOKEN in backend/.env.

    python scripts/check_oanda.py            # checks token, account, EURUSD candles and live prices
    python scripts/check_oanda.py XAUUSD     # test another symbol (MT5-style names are fine)

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
from app.providers.oanda_provider import OandaProvider  # noqa: E402


def fail(message: str) -> int:
    print(f"\n[FAIL] {message}")
    return 1


async def run(symbol: str) -> int:
    print("=" * 64)
    print(" OANDA diagnostic")
    print("=" * 64)
    print(f".env file     : {ENV_FILE} ({'found' if ENV_FILE.exists() else 'NOT FOUND'})")
    try:
        settings = get_settings()
        _ = settings.oanda_symbol_overrides
    except Exception as exc:
        return fail(f"Your .env file has an invalid value: {exc}")
    token = settings.oanda_api_token.strip()
    print(f"Environment   : {settings.oanda_environment}")
    print(f"Token         : {'set (' + token[:4] + '…' + token[-4:] + ')' if token else 'EMPTY'}")
    if not token:
        return fail("OANDA_API_TOKEN is empty. See FAILOVER.md, Step 1.")

    provider = OandaProvider(settings)
    ticks: List[Tick] = []

    async def on_tick(t: Tick) -> None:
        ticks.append(t)

    provider.add_tick_listener(on_tick)
    try:
        try:
            await provider.connect()
        except DataBridgeError as exc:
            hint = ""
            if "rejected" in exc.message:
                hint = ("\n       Hint: a practice token only works with OANDA_ENVIRONMENT=practice, "
                        "a live token only with live. Regenerate the token if unsure.")
            return fail(f"{exc.message}{hint}")
        details = provider.status().details
        print(f"[OK] Account  : {details.get('account_id')} ({details.get('instruments')} instruments)")

        try:
            instrument = provider.normalize_symbol(symbol)
        except DataBridgeError as exc:
            return fail(exc.message)
        print(f"[OK] Symbol   : {symbol} -> {instrument}")

        candles = await provider.get_history(instrument, Timeframe.M1, 5)
        if not candles:
            return fail(f"No candles returned for {instrument}")
        print(f"[OK] Last {len(candles)} M1 candles (UTC):")
        for c in candles:
            t = datetime.fromtimestamp(c.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"     {t}  O={c.open}  H={c.high}  L={c.low}  C={c.close}  V={int(c.volume)}")

        await provider.acquire(instrument)
        print("Waiting up to 15 s for live prices (none arrive while the market is closed)...")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(ticks) < 3:
            await asyncio.sleep(0.2)
        if ticks:
            last = ticks[-1]
            print(f"[OK] Live     : {len(ticks)} prices, last bid={last.bid} ask={last.ask}")
        else:
            print("[WARN] No live prices yet - normal on weekends; otherwise check your network/firewall.")
    finally:
        await provider.disconnect()

    print("\nOANDA is ready to act as the fallback data source.")
    return 0


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    try:
        return asyncio.run(run(symbol))
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())
