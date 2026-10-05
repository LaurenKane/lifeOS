"""transactions router — real, database-backed manual transaction CRUD.

M1: a manual transaction is a `source_record` like every other transaction, and
creating one writes, in ONE transaction:

    1. an `import_batch` with provider='manual', import_method='manual'
    2. a `journal_entry` and its two `journal_line` legs
    3. the `source_record`, already `status='posted'` and pointing at the entry

**THE WRITER IS NOT HERE.** `_write_manual_transaction` below is a thin adapter
over `finance.api.writers.write_posted_transaction`, and that is the point: a
file import and a typed entry are the same fact, so they must not have two
writers that can disagree about the counter-leg, the FX rate or the currency
exponent. What stays here is what is specific to a typed request — the
`raw_data` shape, the `manual` provider pair, and the mapping from the writer's
`ReferenceNotFound`/`PostingRefused` to the status codes this router has always
returned. Those are unchanged: 404 for a row that names nothing, 422 for a
posting the domain rules refuse.

**No parallel representation.** There is no in-memory list here any more. What a
manual transaction IS, is what an imported transaction is: a raw record plus a
balanced journal entry. Anything else would be a second ledger that the balance
trigger cannot see.

The counter-leg convention lives in
`finance.domain.services.manual_posting` and is documented at length there.
Short version: a EUR 40.50 expense out of checking is `checking -40.50` against
a seeded `equity` account `+40.50`, and when that account cannot be resolved the
request is REFUSED. A silently mis-posted expense is far worse than a refused
one.

**Every write is inside `with session.begin()`.** Not a committing context
manager: a FastAPI dependency's teardown runs after the response is serialised,
so a commit
rejected by the deferred balance trigger would surface as a 500 after the bytes
were already sent. See `finance.api.deps`.

The raw side is append-only and the database enforces it. `raw_data` and
`raw_description` are never written here after the INSERT, and DELETE is refused
by the `raw_data_immutable` trigger — which this router surfaces as a 409 rather
than swallowing or crashing. `import_batch.raw_payload` is deliberately NULL:
there is no file, so there is nothing to checksum and nothing to replay from.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from core.money import Currency, Money
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import and_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from finance.api.categorize import categorize_posted_record, upsert_learned_rule
from finance.api.deps import get_session
from finance.api.schemas import (
    ManualTransactionRequest,
    ManualTransactionUpdate,
    TransactionSummary,
)
from finance.api.writers import (
    PostingRefused,
    ReferenceNotFound,
    finish_import_batch,
    leg_count,
    open_import_batch,
    resolve_contra_account,
    write_posted_transaction,
)
from finance.domain.models.accounts import Account
from finance.domain.models.importer import SourceRecord
from finance.domain.models.ledger import JournalEntry, JournalLine
from finance.domain.models.reference import Currency as CurrencyRow
from finance.domain.models.taxonomy import Category
from finance.domain.services.manual_posting import (
    PostingLeg,
    absorb_fx_residual,
    build_expense_legs,
)
from finance.public import TransactionStatus

router = APIRouter(tags=["finance"], prefix="/transactions")

#: The dependency, annotated rather than declared as a default argument: this
#: project runs ruff with `B` selected, and `Depends(...)` in a default value is
#: exactly the pattern that rule exists for. It also leaves `status` free to be
#: the query parameter it is on the wire.
SessionDep = Annotated[Session, Depends(get_session)]

#: SQLSTATE 23514, `check_violation`. Every invariant trigger in migration 0001
#: raises with it: the balance check, the minimum-two-lines check and
#: `raw_data_immutable`. Restated here rather than imported, because a handler
#: that reads the constant out of the trigger body agrees with every value of it.
CHECK_VIOLATION = "23514"

#: SQLSTATE 23505, `unique_violation`. For a manual entry this is
#: `uq_sr_fingerprint`: the identical transaction is already recorded.
UNIQUE_VIOLATION = "23505"

#: The import_batch identity of a manually entered transaction. Both values are
#: in the migration's CHECK list, so this is a data decision the schema already
#: made rather than a new one.
MANUAL_PROVIDER = "manual"
MANUAL_IMPORT_METHOD = "manual"


# ---------------------------------------------------------------------------
# Reading the tables
# ---------------------------------------------------------------------------


def _require_account(session: Session, account_id: int) -> Account:
    """Load one account, or refuse.

    404 rather than 500: a request naming an account that does not exist is a
    client mistake, and the `ForeignKeyViolation` the INSERT would raise is an
    unhandled database error that says less about it.

    `finance.api.writers._require_account` raises the same refusal for the write
    path; the two are separate on purpose. This one still raises
    `HTTPException`, because the callers here are handlers whose contract is a
    status code, and it is called BEFORE the transaction block so the read is a
    plain lookup rather than a write-path refusal.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No account {account_id}",
        )
    return account


