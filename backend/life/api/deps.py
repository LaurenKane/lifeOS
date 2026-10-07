"""deps.py — where a request gets a database session.

Mirrors `finance/api/deps.py` and copies its reasoning verbatim, because the
failure it guards against is the same: a context manager that commits when
the `with` block exits would push an at-COMMIT rejection AFTER the response
was serialised — a 500 instead of a status code. Here the handler owns the
commit:

    with session.begin():
        ...writes...

so a rejected COMMIT becomes a 409/422 while bytes are still pending.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from life.db import get_sessionmaker

__all__ = ["get_session"]


def get_session() -> Iterator[Session]:
    """Yield a session. The handler decides whether it commits."""
    with get_sessionmaker()() as session:
        yield session
