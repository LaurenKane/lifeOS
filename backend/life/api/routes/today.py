"""today router — the Dashboard's life half, in one read.

The Dashboard composes at the app layer (ADR 0012): this endpoint serves the
life module's pieces in one response so the frontend makes one call instead
of four; the finance cards come from the finance module's own endpoints,
untouched. Nothing crosses schemas in the database — there is no finance
import in this package at all.

The composition is deliberately honest about the cap: `kept_back` counts the
eligible things beyond the five shown, so the screen says "N more wait"
instead of hiding a pile, rescheduling behind the user's back, or nagging
about everything (Q4, Q24).
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import InboxStatus, TodayBoard
from life.domain.models.action import Action
from life.domain.models.thought import Thought
from life.domain.models.upkeep import Receipt, Upkeep
from life.domain.services.today import CAP_TOP_LEVEL, ActionRow, choose_do_now
from life.domain.services.upkeep_state import (
    earned_cadence_days,
    next_opportunity,
    visible_on_today,
)
from life.local import local_now, local_today
from life.public import ActionSummary, UpkeepSummary

#: The stale-thought threshold (Q14), in days. One constant names the Inbox
#: nudge; the API only groups the count.
STALE_INBOX_DAYS: int = 14

router = APIRouter(tags=["life"], prefix="/life/today")

SessionDep = Annotated[Session, Depends(get_session)]


def _action_summary(action: Action) -> ActionSummary:
    return ActionSummary(
        id=action.id,
        text=action.title,
        goal_id=action.goal_id,
        parent_action_id=action.parent_action_id,
        due_date=action.due_date,
        planned_date=action.planned_date,
        urgent=action.urgent,
        is_done=action.is_done,
        done_at=action.done_at,
        created_at=action.created_at,
    )


def _upkeep_summaries(session: Session) -> list[UpkeepSummary]:
    """All active upkeeps with facts attached (last-done, earned cadence)."""
    summaries: list[UpkeepSummary] = []
    upkeeps = list(
        session.execute(select(Upkeep).where(Upkeep.is_active)).scalars().all()
    )
    for upkeep in upkeeps:
        moments: list[dt.datetime] = list(
            session.execute(
                select(Receipt.receipted_at)
                .where(Receipt.upkeep_id == upkeep.id)
                .order_by(Receipt.receipted_at)
            )
            .scalars()
            .all()
        )
        last_done = max(moments, default=None)
        cadence = earned_cadence_days(moments)
        summaries.append(
            UpkeepSummary(
                id=upkeep.id,
                title=upkeep.title,
                aim_days=upkeep.aim_days,
                last_done_at=last_done,
                earned_cadence_days=cadence,
                next_opportunity=next_opportunity(last_done, cadence),
                created_at=upkeep.created_at,
            )
        )
    return summaries


@router.get(
    "",
    response_model=TodayBoard,
    summary="The Dashboard's life half: board, urgent, upcoming, upkeep, inbox",
)
def today(
    session: SessionDep,
    upcoming_days: int = 3,
) -> TodayBoard:
    """Everything the Dashboard's life side needs, in exactly one call."""
    today_date = local_today()

    with session.begin():
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
        do_now = [a for a in open_actions if a.id in board.selected]
        urgent = [a for a in open_actions if a.id in board.urgent_ids]

        horizon = today_date + dt.timedelta(days=upcoming_days)
        upcoming = [
            a
            for a in open_actions
            if a.due_date is not None
            and today_date < a.due_date <= horizon
            and a.id not in board.selected
            and a.id not in board.urgent_ids
        ]

        upkeep_opportunities = [
            summary
            for summary in _upkeep_summaries(session)
            if visible_on_today(summary.next_opportunity, today=today_date)
        ]

        inbox_rows = session.execute(
            select(Thought.created_at, Thought.resolved_at).where(
                Thought.resolved_kind.is_(None)
            )
        ).all()
        stale_boundary = local_now() - dt.timedelta(days=STALE_INBOX_DAYS)
        inbox = InboxStatus(
            count=len(inbox_rows),
            stale=sum(1 for row in inbox_rows if row[0] < stale_boundary),
        )

        return TodayBoard(
            do_now=[_action_summary(a) for a in do_now],
            kept_back=board.kept_back,
            cap=CAP_TOP_LEVEL,
            urgent=[_action_summary(a) for a in urgent],
            upcoming=[_action_summary(a) for a in upcoming],
            upkeep_opportunities=upkeep_opportunities,
            inbox=inbox,
        )