def _require_currency(session: Session, code: str) -> CurrencyRow:
    """Load `finance.currency` for `code`, or refuse.

    The table is the authority for `decimals`, which is the whole point: there
    is no 2-decimal assumption anywhere below this line, so JPY (0) and BTC (8)
    are rows rather than special cases.
    """
    row = session.get(CurrencyRow, code)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown currency {code!r}; it must exist in the currency table, "
                "because currency.decimals is the authority for its minor units"
            ),
        )
    return row


def _require_category(session: Session, category_id: int) -> Category:
    """Load one category, or refuse.

    Same reasoning as `_require_account`: an id that names no row is a client
    mistake, and the `ForeignKeyViolation` the UPDATE would raise is a 500 that
    says less.
    """
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No category {category_id}",
        )
    return category


# ---------------------------------------------------------------------------
# Writing a manual transaction
# ---------------------------------------------------------------------------


def _raw_payload(
    request: ManualTransactionRequest, *, counter_account_id: int
) -> dict[str, object]:
    """What the user actually typed, verbatim, as `raw_data`.

    The exact request, not a re-rendering of it, so a replay can reproduce the
    fingerprint without this API being available. The signed amount is kept as
    the STRING that came in: parsing it again is the pipeline's job, and storing
    a parsed-and-reformatted number here would make `raw_data` a derived value
    pretending to be evidence.

    Absent fields are kept as `None` rather than dropped, so the shape of the
    request stays recoverable. No float appears anywhere: the minor units live
    in `source_record.raw_amount`, which is a BIGINT.
    """
    return {
        "source": "api",
        "provider": MANUAL_PROVIDER,
        "description": request.description,
        "amount": request.amount,
        "currency": request.currency,
        "booked_date": request.booked_date.isoformat(),
        "value_date": (
            request.value_date.isoformat() if request.value_date is not None else None
        ),
        "counter_account_id": counter_account_id,
    }


def _legs(**kwargs: object) -> Sequence[PostingLeg]:
    """The leg policy for a manual posting, named so a test can replace it.

    This looks like indirection for its own sake and is not.
    `TestAnUnbalancedEntryIsRefusedByTheDatabase` sabotages
    `build_expense_legs` and `absorb_fx_residual` ON THIS MODULE, and it is the
    only proof the database would actually catch a bad entry. When this router
    stopped owning the posting (see `finance.api.writers`), that sabotage had
    somewhere to land or the test would go green while checking a perfectly
    balanced entry — which is worse than no test at all, and its own docstring
    says so.

    So the POLICY is injected and the DEFAULT is the same two functions the
    writer calls itself: same arithmetic, same behaviour. Only the seam is new,
    and it exists so that "the writer builds legs" and "which legs get built" are
    two names rather than one.

    Both are resolved from module globals at CALL time, which is what makes
    `monkeypatch.setattr` on this module reach them.
    """
    return absorb_fx_residual(build_expense_legs(**kwargs))  # type: ignore[arg-type]


