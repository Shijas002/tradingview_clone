"""
REST endpoints (prefix /api).

GET  /api/health                        - liveness + provider summary
GET  /api/providers                     - connection status of each provider
POST /api/providers/{name}/reconnect    - force a reconnect (MT5)
GET  /api/timeframes                    - supported timeframes
GET  /api/symbols                       - list / search symbols
GET  /api/history                       - historical candles (initial load + lazy load)
GET  /api/stream/stats                  - live subscription statistics
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request

from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, SymbolNotFoundError
from app.models.market import HistoryResponse, ProviderStatus, SymbolInfo, Timeframe
from app.providers.mt5_provider import MT5Provider
from app.providers.registry import ProviderRegistry
from app.services.stream_manager import StreamManager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["market-data"])

APP_VERSION = "0.4.0-phase4-upstox"


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def _manager(request: Request) -> StreamManager:
    return request.app.state.stream_manager


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    statuses = _registry(request).statuses()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "server_time": int(time.time()),
        "providers": {s.name: s.connected for s in statuses},
    }


@router.get("/providers", response_model=List[ProviderStatus])
async def providers(request: Request) -> List[ProviderStatus]:
    return _registry(request).statuses()


@router.post("/providers/{name}/reconnect", response_model=ProviderStatus)
async def reconnect_provider(name: str, request: Request) -> ProviderStatus:
    provider = _registry(request).get(name)
    log.info("Manual reconnect requested for '%s'", provider.name)
    if isinstance(provider, MT5Provider):
        await provider.reconnect()
    else:
        await provider.disconnect()
        await provider.connect()
    return provider.status()


@router.get("/timeframes")
async def timeframes() -> List[Dict[str, Any]]:
    return [{"value": tf.value, "label": tf.label, "seconds": tf.seconds} for tf in Timeframe]


@router.get("/symbols", response_model=List[SymbolInfo])
async def symbols(
    request: Request,
    provider: Optional[str] = Query(default=None, description="Provider name; defaults to DEFAULT_PROVIDER"),
    query: Optional[str] = Query(default=None, max_length=64, description="Case-insensitive search text"),
    limit: int = Query(default=200, ge=1, le=5000),
) -> List[SymbolInfo]:
    name = provider or _settings(request).default_provider
    return await _registry(request).get(name).get_symbols(query=query, limit=limit)


@router.get("/history", response_model=HistoryResponse)
async def history(
    request: Request,
    symbol: str = Query(..., min_length=1, max_length=64),
    timeframe: Timeframe = Query(default=Timeframe.M1),
    provider: Optional[str] = Query(default=None),
    count: Optional[int] = Query(default=None, ge=1),
    to: Optional[int] = Query(
        default=None,
        ge=0,
        description="UNIX seconds. Return bars that open strictly before this time (lazy-load older data).",
    ),
) -> HistoryResponse:
    settings = _settings(request)
    bars = count or settings.history_default_bars
    if bars > settings.history_max_bars:
        raise InvalidRequestError(f"count must be <= HISTORY_MAX_BARS ({settings.history_max_bars})")
    prov = _registry(request).get(provider or settings.default_provider)
    to_dt = datetime.fromtimestamp(to, tz=timezone.utc) if to is not None else None
    try:
        sym = prov.normalize_symbol(symbol)
    except SymbolNotFoundError:
        # Pine's request.security("OANDA:EURUSD", ...) style prefix -> try the bare name
        if ":" not in symbol:
            raise
        sym = prov.normalize_symbol(symbol.split(":", 1)[1])
    candles = await prov.get_history(sym, timeframe, bars, to=to_dt)
    return HistoryResponse(
        provider=prov.name, symbol=sym, timeframe=timeframe, count=len(candles), candles=candles
    )


@router.get("/stream/stats")
async def stream_stats(request: Request) -> Dict[str, Any]:
    return _manager(request).stats()
