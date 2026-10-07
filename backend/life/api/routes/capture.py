"""capture router — one line in, one decision-free outcome.

The rule everything here serves (discovery Q11): a capture must not have to
decide anything. The parser suggests; the row always lands; every
auto-decision arrives inside an echo (Q22: retap to change, undo to reverse).

Flow, in one pass, reading the parse result:

- a confident upkeep match ("cleaned the bathroom" == a named Upkeep plus a
  receipt verb) → receipt recorded, Thought resolved to it. Undo deletes the
  capture's own receipt (provenance: `receipt.thought_id`) — never a
  manually filed one.
- an upkeep match without a completion reading ("bathroom") → no receipt
  (Q21b: a loosely-phrased fact must not rewrite the cadence), no silent
  branch either: `upkeep_choice` carries the matched upkeep back, and the
  client asks the one question.
- otherwise a date or an urgent flag → a dated/urgent Action, Thought
  resolved to it.
- otherwise the Thought simply rests in the Inbox — a normal outcome
  (glossary: a willing resting place, not debt).
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import (
    CaptureRequest,
    CaptureResponse,
    ParsedCaptureInfo,
    UpkeepChoice,
)
from life.domain.models.action import Action
from life.domain.models.thought import Thought
from life.domain.models.upkeep import Receipt, Upkeep
from life.domain.services.parse import parse_capture
from life.domain.services.upkeep_match import match_upkeep
from life.local import local_now, local_today
from life.public import (
    ActionSummary,
    ThoughtResolution,
    ThoughtSummary,
    UpkeepSummary,
)

router = APIRouter(tags=["life"], prefix="/life/capture")

SessionDep = Annotated[Session, Depends(get_session)]


def _response(
    thought: Thought,
    *,
    created_action: Action | None = None,
    created_receipt: Receipt | None = None,
    receipted_upkeep: Upkeep | None = None,
    upkeep_choice: UpkeepChoice | None = None,
    due_date: dt.date | None = None,
    planned_date: dt.date | None = None,
    urgent: bool = False,
) -> CaptureResponse:
    """Assemble the one capture response from the rows the pass produced."""
    return CaptureResponse(
        thought=ThoughtSummary(
            id=thought.id,
            text=thought.text,
            resolved_kind=ThoughtResolution(thought.resolved_kind)
            if thought.resolved_kind
            else None,
            action_id=thought.action_id,
            goal_id=thought.goal_id,
            upkeep_id=thought.upkeep_id,
            created_at=thought.created_at,
        ),
        parsed=ParsedCaptureInfo(
            due_date=due_date, planned_date=planned_date, urgent=urgent
        ),
        created_action=(
            ActionSummary(
                id=created_action.id,
                text=created_action.title,
                goal_id=created_action.goal_id,
                parent_action_id=created_action.parent_action_id,
                due_date=created_action.due_date,
                planned_date=created_action.planned_date,
                urgent=created_action.urgent,
                is_done=created_action.is_done,
                done_at=created_action.done_at,
                created_at=created_action.created_at,
            )
            if created_action is not None
            else None
        ),
        created_receipt_id=created_receipt.id if created_receipt else None,
        receipted_upkeep=(
            UpkeepSummary(
                id=receipted_upkeep.id,
                title=receipted_upkeep.title,
                aim_days=receipted_upkeep.aim_days,
                created_at=receipted_upkeep.created_at,
            )
            if receipted_upkeep is not None
            else None
        ),
        upkeep_choice=upkeep_choice,
    )


@router.post(
    "", response_model=CaptureResponse, status_code=http_status.HTTP_201_CREATED
)
def capture(body: CaptureRequest, session: SessionDep) -> CaptureResponse:
    """Capture one line. Always 201; a plain Thought is a normal outcome."""
    parsed = parse_capture(body.text, today=local_today())
    now = local_now()

    with session.begin():
        thought = Thought(text=parsed.text)
        session.add(thought)
        session.flush()

        candidates = session.scalars(select(Upkeep)).all()
        upkeep_match = match_upkeep(parsed.text, [(u.id, u.title) for u in candidates])

        if upkeep_match is not None and upkeep_match.confident:
            receipt = Receipt(upkeep_id=upkeep_match.upkeep_id, thought_id=thought.id)
            session.add(receipt)
            session.flush()
            upkeep = session.get(Upkeep, upkeep_match.upkeep_id)
            assert upkeep is not None, "the matched upkeep was just listed"
            thought.resolved_kind = "upkeep"
            thought.upkeep_id = upkeep.id
            thought.resolved_at = now
            return _response(
                thought,
                created_receipt=receipt,
                receipted_upkeep=upkeep,
                due_date=parsed.due_date,
                planned_date=parsed.planned_date,
                urgent=parsed.urgent,
            )

        if upkeep_match is not None:
            # A named upkeep without a completion reading: ask, never guess.
            return _response(
                thought,
                upkeep_choice=UpkeepChoice(
                    upkeep_id=upkeep_match.upkeep_id,
                    title=upkeep_match.title,
                ),
                due_date=parsed.due_date,
                planned_date=parsed.planned_date,
                urgent=parsed.urgent,
            )

        if parsed.due_date or parsed.planned_date or parsed.urgent:
            action = Action(
                title=parsed.text,
                due_date=parsed.due_date,
                planned_date=parsed.planned_date,
                urgent=parsed.urgent,
            )
            session.add(action)
            session.flush()
            thought.resolved_kind = "action"
            thought.action_id = action.id
            thought.resolved_at = now
            return _response(
                thought,
                created_action=action,
                due_date=parsed.due_date,
                planned_date=parsed.planned_date,
                urgent=parsed.urgent,
            )

        # A plain Thought: resting in the inbox, a normal outcome.
        return _response(
            thought,
            due_date=parsed.due_date,
            planned_date=parsed.planned_date,
            urgent=parsed.urgent,
        )


@router.delete("/{thought_id}", summary="Undo one capture")
def undo_capture(thought_id: int, session: SessionDep) -> dict[str, bool]:
    """Revert one capture — the Q22 undo.

    Scope is deliberately narrow and provenance-driven: it deletes the
    receipt rows THIS capture created (`receipt.thought_id`, never a manually
    filed one), the Action it auto-created (with any subactions, since it is
    one flight), and unresolved-only Thoughts entirely (nothing but the
    capture itself existed). A thought that was resolved by USER decision is
    not un-decidable here: resolution-by-user is intent, auto-creation is a
    suggestion the echo can still recall.
    """
    with session.begin():
        thought = session.get(Thought, thought_id)
        if thought is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such thought")

        # The provenance pointers are READ first (unresolving clears them),
        # then the resolution is unwound, then the created rows go — in that
        # order, because the thought's FK is what pins them.
        created_action_id = thought.action_id
        had_receipt = thought.upkeep_id is not None

        if thought.resolved_kind is None:
            session.delete(thought)
        else:
            thought.resolved_kind = None
            thought.action_id = None
            thought.goal_id = None
            thought.upkeep_id = None
            thought.resolved_at = None
        session.flush()

        if created_action_id is not None:
            session.query(Action).filter(
                Action.parent_action_id == created_action_id
            ).delete(synchronize_session=False)
            session.query(Action).filter(Action.id == created_action_id).delete(
                synchronize_session=False
            )
        if had_receipt:
            session.query(Receipt).filter(Receipt.thought_id == thought.id).delete(
                synchronize_session=False
            )
        session.flush()
    return {"undone": True}