def _resolved_counter_account_id(session: Session, requested_id: int | None) -> int:
    """The contra-account's id, resolved here so `raw_data` can record it.

    `_raw_payload` stores the id that was actually used rather than the id that
    was asked for, because when no id is asked for there IS no other answer to
    store: the default resolver picks by name, and recording "None" in the raw
    payload would make a replay unable to reproduce the fingerprint.

    Resolving twice — once here, once inside the writer — is deliberate. The
    writer owns the refusal (it raises `PostingRefused`, not `HTTPException`),
    and this call happens BEFORE the batch is opened so a request that will be
    refused leaves no batch behind at all. The cost is one extra indexed scan;
    the alternative is a payload whose value can disagree with the entry.
    """
    return resolve_contra_account(session, requested_id=requested_id).id


def _write_manual_transaction(
    session: Session,
    request: ManualTransactionRequest,
) -> int:
    """Write one manual transaction and return its `source_record` id.

    A THIN ADAPTER over `finance.api.writers`, and deliberately so. What this
    function still owns is everything that is specific to a typed entry: the
    `raw_data` shape (the exact request, so a replay can reproduce the
    fingerprint without this API), the `manual` provider pair, and turning the
    writer's two refusals into the status codes this router has always returned.
    What it no longer owns is the posting itself — the counter-leg convention,
    the FX rate, the currency exponent and the fingerprint all live in the
    writer now, which is what makes "exactly one writer" true rather than
    aspirational.

    The caller owns the transaction: this function never begins or commits one,
    so a background path can reuse it without a second, differently-committed
    write.
    """
    funding = _require_account(session, request.account_id)
    currency_row = _require_currency(session, request.currency)

    # The account/currency agreement is checked HERE, before anything is written,
    # and the order is load-bearing: asking for a EUR rate on a EUR/USD transaction
    # is a legitimate question with a legitimate answer, so looking the rate up
    # first would report a missing rate when the actual mistake is that the
    # transaction is in the wrong currency for its own account. The specific error
    # first. (The writer checks it again, against the amount it was handed, because
    # a writer with two callers cannot trust a caller to have checked.)
    if funding.currency.strip() != request.currency:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Account {funding.id} holds {funding.currency.strip()} but the "
                f"transaction is in {request.currency}. "
                "`journal_line.currency` is a snapshot of the account's own "
                "currency, so this is a mistake in the request, not a conversion."
            ),
        )

    # `currency.decimals` is the only authority for the exponent. A `scaleb(2)`
    # here would store JPY 1,234 as 123,400: off by a factor of 100.
    currency = Currency(code=currency_row.code, decimals=currency_row.decimals)
    amount = Money.from_decimal(request.amount_decimal, currency)

    try:
        # Resolved INSIDE the try so a refusal maps to a 422 here rather than
        # escaping as a bare ValueError, and after the account/currency check so
        # the more specific error still wins.
        counter_account_id = _resolved_counter_account_id(
            session, request.counter_account_id
        )
        batch_id = open_import_batch(
            session,
            provider=MANUAL_PROVIDER,
            import_method=MANUAL_IMPORT_METHOD,
            status="completed",
            account_id=funding.id,
            stats={"manual_entry": 1},
        )
        record_id = write_posted_transaction(
            session,
            batch_id=batch_id,
            funding_account_id=funding.id,
            description=request.description,
            amount=amount,
            booked_date=request.booked_date,
            raw_data=_raw_payload(request, counter_account_id=counter_account_id),
            counter_account_id=counter_account_id,
            occurrence_index=1,
            leg_builder=_legs,
        )
        # Categorize what the user just typed, when the engine is sure. A
        # manual entry with no matching rule still lands uncategorized — the
        # review queue is the honest answer for a first-seen payee, and the
        # correction the user then makes is what teaches the system.
        categorize_posted_record(
            session,
            source_record_id=record_id,
            account_id=funding.id,
            description=request.description,
        )
        finish_import_batch(
            session,
            batch_id=batch_id,
            status="completed",
            stats={
                "manual_entry": 1,
                "journal_entry_legs": leg_count(session, source_record_id=record_id),
            },
        )
    except ReferenceNotFound as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PostingRefused as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return record_id


