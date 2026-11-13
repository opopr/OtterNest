"""Database connection; PostgreSQL is required outside tests."""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def configure_database(url: str | None = None) -> None:
    global _engine, _session_factory
    url = url or os.environ.get("OTTERNEST_DATABASE_URL")
    if not url:
        raise RuntimeError("Set OTTERNEST_DATABASE_URL before starting OtterNest")
    _engine = create_engine(url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    with _session_factory() as session:
        yield session


def get_engine():
    if _engine is None:
        configure_database()
    return _engine
