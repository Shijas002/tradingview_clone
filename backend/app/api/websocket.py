"""
WebSocket endpoint: ws://<host>:<port>/ws/market

Client -> server (JSON):
  {"action": "subscribe",   "provider": "mt5", "symbol": "EURUSD", "timeframe": "M1"}
  {"action": "unsubscribe", "provider": "mt5", "symbol": "EURUSD", "timeframe": "M1"}
  {"action": "list_subscriptions"}
  {"action": "ping", "ts": 1700000000000}

Server -> client (JSON):
  {"type": "welcome", "client_id": 1, "providers": [...], "timeframes": [...]}
  {"type": "subscribed", "provider", "symbol", "timeframe", "candle": {...} | null}
  {"type": "unsubscribed", "provider", "symbol", "timeframe"}
  {"type": "tick",   "data": {provider, symbol, time_ms, bid, ask, last, volume, price}}
  {"type": "candle", "provider", "symbol", "timeframe", "data": {time, open, high, low, close, volume}}
  {"type": "status", "data": {name, connected, message, details}}
  {"type": "subscriptions", "items": [...]}
  {"type": "pong", "ts": ..., "server_time_ms": ...}
  {"type": "error", "message": "...", "request": {...}}
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.core.exceptions import DataBridgeError
from app.models.market import (
    ClientMessage,
    ListSubscriptionsMessage,
    PingMessage,
    SubscribeMessage,
    UnsubscribeMessage,
)
from app.services.stream_manager import ClientSession, StreamManager

log = logging.getLogger(__name__)
router = APIRouter()

_client_message_adapter: TypeAdapter = TypeAdapter(ClientMessage)
MAX_MESSAGE_BYTES = 16 * 1024


def _error(message: str, request: Any = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"type": "error", "message": message}
    if request is not None:
        payload["request"] = request
    return payload


async def _dispatch(manager: StreamManager, session: ClientSession, raw: str) -> None:
    if len(raw) > MAX_MESSAGE_BYTES:
        session.enqueue(_error("Message too large"))
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        session.enqueue(_error("Invalid JSON"))
        return
    try:
        msg = _client_message_adapter.validate_python(data)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        session.enqueue(_error(f"Invalid message: {details}", data))
        return

    try:
        if isinstance(msg, SubscribeMessage):
            reply = await manager.subscribe(session, msg.provider, msg.symbol, msg.timeframe)
        elif isinstance(msg, UnsubscribeMessage):
            reply = await manager.unsubscribe(session, msg.provider, msg.symbol, msg.timeframe)
        elif isinstance(msg, ListSubscriptionsMessage):
            reply = manager.list_subscriptions(session)
        elif isinstance(msg, PingMessage):
            reply = {"type": "pong", "ts": msg.ts, "server_time_ms": int(time.time() * 1000)}
        else:  # pragma: no cover - union is exhaustive
            reply = _error("Unsupported action", data)
    except DataBridgeError as exc:
        reply = _error(exc.message, data)
    except Exception as exc:
        log.exception("Unhandled error processing message from client %s: %s", session.id, data)
        reply = _error(f"Internal error: {type(exc).__name__}", data)
    session.enqueue(reply)


@router.websocket("/ws/market")
async def market_stream(websocket: WebSocket) -> None:
    manager: StreamManager = websocket.app.state.stream_manager
    await websocket.accept()
    session = manager.register_client(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            await _dispatch(manager, session, raw)
    except WebSocketDisconnect as exc:
        log.debug("Client %s closed the socket (code=%s)", session.id, exc.code)
    except RuntimeError as exc:  # receive after close
        log.debug("Client %s socket runtime error: %s", session.id, exc)
    except Exception:
        log.exception("Unexpected WebSocket error for client %s", session.id)
    finally:
        await manager.unregister_client(session)
