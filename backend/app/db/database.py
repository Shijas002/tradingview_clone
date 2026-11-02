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

from sqlalchemy import create_engine, event, inspect, text
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
        self._add_missing_columns()
        log.info("Database ready: %s", self.url)

    def _add_missing_columns(self) -> None:
        """
        Tiny additive migration: columns added to a model in a later version are
        added to the existing table (with their default), so older databases keep
        working without a migration tool. Columns are never removed or changed.
        """
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                existing = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in existing:
                        continue
                    ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(dialect=self.engine.dialect)}'
                    default = getattr(col.default, "arg", None) if col.default is not None else None
                    if isinstance(default, bool):
                        ddl += f" DEFAULT {1 if default else 0}"
                    elif isinstance(default, (int, float)):
                        ddl += f" DEFAULT {default}"
                    elif isinstance(default, str):
                        ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                    conn.execute(text(ddl))
                    log.info("Database upgraded: added column %s.%s", table.name, col.name)

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

