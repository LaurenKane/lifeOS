"""transactions router — read access to transactions.

M0 scope: typed route surface so the OpenAPI spec is real. Handlers are backed
by an in-memory list, which is honest about being a stub rather than pretending
to query a table that does not exist yet.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from finance.api.schemas import ManualTransactionRequest, TransactionSummary
from finance.public import TransactionStatus

router = APIRouter(tags=["finance"], prefix="/transactions")

_TRANSACTIONS: list[TransactionSummary] = []


@router.get("", summary="List transactions", response_model=list[TransactionSummary])
def list_transactions(
    account_id: str | None = None,
    status: TransactionStatus | None = None,
) -> list[TransactionSummary]:
    """Transactions, optionally filtered by account and pipeline status.

    Status is a `SourceRecord` state, never a `JournalEntry` one: one entry can
    be produced by several source records over time, so the state belongs to the
    raw record.
    """
    results = list(_TRANSACTIONS)
    if account_id is not None:
        results = [t for t in results if t.account_id == account_id]
    if status is not None:
        results = [t for t in results if t.status == status]
    return results


@router.post(
    "",
    summary="Create a manual transaction",
    response_model=TransactionSummary,
    status_code=201,
)
def create_transaction(
    payload: ManualTransactionRequest,
) -> TransactionSummary:
    """Record a user-entered transaction.

    The request carries `amount` as a string so no float can enter the ledger
    from a JSON client. M0 stores the validated value in memory; the row lands
    with the M1 migrations.
    """
    record = TransactionSummary(
        id=f"manual-{len(_TRANSACTIONS) + 1}",
        account_id=payload.account_id,
        # Empty until the normalizer runs; a manual row is created as
        # `imported`, not `posted`.
        fingerprint="",
        raw_description=payload.description,
        raw_amount=int(payload.amount_decimal.scaleb(2)),
        raw_currency=payload.currency,
        raw_date=payload.booked_date,
        status=TransactionStatus.IMPORTED,
    )
    _TRANSACTIONS.append(record)
    return record


@router.get(
    "/{record_id}", summary="Get a transaction", response_model=TransactionSummary
)
def get_transaction(record_id: str) -> TransactionSummary:
    """One transaction by source-record id.

    Raises:
        HTTPException: 404 when no such record.
    """
    for transaction in _TRANSACTIONS:
        if transaction.id == record_id:
            return transaction
    raise HTTPException(status_code=404, detail=f"No transaction {record_id}")


@router.get(
    "/uncategorized",
    summary="List uncategorized transactions",
    response_model=list[TransactionSummary],
)
def list_uncategorized() -> list[TransactionSummary]:
    """Transactions with no category, in date order.

    `categorized` is derived (`category_id IS NOT NULL`), not a stored state, so
    this is a filter over the same list rather than a separate table.
    """
    return sorted(
        (t for t in _TRANSACTIONS if t.category_id is None),
        key=lambda t: (t.raw_date, t.id),
    )
