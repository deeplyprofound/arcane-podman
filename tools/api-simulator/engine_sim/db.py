"""SQLAlchemy engine + session for the simulator's state store.

The fake engine's state (containers, volumes, networks, images, exec, events)
lives in a real relational DB — SQLite, in-memory by default — so the store is
queryable and behaves with real transactional state rather than ad-hoc dicts.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_sessionmaker(url: str = "sqlite+pysqlite:///:memory:") -> sessionmaker[Session]:
    # uvicorn runs sync routes across a thread pool. For SQLite (esp. :memory:)
    # a StaticPool + check_same_thread=False shares ONE connection across all
    # threads, so the seeded schema/state is visible everywhere. Without this,
    # :memory: gives each thread its own empty DB ("no such table").
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(maker: sessionmaker[Session]) -> Iterator[Session]:
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
