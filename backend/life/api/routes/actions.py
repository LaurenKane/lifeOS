"""actions router — the concrete steps, and today's board membership.

Every route here accepts the shape itself: the Do-now board (today.py) is a
read-side composition — `planned_date` and `due_date` are the only writes —
so "pull into today" is a PATCH, not a special endpoint, and the cap needs no
write-side enforcement (the board decides what it shows; the data keeps its
own promises).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import ActionCreateRequest, ActionUpdateRequest
from life.domain.models.action import Action
from life.domain.models.goal import Goal
from life.local import local_now
from life.public import ActionSummary

router = APIRouter(tags=["life"], prefix="/life/actions")

SessionDep = Annotated[Session, Depends(get_session)]


def _summary(action: Action) -> ActionSummary:
    """One action as the public read model."""
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


def _get_or_404(session: Session, action_id: int) -> Action:
    action = session.get(Action, action_id)
    if action is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such action")
    return action


@router.get("", response_model=list[ActionSummary], summary="List actions")
def list_actions(
    session: SessionDep,
    goal_id: int | None = None,
    parent_id: int | None = None,
    status: str = "open",
) -> list[ActionSummary]:
    """Top-level actions by default (the board's own shape); subactions come
    by `parent_id`. `status` filters open/done — a done Action is a memory,
    kept for the pixel graph, never shown as an obligation."""
    query = select(Action).order_by(Action.created_at)
    if parent_id is not None:
        query = query.where(Action.parent_action_id == parent_id)
    else:
        query = query.where(Action.parent_action_id.is_(None))
    if status == "open":
        query = query.where(Action.is_done.is_(False))
    elif status == "done":
        query = query.where(Action.is_done)
    elif status != "all":
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST, "status must be open, done or all"
        )
    if goal_id is not None:
        query = query.where(Action.goal_id == goal_id)
    return [_summary(a) for a in session.execute(query).scalars().all()]


@router.post("", response_model=ActionSummary, status_code=http_status.HTTP_201_CREATED)
def create_action(body: ActionCreateRequest, session: SessionDep) -> ActionSummary:
    """Create an Action. A due date is the world's; a planned date is a
    chosen day; neither is required (a Thought-like step is fine)."""
    with session.begin():
        if body.goal_id is not None and session.get(Goal, body.goal_id) is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such goal")
        action = Action(
            title=body.text,
            goal_id=body.goal_id,
            due_date=body.due_date,
            planned_date=body.planned_date,
            urgent=body.urgent,
        )
        session.add(action)
        session.flush()
        return _summary(action)


@router.patch("/{action_id}", response_model=ActionSummary)
def update_action(
    action_id: int, body: ActionUpdateRequest, session: SessionDep
) -> ActionSummary:
    """Partial edit. This is where a retapped echo's changed date lands."""
    with session.begin():
        action = _get_or_404(session, action_id)
        if body.text is not None:
            action.title = body.text
        if body.goal_id is not None:
            if session.get(Goal, body.goal_id) is None:
                raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such goal")
            action.goal_id = body.goal_id
        if body.due_date is not None:
            action.due_date = body.due_date
        if body.planned_date is not None:
            action.planned_date = body.planned_date
        if body.urgent is not None:
            action.urgent = body.urgent
        session.flush()
        return _summary(action)


@router.post("/{action_id}/done", response_model=ActionSummary)
def mark_done(action_id: int, session: SessionDep) -> ActionSummary:
    """Done. Subactions are left alone — ticking a parent does not pretend
    its boxes were ticked; only a parent that is itself fully open stays
    honest about what remains."""
    with session.begin():
        action = _get_or_404(session, action_id)
        if action.is_done:
            return _summary(action)
        action.is_done = True
        action.done_at = local_now()
        session.flush()
        return _summary(action)


@router.post(
    "/{action_id}/undone", response_model=ActionSummary, include_in_schema=False
)
def mark_undone(action_id: int, session: SessionDep) -> ActionSummary:
    """Re-open — the undo half of the echo contract (Q22)."""
    with session.begin():
        action = _get_or_404(session, action_id)
        action.is_done = False
        action.done_at = None
        session.flush()
        return _summary(action)


@router.post(
    "/{action_id}/subactions",
    response_model=ActionSummary,
    status_code=http_status.HTTP_201_CREATED,
    summary="Add a subaction",
)
def add_subaction(
    action_id: int, body: ActionCreateRequest, session: SessionDep
) -> ActionSummary:
    """Add a subaction. Depth is enforced by the `trg_action_depth` trigger:
    a subaction that already has a parent is refused at the database, and the
    refusal surfaces here as a 422."""
    with session.begin():
        parent = _get_or_404(session, action_id)
        if parent.parent_action_id is not None:
            raise HTTPException(
                http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                "subactions are one level deep; this action already has a parent",
            )
        subaction = Action(title=body.text, parent_action_id=parent.id)
        session.add(subaction)
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(
                http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                "the database refused this subaction (one-level rule)",
            ) from None
        return _summary(subaction)
