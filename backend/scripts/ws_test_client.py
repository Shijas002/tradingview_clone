"""
Command-line WebSocket client to watch the live stream without a UI.

    python scripts/ws_test_client.py                                  # simulated EURUSD M1
    python scripts/ws_test_client.py --provider mt5 --symbol EURUSD --timeframe M1
    python scripts/ws_test_client.py --symbol XAUUSD --seconds 30 --no-ticks

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import websockets
except ImportError:
    print("The 'websockets' package is missing. Run: pip install -r requirements.txt")
    sys.exit(1)

from app.core.config import get_settings  # noqa: E402


def _fmt_ts(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%H:%M:%S")


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    host = "127.0.0.1" if settings.app_host in ("0.0.0.0", "") else settings.app_host
    url = args.url or f"ws://{host}:{settings.app_port}/ws/market"
    print(f"Connecting to {url} ...")
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            await ws.send(json.dumps({
                "action": "subscribe",
                "provider": args.provider,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            }))
            deadline = time.monotonic() + args.seconds if args.seconds else None
            counts = {"tick": 0, "candle": 0}
            while True:
                timeout = None if deadline is None else max(0.1, deadline - time.monotonic())
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "tick":
                    counts["tick"] += 1
                    if not args.no_ticks:
                        d = msg["data"]
                        print(f"TICK   {d['symbol']:<10} {_fmt_ts(d['time_ms'] / 1000)}  bid={d['bid']}  ask={d['ask']}")
                elif kind == "candle":
                    counts["candle"] += 1
                    c = msg["data"]
                    print(f"CANDLE {msg['symbol']:<10} {msg['timeframe']:<4} {_fmt_ts(c['time'])}  "
                          f"O={c['open']} H={c['high']} L={c['low']} C={c['close']} V={c['volume']}")
                elif kind == "error":
                    print(f"ERROR  {msg['message']}")
                    if args.exit_on_error:
                        return 1
                else:
                    print(f"{kind.upper():<6} {json.dumps({k: v for k, v in msg.items() if k != 'type'})}")
            print(f"\nDone. Received {counts['tick']} ticks and {counts['candle']} candle updates.")
            return 0 if counts["candle"] > 0 else 1
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        print(f"Connection failed: {exc}\nIs the server running? Start it with: python run.py")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch the data-bridge WebSocket stream")
    parser.add_argument("--provider", default="simulated", help="simulated | mt5")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M1", help="M1 M5 M15 M30 H1 H4 D1 W1 MN1")
    parser.add_argument("--seconds", type=float, default=0, help="Stop after N seconds (0 = run until Ctrl+C)")
    parser.add_argument("--no-ticks", action="store_true", help="Only print candle updates")
    parser.add_argument("--exit-on-error", action="store_true")
    parser.add_argument("--url", default=None, help="Override WebSocket URL")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