# ---------------------------------------------------------------------------
# Reading one transaction back
# ---------------------------------------------------------------------------


def _summary(
    record: SourceRecord, line: JournalLine | None, *, learned: bool = False
) -> TransactionSummary:
    """One `source_record` as `TransactionSummary`.

    `fingerprint` goes back to the 64-character hex the API accepted, because
    that is what a client compares against `FingerprintResult.fingerprint` from
    `finance.public`; the BYTEA column is the same value.

    `category_id` and `transfer_match_id` come from the funding leg — the
    `journal_line` on the account this transaction belongs to — because that is
    where categorisation is decided. A record whose entry has been unlinked has
    no line and therefore no category.

    `learned` is True only from the PATCH that taught the system; every other
    caller leaves the default, because nothing was learned there.
    """
    return TransactionSummary(
        id=record.id,
        account_id=record.account_id,
        fingerprint=record.fingerprint.hex(),
        raw_description=record.raw_description,
        raw_amount=record.raw_amount,
        raw_currency=record.raw_currency.strip(),
        raw_date=record.raw_date,
        status=TransactionStatus(record.status),
        journal_entry_id=record.journal_entry_id,
        transfer_match_id=None if line is None else line.transfer_match_id,
        category_id=None if line is None else line.category_id,
        learned=learned,
    )


def _funding_line_clause() -> ColumnElement[bool]:
    """Join predicate: the `journal_line` on THIS record's own account.

    Without `account_id` in the predicate, a transfer entry contributes two rows
    to a listing of one transaction and the counter-leg's category is reported as
    the transaction's category.
    """
    return and_(
        JournalLine.journal_entry_id == SourceRecord.journal_entry_id,
        JournalLine.account_id == SourceRecord.account_id,
    )


def _get_record(
    session: Session, record_id: int
) -> tuple[SourceRecord, JournalLine | None]:
    """Load one record and its funding leg, or refuse.

    `limit(1)` ordered by `sort_order` rather than "expect exactly one": a split
    entry may legitimately hold two lines on one account, and picking the first
    by an explicit order is defined, where letting the driver return both rows
    would be a 500 in the middle of a read.
    """
    row = session.execute(
        select(SourceRecord, JournalLine)
        .outerjoin(JournalLine, _funding_line_clause())
        .where(SourceRecord.id == record_id)
        .order_by(JournalLine.sort_order)
        .limit(1)
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No transaction {record_id}",
        )
    record, line = row
    return record, line


# ---------------------------------------------------------------------------
# Database rejections, turned into status codes
# ---------------------------------------------------------------------------


def _as_http_error(exc: DBAPIError) -> HTTPException | None:
    """Map a database rejection to a client error, or None if it is not one.

    Two SQLSTATEs are mapped, and anything else comes back as None so the caller
    re-raises: an unmapped database error is a server fault, and dressing it as a
    4xx would tell the client its request was wrong when it was not.
    """
    origin = exc.orig if exc.orig is not None else exc
    code = getattr(origin, "sqlstate", None)
    if not isinstance(code, str):
        code = getattr(origin, "pgcode", None)
    if not isinstance(code, str):
        return None

    message = str(origin).splitlines()[0]
    if code == CHECK_VIOLATION:
        return HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Refused by the ledger: {message}",
        )
    if code == UNIQUE_VIOLATION:
        return HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                "An identical transaction is already recorded (same account, "
                f"amount, date and description): {message}"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", summary="List transactions", response_model=list[TransactionSummary])
def list_transactions(
    session: SessionDep,
    account_id: int | None = None,
    status: TransactionStatus | None = None,
) -> list[TransactionSummary]:
    """Transactions, optionally filtered by account and pipeline status.

    Status is a `SourceRecord` state, never a `JournalEntry` one: one entry can
    be produced by several source records over time, so the state belongs to the
    raw record.
    """
    query = select(SourceRecord, JournalLine).outerjoin(
        JournalLine, _funding_line_clause()
    )
    if account_id is not None:
        query = query.where(SourceRecord.account_id == account_id)
    if status is not None:
        query = query.where(SourceRecord.status == status.value)
    rows = session.execute(query.order_by(SourceRecord.raw_date, SourceRecord.id)).all()
    return [_summary(record, line) for record, line in rows]


