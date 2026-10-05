"""review router — the transfer-matching review queue.

The transfer linker's questions live here. The sweep links what it is sure
about and queues the rest as `transfer_review` rows; this router lists those
rows for a human and records the three answers:

    CONFIRM  link the outbound leg to the chosen candidate (`user_confirmed`)
    REJECT   the pair was not a transfer; lines untouched
    IGNORE   stop suggesting these pairs (`transfer_skip`), lines untouched

Nothing here guesses. A confirm names its candidate, and an unknown candidate
is a 400 rather than a silent pick.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance.api.deps import get_session
from finance.api.schemas import (
    ConfirmTransferRequest,
    TransferReviewCandidateOut,
    TransferReviewItem,
    TransferReviewLegOut,
    TransferReviewStats,
)
from finance.api.transfer_linker import (
    confirm_review,
    ignore_review,
    reject_review,
)
from finance.domain.models.accounts import Account
from finance.domain.models.ledger import JournalEntry, JournalLine
from finance.domain.models.transfers import TransferReview
from finance.domain.services.transfer_match import (
    JournalLineRef,
    transfer_match,
)

router = APIRouter(tags=["finance"], prefix="/review")


class ReviewDecision(StrEnum):
    """The three outcomes the queue offers."""

    CONFIRM = "confirm"
    REJECT = "reject"
    IGNORE = "ignore"


#: The dependency, annotated rather than declared as a default argument: this
#: project runs ruff with `B` selected, and `Depends(...)` in a default value
#: is exactly the pattern that rule exists for. Same idiom as
#: `routes/transactions.py`.
SessionDep = Annotated[Session, Depends(get_session)]


def _leg_out(
    session: Session, *, line_id: int
) -> tuple[TransferReviewLegOut, JournalLineRef]:
    """One journal line as a review leg, plus its pure-rule reference.

    Both come from the same three rows (`journal_line`, its `journal_entry`
    for the date and description, its `account` for the name), so the JSON
    and the confidence below cannot disagree about the amount or the date.
    """
    line = session.get(JournalLine, line_id)
    if line is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"journal_line {line_id} named by a review no longer exists",
        )
    entry = session.get(JournalEntry, line.journal_entry_id)
    if entry is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"journal_entry {line.journal_entry_id} no longer exists",
        )
    account = session.get(Account, line.account_id)
    if account is None:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"account {line.account_id} no longer exists",
        )
    entry_date: date = entry.entry_date
    description = entry.description or ""
    leg = TransferReviewLegOut(
        journal_line_id=line.id,
        description=description,
        amount_minor=line.amount,
        currency=line.currency.strip(),
        booked_date=entry_date,
        account_id=account.id,
        account_name=account.name,
    )
    ref = JournalLineRef(
        journal_line_id=line.id,
        account_id=line.account_id,
        amount_minor=line.amount,
        currency=line.currency.strip(),
        booked_date=entry_date,
        already_matched=False,
    )
    return leg, ref


def _confidence(outbound: JournalLineRef, candidate: JournalLineRef) -> Decimal:
    """The matcher's score for one queued pair, recomputed live.

    The linker stores no score on the review row, so the honest source is the
    pure `transfer_match` rule itself, run on the pair as it stands now. A
    pair that no longer satisfies the rule (e.g. an `entry_date` moved by a
    PATCH after queueing) reports `0.00` rather than a stale number.
    """
    pair = transfer_match(outbound, candidate)
    if pair is None:
        return Decimal("0.00")
    return pair.confidence


def _item(session: Session, *, review: TransferReview) -> TransferReviewItem:
    """One pending review as the queue's JSON contract."""
    outbound_leg, outbound_ref = _leg_out(
        session, line_id=review.outbound_journal_line_id
    )
    candidates: list[TransferReviewCandidateOut] = []
    for candidate_id in review.candidate_journal_line_ids:
        leg, ref = _leg_out(session, line_id=int(candidate_id))
        candidates.append(
            TransferReviewCandidateOut(
                journal_line_id=leg.journal_line_id,
                description=leg.description,
                amount_minor=leg.amount_minor,
                currency=leg.currency,
                booked_date=leg.booked_date,
                account_id=leg.account_id,
                account_name=leg.account_name,
                confidence=_confidence(outbound_ref, ref),
            )
        )
    reason = str(review.reason)
    typed_reason: Literal["multi_candidate", "low_confidence"]
    if reason == "multi_candidate":
        typed_reason = "multi_candidate"
    elif reason == "low_confidence":
        typed_reason = "low_confidence"
    else:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"transfer_review {review.id} has unknown reason {reason!r}",
        )
    return TransferReviewItem(
        id=review.id,
        outbound=outbound_leg,
        candidates=candidates,
        reason=typed_reason,
        created_at=review.created_at,
    )


