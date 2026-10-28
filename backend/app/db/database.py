"""
SQLAlchemy engine/session setup for the local SQLite database.

The database lives at backend/data/tradingview.db by default (DATABASE_URL).
Tables are created automatically on startup; the schema is small and additive,
so new tables in later phases appear without manual migrations.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        is_sqlite = url.startswith("sqlite")
        connect_args = {"check_same_thread": False, "timeout": 15} if is_sqlite else {}
        self.engine: Engine = create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)
        if is_sqlite:

            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        self._sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        # import models so they register with Base.metadata
        from app.db import models  # noqa: F401

        Base.metadata.create_all(self.engine)
        log.info("Database ready: %s", self.url)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s: Session = self._sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()

