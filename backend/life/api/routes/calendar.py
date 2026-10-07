"""calendar router — the fenced ICS feed (ADR 0013).

One GET, `/life/calendar/{token}`: all-day events for open Actions that carry
a `due_date` — dates and titles and nothing else. The fence is layered:

- *Token:* `LIFEOS_ICS_TOKEN` in the environment. When unset the feed is OFF
  and a wrong token answers the SAME 404 as off — one answer, no oracle for
  guessing. The token is never written in this repository (SAFETY.md rule 3).
- *Tripwire:* `MinuteGate` below caps reads per remote host per minute. It is
  a tripwire, NOT a boundary — process memory resets on restart and no lock
  state persists. The real fences are the token and the tailnet (ADR 0013).
- *Content:* DESCRIPTION is withheld — a calendar is where dates live, not
  where the pile lives. The response carries `Cache-Control: private,
  no-store` because dates change and nothing here is worth a stale copy.

Read-only, GET only, never any other module's data. This is the one route
whose reply is not JSON — `text/calendar` is the contract here — so it uses
`Response` directly instead of a schema from `life.api.schemas`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated

from config import get_settings
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.domain.models.action import Action
from life.domain.services.calendar_feed import FeedFact, compose_feed

router = APIRouter(tags=["life"], prefix="/life/calendar")

SessionDep = Annotated[Session, Depends(get_session)]

#: The tripwire's cap: reads per remote host per minute. Generous enough that
#: a calendar client's own schedule never brushes against it.
GATE_CAP_PER_MINUTE: int = 60


class MinuteGate:
    """A per-key sliding-minute counter — a tripwire, not a boundary.

    Keys are the request's remote host; hits older than one minute fall out
    of the window on sight. Cap and clock are constructor parameters so tests
    can inject a fake clock instead of waiting on the real one. Honest about
    its shape (ADR 0013): process memory resets on restart, and a limit
    exceeded answers 429 briefly — no lock state is persisted anywhere.
    """

    def __init__(
        self,
        *,
        cap: int = GATE_CAP_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cap = cap
        self._clock = clock
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        """Record one hit for `key` and raise 429 when over cap."""
        now = self._clock()
        window_start = now - 60.0
        recent = [t for t in self._hits.get(key, []) if t > window_start]
        if len(recent) >= self._cap:
            self._hits[key] = recent
            raise HTTPException(status_code=429)
        recent.append(now)
        self._hits[key] = recent


#: The module-level instance the route uses; replaced only in tests.
minute_gate = MinuteGate()


@router.get(
    "/{token}",
    summary="The fenced ICS feed of dated open Actions",
)
def calendar_feed(token: str, request: Request, session: SessionDep) -> Response:
    """Serve the calendar the user subscribed a phone to, if the token opens it."""
    # Before the DB: a gate that is not a boundary still keeps idle guessing
    # from turning into queries.
    gate_key = request.client.host if request.client else "unknown"
    minute_gate.check(gate_key)

    settings = get_settings()
    if not settings.ICS_TOKEN or token != settings.ICS_TOKEN:
        # One answer for off and wrong: no oracle for telling them apart.
        raise HTTPException(status_code=404)

    with session.begin():
        rows = session.execute(
            select(Action.id, Action.title, Action.due_date)
            .where(Action.is_done.is_(False), Action.due_date.is_not(None))
            .order_by(Action.due_date, Action.created_at)
        ).all()

    ics = compose_feed(
        [
            FeedFact(action_id=row.id, title=row.title, due_date=row.due_date)
            for row in rows
        ],
        now_utc=datetime.now(timezone.utc),
    )
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Cache-Control": "private, no-store"},
    )
