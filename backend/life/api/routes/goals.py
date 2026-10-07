"""goals router — the why, the area, the Minimum, the Current thing.

The Goal lifecycle is a SWITCH, not a state machine (glossary / ADR 0011):
`is_active` toggles; there is no failure, no percent, and no way for the API
to mark a Goal as anything the user didn't say. Wishlist rows attach here —
they are wants, kept out of every dated surface by construction.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import (
    GoalCreateRequest,
    GoalUpdateRequest,
    WishlistItemCreateRequest,
    WishlistItemUpdateRequest,
)
from life.domain.models.goal import Goal, WishlistItem
from life.public import Area, GoalSummary, WishlistItemSummary

router = APIRouter(tags=["life"], prefix="/life/goals")

SessionDep = Annotated[Session, Depends(get_session)]


def _summary(goal: Goal) -> GoalSummary:
    return GoalSummary(
        id=goal.id,
        title=goal.title,
        why=goal.why,
        area=Area(goal.area) if goal.area else None,
        minimum=goal.minimum,
        current_focus=goal.current_focus,
        is_active=goal.is_active,
        created_at=goal.created_at,
    )


def _get_or_404(session: Session, goal_id: int) -> Goal:
    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such goal")
    return goal


@router.get("", response_model=list[GoalSummary])
def list_goals(
    session: SessionDep,
    include_paused: bool = True,
) -> list[GoalSummary]:
    """Every Goal, paused ones included by default — a paused Goal is
    resting, not gone: the Goals page shows it so re-activation is a tap, and
    the Dashboard filters client-side."""
    query = select(Goal).order_by(Goal.created_at.desc())
    if not include_paused:
        query = query.where(Goal.is_active.is_(True))
    return [_summary(g) for g in session.execute(query).scalars().all()]


@router.post("", response_model=GoalSummary, status_code=http_status.HTTP_201_CREATED)
def create_goal(body: GoalCreateRequest, session: SessionDep) -> GoalSummary:
    """A Goal needs only a title; why/area/minimum are refinements the Goals
    page accepts later — deliberately, so promoting a Thought costs one
    decision (the promote) rather than six."""
    with session.begin():
        goal = Goal(
            title=body.title,
            why=body.why,
            area=str(body.area) if body.area else None,
            minimum=body.minimum,
            current_focus=body.current_focus,
        )
        session.add(goal)
        session.flush()
        return _summary(goal)


@router.patch("/{goal_id}", response_model=GoalSummary)
def update_goal(
    goal_id: int, body: GoalUpdateRequest, session: SessionDep
) -> GoalSummary:
    """Partial edit; None means leave-as-is — including `area=None` staying
    the current value (detaching an area is an explicit call to this route
    with the field supplied; a Goal with no area is legal at any time)."""
    with session.begin():
        goal = _get_or_404(session, goal_id)
        if body.title is not None:
            goal.title = body.title
        if body.why is not None:
            goal.why = body.why
        if body.area is not None:
            goal.area = str(body.area)
        if body.minimum is not None:
            goal.minimum = body.minimum
        if body.current_focus is not None:
            goal.current_focus = body.current_focus
        session.flush()
        return _summary(goal)


@router.post("/{goal_id}/pause", response_model=GoalSummary)
def pause_goal(goal_id: int, session: SessionDep) -> GoalSummary:
    """Resting: out of suggestions, visible on the Goals page. Nothing is
    lost, nothing is judged."""
    with session.begin():
        goal = _get_or_404(session, goal_id)
        goal.is_active = False
        session.flush()
        return _summary(goal)


@router.post("/{goal_id}/activate", response_model=GoalSummary)
def activate_goal(goal_id: int, session: SessionDep) -> GoalSummary:
    """Back in the suggestion pool."""
    with session.begin():
        goal = _get_or_404(session, goal_id)
        goal.is_active = True
        session.flush()
        return _summary(goal)


@router.get(
    "/{goal_id}/wishlist",
    response_model=list[WishlistItemSummary],
)
def list_wishlist(goal_id: int, session: SessionDep) -> list[WishlistItemSummary]:
    """The Goal's wants, oldest first. Ticking one is a memory, never a
    completed obligation."""
    rows = (
        session.execute(
            select(WishlistItem)
            .where(WishlistItem.goal_id == goal_id)
            .order_by(WishlistItem.created_at)
        )
        .scalars()
        .all()
    )
    return [
        WishlistItemSummary(id=r.id, goal_id=r.goal_id, text=r.text, is_done=r.is_done)
        for r in rows
    ]


@router.post(
    "/{goal_id}/wishlist",
    response_model=WishlistItemSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def add_wishlist(
    goal_id: int, body: WishlistItemCreateRequest, session: SessionDep
) -> WishlistItemSummary:
    """One want, attached. No date, no urgency, no pressure (glossary)."""
    with session.begin():
        goal = _get_or_404(session, goal_id)
        item = WishlistItem(goal_id=goal.id, text=body.text)
        session.add(item)
        session.flush()
        return WishlistItemSummary(
            id=item.id, goal_id=item.goal_id, text=item.text, is_done=item.is_done
        )


@router.patch(
    "/{goal_id}/wishlist/{item_id}",
    response_model=WishlistItemSummary,
)
def update_wishlist(
    goal_id: int, item_id: int, body: WishlistItemUpdateRequest, session: SessionDep
) -> WishlistItemSummary:
    """Tick or rename a want."""
    with session.begin():
        item = session.get(WishlistItem, item_id)
        if item is None or item.goal_id != goal_id:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such want")
        if body.text is not None:
            item.text = body.text
        if body.is_done is not None:
            item.is_done = body.is_done
        session.flush()
        return WishlistItemSummary(
            id=item.id, goal_id=item.goal_id, text=item.text, is_done=item.is_done
        )
