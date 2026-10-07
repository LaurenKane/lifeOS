"""The application engine and session factory for the life schema.

Mirrors `finance/db.py` exactly, for the same reasons stated there: a lazy
`lru_cache`d engine, `pool_pre_ping`, and `config.pg_connect_args` pinning the
session's `search_path` — the one helper both Alembic `env.py` files and the
application engine must agree on.

This is `life/db.py` and not `core/db.py` for the same reason finance's
docstring gives: `core/` is shared primitives only, and an engine is
infrastructure that would have to hardcode a module's search_path.
"""

from __future__ import annotations

from functools import lru_cache

from config import get_settings, pg_connect_args
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from life.domain.models import SCHEMA

__all__ = ["SCHEMA", "get_engine", "get_sessionmaker"]


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide engine, built on first use. Lazy on purpose — a health
    check must run with Postgres down and say so."""
    return create_engine(
        get_settings().DATABASE_URL,
        connect_args=pg_connect_args(SCHEMA),
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """The session factory bound to `get_engine()`, `expire_on_commit=False`."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
