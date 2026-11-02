"""
Paper trading API (v1.1, prefix /api/paper).

GET    /api/paper/account                 balance, equity, open positions, working orders, stats
GET    /api/paper/history?limit=          filled / cancelled orders and closed positions
POST   /api/paper/orders                  place an order (market | limit | stop, optional SL/TP)
PUT    /api/paper/orders/{id}             modify a working order (qty, price, SL, TP)
DELETE /api/paper/orders/{id}             cancel a working order
PUT    /api/paper/positions/{id}          change a position's SL / TP
POST   /api/paper/positions/{id}/close    close (all, or "qty" of) a position at market
POST   /api/paper/close-all               flatten every position
POST   /api/paper/reset                   new account (balance, currency, commission)
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.core.exceptions import InvalidRequestError
from app.services.paper_broker import PaperBroker

router = APIRouter(prefix="/api/paper", tags=["paper trading"])


def _broker(request: Request) -> PaperBroker:
    return request.app.state.paper


class OrderIn(BaseModel):
    provider: str = Field(..., min_length=1, max_length=40)
    symbol: str = Field(..., min_length=1, max_length=80)
    side: Literal["buy", "sell"]
    type: Literal["market", "limit", "stop"] = "market"
    qty: float = Field(..., gt=0)
    price: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)


class OrderPatch(BaseModel):
    qty: Optional[float] = Field(default=None, gt=0)
    price: Optional[float] = Field(default=None, gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    clear: List[Literal["stop_loss", "take_profit"]] = Field(default_factory=list)


class PositionPatch(BaseModel):
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    clear: List[Literal["stop_loss", "take_profit"]] = Field(default_factory=list)


class CloseIn(BaseModel):
    qty: Optional[float] = Field(default=None, gt=0)


class ResetIn(BaseModel):
    balance: Optional[float] = Field(default=None, gt=0, le=1e12)
    currency: Optional[str] = Field(default=None, min_length=1, max_length=8)
    commission_pct: Optional[float] = Field(default=None, ge=0, le=5)


@router.get("/account")
async def account(request: Request) -> Dict[str, Any]:
    return await _broker(request).account()


@router.get("/history")
async def history(request: Request, limit: int = Query(default=200, ge=1, le=2000)) -> Dict[str, Any]:
    return await _broker(request).history(limit)


@router.post("/orders", status_code=201)
async def place(body: OrderIn, request: Request) -> Dict[str, Any]:
    if body.type != "market" and body.price is None:
        raise InvalidRequestError(f"A {body.type} order needs a price")
    return await _broker(request).place_order(body.provider, body.symbol, body.side, body.type, body.qty, body.price,
                                              body.stop_loss, body.take_profit)


@router.put("/orders/{order_id}")
async def modify(order_id: str, body: OrderPatch, request: Request) -> Dict[str, Any]:
    return await _broker(request).modify_order(order_id, body.qty, body.price, body.stop_loss, body.take_profit, list(body.clear))


@router.delete("/orders/{order_id}")
async def cancel(order_id: str, request: Request) -> Dict[str, Any]:
    return await _broker(request).cancel_order(order_id)


@router.put("/positions/{position_id}")
async def modify_position(position_id: str, body: PositionPatch, request: Request) -> Dict[str, Any]:
    return await _broker(request).modify_position(position_id, body.stop_loss, body.take_profit, list(body.clear))


@router.post("/positions/{position_id}/close")
async def close_position(position_id: str, body: CloseIn, request: Request) -> Dict[str, Any]:
    return await _broker(request).close_position(position_id, body.qty)


@router.post("/close-all")
async def close_all(request: Request) -> Dict[str, Any]:
    return {"closed": await _broker(request).close_all()}


@router.post("/reset")
async def reset(body: ResetIn, request: Request) -> Dict[str, Any]:
    return await _broker(request).reset(body.balance, body.currency, body.commission_pct)
