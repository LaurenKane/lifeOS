"""reflection router — the Dashboard's one "Looking back" read.

Discovery from the fixed brief: the Dashboard says "you advanced {goal} with
N small steps in the last 30 days", grounded in facts that already exist —
done Actions filed under a Goal, receipts under an Upkeep. No new tables, no
write here, no percent: the window is ROLLING (now minus 30 days) because the
label says "last 30 days" and a calendar month lies about a 7-day-old month.

One GET, one transaction, a few scalar/select queries; the counting and the
max-3-texts shaping are the pure `life.domain.services.reflection`. Paused
Goals and archived Upkeeps rest — the read keeps them off the board, they are
visible on their own pages. Entries with a count of zero are absent, and an
empty response is a normal 200 the Dashboard answers with silence. Every
returned word is a fact, never a judgment — vocabulary law.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import (
    ReflectionGoal,
    ReflectionSummary,
    ReflectionUpkeep,
)
from life.domain.models.action import Action
from life.domain.models.goal import Goal
from life.domain.models.upkeep import Receipt, Upkeep
from life.domain.services.reflection import (
    WINDOW_DAYS,
    DoneActionFact,
    GoalFact,
    ReceiptFact,
    UpkeepFact,
    reflection_window,
    shape_reflection,
)
from life.local import local_now
from life.public import Area

router = APIRouter(tags=["life"], prefix="/life/reflection")

SessionDep = Annotated[Session, Depends(get_session)]


@router.get(
    "",
    response_model=ReflectionSummary,
    summary="The last 30 days, in what actually happened",
)
def reflection_summary(session: SessionDep) -> ReflectionSummary:
    """Everything the Dashboard's "Looking back" panel needs, in one call.

    The SQL pre-filters from the window's start edge; the pure service applies
    both edges again so it stays the one authority on them (`reflection_window`
    and this reply's `window_days` are the same constant by construction).
    """
    with session.begin():
        now = local_now()
        window_start = reflection_window(now)

        goals = (
            session.execute(
                select(Goal).where(Goal.is_active).order_by(Goal.created_at, Goal.id)
            )
            .scalars()
            .all()
        )
        upkeeps = (
            session.execute(
                select(Upkeep)
                .where(Upkeep.is_active)
                .order_by(Upkeep.created_at, Upkeep.id)
            )
            .scalars()
            .all()
        )
        # Done only, only under a Goal, from the window's start on. Newest
        # first, so the service's text cap keeps the three newest for free.
        done_actions = session.execute(
            select(Action.goal_id, Action.title, Action.done_at)
            .where(
                Action.is_done,
                Action.goal_id.is_not(None),
                Action.done_at >= window_start,
                Action.done_at <= now,
            )
            .order_by(Action.done_at.desc(), Action.id.desc())
        ).all()
        # An upkeep's receipts, oldest first: order is irrelevant to a count,
        # but the read is cheap and deterministic either way.
        receipt_upkeep_ids = (
            session.execute(
                select(Receipt.upkeep_id)
                .where(
                    Receipt.receipted_at >= window_start,
                    Receipt.receipted_at <= now,
                )
                .order_by(Receipt.id)
            )
            .scalars()
            .all()
        )

        reflection = shape_reflection(
            [
                GoalFact(
                    goal_id=g.id,
                    title=g.title,
                    area=g.area,
                    created_at=g.created_at,
                    is_active=True,
                )
                for g in goals
            ],
            [
                UpkeepFact(
                    upkeep_id=u.id,
                    title=u.title,
                    created_at=u.created_at,
                    is_active=True,
                )
                for u in upkeeps
            ],
            [
                # goal_id/done_at are NOT NULL by the WHERE above; the guard
                # is the one narrow the type checker needs to believe it.
                DoneActionFact(goal_id=goal_id, text=title, done_at=done_at)
                for (goal_id, title, done_at) in done_actions
                if goal_id is not None and done_at is not None
            ],
            [ReceiptFact(upkeep_id=upkeep_id) for upkeep_id in receipt_upkeep_ids],
            now_local=now,
            window_start=window_start,
        )

        return ReflectionSummary(
            window_days=WINDOW_DAYS,
            goals=[
                ReflectionGoal(
                    goal_id=row.goal_id,
                    title=row.title,
                    area=Area(row.area) if row.area else None,
                    count=row.count,
                    done_texts=row.done_texts,
                )
                for row in reflection.goals
            ],
            upkeeps=[
                ReflectionUpkeep(
                    upkeep_id=row.upkeep_id, title=row.title, count=row.count
                )
                for row in reflection.upkeeps
            ],
        )
