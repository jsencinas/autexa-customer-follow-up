"""Database engine, session factory and declarative base."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.timeutils import ensure_utc


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as naive UTC and always returns aware UTC (portable across SQLite/PostgreSQL)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def make_engine(url: str) -> Engine:
    """Create an engine; for SQLite, create the parent directory and enable foreign keys."""
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_db(url: str) -> sessionmaker[Session]:
    """Initialise the global engine/session factory and create all tables."""
    global _engine, _session_factory
    import app.models.db_models  # noqa: F401  (register models)

    _engine = make_engine(url)
    Base.metadata.create_all(_engine)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _session_factory


def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory, initialising it from settings if needed."""
    if _session_factory is None:
        from app.config import get_settings

        return init_db(get_settings().database_url)
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is closed after the request."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
