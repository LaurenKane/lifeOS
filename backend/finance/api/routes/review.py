"""review router — the uncategorized queue.

The single most important screen in the app (section I). Its shape is the
project's answer to "perfect automatic dedup is impossible": nothing is guessed
past its confidence, so everything ambiguous lands here instead.

Three decisions per item, and each one feeds the learning loop:

    CONFIRM  raise the merchant alias confidence to 1.0, write a learned rule
    REJECT   leave uncategorized; the candidate was wrong
    IGNORE   not a transaction at all; mark the source record so it stops
             reappearing in the queue
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import APIRouter, HTTPException

from finance.api.schemas import TransactionSummary
from finance.public import TransactionSummary as PublicTransactionSummary

router = APIRouter(tags=["finance"], prefix="/review")


class ReviewDecision(StrEnum):
    """The three outcomes the queue offers."""

    CONFIRM = "confirm"
    REJECT = "reject"
    IGNORE = "ignore"


# M0: decisions are not persisted — they need the tables.
_DECISIONS: dict[str, ReviewDecision] = {}


@router.get(
    "",
    summary="List transactions awaiting review",
    response_model=list[PublicTransactionSummary],
)
def list_review_queue() -> list[PublicTransactionSummary]:
    """Everything the pipeline could not decide on its own.

    Ordered by date so the oldest, least-recently-looked-at item is first. A
    queue ordered by insertion age would bury a six-month-old Amex row behind
    yesterday's duplicates.
    """
    return []


@router.post(
    "/{record_id}/{decision}",
    summary="Decide a review item",
    response_model=TransactionSummary,
)
def decide(record_id: int, decision: ReviewDecision) -> TransactionSummary:
    """Record CONFIRM, REJECT or IGNORE for one queued transaction.

    `decision` is a path parameter constrained to the enum, so an unknown value
    is a 422 rather than a silently-ignored string. `record_id` is typed `int`
    for the same reason: a malformed id is a 422, not a 404 from a lookup that
    could never have matched.

    Raises:
        HTTPException: 404 when no such record is queued.
    """
    raise HTTPException(
        status_code=404,
        detail=f"No queued transaction {record_id} for {decision.value}",
    )


@router.get("/stats", summary="Queue depth by reason", response_model=dict)
def review_stats() -> dict[str, int]:
    """How many items are waiting, and why.

    Reported rather than hidden: a queue that grows silently is how a review
    workflow stops being used at all.
    """
    return {
        "uncategorized": 0,
        "possible_duplicate": 0,
        "possible_transfer": 0,
    }
