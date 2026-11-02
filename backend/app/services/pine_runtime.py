"""
Server-side Pine Script execution (v1.1) for indicator alerts.

The same PineTS engine the chart uses (``app/pine/pinets.min.browser.js``,
PineTS 0.13.1, AGPL-3.0) runs inside an embedded V8 provided by the
``mini-racer`` package. That lets alerts on indicators and Pine
``alertcondition()`` / ``alert()`` fire while no browser is open.

``request.security`` works: the script's requests for other symbols /
timeframes call back into Python (``fetch_bars``), which serves them from the
candle cache.

If ``mini-racer`` is not installed (or unsupported on the platform) the runtime
reports ``available = False`` and indicator alerts show that message instead of
running; price alerts are unaffected.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.models.market import Candle, Timeframe

log = logging.getLogger(__name__)

PINE_DIR = Path(__file__).resolve().parents[1] / "pine"
PINE_TIMEFRAMES = {"M1": "1", "M5": "5", "M15": "15", "M30": "30", "H1": "60", "H4": "240", "D1": "D", "W1": "W", "MN1": "M"}
RUN_TIMEOUT_S = 30.0

FetchBars = Callable[[str, str, Timeframe, int], Awaitable[List[Candle]]]


class PineError(Exception):
    """The script failed to compile or run (message is shown to the user)."""


def alertcondition_titles(source: str) -> List[str]:
    """Titles of the alertcondition() calls in a script (2nd argument or title=...)."""
    titles: List[str] = []
    for m in re.finditer(r"\balertcondition\s*\(", source):
        depth, i, start = 1, m.end(), m.end()
        quote: Optional[str] = None
        while i < len(source) and depth:
            ch = source[i]
            if quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        args = _split_args(source[start:i - 1])
        title = None
        for arg in args:
            km = re.match(r"\s*title\s*=\s*(['\"])(.*)\1\s*$", arg, re.S)
            if km:
                title = km.group(2)
        if title is None and len(args) >= 2:
            pm = re.match(r"\s*(['\"])(.*)\1\s*$", args[1], re.S)
            if pm:
                title = pm.group(2)
        titles.append(title or f"alertcondition #{len(titles) + 1}")
    return titles


def _split_args(text: str) -> List[str]:
    out, depth, quote, cur = [], 0, None, []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "([":
            depth += 1
            cur.append(ch)
        elif ch in ")]":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def uses_alert_function(source: str) -> bool:
    return re.search(r"(?<![\w.])alert\s*\(", source) is not None


class PineRuntime:
    def __init__(self, fetch_bars: FetchBars) -> None:
        self._fetch_bars = fetch_bars
        self._ctx: Any = None
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._lock = asyncio.Lock()
        self.available = False
        self.error: Optional[str] = None
        self.runs = 0
        try:
            import py_mini_racer  # noqa: F401
            self.available = True
        except Exception as exc:  # pragma: no cover - depends on the installation
            self.error = (
                f"Server-side Pine needs the 'mini-racer' package ({type(exc).__name__}: {exc}). "
                "Run: pip install -r requirements.txt"
            )

    async def _ensure(self) -> Any:
        if self._ctx is not None:
            return self._ctx
        if not self.available:
            raise PineError(self.error or "Pine runtime unavailable")
        from py_mini_racer import MiniRacer

        ctx = MiniRacer()
        ctx.eval("var window = globalThis; var self = globalThis;")
        ctx.eval((PINE_DIR / "pinets.min.browser.js").read_text(encoding="utf-8"))
        ctx.eval((PINE_DIR / "harness.js").read_text(encoding="utf-8"))
        stack = contextlib.AsyncExitStack()
        js_fetch = await stack.enter_async_context(ctx.wrap_py_function(self._py_fetch))
        ctx.eval("(f) => { globalThis.PYFETCH = f; }")(js_fetch)
        self._ctx, self._stack = ctx, stack
        log.info("Server-side Pine runtime ready (V8 %s)", getattr(ctx, "v8_version", "?"))
        return ctx

    async def _py_fetch(self, provider: Any, symbol: Any, tf: Any, count: Any) -> str:
        bars = await self._fetch_bars(str(provider), str(symbol), Timeframe(str(tf)), int(count))
        return json.dumps([b.model_dump() for b in bars])

    async def run(
        self,
        source: str,
        inputs: Optional[Dict[str, Any]],
        bars: List[Candle],
        provider: str,
        symbol: str,
        timeframe: Timeframe,
        digits: int = 5,
    ) -> Dict[str, Any]:
        args = json.dumps(
            {
                "source": source,
                "inputs": inputs or {},
                "bars": [b.model_dump() for b in bars],
                "tickerId": symbol,
                "timeframe": PINE_TIMEFRAMES[timeframe.value],
                "backendTf": timeframe.value,
                "provider": provider,
                "digits": digits,
            }
        )
        async with self._lock:
            ctx = await self._ensure()
            try:
                promise = ctx.eval(f"__pineRun({json.dumps(args)})")
                out = await asyncio.wait_for(promise, timeout=RUN_TIMEOUT_S)
            except asyncio.TimeoutError as exc:
                await self._discard()
                raise PineError(f"Script took longer than {RUN_TIMEOUT_S:.0f} s and was stopped") from exc
            except Exception as exc:
                raise PineError(_clean_js_error(exc)) from exc
            self.runs += 1
        return json.loads(out)

    async def _discard(self) -> None:
        ctx, stack = self._ctx, self._stack
        self._ctx = self._stack = None
        try:
            if stack is not None:
                await stack.aclose()
        except Exception:
            pass
        try:
            if ctx is not None:
                ctx.close()
        except Exception:
            pass

    async def close(self) -> None:
        async with self._lock:
            await self._discard()


def _clean_js_error(exc: Exception) -> str:
    text = str(exc)
    m = re.search(r"reason:\s*(.*)", text, re.S)
    if m:
        text = m.group(1)
    text = text.strip().split("\n    at ")[0].strip()
    return text[:500] or type(exc).__name__
