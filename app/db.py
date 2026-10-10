from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Boolean, DateTime, Integer, JSON, create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

_SCHEMA_LOCK_KEY = 724_001  # serialises schema setup when several server processes boot together


def normalize_url(url: str) -> str:
    """Accept the URL forms hosting platforms hand out and select the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Database:
    def __init__(self, url: str, pool_size: int = 10, max_overflow: int = 20):
        url = normalize_url(url)
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        if self.is_sqlite:
            kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        else:
            # pre_ping drops connections a restarted server or a proxy has silently closed.
            kwargs = {"pool_size": pool_size, "max_overflow": max_overflow, "pool_pre_ping": True,
                      "pool_recycle": 1800}
        self.engine: Engine = create_engine(url, future=True, **kwargs)
        if self.is_sqlite:
            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _record):  # pragma: no cover - driver hook
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.close()

        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
            Base.metadata.create_all(conn)
            self._add_missing_columns(conn)

    def _column_default(self, col) -> str:
        """DEFAULT clause that lets ALTER TABLE add a NOT NULL column to a table that already has rows."""
        if col.nullable:
            return ""
        if isinstance(col.type, Boolean):
            return " DEFAULT false" if self.engine.dialect.name == "postgresql" else " DEFAULT 0"
        if isinstance(col.type, Integer):
            return " DEFAULT 0"
        if isinstance(col.type, JSON):
            return " DEFAULT '{}'"
        if isinstance(col.type, DateTime):
            return " DEFAULT CURRENT_TIMESTAMP"
        return " DEFAULT ''"

    def _add_missing_columns(self, conn: Connection) -> None:
        """create_all never alters existing tables; add columns introduced after a database was first created."""
        insp = inspect(conn)
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=self.engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{self._column_default(col)}'))

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
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