def _pending_or_404(session: Session, *, review_id: int) -> TransferReview:
    """The pending review, or why there is nothing to decide.

    Read here so the route can answer 404 itself: `confirm_review` and its
    siblings raise a bare `ValueError` for both "missing" and "bad candidate",
    and the two need different status codes.
    """
    review = session.get(TransferReview, review_id)
    if review is None or review.status != "pending":
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No pending transfer review {review_id}",
        )
    return review


@router.get(
    "/transfers",
    summary="List pending transfer reviews",
    response_model=list[TransferReviewItem],
)
def list_transfer_reviews(session: SessionDep) -> list[TransferReviewItem]:
    """Everything the matcher queued and no human has decided yet.

    Ordered by queue age so the oldest question is first. Resolved rows are
    absent: confirming, rejecting or ignoring removes the item from this list
    rather than marking it in place.
    """
    reviews = session.scalars(
        select(TransferReview)
        .where(TransferReview.status == "pending")
        .order_by(TransferReview.created_at, TransferReview.id)
    ).all()
    return [_item(session, review=review) for review in reviews]


@router.get(
    "/transfers/stats",
    summary="Pending transfer reviews by reason",
    response_model=TransferReviewStats,
)
def transfer_review_stats(session: SessionDep) -> TransferReviewStats:
    """How many transfer questions are waiting, and why."""
    rows = session.execute(
        select(TransferReview.reason, func.count(TransferReview.id))
        .where(TransferReview.status == "pending")
        .group_by(TransferReview.reason)
    ).all()
    counts = {str(reason): int(total) for reason, total in rows}
    multi = counts.get("multi_candidate", 0)
    low = counts.get("low_confidence", 0)
    return TransferReviewStats(
        multi_candidate=multi, low_confidence=low, total=multi + low
    )


@router.post(
    "/transfers/{review_id}/confirm",
    summary="Confirm a transfer review",
    response_model=dict,
)
def confirm_transfer_review(
    review_id: int,
    payload: ConfirmTransferRequest,
    session: SessionDep,
) -> dict[str, object]:
    """Link the outbound leg to the chosen candidate.

    A review with more than one candidate requires
    `candidate_journal_line_id` naming one of them; a single-candidate review
    uses its sole candidate when this is absent. The link is stored with
    `match_method="user_confirmed"` and both entries are flagged as transfers.

    Raises:
        HTTPException: 404 when no pending review carries `review_id`; 400
            when a candidate is required and missing, or names a line the
            review did not list.
    """
    with session.begin():
        # Every read is inside the block: `Session.get()` autobegins, and
        # `session.begin()` refuses when a transaction is already running.
        _pending_or_404(session, review_id=review_id)
        try:
            match_id = confirm_review(
                session,
                review_id=review_id,
                candidate_journal_line_id=payload.candidate_journal_line_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
    return {"id": review_id, "status": "confirmed", "transfer_match_id": match_id}


@router.post(
    "/transfers/{review_id}/reject",
    summary="Reject a transfer review",
    response_model=dict,
)
def reject_transfer_review(review_id: int, session: SessionDep) -> dict[str, object]:
    """Reject a transfer question. No match, lines untouched.

    Raises:
        HTTPException: 404 when no pending review carries `review_id`.
    """
    with session.begin():
        _pending_or_404(session, review_id=review_id)
        try:
            reject_review(session, review_id=review_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
    return {"id": review_id, "status": "rejected"}


@router.post(
    "/transfers/{review_id}/ignore",
    summary="Ignore a transfer review",
    response_model=dict,
)
def ignore_transfer_review(review_id: int, session: SessionDep) -> dict[str, object]:
    """Ignore a transfer question and stop suggesting its pairs.

    One `transfer_skip` row per candidate, so re-running the sweep does not
    re-queue a pair the user has already dismissed.

    Raises:
        HTTPException: 404 when no pending review carries `review_id`.
    """
    with session.begin():
        _pending_or_404(session, review_id=review_id)
        try:
            ignore_review(session, review_id=review_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
    return {"id": review_id, "status": "ignored"}
