"""digest.py — gather the day's facts, compose the message, push it.

The fact-gathering mirrors the today route's selection logic exactly — the
same queries, the same service helpers (`choose_do_now`, `visible_on_today`),
the same horizon — because a push that disagreed with the Dashboard about
what today holds would be worse than no push. This module does not restyle
the route: the route's `_upkeep_summaries` is imported, not copied, so
cadence facts have exactly one computation.

Display strings are composed HERE: the dataclasses that travel to
`life.domain.services.digest` carry display-ready strings, so the pure
composer stays free of database types and row objects.
"""

from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from life.api.ntfy import NtfyConfig, post_to_ntfy
from life.api.routes.today import _upkeep_summaries
from life.domain.models.action import Action
from life.domain.services.digest import (
    DigestFacts,
    DigestMessage,
    UpcomingFact,
    compose_digest,
)
from life.domain.services.today import ActionRow, choose_do_now
from life.domain.services.upkeep_state import visible_on_today
from life.local import local_today

__all__ = ["compose_today_digest", "gather_digest_facts", "send_digest"]

#: The same horizon the today route serves (`upcoming_days=3`): the digest
#: speaks the same "upcoming" strip the Dashboard shows.
_UPCOMING_DAYS: int = 3


def _upkeep_display(title: str, aim_days: int | None) -> str:
    """One upkeep as display text: the title, plus its aim when it has one."""
    if aim_days is None:
        return title
    return f"{title} (aim {aim_days} days)"


def gather_digest_facts(session: Session, *, today_date: dt.date) -> DigestFacts:
    """The day's board as display-ready strings, picked exactly as the route
    picks it.

    Args:
        session: The caller's session. Not begun or committed here — this is
            a read, run inside the caller's transaction.
        today_date: The user's local day. Passed in rather than read from the
            clock so the facts and the composed title can never straddle
            midnight.

    Returns:
        One `DigestFacts`, sections in the message's fixed order.
    """
    open_actions = (
        session.execute(
            select(Action)
            .where(Action.is_done.is_(False))
            .order_by(Action.created_at, Action.id)
        )
        .scalars()
        .all()
    )

    board = choose_do_now(
        [
            ActionRow(
                id=a.id,
                urgent=a.urgent,
                is_done=a.is_done,
                parent_action_id=a.parent_action_id,
                due_date=a.due_date,
                planned_date=a.planned_date,
                order_index=i,
            )
            for i, a in enumerate(open_actions)
        ],
        today=today_date,
    )
    # The board's own order (earliest real-world first via `choose_do_now`),
    # not the capture order — the push must agree with the Dashboard's
    # display, top line down.
    by_id = {a.id: a for a in open_actions}
    do_now = [by_id[i] for i in board.selected]
    urgent = [by_id[i] for i in board.urgent_ids]

    horizon = today_date + dt.timedelta(days=_UPCOMING_DAYS)
    upcoming: list[tuple[str, dt.date]] = []
    for a in open_actions:
        if (
            a.due_date is not None
            and today_date < a.due_date <= horizon
            and a.id not in board.selected
            and a.id not in board.urgent_ids
        ):
            upcoming.append((a.title, a.due_date))

    upkeep_opportunities = [
        summary
        for summary in _upkeep_summaries(session)
        if visible_on_today(summary.next_opportunity, today=today_date)
    ]

    return DigestFacts(
        do_now=[a.title for a in do_now],
        urgent=[a.title for a in urgent],
        kept_back=board.kept_back,
        upkeep_opportunities=[
            _upkeep_display(summary.title, summary.aim_days)
            for summary in upkeep_opportunities
        ],
        upcoming=[UpcomingFact(text=text, due_date=date) for text, date in upcoming],
    )


def compose_today_digest(
    session_factory: sessionmaker[Session],
) -> DigestMessage:
    """The message for today, with no HTTP: open a session, gather, compose.

    Used by `send_digest` and by the CLI's `--dry-run`, which must print the
    day's message without touching ntfy (and without any ntfy configuration).
    """
    today_date = local_today()
    with session_factory() as session:
        with session.begin():
            facts = gather_digest_facts(session, today_date=today_date)
    return compose_digest(facts, today_local=today_date)


def send_digest(
    config: NtfyConfig, session_factory: sessionmaker[Session], *, http: httpx.Client
) -> DigestMessage:
    """Gather the day's facts, compose the message, POST it to ntfy.

    The caller owns the HTTP client (and with it timeout and retry policy)
    and the session factory — the same `finance.db.get_sessionmaker()` the
    CLI's other subcommands use. Failures surface as `NtfyError` from the
    POST; database errors are the caller's to classify.

    Args:
        config: Server, topic and optional token.
        session_factory: The CLI's session factory, for the digest's one
            read-only transaction.
        http: The caller's `httpx.Client`.

    Returns:
        The message that was pushed.
    """
    message = compose_today_digest(session_factory)
    post_to_ntfy(message, config, http=http)
    return message
