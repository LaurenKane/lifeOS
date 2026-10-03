"""The application engine and session factory for the finance schema.

Where this file lives is a decision, not a convenience. It is `finance/db.py`
and NOT `core/db.py`, because `core/` is shared primitives only
(ARCHITECTURE.md §2) and an `Engine` is infrastructure. A `core` engine would
have to hardcode finance's `search_path`, which makes `core` know a module's
schema - precisely the coupling rule 1 in `backend/core/__init__.py` exists to
prevent. That rule is a convention with no automated gate, which is the reason
this placement has to be defended here rather than left to a linter.

The engine is LAZY, and that is load-bearing rather than tidy. A health check has
to be able to run with Postgres DOWN and say so. An engine built at module
import time connects eagerly and breaks that with a traceback that says nothing
about the thing being tested. `@lru_cache(maxsize=1)` with the `create_engine`
call inside the function means importing this module costs nothing and touches
nothing.
"""

from __future__ import annotations

from functools import lru_cache

from config import get_settings, pg_connect_args
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance.domain.models import SCHEMA

__all__ = ["SCHEMA", "get_engine", "get_sessionmaker"]


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide engine. Built on first use, cached after that.

    Lazy on purpose - see the module docstring. `lru_cache` rather than a
    module-level singleton so that a test can clear it, and so the object is
    only ever built once per process no matter how many callers ask.
    """
    return create_engine(
        get_settings().DATABASE_URL,
        # The same search_path both Alembic env.py files pin. See
        # config.pg_connect_args for why all three have to agree.
        connect_args=pg_connect_args(SCHEMA),
        # Every connection carries its own transaction state, and nothing here
        # is worth a pool across processes. A wrong `search_path` in a pooled
        # connection would also outlive the setting that caused it.
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """The session factory bound to `get_engine()`.

    `expire_on_commit=False`: without it, every attribute is re-read from the
    database after a commit, which costs a round trip per row to re-fetch values
    already in hand.
    """
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
