"""The application engine and session factory for the finance schema.

Where this file lives is a decision, not a convenience. It is `finance/db.py`
and NOT `core/db.py`, because `core/` is shared primitives only
(ARCHITECTURE.md §2) and an `Engine` is infrastructure. A `core` engine would
have to hardcode finance's `search_path`, which makes `core` know a module's
schema - precisely the coupling `.importlinter` contract 0 exists to prevent.
Contract 0 would not catch that mistake, which is the reason it should not be
done rather than a reason it is safe.

The engine is LAZY, and that is load-bearing rather than tidy.
`backend/tests/test_import_smoke.py` asserts that `worker.health_check()` runs
with Postgres DOWN, and `worker.py` is deliberately DB-free at import time. An
engine built at module import time connects eagerly and breaks that test with a
traceback that says nothing about the thing being tested. `@lru_cache(maxsize=1)`
with the `create_engine` call inside the function means importing this module
costs nothing and touches nothing.

Consumers take sessions from `session_scope()`, which is the only place that
knows when a transaction ends.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from config import get_settings, pg_connect_args
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from finance.domain.models import SCHEMA

__all__ = ["SCHEMA", "get_engine", "get_sessionmaker", "session_scope"]


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

    `expire_on_commit=False`: after a commit the session is closed by
    `session_scope`, and re-reading every attribute from the database on the
    way out costs a round trip per row to re-fetch values already in hand.
    """
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session with a transaction, committed on success and rolled back on
    any exception.

    The commit lives here rather than in a middleware or a dependency, so that
    "did this unit of work happen" is answered in exactly one place. Note the
    asymmetry that the ledger imposes: a balanced journal entry is only checked
    at COMMIT, by the deferrable trigger, so an entry written here and
    committed here is validated here - and a caller that wants to know whether
    it balanced has to be inside this context manager, not after it.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
