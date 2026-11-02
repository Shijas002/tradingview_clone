"""
Automatic journal import (v1.1): your real trades from MT5 and Upstox.

* **MT5** - ``history_deals_get`` deals are grouped by position id. Opening
  deals (entry IN) give side, average entry and time; closing deals (OUT / OUT_BY)
  give the average exit. The broker's exact net result (profit + commission +
  swap + fee, account currency) is stored as ``broker_pnl``; the quantity is
  stored in units (lots x contract size) so the journal's formula agrees too.
  SL/TP come from the opening order. Still-open positions are imported as open
  trades and closed later automatically. MT5 times (server time) are converted to UTC.
* **Upstox** - executed trades (today's trade book, plus the trade-history API
  for older days) are matched per instrument first-in-first-out into round trips
  (flat -> position -> flat). Upstox charges are not part of the trade data, so
  P&L is the gross price difference x quantity.

Each imported trade has ``external_id`` (``mt5:<position>`` / ``upstox:<key>:<first trade>``),
so re-imports update instead of duplicating, and your own fields (setup, tags,
rating, notes, screenshot) are never overwritten.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.core.config import Settings
from app.core.exceptions import DataBridgeError, InvalidRequestError, ProviderUnavailableError
from app.db.database import Database
from app.db.models import JournalEntry, now_ms
from app.providers.registry import ProviderRegistry
from app.services.notifier import Notifier

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
DEAL_BUY, DEAL_SELL = 0, 1
ENTRY_IN, ENTRY_OUT, ENTRY_INOUT, ENTRY_OUT_BY = 0, 1, 2, 3
EPS = 1e-9
Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]

# fields an import may change; everything else (setup, tags, notes, rating, screenshot) belongs to the user
IMPORT_FIELDS = ("provider", "symbol", "side", "status", "entry_time", "entry_price", "quantity", "exit_time",
                 "exit_price", "stop_loss", "take_profit", "fees", "broker_pnl")


# ---------------------------------------------------------------- MT5 mapping
def mt5_round_trips(data: Dict[str, Any], offset_s: int = 0, provider_name: str = "mt5") -> List[Dict[str, Any]]:
    """Turn MT5 deals + open positions into journal trades (pure function, unit-tested)."""
    deals = [d for d in data.get("deals") or [] if int(d.get("type", -1)) in (DEAL_BUY, DEAL_SELL) and d.get("position_id")]
    contract: Dict[str, float] = data.get("contract") or {}
    sltp = {int(k): v for k, v in (data.get("sltp") or {}).items()}
    open_ids = {int(p.get("ticket") or p.get("identifier") or 0): p for p in data.get("positions") or []}
    currency = data.get("currency") or ""
    by_pos: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for d in deals:
        by_pos[int(d["position_id"])].append(d)

    trades: List[Dict[str, Any]] = []
    for pid, ds in by_pos.items():
        ds.sort(key=lambda d: (d.get("time_msc") or d.get("time", 0) * 1000))
        ins = [d for d in ds if int(d.get("entry", -1)) in (ENTRY_IN, ENTRY_INOUT)]
        outs = [d for d in ds if int(d.get("entry", -1)) in (ENTRY_OUT, ENTRY_OUT_BY)]
        if not ins:
            continue  # opened before the import window
        symbol = ins[0]["symbol"]
        size = contract.get(symbol, 1.0) or 1.0
        in_vol = sum(float(d["volume"]) for d in ins)
        entry = sum(float(d["price"]) * float(d["volume"]) for d in ins) / in_vol
        side = "long" if int(ins[0]["type"]) == DEAL_BUY else "short"
        out_vol = sum(float(d["volume"]) for d in outs)
        closed = bool(outs) and out_vol >= in_vol - EPS and pid not in open_ids
        net = sum(float(d.get("profit") or 0) + float(d.get("commission") or 0) + float(d.get("swap") or 0) + float(d.get("fee") or 0) for d in ds)
        costs = -sum(float(d.get("commission") or 0) + float(d.get("fee") or 0) + min(float(d.get("swap") or 0), 0.0) for d in ds)
        sl, tp = sltp.get(pid, (None, None))
        t = {
            "external_id": f"mt5:{pid}", "source": "mt5", "provider": provider_name, "symbol": symbol, "side": side,
            "status": "closed" if closed else "open",
            "entry_time": int(ins[0]["time"]) - offset_s, "entry_price": round(entry, 10), "quantity": round(in_vol * size, 8),
            "exit_time": None, "exit_price": None, "stop_loss": sl, "take_profit": tp, "fees": round(max(costs, 0.0), 10),
            "broker_pnl": round(net, 10) if closed else None,
            "notes": f"MT5 position #{pid} · {in_vol:g} lot(s) · contract {size:g}" + (f" · P&L in {currency}" if currency else ""),
        }
        if closed:
            t["exit_price"] = round(sum(float(d["price"]) * float(d["volume"]) for d in outs) / out_vol, 10)
            t["exit_time"] = int(outs[-1]["time"]) - offset_s
        trades.append(t)

    seen = {t["external_id"] for t in trades}
    for pid, p in open_ids.items():  # open positions whose opening deal is older than the window
        if not pid or f"mt5:{pid}" in seen:
            continue
        size = contract.get(p.get("symbol"), 1.0) or 1.0
        trades.append({
            "external_id": f"mt5:{pid}", "source": "mt5", "provider": provider_name, "symbol": p.get("symbol"),
            "side": "long" if int(p.get("type", 0)) == 0 else "short", "status": "open",
            "entry_time": int(p.get("time") or 0) - offset_s, "entry_price": float(p.get("price_open") or 0),
            "quantity": round(float(p.get("volume") or 0) * size, 8), "exit_time": None, "exit_price": None,
            "stop_loss": p.get("sl") or None, "take_profit": p.get("tp") or None, "fees": 0.0, "broker_pnl": None,
            "notes": f"MT5 position #{pid} · {float(p.get('volume') or 0):g} lot(s)",
        })
    return trades


# ------------------------------------------------------------- Upstox mapping
def _upstox_time(row: Dict[str, Any]) -> int:
    for key in ("exchange_timestamp", "order_timestamp"):
        v = row.get(key)
        if v:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return int(datetime.strptime(str(v)[:19], fmt).replace(tzinfo=IST).timestamp())
                except ValueError:
                    continue
    v = row.get("trade_date")
    if v:
        try:
            d = datetime.strptime(str(v)[:10], "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST)
            return int(d.timestamp())
        except ValueError:
            pass
    return int(time.time())


def upstox_round_trips(rows: List[Dict[str, Any]], resolve: Callable[[Dict[str, Any]], Tuple[str, str]]) -> List[Dict[str, Any]]:
    """FIFO-match executed trades into round trips per instrument (pure function, unit-tested)."""
    norm = []
    seen_ids = set()
    for r in rows:
        tid = str(r.get("trade_id") or "")
        if tid and tid in seen_ids:
            continue
        seen_ids.add(tid)
        side = str(r.get("transaction_type") or "").upper()
        qty = float(r.get("quantity") or 0)
        price = float(r.get("average_price") or r.get("price") or 0)
        if side not in ("BUY", "SELL") or qty <= 0 or price <= 0:
            continue
        key, symbol = resolve(r)
        norm.append({"key": key, "symbol": symbol, "side": side, "qty": qty, "price": price, "time": _upstox_time(r), "id": tid})
    norm.sort(key=lambda x: (x["time"], x["id"]))

    trades: List[Dict[str, Any]] = []
    state: Dict[str, Dict[str, Any]] = {}
    started: Dict[str, int] = defaultdict(int)  # trips started by one fill (2 when a fill reverses the position)
    for f in norm:
        st = state.get(f["key"])
        qty = f["qty"]
        while qty > EPS:
            if st is None:
                fid = f["id"] or str(f["time"])
                st = state[f["key"]] = {"side": "long" if f["side"] == "BUY" else "short", "pos": 0.0, "in_qty": 0.0, "in_val": 0.0,
                                        "out_qty": 0.0, "out_val": 0.0, "first": f, "last_time": f["time"], "symbol": f["symbol"],
                                        "n": started[fid]}
                started[fid] += 1
            opening = (st["side"] == "long") == (f["side"] == "BUY")
            if opening:
                st["pos"] += qty
                st["in_qty"] += qty
                st["in_val"] += qty * f["price"]
                qty = 0.0
            else:
                take = min(qty, st["pos"])
                st["pos"] -= take
                st["out_qty"] += take
                st["out_val"] += take * f["price"]
                qty -= take
            st["last_time"] = f["time"]
            if st["pos"] <= EPS:
                trades.append(_upstox_trip(f["key"], st, closed=True))
                st = None
                state.pop(f["key"], None)
    for key, st in state.items():
        trades.append(_upstox_trip(key, st, closed=False))
    return trades


def _upstox_trip(key: str, st: Dict[str, Any], closed: bool) -> Dict[str, Any]:
    first = st["first"]
    return {
        "external_id": f"upstox:{key}:{first['id'] or first['time']}" + (f"-{st['n']}" if st.get("n") else ""),
        "source": "upstox", "provider": "upstox",
        "symbol": st["symbol"], "side": st["side"], "status": "closed" if closed else "open",
        "entry_time": first["time"], "entry_price": round(st["in_val"] / st["in_qty"], 6), "quantity": st["in_qty"],
        "exit_time": st["last_time"] if closed else None,
        "exit_price": round(st["out_val"] / st["out_qty"], 6) if closed and st["out_qty"] else None,
        "stop_loss": None, "take_profit": None, "fees": 0.0, "broker_pnl": None,
        "notes": "Imported from Upstox (P&L before charges)",
    }


# ------------------------------------------------------------------ service
class JournalImporter:
    def __init__(self, db: Database, registry: ProviderRegistry, broadcast: Broadcast, notifier: Optional[Notifier],
                 settings: Settings, notion: Any = None) -> None:
        self.db = db
        self.registry = registry
        self.broadcast = broadcast
        self.notifier = notifier
        self.settings = settings
        self.notion = notion
        self._file: Path = settings.data_path / "journal_import.json"
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _save_state(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    async def start(self) -> None:
        if self.settings.journal_import_sources:
            self._task = asyncio.create_task(self._loop(), name="journal-import")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(20)  # let providers connect first
            while True:
                for source in self.settings.journal_import_sources:
                    last = (self.state.get(source) or {}).get("last_success")
                    days = 30 if not last else max(2, int((time.time() - last / 1000) / 86400) + 2)
                    try:
                        await self.run(source, min(days, 90), automatic=True)
                    except DataBridgeError as exc:
                        log.info("Automatic %s journal import skipped: %s", source, exc.message)
                    except Exception:
                        log.exception("Automatic %s journal import failed", source)
                await asyncio.sleep(self.settings.journal_import_interval_s)
        except asyncio.CancelledError:
            raise

    def status(self) -> Dict[str, Any]:
        available = {}
        for name in ("mt5", "upstox"):
            try:
                p = self.registry.get(name)
                available[name] = {"enabled": True, "connected": p.is_connected, "message": p.status().message}
            except DataBridgeError:
                available[name] = {"enabled": False, "connected": False, "message": f"'{name}' is not in ENABLED_PROVIDERS"}
        return {"automatic": self.settings.journal_import_sources, "interval_s": self.settings.journal_import_interval_s,
                "sources": available, "last": self.state}

    # ------------------------------------------------------------- import
    async def run(self, source: str, days: int = 30, automatic: bool = False) -> Dict[str, Any]:
        if source not in ("mt5", "upstox"):
            raise InvalidRequestError("source must be mt5 or upstox")
        async with self._lock:
            try:
                trades = await (self._fetch_mt5(days) if source == "mt5" else self._fetch_upstox(days))
            except DataBridgeError as exc:
                self.state.setdefault(source, {}).update({"last_error": exc.message, "last_attempt": now_ms()})
                await asyncio.to_thread(self._save_state)
                raise
            result = await asyncio.to_thread(self._upsert, trades)
            self.state.setdefault(source, {}).update({
                "last_success": now_ms(), "last_attempt": now_ms(), "last_error": None, "days": days, **{k: result[k] for k in ("created", "updated", "unchanged")},
            })
            await asyncio.to_thread(self._save_state)
        if result["created"] or result["updated"]:
            if self.notion is not None:
                self.notion.wake()
            await self.broadcast({"type": "journal_changed"})
            if self.notifier is not None and result["newly_closed"]:
                lines = [f"{t['side']} {t['symbol']} · P&L {t['pnl']:+.2f}" if t.get("pnl") is not None else f"{t['side']} {t['symbol']}"
                         for t in result["newly_closed"][:10]]
                self.notifier.notify("journal", f"📓 {len(result['newly_closed'])} trade(s) imported from {source.upper()}", lines)
        log.info("Journal import %s (%s days%s): %s new, %s updated, %s unchanged", source, days, ", auto" if automatic else "",
                 result["created"], result["updated"], result["unchanged"])
        return {"source": source, "days": days, **{k: v for k, v in result.items() if k != "newly_closed"},
                "closed_imported": len(result["newly_closed"])}

    async def _fetch_mt5(self, days: int) -> List[Dict[str, Any]]:
        provider = self.registry.get("mt5")
        if not provider.is_connected:
            raise ProviderUnavailableError(f"MT5 is not connected: {provider.status().message}")
        now = int(time.time())
        offset = provider.time_offset_s or 0  # type: ignore[attr-defined]
        data = await provider.get_account_trades(now - days * 86400 - 86400, now + 2 * 86400)  # type: ignore[attr-defined]
        return mt5_round_trips(data, offset_s=offset)

    async def _fetch_upstox(self, days: int) -> List[Dict[str, Any]]:
        provider = self.registry.get("upstox")
        today = datetime.now(IST).date()
        rows: List[Dict[str, Any]] = []
        if days > 1:
            try:
                rows += await provider.get_trade_history(today - timedelta(days=days), today - timedelta(days=1))  # type: ignore[attr-defined]
            except DataBridgeError as exc:
                log.warning("Upstox trade history unavailable: %s", exc.message)
        rows += await provider.get_trades_today()  # type: ignore[attr-defined]

        def resolve(r: Dict[str, Any]) -> Tuple[str, str]:
            key = str(r.get("instrument_token") or r.get("instrument_key") or "")
            try:
                inst = provider.instruments.by_key(key) if key else None  # type: ignore[attr-defined]
            except Exception:
                inst = None
            if inst is not None:
                return inst.key, inst.symbol
            sym = str(r.get("trading_symbol") or r.get("tradingsymbol") or r.get("symbol") or r.get("scrip_name") or key or "?")
            exch = str(r.get("exchange") or "NSE").split("_")[0]
            return key or f"{exch}:{sym}", f"{exch}:{sym}"

        return upstox_round_trips(rows, resolve)

    def _upsert(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        from app.services.notion_journal import trade_metrics

        created = updated = unchanged = 0
        newly_closed: List[Dict[str, Any]] = []
        notion_on = bool(self.notion is not None and getattr(self.notion, "enabled", False))
        with self.db.session() as s:
            for t in trades:
                if not t.get("symbol") or not t.get("entry_price"):
                    continue
                e = s.query(JournalEntry).filter(JournalEntry.external_id == t["external_id"]).first()
                if e is not None and e.notion_status == "deleting":
                    continue
                if e is None:
                    now = now_ms()
                    e = JournalEntry(id=uuid.uuid4().hex, created_at=now, updated_at=now, timeframe="", setup="",
                                     tags=[t["source"]], rating=None, notes=t.get("notes", ""), source=t["source"],
                                     external_id=t["external_id"], notion_status="pending" if notion_on else "local",
                                     **{k: t.get(k) for k in IMPORT_FIELDS})
                    e.fees = e.fees or 0.0
                    s.add(e)
                    created += 1
                    if e.status == "closed":
                        newly_closed.append({"side": e.side, "symbol": e.symbol, "pnl": trade_metrics(e)[0]})
                    continue
                was_open = e.status == "open"
                changes = {k: t.get(k) for k in IMPORT_FIELDS if t.get(k) is not None or k in ("exit_time", "exit_price", "broker_pnl")}
                # keep user-entered SL/TP when the broker has none
                for k in ("stop_loss", "take_profit"):
                    if changes.get(k) is None:
                        changes.pop(k, None)
                diff = {k: v for k, v in changes.items() if getattr(e, k) != v}
                if not diff:
                    unchanged += 1
                    continue
                for k, v in diff.items():
                    setattr(e, k, v)
                e.fees = e.fees or 0.0
                e.updated_at = now_ms()
                e.notion_status = "pending" if notion_on else "local"
                updated += 1
                if was_open and e.status == "closed":
                    newly_closed.append({"side": e.side, "symbol": e.symbol, "pnl": trade_metrics(e)[0]})
        return {"created": created, "updated": updated, "unchanged": unchanged, "newly_closed": newly_closed}
