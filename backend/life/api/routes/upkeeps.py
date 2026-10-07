"""upkeeps router — receipts and the facts they follow from.

The read computes `MAX(receipt.receipted_at)` and the median-gap cadence
rather than reading columns, because there ARE no cadence columns by design
(ADR 0011: cadence plus last-done, no due date). `next_opportunity` is
derived arithmetic, not a schedule the system invented — a cadence with no
evidence yields None, which is the honest shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from life.api.deps import get_session
from life.api.schemas import (
    ReceiptCreateRequest,
    UpkeepCreateRequest,
    UpkeepUpdateRequest,
)
from life.domain.models.upkeep import Receipt, Upkeep
from life.domain.services.upkeep_state import earned_cadence_days, next_opportunity
from life.local import local_now
from life.public import UpkeepSummary

router = APIRouter(tags=["life"], prefix="/life/upkeeps")

SessionDep = Annotated[Session, Depends(get_session)]


def _summarize(
    upkeep: Upkeep,
    *,
    last_done_at: datetime | None,
    earned_cadence_days: int | None,
) -> UpkeepSummary:
    """One upkeep row plus its derived cadence facts."""
    return UpkeepSummary(
        id=upkeep.id,
        title=upkeep.title,
        aim_days=upkeep.aim_days,
        last_done_at=last_done_at,
        earned_cadence_days=earned_cadence_days,
        next_opportunity=next_opportunity(last_done_at, earned_cadence_days),
        created_at=upkeep.created_at,
    )


def _with_receipts(session: Session, upkeeps: list[Upkeep]) -> list[UpkeepSummary]:
    """Attach last-done and the earned cadence, queried per upkeep.

    An N+1 by design: N is the number of namable upkeeps one person keeps
    (handfuls, not thousands), and the alternative — one window-function — is
    only worth it when the read is hot. It is not.
    """
    summaries: list[UpkeepSummary] = []
    for upkeep in upkeeps:
        moments: list[datetime] = list(
            session.execute(
                select(Receipt.receipted_at)
                .where(Receipt.upkeep_id == upkeep.id)
                .order_by(Receipt.receipted_at)
            )
            .scalars()
            .all()
        )
        last_done = max(moments, default=None)
        summaries.append(
            _summarize(
                upkeep,
                last_done_at=last_done,
                earned_cadence_days=earned_cadence_days(moments),
            )
        )
    return summaries


@router.get("", response_model=list[UpkeepSummary])
def list_upkeeps(
    session: SessionDep,
    include_inactive: bool = False,
) -> list[UpkeepSummary]:
    """All named upkeeps with their derived cadence facts."""
    query = select(Upkeep).order_by(Upkeep.created_at)
    if not include_inactive:
        query = query.where(Upkeep.is_active.is_(True))
    rows = list(session.execute(query).scalars().all())
    return _with_receipts(session, rows)


@router.get(
    "/{upkeep_id}",
    response_model=UpkeepSummary,
)
def get_upkeep(upkeep_id: int, session: SessionDep) -> UpkeepSummary:
    """One upkeep with facts attached."""
    rows = list(session.execute(select(Upkeep).where(Upkeep.id == upkeep_id)).scalars())
    if not rows:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such upkeep")
    return _with_receipts(session, rows)[0]


@router.post("", response_model=UpkeepSummary, status_code=http_status.HTTP_201_CREATED)
def create_upkeep(body: UpkeepCreateRequest, session: SessionDep) -> UpkeepSummary:
    """Name a recurring thing. The Aim is optional; "I do this but can't say
    how often" is a legitimate state, and the earned cadence fills in."""
    with session.begin():
        upkeep = Upkeep(title=body.title, aim_days=body.aim_days)
        session.add(upkeep)
        session.flush()
        return _summarize(upkeep, last_done_at=None, earned_cadence_days=None)


@router.patch("/{upkeep_id}", response_model=UpkeepSummary)
def update_upkeep(
    upkeep_id: int, body: UpkeepUpdateRequest, session: SessionDep
) -> UpkeepSummary:
    """Edit the title or the Aim. Changing an aim does not rewrite history:
    the earned cadence keeps its receipts, and the board simply starts using
    the new intent."""
    with session.begin():
        upkeep = session.get(Upkeep, upkeep_id)
        if upkeep is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such upkeep")
        if body.title is not None:
            upkeep.title = body.title
        if body.aim_days is not None:
            upkeep.aim_days = body.aim_days
        if body.is_active is not None:
            upkeep.is_active = body.is_active
        session.flush()
        return _summarize(upkeep, last_done_at=None, earned_cadence_days=None)


@router.post(
    "/{upkeep_id}/receipts",
    status_code=http_status.HTTP_201_CREATED,
)
def record_receipt(
    upkeep_id: int, body: ReceiptCreateRequest, session: SessionDep
) -> dict[str, int]:
    """One receipt. `receipted_at` backdates to the moment it happened;
    absent means now. The response stays small: `{"receipted": <id>}` — the
    cadence facts are the GET's job, and the Upkeep page refetches."""
    with session.begin():
        upkeep = session.get(Upkeep, upkeep_id)
        if upkeep is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, "no such upkeep")
        receipt = Receipt(
            upkeep_id=upkeep.id, receipted_at=body.receipted_at or local_now()
        )
        session.add(receipt)
        session.flush()
        return {"receipted": receipt.id}
