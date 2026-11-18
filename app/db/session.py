"""Engine and session setup: one engine per process, rebuilt for tests."""
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """SQLite creates the database file itself, but not the folder it goes in."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    path = database_url[len(prefix):]
    if not path or path == ":memory:":
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def configure_engine(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    """Create the engine and session factory for database_url.

    Called with settings.database_url on first use, and with a fresh
    temporary database in tests (see tests/conftest.py).
    """
    global _engine, _session_factory

    connect_args = {}
    if database_url.startswith("sqlite"):
        _ensure_sqlite_parent_dir(database_url)
        # The app runs in a different thread than the tests that use it.
        connect_args = {"check_same_thread": False}

    engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):
        # SQLite only enforces foreign keys when told to, per connection.
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    factory = sessionmaker(bind=engine)
    _engine, _session_factory = engine, factory
    return engine, factory


def get_engine() -> Engine:
    engine = _engine
    if engine is None:
        engine, _ = configure_engine(settings.database_url)
    return engine


def get_session_factory() -> sessionmaker[Session]:
    factory = _session_factory
    if factory is None:
        _, factory = configure_engine(settings.database_url)
    return factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables on the current engine. No Alembic."""
    Base.metadata.create_all(get_engine())
