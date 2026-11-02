"""
Paper trading (v1.1) - a simulated broker on live prices, like TradingView's.

* One account (``default``) with a cash balance, optional commission (% of value)
  and netting positions: one position per symbol; buying while short reduces /
  closes / flips it.
* Orders: market, limit, stop - each may carry a stop loss and take profit that is
  attached to the resulting position.
* Fills use live ticks: buys at the ask, sells at the bid (MT5 / OANDA quotes);
  for exchange feeds without a book the last price. Market orders without a recent
  tick fill at the latest candle close.
  - buy limit:  ask <= price      sell limit: bid >= price
  - buy stop:   ask >= price      sell stop:  bid <= price
  - long SL/TP: bid <= SL / bid >= TP     short SL/TP: ask >= SL / ask <= TP
* P&L = price difference x quantity (same units as the journal; for Forex use units,
  100000 = 1 lot).
* Every closed position can be written to the trade journal automatically
  (PAPER_AUTO_JOURNAL) - it is created when the position opens and closed with the
  exact realized P&L.
* WebSocket messages: ``paper_changed`` after any change, ``paper_pnl`` (every
  second while positions are open) with live prices and open P&L.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from app.core.config import Settings
from app.core.exceptions import DataBridgeError, InvalidRequestError, NotFoundError, ProviderUnavailableError
from app.db.database import Database
from app.db.models import JournalEntry, PaperAccount, PaperOrder, PaperPosition, now_ms
from app.models.market import Tick, Timeframe
from app.providers.registry import ProviderRegistry
from app.services.notifier import Notifier

log = logging.getLogger(__name__)

ACCOUNT_ID = "default"
EPS = 1e-9
FRESH_TICK_S = 120
Broadcast = Callable[[Dict[str, Any]], Awaitable[None]]
FeedKey = Tuple[str, str]


@dataclass
class _Quote:
    bid: float
    ask: float
    last: float
    time_ms: int

    def buy_price(self) -> float:
        return self.ask or self.last

    def sell_price(self) -> float:
        return self.bid or self.last


@dataclass
class _Feed:
    provider: str
    symbol: str
    acquired: bool = False
    quote: Optional[_Quote] = None
    orders: List[Dict[str, Any]] = field(default_factory=list)  # working orders (id, side, type, price)
    position: Optional[Dict[str, Any]] = None  # open position (id, side, sl, tp, qty, avg)


def _uid() -> str:
    return uuid.uuid4().hex[:16]


class PaperBroker:
    def __init__(
        self,
        db: Database,
        registry: ProviderRegistry,
        broadcast: Broadcast,
        notifier: Optional[Notifier],
        settings: Settings,
        notion: Any = None,
    ) -> None:
        self.db = db
        self.registry = registry
        self.broadcast = broadcast
        self.notifier = notifier
        self.settings = settings
        self.notion = notion
        self.feeds: Dict[FeedKey, _Feed] = {}
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._busy: Set[str] = set()  # order / position ids being filled
        self._quotes: Dict[FeedKey, _Quote] = {}  # last quote of every symbol any chart / alert is watching
        self._pnl_dirty = False

    # ============================================================ lifecycle
    async def start(self) -> None:
        await asyncio.to_thread(self._ensure_account)
        for provider in self.registry.all():
            provider.add_tick_listener(self._on_tick)
        await self.reload()
        self._task = asyncio.create_task(self._loop(), name="paper-broker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for f in self.feeds.values():
                await self._release(f)
            self.feeds.clear()

    def _ensure_account(self) -> None:
        with self.db.session() as s:
            if s.get(PaperAccount, ACCOUNT_ID) is None:
                bal = self.settings.paper_initial_balance
                s.add(PaperAccount(id=ACCOUNT_ID, currency=self.settings.paper_currency, initial_balance=bal, balance=bal,
                                   commission_pct=self.settings.paper_commission_pct, created_at=now_ms(), reset_at=now_ms()))

    # ================================================================ feeds
    def _canonical(self, provider_name: str, symbol: str) -> Tuple[str, str]:
        provider = self.registry.get(provider_name)
        return provider.name, provider.normalize_symbol(symbol)

    async def _acquire(self, f: _Feed) -> None:
        if f.acquired:
            return
        try:
            f.symbol = await self.registry.get(f.provider).acquire(f.symbol)
            f.acquired = True
        except DataBridgeError as exc:
            log.warning("Paper feed %s:%s not available yet: %s", f.provider, f.symbol, exc.message)

    async def _release(self, f: _Feed) -> None:
        if not f.acquired:
            return
        try:
            await self.registry.get(f.provider).release(f.symbol)
        except Exception:
            log.debug("paper release failed", exc_info=True)
        f.acquired = False

    def _snapshot_rows(self) -> Tuple[List[PaperOrder], List[PaperPosition]]:
        with self.db.session() as s:
            orders = s.query(PaperOrder).filter(PaperOrder.account_id == ACCOUNT_ID, PaperOrder.status == "working").all()
            positions = s.query(PaperPosition).filter(PaperPosition.account_id == ACCOUNT_ID, PaperPosition.status == "open").all()
            return orders, positions

    async def reload(self) -> None:
        orders, positions = await asyncio.to_thread(self._snapshot_rows)
        async with self._lock:
            wanted: Dict[FeedKey, _Feed] = {}
            for o in orders:
                f = wanted.setdefault((o.provider, o.symbol), _Feed(o.provider, o.symbol))
                f.orders.append({"id": o.id, "side": o.side, "type": o.type, "price": o.price})
            for p in positions:
                f = wanted.setdefault((p.provider, p.symbol), _Feed(p.provider, p.symbol))
                f.position = {"id": p.id, "side": p.side, "sl": p.stop_loss, "tp": p.take_profit, "qty": p.qty, "avg": p.avg_price}
            for key in list(self.feeds):
                if key not in wanted:
                    await self._release(self.feeds.pop(key))
            for key, nf in wanted.items():
                f = self.feeds.get(key)
                if f is None:
                    self.feeds[key] = nf
                    f = nf
                else:
                    f.orders, f.position = nf.orders, nf.position
                await self._acquire(f)

    # ================================================================ ticks
    async def _on_tick(self, tick: Tick) -> None:
        quote = _Quote(bid=tick.bid or tick.price, ask=tick.ask or tick.price, last=tick.last or tick.price, time_ms=tick.time_ms)
        self._quotes[(tick.provider, tick.symbol)] = quote
        f = self.feeds.get((tick.provider, tick.symbol))
        if f is None:
            return
        f.quote = quote
        if f.position:
            self._pnl_dirty = True
        q = f.quote
        for o in list(f.orders):
            if o["id"] in self._busy or o["type"] == "market":
                continue
            px = o["price"]
            hit = (
                (o["side"] == "buy" and o["type"] == "limit" and q.buy_price() <= px)
                or (o["side"] == "sell" and o["type"] == "limit" and q.sell_price() >= px)
                or (o["side"] == "buy" and o["type"] == "stop" and q.buy_price() >= px)
                or (o["side"] == "sell" and o["type"] == "stop" and q.sell_price() <= px)
            )
            if hit:
                self._spawn_fill(o["id"], q.buy_price() if o["side"] == "buy" else q.sell_price(), "")
        p = f.position
        if p and p["id"] not in self._busy:
            if p["side"] == "long":
                mark = q.sell_price()
                if p["sl"] is not None and mark <= p["sl"]:
                    self._spawn_exit(f, p, mark, "stop loss")
                elif p["tp"] is not None and mark >= p["tp"]:
                    self._spawn_exit(f, p, mark, "take profit")
            else:
                mark = q.buy_price()
                if p["sl"] is not None and mark >= p["sl"]:
                    self._spawn_exit(f, p, mark, "stop loss")
                elif p["tp"] is not None and mark <= p["tp"]:
                    self._spawn_exit(f, p, mark, "take profit")

    def _spawn_fill(self, order_id: str, price: float, note: str) -> None:
        self._busy.add(order_id)
        asyncio.create_task(self._fill_and_publish(order_id, price, note))

    def _spawn_exit(self, f: _Feed, p: Dict[str, Any], price: float, note: str) -> None:
        self._busy.add(p["id"])

        async def run() -> None:
            try:
                order_id = await asyncio.to_thread(
                    self._create_order_row, f.provider, f.symbol, "sell" if p["side"] == "long" else "buy", "market", p["qty"],
                    None, None, None, note,
                )
                self._busy.add(order_id)
                await self._fill_and_publish(order_id, price, note)
            finally:
                self._busy.discard(p["id"])

        asyncio.create_task(run())

    async def _fill_and_publish(self, order_id: str, price: float, note: str) -> Optional[Dict[str, Any]]:
        try:
            result = await asyncio.to_thread(self._fill_tx, order_id, price, note)
        except Exception:
            log.exception("Paper fill of %s failed", order_id)
            result = None
        finally:
            self._busy.discard(order_id)
        if result is None:
            return None
        await self.reload()
        self._notify_fill(result)
        if result.get("journal_changed"):
            if self.notion is not None:
                self.notion.wake()
            await self.broadcast({"type": "journal_changed"})
        await self.broadcast({"type": "paper_changed"})
        return result

    # ============================================================ database
    def _create_order_row(self, provider: str, symbol: str, side: str, otype: str, qty: float, price: Optional[float],
                          sl: Optional[float], tp: Optional[float], note: str = "") -> str:
        oid = _uid()
        with self.db.session() as s:
            s.add(PaperOrder(id=oid, account_id=ACCOUNT_ID, provider=provider, symbol=symbol, side=side, type=otype, qty=qty,
                             price=price, stop_loss=sl, take_profit=tp, status="working", created_at=now_ms(), updated_at=now_ms(),
                             note=note))
        return oid

    def _fill_tx(self, order_id: str, price: float, note: str) -> Optional[Dict[str, Any]]:
        now = now_ms()
        with self.db.session() as s:
            o = s.get(PaperOrder, order_id)
            if o is None or o.status != "working":
                return None
            acct = s.get(PaperAccount, ACCOUNT_ID)
            pos = (
                s.query(PaperPosition)
                .filter(PaperPosition.account_id == ACCOUNT_ID, PaperPosition.provider == o.provider,
                        PaperPosition.symbol == o.symbol, PaperPosition.status == "open")
                .first()
            )
            commission = abs(price * o.qty) * (acct.commission_pct or 0.0) / 100.0
            realized = 0.0
            opened: Optional[PaperPosition] = None
            closed: Optional[PaperPosition] = None
            added: Optional[PaperPosition] = None
            buy = o.side == "buy"
            if pos is None:
                opened = self._new_position(s, o, o.qty, price, now, commission)
            elif (pos.side == "long") == buy:
                pos.avg_price = (pos.avg_price * pos.qty + price * o.qty) / (pos.qty + o.qty)
                pos.qty += o.qty
                pos.max_qty = max(pos.max_qty or 0.0, pos.qty)
                pos.commission = (pos.commission or 0.0) + commission
                if o.stop_loss is not None:
                    pos.stop_loss = o.stop_loss
                if o.take_profit is not None:
                    pos.take_profit = o.take_profit
                added = pos
            else:
                close_qty = min(o.qty, pos.qty)
                direction = 1.0 if pos.side == "long" else -1.0
                realized = (price - pos.avg_price) * close_qty * direction
                share = close_qty / o.qty
                pos.realized_pnl = (pos.realized_pnl or 0.0) + realized
                pos.commission = (pos.commission or 0.0) + commission * share
                pos.exit_value = (pos.exit_value or 0.0) + price * close_qty
                pos.exit_qty = (pos.exit_qty or 0.0) + close_qty
                pos.qty -= close_qty
                if pos.qty <= EPS:
                    pos.qty = 0.0
                    pos.status = "closed"
                    pos.closed_at = now
                    closed = pos
                remaining = o.qty - close_qty
                if remaining > EPS:
                    opened = self._new_position(s, o, remaining, price, now, commission * (1 - share))
            acct.balance = (acct.balance or 0.0) + realized - commission
            o.status = "filled"
            o.fill_price = price
            o.filled_at = now
            o.updated_at = now
            o.commission = commission
            o.realized_pnl = realized
            if note:
                o.note = note
            s.flush()

            journal_changed = False
            if self.settings.paper_auto_journal:
                if closed is not None:
                    journal_changed |= self._journal_close(s, closed)
                if added is not None:
                    journal_changed |= self._journal_update_open(s, added)
                if opened is not None:
                    journal_changed |= self._journal_open(s, opened)
            return {
                "order": {"id": o.id, "symbol": o.symbol, "provider": o.provider, "side": o.side, "type": o.type, "qty": o.qty,
                          "price": price, "note": o.note},
                "realized": realized,
                "commission": commission,
                "opened": opened.side if opened else None,
                "closed": bool(closed),
                "closed_pnl": (closed.realized_pnl - (closed.commission or 0.0)) if closed else None,
                "balance": acct.balance,
                "currency": acct.currency,
                "journal_changed": journal_changed,
            }

    @staticmethod
    def _new_position(s: Any, o: PaperOrder, qty: float, price: float, now: int, commission: float) -> PaperPosition:
        pos = PaperPosition(
            id=_uid(), account_id=ACCOUNT_ID, provider=o.provider, symbol=o.symbol, side="long" if o.side == "buy" else "short",
            qty=qty, max_qty=qty, avg_price=price, stop_loss=o.stop_loss, take_profit=o.take_profit, status="open",
            opened_at=now, realized_pnl=0.0, commission=commission, exit_value=0.0, exit_qty=0.0,
        )
        s.add(pos)
        s.flush()
        return pos

    # ------------------------------------------------------------- journal
    def _journal_status(self) -> str:
        return "pending" if (self.notion is not None and getattr(self.notion, "enabled", False)) else "local"

    def _journal_open(self, s: Any, pos: PaperPosition) -> bool:
        now = now_ms()
        e = JournalEntry(
            id=uuid.uuid4().hex, created_at=now, updated_at=now, provider=pos.provider, symbol=pos.symbol, timeframe="",
            side=pos.side, status="open", entry_time=pos.opened_at // 1000, entry_price=pos.avg_price, quantity=pos.qty,
            stop_loss=pos.stop_loss, take_profit=pos.take_profit, fees=pos.commission or 0.0, setup="", tags=["paper"],
            notes="Paper trade", source="paper", external_id=f"paper:{pos.id}", notion_status=self._journal_status(),
        )
        s.add(e)
        pos.journal_id = e.id
        return True

    def _journal_update_open(self, s: Any, pos: PaperPosition) -> bool:
        e = s.get(JournalEntry, pos.journal_id) if pos.journal_id else None
        if e is None:
            return False
        e.entry_price = pos.avg_price
        e.quantity = pos.max_qty or pos.qty
        e.stop_loss, e.take_profit = pos.stop_loss, pos.take_profit
        e.fees = pos.commission or 0.0
        e.updated_at = now_ms()
        e.notion_status = self._journal_status()
        return True

    def _journal_close(self, s: Any, pos: PaperPosition) -> bool:
        e = s.get(JournalEntry, pos.journal_id) if pos.journal_id else None
        if e is None:
            self._journal_open(s, pos)
            s.flush()
            e = s.get(JournalEntry, pos.journal_id)
        exit_price = (pos.exit_value / pos.exit_qty) if pos.exit_qty else pos.avg_price
        e.status = "closed"
        e.exit_price = exit_price
        e.exit_time = (pos.closed_at or now_ms()) // 1000
        e.quantity = pos.exit_qty or pos.max_qty or e.quantity
        e.entry_price = pos.avg_price
        e.fees = pos.commission or 0.0
        e.stop_loss, e.take_profit = pos.stop_loss, pos.take_profit
        e.broker_pnl = round((pos.realized_pnl or 0.0) - (pos.commission or 0.0), 10)
        e.updated_at = now_ms()
        e.notion_status = self._journal_status()
        return True

    # ============================================================ commands
    async def place_order(self, provider: str, symbol: str, side: str, otype: str, qty: float, price: Optional[float],
                          stop_loss: Optional[float], take_profit: Optional[float]) -> Dict[str, Any]:
        provider, symbol = self._canonical(provider, symbol)
        if qty <= 0:
            raise InvalidRequestError("Quantity must be above 0")
        if otype in ("limit", "stop") and (price is None or price <= 0):
            raise InvalidRequestError(f"A {otype} order needs a price")
        key = (provider, symbol)
        async with self._lock:
            f = self.feeds.get(key)
            if f is None:
                f = self.feeds[key] = _Feed(provider, symbol)
            await self._acquire(f)
        quote = await self._quote(f)
        ref = price if otype != "market" else (quote.buy_price() if side == "buy" else quote.sell_price()) if quote else None
        if ref is not None:
            if side == "buy":
                if stop_loss is not None and stop_loss >= ref:
                    raise InvalidRequestError(f"Stop loss {stop_loss} must be below the buy price {ref}")
                if take_profit is not None and take_profit <= ref:
                    raise InvalidRequestError(f"Take profit {take_profit} must be above the buy price {ref}")
            else:
                if stop_loss is not None and stop_loss <= ref:
                    raise InvalidRequestError(f"Stop loss {stop_loss} must be above the sell price {ref}")
                if take_profit is not None and take_profit >= ref:
                    raise InvalidRequestError(f"Take profit {take_profit} must be below the sell price {ref}")
        order_id = await asyncio.to_thread(self._create_order_row, provider, symbol, side, otype,
                                           qty, price if otype != "market" else None, stop_loss, take_profit)
        fill_price: Optional[float] = None
        if otype == "market":
            if quote is None:
                await asyncio.to_thread(self._reject, order_id, "No price available for this symbol right now")
                await self.broadcast({"type": "paper_changed"})
                raise ProviderUnavailableError("No price available for this symbol right now (is the provider connected?)")
            fill_price = quote.buy_price() if side == "buy" else quote.sell_price()
        elif quote is not None:  # marketable limit/stop fills at once
            b, sl_ = quote.buy_price(), quote.sell_price()
            if (side == "buy" and otype == "limit" and b <= price) or (side == "buy" and otype == "stop" and b >= price):
                fill_price = b
            elif (side == "sell" and otype == "limit" and sl_ >= price) or (side == "sell" and otype == "stop" and sl_ <= price):
                fill_price = sl_
        if fill_price is not None:
            self._busy.add(order_id)
            await self._fill_and_publish(order_id, fill_price, "")
        else:
            await self.reload()
            await self.broadcast({"type": "paper_changed"})
            log.info("Paper %s %s %s %s @ %s working", side, otype, qty, symbol, price)
        return await asyncio.to_thread(self._order_dict, order_id)

    async def _quote(self, f: _Feed) -> Optional[_Quote]:
        q = f.quote or self._quotes.get((f.provider, f.symbol))
        if q is not None and time.time() * 1000 - q.time_ms < FRESH_TICK_S * 1000:
            return q
        try:  # no recent tick (market closed / just subscribed): latest candle close
            bars = await self.registry.get(f.provider).get_history(f.symbol, Timeframe.M1, 1)
        except DataBridgeError:
            bars = []
        if bars:
            c = bars[-1].close
            return _Quote(bid=c, ask=c, last=c, time_ms=bars[-1].time * 1000)
        return q

    def _reject(self, order_id: str, reason: str) -> None:
        with self.db.session() as s:
            o = s.get(PaperOrder, order_id)
            if o is not None:
                o.status, o.reason, o.updated_at = "rejected", reason, now_ms()

    async def modify_order(self, order_id: str, qty: Optional[float], price: Optional[float], stop_loss: Optional[float],
                           take_profit: Optional[float], clear: List[str]) -> Dict[str, Any]:
        def tx() -> None:
            with self.db.session() as s:
                o = s.get(PaperOrder, order_id)
                if o is None or o.account_id != ACCOUNT_ID:
                    raise NotFoundError(f"Order {order_id} not found")
                if o.status != "working":
                    raise InvalidRequestError(f"Order is {o.status} and can't be modified")
                if qty is not None:
                    if qty <= 0:
                        raise InvalidRequestError("Quantity must be above 0")
                    o.qty = qty
                if price is not None:
                    if o.type == "market":
                        raise InvalidRequestError("A market order has no price")
                    o.price = price
                if stop_loss is not None:
                    o.stop_loss = stop_loss
                if take_profit is not None:
                    o.take_profit = take_profit
                if "stop_loss" in clear:
                    o.stop_loss = None
                if "take_profit" in clear:
                    o.take_profit = None
                o.updated_at = now_ms()

        await asyncio.to_thread(tx)
        await self.reload()
        await self.broadcast({"type": "paper_changed"})
        return await asyncio.to_thread(self._order_dict, order_id)

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        def tx() -> None:
            with self.db.session() as s:
                o = s.get(PaperOrder, order_id)
                if o is None or o.account_id != ACCOUNT_ID:
                    raise NotFoundError(f"Order {order_id} not found")
                if o.status != "working":
                    raise InvalidRequestError(f"Order is already {o.status}")
                o.status, o.updated_at = "cancelled", now_ms()

        await asyncio.to_thread(tx)
        await self.reload()
        await self.broadcast({"type": "paper_changed"})
        return await asyncio.to_thread(self._order_dict, order_id)

    async def modify_position(self, position_id: str, stop_loss: Optional[float], take_profit: Optional[float], clear: List[str]) -> Dict[str, Any]:
        def tx() -> bool:
            with self.db.session() as s:
                p = s.get(PaperPosition, position_id)
                if p is None or p.account_id != ACCOUNT_ID:
                    raise NotFoundError(f"Position {position_id} not found")
                if p.status != "open":
                    raise InvalidRequestError("Position is closed")
                if stop_loss is not None:
                    p.stop_loss = stop_loss
                if take_profit is not None:
                    p.take_profit = take_profit
                if "stop_loss" in clear:
                    p.stop_loss = None
                if "take_profit" in clear:
                    p.take_profit = None
                changed = False
                if self.settings.paper_auto_journal:
                    changed = self._journal_update_open(s, p)
                return changed

        journal = await asyncio.to_thread(tx)
        await self.reload()
        if journal:
            if self.notion is not None:
                self.notion.wake()
            await self.broadcast({"type": "journal_changed"})
        await self.broadcast({"type": "paper_changed"})
        return await asyncio.to_thread(self._position_dict, position_id)

    async def close_position(self, position_id: str, qty: Optional[float] = None, note: str = "close") -> Dict[str, Any]:
        def load() -> PaperPosition:
            with self.db.session() as s:
                p = s.get(PaperPosition, position_id)
                if p is None or p.account_id != ACCOUNT_ID:
                    raise NotFoundError(f"Position {position_id} not found")
                if p.status != "open":
                    raise InvalidRequestError("Position is already closed")
                s.expunge(p)
                return p

        p = await asyncio.to_thread(load)
        close_qty = p.qty if qty is None else min(max(qty, 0.0), p.qty)
        if close_qty <= 0:
            raise InvalidRequestError("Quantity must be above 0")
        f = self.feeds.get((p.provider, p.symbol)) or _Feed(p.provider, p.symbol)
        quote = await self._quote(f)
        if quote is None:
            raise ProviderUnavailableError("No price available to close this position right now")
        side = "sell" if p.side == "long" else "buy"
        order_id = await asyncio.to_thread(self._create_order_row, p.provider, p.symbol, side, "market", close_qty, None, None, None, note)
        self._busy.add(order_id)
        await self._fill_and_publish(order_id, quote.sell_price() if side == "sell" else quote.buy_price(), note)
        return await asyncio.to_thread(self._order_dict, order_id)

    async def close_all(self) -> int:
        _, positions = await asyncio.to_thread(self._snapshot_rows)
        n = 0
        for p in positions:
            try:
                await self.close_position(p.id)
                n += 1
            except DataBridgeError as exc:
                log.warning("Could not close %s: %s", p.symbol, exc.message)
        return n

    async def reset(self, balance: Optional[float], currency: Optional[str], commission_pct: Optional[float]) -> Dict[str, Any]:
        def tx() -> None:
            with self.db.session() as s:
                acct = s.get(PaperAccount, ACCOUNT_ID)
                s.query(PaperOrder).filter(PaperOrder.account_id == ACCOUNT_ID).delete()
                s.query(PaperPosition).filter(PaperPosition.account_id == ACCOUNT_ID).delete()
                if balance is not None:
                    acct.initial_balance = balance
                if currency:
                    acct.currency = currency[:8].upper()
                if commission_pct is not None:
                    acct.commission_pct = commission_pct
                acct.balance = acct.initial_balance
                acct.reset_at = now_ms()

        await asyncio.to_thread(tx)
        await self.reload()
        await self.broadcast({"type": "paper_changed"})
        log.info("Paper account reset")
        return await self.account()

    # ============================================================== queries
    def _order_dict(self, order_id: str) -> Dict[str, Any]:
        with self.db.session() as s:
            o = s.get(PaperOrder, order_id)
            if o is None:
                raise NotFoundError(f"Order {order_id} not found")
            return _order_out(o)

    def _position_dict(self, position_id: str) -> Dict[str, Any]:
        with self.db.session() as s:
            p = s.get(PaperPosition, position_id)
            if p is None:
                raise NotFoundError(f"Position {position_id} not found")
            return self._position_out(p)

    def _mark(self, p: PaperPosition) -> Optional[float]:
        f = self.feeds.get((p.provider, p.symbol))
        q = (f.quote if f is not None else None) or self._quotes.get((p.provider, p.symbol))
        if q is None:
            return None
        return q.sell_price() if p.side == "long" else q.buy_price()

    def _position_out(self, p: PaperPosition) -> Dict[str, Any]:
        mark = self._mark(p) if p.status == "open" else None
        direction = 1.0 if p.side == "long" else -1.0
        unrealized = (mark - p.avg_price) * p.qty * direction if mark is not None else None
        return {
            "id": p.id, "provider": p.provider, "symbol": p.symbol, "side": p.side, "qty": p.qty, "max_qty": p.max_qty,
            "avg_price": p.avg_price, "stop_loss": p.stop_loss, "take_profit": p.take_profit, "status": p.status,
            "opened_at": p.opened_at, "closed_at": p.closed_at, "realized_pnl": p.realized_pnl, "commission": p.commission,
            "exit_price": (p.exit_value / p.exit_qty) if p.exit_qty else None, "last": mark, "unrealized_pnl": unrealized,
            "journal_id": p.journal_id,
        }

    async def account(self) -> Dict[str, Any]:
        def q() -> Dict[str, Any]:
            with self.db.session() as s:
                acct = s.get(PaperAccount, ACCOUNT_ID)
                positions = s.query(PaperPosition).filter(PaperPosition.account_id == ACCOUNT_ID, PaperPosition.status == "open").all()
                orders = (
                    s.query(PaperOrder).filter(PaperOrder.account_id == ACCOUNT_ID, PaperOrder.status == "working")
                    .order_by(PaperOrder.created_at.desc()).all()
                )
                closed = s.query(PaperPosition).filter(PaperPosition.account_id == ACCOUNT_ID, PaperPosition.status == "closed").all()
                pos_out = [self._position_out(p) for p in positions]
                unrealized = sum(p["unrealized_pnl"] or 0.0 for p in pos_out)
                nets = [(c.realized_pnl or 0.0) - (c.commission or 0.0) for c in closed]
                wins = [x for x in nets if x > 0]
                losses = [x for x in nets if x < 0]
                return {
                    "id": acct.id, "currency": acct.currency, "initial_balance": acct.initial_balance, "balance": acct.balance,
                    "equity": acct.balance + unrealized, "unrealized_pnl": unrealized, "realized_pnl": acct.balance - acct.initial_balance,
                    "commission_pct": acct.commission_pct, "reset_at": acct.reset_at,
                    "positions": pos_out, "orders": [_order_out(o) for o in orders],
                    "stats": {
                        "closed": len(nets), "wins": len(wins), "losses": len(losses),
                        "win_rate": (len(wins) / len(nets) * 100.0) if nets else None,
                        "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
                    },
                }

        return await asyncio.to_thread(q)

    async def history(self, limit: int = 200) -> Dict[str, Any]:
        def q() -> Dict[str, Any]:
            with self.db.session() as s:
                orders = (
                    s.query(PaperOrder).filter(PaperOrder.account_id == ACCOUNT_ID, PaperOrder.status != "working")
                    .order_by(PaperOrder.updated_at.desc()).limit(limit).all()
                )
                closed = (
                    s.query(PaperPosition).filter(PaperPosition.account_id == ACCOUNT_ID, PaperPosition.status == "closed")
                    .order_by(PaperPosition.closed_at.desc()).limit(limit).all()
                )
                return {"orders": [_order_out(o) for o in orders], "positions": [self._position_out(p) for p in closed]}

        return await asyncio.to_thread(q)

    # ============================================================ live P&L
    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if not self._pnl_dirty:
                    continue
                self._pnl_dirty = False
                try:
                    acct = await self.account()
                    await self.broadcast({"type": "paper_pnl", "data": {
                        "equity": acct["equity"], "unrealized_pnl": acct["unrealized_pnl"], "balance": acct["balance"],
                        "positions": [{"id": p["id"], "last": p["last"], "unrealized_pnl": p["unrealized_pnl"]} for p in acct["positions"]],
                    }})
                except Exception:
                    log.debug("paper pnl broadcast failed", exc_info=True)
        except asyncio.CancelledError:
            raise

    def _notify_fill(self, r: Dict[str, Any]) -> None:
        o = r["order"]
        what = f"{o['side'].upper()} {o['qty']:g} {o['symbol']} @ {o['price']:g}"
        lines = [what + (f" ({o['note']})" if o.get("note") else "")]
        if r["closed"]:
            lines.append(f"Position closed · P&L {r['closed_pnl']:+.2f} {r['currency']}")
        elif r["opened"]:
            lines.append(f"Opened {r['opened']} position")
        lines.append(f"Balance {r['balance']:.2f} {r['currency']}")
        log.info("Paper fill: %s", " | ".join(lines))
        if self.notifier is not None:
            self.notifier.notify("paper", "📄 Paper trade", lines)


def _order_out(o: PaperOrder) -> Dict[str, Any]:
    return {
        "id": o.id, "provider": o.provider, "symbol": o.symbol, "side": o.side, "type": o.type, "qty": o.qty, "price": o.price,
        "stop_loss": o.stop_loss, "take_profit": o.take_profit, "status": o.status, "reason": o.reason or "",
        "created_at": o.created_at, "updated_at": o.updated_at, "filled_at": o.filled_at, "fill_price": o.fill_price,
        "commission": o.commission, "realized_pnl": o.realized_pnl, "note": o.note or "",
    }
