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
