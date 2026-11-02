"""
ORM models.

JSON columns keep the frontend's structures (panes, indicators, drawings) as-is,
so the UI can evolve without schema migrations.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def now_ms() -> int:
    return int(time.time() * 1000)


class Workspace(Base):
    """Layout, panes (symbol/timeframe/indicators), watchlist and UI settings."""

    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ms)


class DrawingSet(Base):
    """All drawings of one symbol, key = "provider:symbol" (lower-case)."""

    __tablename__ = "drawings"
    symbol_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    drawings: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ms)


class Script(Base):
    """User Pine scripts."""

    __tablename__ = "scripts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ms)


class Alert(Base):
    """Server-side price / trend-line alert, evaluated on every tick."""

    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    provider: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(80))
    condition: Mapped[str] = mapped_column(String(32))  # see app.services.alert_engine.CONDITIONS
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    line: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)  # trend line {t1,p1,t2,p2,extend}
    trigger: Mapped[str] = mapped_column(String(20), default="once")  # once | once_per_minute | every_time
    message: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # ms
    drawing_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    last_triggered_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | triggered | expired | paused
    # --- indicator / Pine alerts (v1.1) ---
    kind: Mapped[str] = mapped_column(String(12), default="price")  # price | indicator
    timeframe: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    script_name: Mapped[str] = mapped_column(String(200), default="")
    script_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    script_inputs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    target: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)  # "plot:RSI" | "plot2:..." | "alertcondition:Title" | "alert"
    target2: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)  # compare against another plot ("plot:Signal")
    frequency: Mapped[str] = mapped_column(String(20), default="once_per_bar_close")  # once | once_per_bar | once_per_bar_close
    last_bar_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notify_telegram: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # e.g. Pine script error


class AlertEvent(Base):
    """History of triggered alerts."""

    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(String(64), ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    triggered_at: Mapped[int] = mapped_column(Integer, default=now_ms, index=True)
    price: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(Text, default="")


class JournalEntry(Base):
    """
    One trade in the journal (Phase 5). Kept locally first; mirrored to Notion
    when NOTION_API_KEY is configured. Prices/quantities are in the symbol's units.
    """

    __tablename__ = "journal_entries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ms, index=True)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    provider: Mapped[str] = mapped_column(String(40), default="")
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="")
    side: Mapped[str] = mapped_column(String(8))  # long | short
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    entry_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # UNIX seconds
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    exit_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    setup: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5 execution quality
    notes: Mapped[str] = mapped_column(Text, default="")
    screenshot: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # file name in data/journal/
    screenshot_version: Mapped[int] = mapped_column(Integer, default=0)
    # Notion mirror
    notion_page_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notion_status: Mapped[str] = mapped_column(String(12), default="local")  # local | pending | synced | error | deleting
    notion_error: Mapped[str] = mapped_column(Text, default="")
    notion_synced_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notion_screenshot_version: Mapped[int] = mapped_column(Integer, default=0)
    notion_image_block_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notion_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # --- v1.1: imported / paper trades ---
    source: Mapped[str] = mapped_column(String(12), default="manual")  # manual | paper | mt5 | upstox
    external_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    broker_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # net P&L reported by the broker (account currency)


# ---------------------------------------------------------------------------
# v1.1 - candle cache
# ---------------------------------------------------------------------------
class CandleBar(Base):
    """Cached OHLCV bar, so charts reopen instantly and providers are asked only for new data."""

    __tablename__ = "candle_cache"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class CandleCoverage(Base):
    """A time range [start, end] for which every bar of a series is in candle_cache."""

    __tablename__ = "candle_coverage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    at_start: Mapped[bool] = mapped_column(Boolean, default=False)  # no older bars exist at the provider
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ms)


# ---------------------------------------------------------------------------
# v1.1 - paper trading
# ---------------------------------------------------------------------------
class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    initial_balance: Mapped[float] = mapped_column(Float, default=100_000.0)
    balance: Mapped[float] = mapped_column(Float, default=100_000.0)  # cash incl. realized P&L and commissions
    commission_pct: Mapped[float] = mapped_column(Float, default=0.0)  # % of traded notional per fill
    created_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    reset_at: Mapped[int] = mapped_column(Integer, default=now_ms)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    type: Mapped[str] = mapped_column(String(8))  # market | limit | stop
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # limit / stop price
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # attached to the resulting position
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="working")  # working | filled | cancelled | rejected
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    filled_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)  # P&L this fill realized (closing part)
    note: Mapped[str] = mapped_column(String(40), default="")  # e.g. "stop loss", "take profit", "close"


class PaperPosition(Base):
    """Net position per symbol (like TradingView paper trading). Closed positions are kept as history."""

    __tablename__ = "paper_positions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    side: Mapped[str] = mapped_column(String(6))  # long | short
    qty: Mapped[float] = mapped_column(Float)  # current open quantity (0 when closed)
    max_qty: Mapped[float] = mapped_column(Float, default=0.0)  # largest size held (for the journal)
    avg_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    opened_at: Mapped[int] = mapped_column(Integer, default=now_ms)
    closed_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    exit_value: Mapped[float] = mapped_column(Float, default=0.0)  # sum(exit price * qty) -> average exit
    exit_qty: Mapped[float] = mapped_column(Float, default=0.0)
    journal_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="")
