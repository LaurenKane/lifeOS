"""thoughts router — the Inbox and the one decision.

The Inbox (glossary) is a willing resting place: `resolved_kind IS NULL`
means a Thought is resting, and resting rows are the DEFAULT read. Resolution
is always an explicit POST — the system never promotes on its own; the
capture pass may have auto-created a row, but an auto-created row carries its
own undo (Q22's echo), and an Inbox row the USER resolves is irreversible
intent.

The populating-to-goal/action/upkeep requests live here because resolution is
one decision applied to one Thought — capture.py returns suggestions, this
router accepts the user's answer.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.helper import (
    HelperConfig,
    HelperError,
    chat_completion,
    get_organize_client,
)
from life.api.schemas import (
    OrganizeResponse,
    OrganizeSuggestion,
    ThoughtResolveRequest,
    ThoughtResolveResponse,
)
from life.domain.models.action import Action
from life.domain.models.goal import Goal
from life.domain.models.thought import Thought
from life.domain.models.upkeep import Receipt, Upkeep
from life.domain.services.organize import (
    ThoughtFact,
    build_prompt,
    parse_suggestions,
)
from life.local import local_now, local_today
from life.public import (  # noqa: E501
    ActionSummary,
    Area,
    GoalSummary,
    ThoughtResolution,
    ThoughtSummary,
    UpkeepSummary,
)

router = APIRouter(tags=["life"], prefix="/life/thoughts")

SessionDep = Annotated[Session, Depends(get_session)]

#: How many unresolved Thoughts the organizer sends to the helper — the same
#: cap the catchup read uses: the pile's size is never an accusation, and the
#: cut is quietly quiet, not announced.
THOUGHT_CAP: int = 25

HttpClientDep = Annotated[httpx.Client, Depends(get_organize_client)]


def _summary(thought: Thought) -> ThoughtSummary:
    """One thought as the public read model."""
    return ThoughtSummary(
        id=thought.id,
        text=thought.text,
        resolved_kind=ThoughtResolution(thought.resolved_kind)
        if thought.resolved_kind
        else None,
        action_id=thought.action_id,
        goal_id=thought.goal_id,
        upkeep_id=thought.upkeep_id,
        created_at=thought.created_at,
    )


@router.get("", response_model=list[ThoughtSummary], summary="The Inbox")
def list_thoughts(
    response: Response, session: SessionDep, include_resolved: bool = False
) -> list[ThoughtSummary]:
    """Unresolved Thoughts, newest first.

    `include_resolved=True` returns the full history too — a resolved Thought
    is kept forever as provenance (it is the receipt's paper trail), so the
    read is filtered rather than deleted.

    `helper_available` rides on this read as the `X-Helper-Available` response
    header: the response itself is a plain list (pinned by every consumer and
    db test), so a redundant helper-status read is not needed to know whether
    the "help me sort the pile" button exists. Empty by default — the helper
    is ABSENT when unconfigured, not off-and-waiting.
    """
    response.headers["X-Helper-Available"] = (
        "true" if HelperConfig.from_settings().is_available else "false"
    )
    query = select(Thought).order_by(Thought.created_at.desc())
    if not include_resolved:
        query = query.where(Thought.resolved_kind.is_(None))
    rows = session.execute(query).scalars().all()
    return [_summary(row) for row in rows]


@router.post(
    "/{thought_id}/resolve",
    response_model=ThoughtResolveResponse,
    summary="Resolve a Thought: the one decision it may involve",
)
def resolve(
    thought_id: int, body: ThoughtResolveRequest, session: SessionDep
) -> ThoughtResolveResponse:
    """Apply the user's answer about one Thought.

    - `action` / `goal` / `upkeep` promote: the new row's title is the
      Thought's text; an existing `upkeep_id` with `create_receipt` also
      records a receipt in the same transaction (discovery Q20's answer).
    - `rests` / `dismissed` carry no target: the Thought stays, the Inbox
      nudge goes quiet. Both are resolutions of record, not deletions.
    """
    with session.begin():
        thought = session.get(Thought, thought_id)
        if thought is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such thought")
        if thought.resolved_kind is not None:
            raise HTTPException(
                http_status.HTTP_409_CONFLICT,
                "this Thought is already resolved; edit the target row instead",
            )

        now = local_now()
        created_action: Action | None = None
        created_goal: Goal | None = None
        created_upkeep: Upkeep | None = None

        if body.choice == "action":
            created_action = Action(title=thought.text)
            session.add(created_action)
            session.flush()
            thought.action_id = created_action.id
        elif body.choice == "goal":
            created_goal = Goal(title=thought.text)
            session.add(created_goal)
            session.flush()
            thought.goal_id = created_goal.id
        elif body.choice == "upkeep" and body.upkeep_id is None:
            created_upkeep = Upkeep(title=thought.text, aim_days=body.aim_days)
            session.add(created_upkeep)
            session.flush()
            thought.upkeep_id = created_upkeep.id
        elif body.choice == "upkeep":
            upkeep = session.get(Upkeep, body.upkeep_id)
            if upkeep is None:
                raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such upkeep")
            if body.create_receipt:
                session.add(Receipt(upkeep_id=upkeep.id, thought_id=thought.id))
            created_upkeep = upkeep
            thought.upkeep_id = upkeep.id

        thought.resolved_kind = body.choice
        thought.resolved_at = now
        session.flush()

        return ThoughtResolveResponse(
            thought=_summary(thought),
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
            created_goal=(
                GoalSummary(
                    id=created_goal.id,
                    title=created_goal.title,
                    why=created_goal.why,
                    area=Area(created_goal.area) if created_goal.area else None,
                    minimum=created_goal.minimum,
                    current_focus=created_goal.current_focus,
                    is_active=created_goal.is_active,
                    created_at=created_goal.created_at,
                )
                if created_goal is not None
                else None
            ),
            created_upkeep=(
                UpkeepSummary(
                    id=created_upkeep.id,
                    title=created_upkeep.title,
                    aim_days=created_upkeep.aim_days,
                    last_done_at=None,
                    earned_cadence_days=None,
                    next_opportunity=None,
                    created_at=created_upkeep.created_at,
                )
                if created_upkeep is not None
                else None
            ),
        )


@router.post(
    "/organize",
    response_model=OrganizeResponse,
    summary="The helper's one call: suggestions for the unresolved pile",
)
def organize(session: SessionDep, http: HttpClientDep) -> OrganizeResponse:
    """One tap, one call: the helper sees the unresolved Thoughts (capped like
    the catchup read) and proposes rows. SUGGESTIONS ONLY — nothing is applied
    here; every suggestion is approved or rejected one by one at the client.

    The boundary before the operator: entries the helper got wrong (an unknown
    thought_id, a choice outside the vocabulary, an unparseable answer) are
    silently DROPPED, and the ids are proven against the rows actually sent —
    the database, not the model, decides what exists. A HelperError — no
    answer within the client's patience, or a refusal — is a 502 saying so;
    the pile is untouched either way. An empty pile runs no call at all.
    """
    with session.begin():
        rows = (
            session.execute(
                select(Thought)
                .where(Thought.resolved_kind.is_(None))
                .order_by(Thought.created_at.desc(), Thought.id.desc())
                .limit(THOUGHT_CAP)
            )
            .scalars()
            .all()
        )
        if not rows:
            return OrganizeResponse(suggestions=[])

        facts = [
            ThoughtFact(thought_id=row.id, text=row.text, created_at=row.created_at)
            for row in rows
        ]
        today = local_today()
        try:
            raw = chat_completion(
                HelperConfig.from_settings(),
                prompt=build_prompt(facts, today_local=today),
                http=http,
            )
        except HelperError as exc:
            raise HTTPException(
                http_status.HTTP_502_BAD_GATEWAY,
                "the helper did not answer",
            ) from exc

        parsed = parse_suggestions(raw, facts, today_local=today)
        return OrganizeResponse(
            suggestions=[
                OrganizeSuggestion(
                    thought_id=s.thought_id,
                    choice=s.choice,
                    due_date=s.due_date.isoformat() if s.due_date else None,
                    urgent=s.urgent,
                    why=s.why,
                )
                for s in parsed
            ]
        )
