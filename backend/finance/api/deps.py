"""deps.py — where a request gets a database session.

One place, so "who opened this transaction, and when does it end" has exactly
one answer in the module. Three things about it are load-bearing rather than
stylistic:

**The dependency yields a session; it does NOT commit.** A context manager that
committed on exit would be the obvious choice and it is deliberately not used
here. It
commits when its `with` block exits, which for a FastAPI dependency happens
*after* the response has been serialised — so a balance-trigger rejection at
COMMIT would be raised at the wrong moment, as a 500, after the bytes were
already on the wire. Instead the handler owns the commit:

    with session.begin():
        ...writes...

so the COMMIT that can be rejected happens while the handler can still turn it
into a status code.

**`session.begin()` must therefore be the FIRST thing a handler does.** A read
autobegins a transaction, and `Session.begin()` refuses when one is already in
progress. Handlers that both read and write put the whole request — validation
reads included — inside the one block.

**Exit rolls back.** A handler that only reads leaves an autobegun transaction
open; closing the session releases it. Nothing is left holding locks against the
next request.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from finance.db import get_sessionmaker

__all__ = ["get_session"]


def get_session() -> Iterator[Session]:
    """Yield a session. The handler decides whether it commits.

    Raises:
        Nothing. A database that is down fails when the handler's first statement
            runs, which FastAPI reports as a 500 — a diagnosable error rather
            than an unstartable process.
    """
    with get_sessionmaker()() as session:
        yield session
