from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .base import Base


def sqlite_url(path: str | Path) -> str:
    """Build a SQLAlchemy URL for a local SQLite database file."""

    resolved = Path(path).resolve()
    return f"sqlite+pysqlite:///{resolved.as_posix()}"


def create_database_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_pre_ping: bool = True,
    **engine_options: Any,
) -> Engine:
    """Create a PostgreSQL/SQLite compatible engine with safe defaults."""

    url = make_url(database_url)
    options: dict[str, Any] = {
        "echo": echo,
        "pool_pre_ping": pool_pre_ping,
    }
    options.update(engine_options)

    if url.get_backend_name() == "sqlite":
        connect_args = {"check_same_thread": False, **options.pop("connect_args", {})}
        options["connect_args"] = connect_args
        if url.database in (None, "", ":memory:"):
            options.setdefault("poolclass", StaticPool)

    engine = create_engine(database_url, **options)

    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    """Create the schema for tests/local bootstrapping.

    Production deployments should apply Alembic migrations instead.
    """

    from . import models as _models  # noqa: F401 -- registers mappings

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit a unit of work, rolling it back on any exception."""

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_dependency(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """FastAPI-compatible session dependency."""

    with session_scope(factory) as session:
        yield session
