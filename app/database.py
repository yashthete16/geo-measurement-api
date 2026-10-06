"""Engine / session plumbing."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    kwargs: dict = {}

    if url.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
        if url.database in (None, "", ":memory:"):
            # One shared connection so an in-memory DB is visible to every request/thread.
            kwargs["poolclass"] = StaticPool
        else:
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, **kwargs)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
