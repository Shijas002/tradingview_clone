"""
StreamManager - the hub between providers and WebSocket clients.

Responsibilities
----------------
* Track every connected WebSocket client and what it is subscribed to.
* Reference-count subscriptions so a provider only streams a symbol while at
  least one client needs it (two browser tabs on EURUSD = one MT5 subscription).
* Own one CandleAggregator per (provider, symbol, timeframe) and turn incoming
  ticks into live candle updates.
* Fan messages out through a bounded per-client queue. A slow client never
  blocks the feed: when its queue is full the oldest message is dropped.

Key types
---------
FeedKey = (provider, symbol)              -> one live tick stream
SubKey  = (provider, symbol, timeframe)   -> one live candle stream
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.exceptions import DataBridgeError, ProviderUnavailableError
from app.models.market import ProviderStatus, Tick, Timeframe
from app.providers.registry import ProviderRegistry
from app.services.candle_aggregator import CandleAggregator

log = logging.getLogger(__name__)

FeedKey = Tuple[str, str]
SubKey = Tuple[str, str, str]


class ClientSession:
    """One connected WebSocket plus its outbound queue and writer task."""

    _ids = itertools.count(1)

    def __init__(self, websocket: WebSocket, queue_size: int) -> None:
        self.id: int = next(self._ids)
        self.websocket = websocket
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.subscriptions: Set[SubKey] = set()
        self.dropped: int = 0
        self.closed: bool = False
        self.writer_task: Optional[asyncio.Task] = None

    def enqueue(self, message: Dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                log.warning("Client %s is slow - dropped %s messages so far", self.id, self.dropped)
            try:
                self.queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    async def writer(self) -> None:
        try:
            while True:
                message = await self.queue.get()
                if self.websocket.client_state != WebSocketState.CONNECTED:
                    break
                await self.websocket.send_json(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # socket closed mid-send, etc.
            log.debug("Writer for client %s stopped: %s", self.id, exc)
        finally:
            self.closed = True


class StreamManager:
    def __init__(self, registry: ProviderRegistry, queue_size: int) -> None:
        self._registry = registry
        self._queue_size = queue_size
        self._clients: Dict[int, ClientSession] = {}
        self._feed_clients: Dict[FeedKey, Set[int]] = {}
        self._aggregators: Dict[FeedKey, Dict[str, CandleAggregator]] = {}
        self._sub_clients: Dict[SubKey, Set[int]] = {}
        self._lock = asyncio.Lock()
        # provider -> (active_source, time_shift) last seen; a change means candles must be re-seeded
        self._source_keys: Dict[str, Tuple[Any, Any]] = {}
        self._background: Set[asyncio.Task] = set()

        for provider in registry.all():
            provider.add_tick_listener(self.handle_tick)
            provider.add_status_listener(self.handle_status)

    # ========================================================== clients
    def register_client(self, websocket: WebSocket) -> ClientSession:
        session = ClientSession(websocket, self._queue_size)
        session.writer_task = asyncio.create_task(session.writer(), name=f"ws-writer-{session.id}")
        self._clients[session.id] = session
        log.info("Client %s connected (%s total)", session.id, len(self._clients))
        session.enqueue(
            {
                "type": "welcome",
                "client_id": session.id,
                "providers": [s.model_dump(mode="json") for s in self._registry.statuses()],
                "timeframes": [tf.value for tf in Timeframe],
            }
        )
        return session

    async def unregister_client(self, session: ClientSession) -> None:
        for key in list(session.subscriptions):
            try:
                await self.unsubscribe(session, *key)
            except Exception:
                log.exception("Error cleaning up subscription %s for client %s", key, session.id)
        session.closed = True
        if session.writer_task is not None:
            session.writer_task.cancel()
            try:
                await session.writer_task
            except (asyncio.CancelledError, Exception):
                pass
        self._clients.pop(session.id, None)
        log.info("Client %s disconnected (%s remaining)", session.id, len(self._clients))

    # ===================================================== subscriptions
    async def subscribe(
        self, session: ClientSession, provider_name: str, symbol: str, timeframe: Timeframe
    ) -> Dict[str, Any]:
        provider = self._registry.get(provider_name)
        provider_name = provider.name
        if not provider.is_connected:
            raise ProviderUnavailableError(f"Provider '{provider_name}' is not connected: {provider.status().message}")

        # Each provider decides its canonical spelling (simulator upper-cases, MT5 keeps case).
        symbol = provider.normalize_symbol(symbol)
        feed: FeedKey = (provider_name, symbol)
        key: SubKey = (provider_name, symbol, timeframe.value)

        async with self._lock:
            if key in session.subscriptions:
                agg = self._aggregators.get(feed, {}).get(timeframe.value)
                return self._subscribed_message(key, agg)

            # 1) Start the provider stream if nobody was watching this symbol yet.
            if not self._feed_clients.get(feed):
                await provider.acquire(symbol)
                self._feed_clients[feed] = set()

            try:
                # 2) Create the candle aggregator seeded with the latest real bar.
                tf_map = self._aggregators.setdefault(feed, {})
                agg = tf_map.get(timeframe.value)
                if agg is None:
                    seed_bars = await provider.get_history(symbol, timeframe, 1)
                    agg = CandleAggregator(timeframe, seed_bars[-1] if seed_bars else None)
                    tf_map[timeframe.value] = agg
            except Exception:
                # roll back the provider subscription if we were its only user
                if not self._feed_clients.get(feed):
                    self._feed_clients.pop(feed, None)
                    self._aggregators.pop(feed, None)
                    await provider.release(symbol)
                raise

            # 3) Register the client.
            self._feed_clients[feed].add(session.id)
            self._sub_clients.setdefault(key, set()).add(session.id)
            session.subscriptions.add(key)

        log.info("Client %s subscribed %s/%s/%s", session.id, *key)
        return self._subscribed_message(key, agg)

    async def unsubscribe(
        self, session: ClientSession, provider_name: str, symbol: str, timeframe: Any
    ) -> Dict[str, Any]:
        provider = self._registry.get(provider_name)
        provider_name = provider.name
        tf_value = timeframe.value if isinstance(timeframe, Timeframe) else str(timeframe)
        try:
            symbol = provider.normalize_symbol(symbol)
        except DataBridgeError:
            symbol = symbol.strip()
        feed: FeedKey = (provider_name, symbol)
        key: SubKey = (provider_name, symbol, tf_value)

        async with self._lock:
            if key not in session.subscriptions:
                return {"type": "unsubscribed", "provider": provider_name, "symbol": symbol,
                        "timeframe": tf_value, "note": "was not subscribed"}
            session.subscriptions.discard(key)

            subs = self._sub_clients.get(key, set())
            subs.discard(session.id)
            if not subs:
                self._sub_clients.pop(key, None)
                self._aggregators.get(feed, {}).pop(tf_value, None)

            still_on_feed = any(k[:2] == feed for k in session.subscriptions)
            if not still_on_feed:
                clients = self._feed_clients.get(feed, set())
                clients.discard(session.id)
                if not clients:
                    self._feed_clients.pop(feed, None)
                    self._aggregators.pop(feed, None)
                    try:
                        await provider.release(symbol)
                    except Exception:
                        log.exception("Provider unsubscribe failed for %s", feed)

        log.info("Client %s unsubscribed %s/%s/%s", session.id, *key)
        return {"type": "unsubscribed", "provider": provider_name, "symbol": symbol, "timeframe": tf_value}

    def list_subscriptions(self, session: ClientSession) -> Dict[str, Any]:
        return {
            "type": "subscriptions",
            "items": [
                {"provider": p, "symbol": s, "timeframe": tf} for (p, s, tf) in sorted(session.subscriptions)
            ],
        }

    @staticmethod
    def _subscribed_message(key: SubKey, agg: Optional[CandleAggregator]) -> Dict[str, Any]:
        current = agg.current if agg is not None else None
        return {
            "type": "subscribed",
            "provider": key[0],
            "symbol": key[1],
            "timeframe": key[2],
            "candle": current.model_dump(mode="json") if current is not None else None,
        }

    # ================================================= provider callbacks
    async def handle_tick(self, tick: Tick) -> None:
        feed: FeedKey = (tick.provider, tick.symbol)
        client_ids = self._feed_clients.get(feed)
        if not client_ids:
            return

        tick_msg = {"type": "tick", "data": tick.model_dump(mode="json")}
        for cid in list(client_ids):
            session = self._clients.get(cid)
            if session is not None:
                session.enqueue(tick_msg)

        for tf_value, agg in list(self._aggregators.get(feed, {}).items()):
            candle = agg.update(tick.price, tick.time_ms)
            if candle is None:
                continue
            candle_msg = {
                "type": "candle",
                "provider": tick.provider,
                "symbol": tick.symbol,
                "timeframe": tf_value,
                "data": candle.model_dump(mode="json"),
            }
            for cid in list(self._sub_clients.get((tick.provider, tick.symbol, tf_value), ())):
                session = self._clients.get(cid)
                if session is not None:
                    session.enqueue(candle_msg)

    async def handle_status(self, status: ProviderStatus) -> None:
        message = {"type": "status", "data": status.model_dump(mode="json")}
        for session in list(self._clients.values()):
            session.enqueue(message)

        # Failover provider switched source (or the MT5 time offset changed): the live
        # candles were seeded from the old source, so re-seed them from the new one.
        if "active_source" in status.details:
            key = (status.details.get("active_source"), status.details.get("time_shift_hours"))
            previous = self._source_keys.get(status.name)
            self._source_keys[status.name] = key
            if previous is not None and previous != key and key[0] is not None:
                task = asyncio.create_task(self._reseed(status.name), name=f"reseed-{status.name}")
                self._background.add(task)
                task.add_done_callback(self._background.discard)

    async def _reseed(self, provider_name: str) -> None:
        try:
            provider = self._registry.get(provider_name)
        except DataBridgeError:
            return
        for (p, symbol), tf_map in list(self._aggregators.items()):
            if p != provider_name:
                continue
            for tf_value in list(tf_map):
                try:
                    bars = await provider.get_history(symbol, Timeframe(tf_value), 1)
                except DataBridgeError as exc:
                    log.warning("Re-seed of %s/%s/%s failed: %s", p, symbol, tf_value, exc.message)
                    continue
                current = self._aggregators.get((p, symbol))
                if current is not None and tf_value in current:
                    current[tf_value] = CandleAggregator(Timeframe(tf_value), bars[-1] if bars else None)
        log.info("Re-seeded live candles for provider '%s'", provider_name)

    # ========================================================== shutdown
    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()
        for session in list(self._clients.values()):
            try:
                if session.websocket.client_state == WebSocketState.CONNECTED:
                    await session.websocket.close(code=1001, reason="Server shutting down")
            except Exception:
                pass
            await self.unregister_client(session)

    def stats(self) -> Dict[str, Any]:
        return {
            "clients": len(self._clients),
            "feeds": [f"{p}:{s}" for (p, s) in self._feed_clients],
            "candle_streams": [f"{p}:{s}:{tf}" for (p, s, tf) in self._sub_clients],
        }

    def client_ids(self) -> List[int]:
        return list(self._clients)