@router.post(
    "",
    summary="Create a manual transaction",
    response_model=TransactionSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_transaction(
    payload: ManualTransactionRequest,
    session: SessionDep,
) -> TransactionSummary:
    """Record a user-entered transaction, and post it to the ledger.

    The request carries `amount` as a string so no float can enter the ledger
    from a JSON client, and it is SIGNED: `"40.50"` is money in, `"-40.50"` is
    money out. The minor units come from
    `Money.from_decimal(value, Currency(code, decimals))` with `decimals` read
    out of the `currency` table — never a hardcoded 2.

    What lands is a `source_record` in status `posted`, an `import_batch` of
    provider `manual`, and a two-leg journal entry whose `amount_base` legs sum
    to exactly zero. All in one transaction: the deferrable balance trigger
    fires at COMMIT, and a partially written manual entry is exactly the thing
    that must never exist.

    Raises:
        HTTPException: 404 for an unknown account; 422 for an unknown currency,
            a missing FX rate, or a counter-leg that cannot be resolved; 409 if
            the ledger's own triggers refuse the entry at commit, or if an
            identical transaction is already recorded.
    """
    try:
        with session.begin():
            record_id = _write_manual_transaction(session, payload)
    except DBAPIError as exc:
        mapped = _as_http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    record, line = _get_record(session, record_id)
    return _summary(record, line)


# DECLARATION ORDER IS LOAD-BEARING.
#
# Starlette matches routes in registration order, so a literal segment declared
# AFTER a one-segment parameterised route is unreachable: `GET
# /transactions/uncategorized` is captured by `GET /transactions/{record_id}`
# below and never reaches the handler. Before that route was typed `int` this
# surfaced as a 404 from a lookup miss; retype it and it surfaces as a 422,
# which points the diagnosis at validation rather than at shadowing. Both are
# wrong, so every literal-segment route here is declared BEFORE `/{record_id}`.
@router.get(
    "/uncategorized",
    summary="List uncategorized transactions",
    response_model=list[TransactionSummary],
)
def list_uncategorized(session: SessionDep) -> list[TransactionSummary]:
    """Transactions with no category, in date order.

    `categorized` is derived (`journal_line.category_id IS NOT NULL`), not a
    stored state on the raw record, so this is a filter over the same rows the
    list endpoint returns rather than a separate table.

    The queue is the mirror image of `idx_jl_uncat` in migration 0001 — an
    uncategorised, unmatched, NEGATIVE leg. Negative because this screen exists
    to answer "what did I spend"; an uncategorised positive leg is income
    arriving, which is not a question anyone opens this screen to ask.

    A record with no journal entry has no line to categorise and is therefore not
    queued. That is stated explicitly rather than left to the `amount_base < 0`
    predicate, because the answer is otherwise invisible.
    """
    rows = session.execute(
        select(SourceRecord, JournalLine)
        .outerjoin(JournalLine, _funding_line_clause())
        .where(
            SourceRecord.journal_entry_id.is_not(None),
            JournalLine.category_id.is_(None),
            JournalLine.transfer_match_id.is_(None),
            JournalLine.amount_base < 0,
        )
        .order_by(SourceRecord.raw_date, SourceRecord.id)
    ).all()
    return [_summary(record, line) for record, line in rows]


@router.get(
    "/{record_id}", summary="Get a transaction", response_model=TransactionSummary
)
def get_transaction(record_id: int, session: SessionDep) -> TransactionSummary:
    """One transaction by source-record id.

    The path parameter is typed `int`, so a malformed id is a 422 from FastAPI's
    validation rather than a 404 from a lookup that could never have matched.

    Raises:
        HTTPException: 404 when no such record.
    """
    record, line = _get_record(session, record_id)
    return _summary(record, line)


@router.patch(
    "/{record_id}",
    summary="Update a transaction",
    response_model=TransactionSummary,
)
def update_transaction(
    record_id: int,
    payload: ManualTransactionUpdate,
    session: SessionDep,
) -> TransactionSummary:
    """Change what may be changed, and refuse the rest.

    Only two things are editable, and both are ledger facts rather than evidence:

    * `category_id` on the funding leg — what a user actually came here to
      change, and what moves a transaction out of the uncategorized queue.
    * `entry_date` on the journal entry — the accounting date.

    With `learn=true` alongside a `category_id`, the correction also teaches
    the system: the record's `raw_description` — the frozen evidence, never
    anything the client typed in this request — is stored as a learned rule
    in the SAME transaction, so the category and the lesson commit together
    or not at all. The response's `learned` flag says whether that happened.

    What is NOT editable is the point. `raw_description` and `raw_data` are frozen
    by the `raw_data_immutable` trigger, and there is no way to change the amount,
    the currency or the description here, because the raw side is what a replay
    reads. Correcting an amount means reversing the entry, not editing the
    statement, and this router does not offer that: a reversal is a new fact, not
    an edit of this one.

    Raises:
        HTTPException: 404 for an unknown record or category; 409 for a record
            with no posted entry to correct; 422 when the description reduces
            to no learnable pattern.
    """
    learned = False
    try:
        with session.begin():
            # Every read is inside the block. `Session.get()` autobegins, so a
            # lookup performed before `session.begin()` would make the block
            # raise "a transaction is already begun" — a bug that only appears on
            # the write path, which is exactly where it is hardest to see.
            if payload.category_id is not None:
                _require_category(session, payload.category_id)
            record, line = _get_record(session, record_id)
            if record.journal_entry_id is None or line is None:
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=(
                        f"Transaction {record_id} has no posted journal entry, so "
                        "there is nothing to correct"
                    ),
                )
            if payload.category_id is not None:
                line.category_id = payload.category_id
                if payload.learn:
                    # The pattern comes from the frozen evidence, not the
                    # request: the client names the category, never the text
                    # future transactions must match.
                    try:
                        upsert_learned_rule(
                            session,
                            description=record.raw_description,
                            category_id=payload.category_id,
                        )
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=str(exc),
                        ) from exc
                    learned = True
            if payload.entry_date is not None:
                entry = session.get(JournalEntry, record.journal_entry_id)
                if entry is None:
                    raise HTTPException(
                        status_code=http_status.HTTP_409_CONFLICT,
                        detail=(
                            f"Journal entry {record.journal_entry_id} no longer exists"
                        ),
                    )
                entry.entry_date = payload.entry_date
            session.flush()
    except DBAPIError as exc:
        mapped = _as_http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
    record, line = _get_record(session, record_id)
    return _summary(record, line, learned=learned)


@router.delete(
    "/{record_id}",
    summary="Delete a transaction",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_transaction(record_id: int, session: SessionDep) -> None:
    """Delete a manual transaction — which the database will refuse.

    This handler issues the DELETE and lets the answer be the database's.
    `trg_source_record_raw_immutable` is `BEFORE DELETE` and raises 23514 for
    every row, without exception: `raw_data_immutable` is this project's
    highest-priority invariant, and a service that could quietly delete evidence
    would make it decorative. The refusal is mapped to 409 carrying the trigger's
    own message, so the caller learns the rule rather than a generic error.

    That is not an oversight in the route and not a TODO. Correcting a posted
    transaction means reversing it in the journal — a new, balanced entry — which
    is a later milestone. Until then this endpoint's honest answer is "no, and
    here is why".

    Raises:
        HTTPException: 404 when no such record; 409 when the trigger refuses.
    """
    try:
        with session.begin():
            record, _ = _get_record(session, record_id)
            session.delete(record)
            session.flush()
    except DBAPIError as exc:
        mapped = _as_http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
