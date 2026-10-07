"""catchup router — the away strip's one read and one acknowledgment.

Discovery Q8: after >=2 days away the Dashboard shows ONE strip — "you were
away N days", "do these still matter?" — with per-item clearing; it grounds,
never shames, and ignoring it must be allowed. Q22 makes the quieting one
tap, and Q8's nag-guard makes it a per-day thing: without the ack row every
reload would nag forever.

Both routes read/write on request; no notification infra, no cron. The
summary gathers `last_activity` with three scalar queries inside ONE
transaction — max over thought.created_at, done-action done_at, receipt
receipted_at (the why is `away_days`' docstring). Everything returned is a
fact, not a judgment: no "overdue", no "behind" — vocabulary law.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import (
    CatchUpAcked,
    CatchUpAckRequest,
    CatchUpGoal,
    CatchUpSummary,
    CatchUpThought,
)
from life.domain.models.action import Action
from life.domain.models.away import CatchupAck
from life.domain.models.goal import Goal
from life.domain.models.thought import Thought
from life.domain.models.upkeep import Receipt
from life.domain.services.away import away_days
from life.local import local_now, local_today
from life.public import Area

#: How many unresolved Thoughts the strip shows. The cut is honest elsewhere
#: (Q4's kept_back); here the pile's size is never an accusation, so the cap
#: quietly limits and the summary does not count what was cut.
THOUGHT_CAP: int = 25

router = APIRouter(tags=["life"], prefix="/life/catchup")

SessionDep = Annotated[Session, Depends(get_session)]


@router.get(
    "",
    response_model=CatchUpSummary,
    summary="The away strip: how long you were away and what still asks",
)
def catchup_summary(session: SessionDep) -> CatchUpSummary:
    """Everything the Dashboard's away strip needs, in exactly one call."""
    with session.begin():
        day = local_today()

        last_thought = session.execute(
            select(Thought.created_at)
            .order_by(Thought.created_at.desc(), Thought.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        last_done = session.execute(
            select(Action.done_at)
            .where(Action.done_at.is_not(None))
            .order_by(Action.done_at.desc(), Action.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        last_receipt = session.execute(
            select(Receipt.receipted_at)
            .order_by(Receipt.receipted_at.desc(), Receipt.id.desc())
            .limit(1)
        ).scalar_one_or_none()

        moments = [m for m in (last_thought, last_done, last_receipt) if m is not None]
        last_activity = max(moments, default=None)

        acked_today = (
            session.execute(
                select(CatchupAck.id).where(CatchupAck.day == day).limit(1)
            ).first()
            is not None
        )

        goals = (
            session.execute(
                select(Goal).where(Goal.is_active).order_by(Goal.created_at, Goal.id)
            )
            .scalars()
            .all()
        )
        thoughts = (
            session.execute(
                select(Thought)
                .where(Thought.resolved_kind.is_(None))
                .order_by(Thought.created_at.desc(), Thought.id.desc())
                .limit(THOUGHT_CAP)
            )
            .scalars()
            .all()
        )

        return CatchUpSummary(
            away_days=away_days(last_activity, now_local=local_now()),
            acked_today=acked_today,
            goals=[
                CatchUpGoal(
                    goal_id=g.id,
                    title=g.title,
                    area=Area(g.area) if g.area else None,
                    current_focus=g.current_focus,
                )
                for g in goals
            ],
            thoughts=[
                CatchUpThought(thought_id=t.id, text=t.text, created_at=t.created_at)
                for t in thoughts
            ],
        )


@router.post(
    "/ack",
    response_model=CatchUpAcked,
    summary="Quiet the away strip for the given day",
)
def ack(body: CatchUpAckRequest, session: SessionDep) -> CatchUpAcked:
    """Record the day's acknowledgment — exactly idempotent.

    `ON CONFLICT DO NOTHING` on the UNIQUE day: two taps in one reload, or a
    retry, insert nothing and return the same answer. Day is a LOCAL date
    (`life.local`), never a UTC arm.
    """
    with session.begin():
        session.execute(
            insert(CatchupAck)
            .values(day=body.day)
            .on_conflict_do_nothing(constraint="uq_catchup_ack_day")
        )
    return CatchUpAcked(acked=True)
