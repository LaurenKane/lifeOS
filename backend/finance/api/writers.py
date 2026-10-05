"""writers.py — the ONE place the finance API writes a posted transaction.

There was exactly one writer, in `routes/transactions.py`, and it was written for
a request body: it read a `ManualTransactionRequest`. An imported statement row
is the same fact arrived at by a different road, and giving it a second writer
would be the worst kind of duplication in this module — two places computing a
counter-leg, two places deciding `currency.decimals`, and two places that can
disagree. So this module holds the shared writer and
`routes/transactions.py::_write_manual_transaction` now calls it. One writer, two
callers.

WHY HERE AND NOT IN `finance.ingestion`
=======================================

`routes/__init__.py:7-8` confines database access to the routers, and until this
file there was no shared layer between them — each router had its own session
handling and nothing else. `finance/ingestion/` is the obvious alternative home
(`persistence.py`), and it is the wrong one: `ingestion/` imports zero ORM
modules and zero of `finance.domain` today, so a persistence module there would
be the repository's first INVERTED edge — domain/ingestion code importing the
thing that depends on it. `ARCHITECTURE.md:85-92` records what the last layer
rule violation cost; this is not where to find out whether the rule survives a
better argument. `api/deps.py` is the precedent this mirrors: infrastructure
inside `api/`, next to the callers that own the transaction.

THE CONTRACT
============

**It never begins, commits, or rolls back a transaction.** The caller owns it.
That is not tidiness — `finance.api.deps` explains why: the balance trigger is
`DEFERRABLE INITIALLY DEFERRED`, so it fires at COMMIT, and a COMMIT that a
dependency performed after the response was serialised would be a 500 the client
never sees the meaning of. Callers write `with session.begin():` and let the
exceptions below roll it back.

**It never raises `HTTPException`.** An HTTP status is a transport concern and
this module has no transport. It raises two `ValueError`s instead —
`ReferenceNotFound` (→ 404) and `PostingRefused` (→ 422) — and
`routes/transactions.py` maps both. Every caller's status codes are unchanged,
and a future background worker can reuse this without importing FastAPI.

**It never imports `finance.api.schemas`.** The writer's arguments are typed in
`core` and `finance.domain`, and the Pydantic layer stays a contract. The one
thing it needs from a request, the currency exponent, it reads out of the
`currency` TABLE rather than off a schema default.

**The arithmetic is not reimplemented.** `build_expense_legs` +
`absorb_fx_residual` are the rules, they live in
`domain/services/manual_posting.py`, and they are called here rather than
re-derived. A second implementation of "which account is the other side" is
exactly the divergence `manual_posting.py` was extracted to prevent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, Protocol

from core.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance.domain.models.accounts import Account
from finance.domain.models.importer import ImportBatch, SourceRecord
from finance.domain.models.ledger import JournalEntry, JournalLine
from finance.domain.models.reference import Currency as CurrencyRow
from finance.domain.models.reference import ExchangeRate
from finance.domain.models.transfers import TransferMatch
from finance.domain.services.card_payment import (
    CARD_PAYMENT_REASON,
    CARD_PAYMENT_WINDOW_DAYS_AFTER,
    CardPaymentLeg,
    build_card_payment_legs,
)
from finance.domain.services.manual_posting import (
    BASE_CURRENCY,
    CounterAccountUnresolvedError,
    ManualPostingError,
    PostingAccount,
    PostingLeg,
    absorb_fx_residual,
    build_expense_legs,
    resolve_counter_account_by_id,
    resolve_default_counter_account,
)
from finance.ingestion.dedupe import fingerprint_account_scope
from finance.ingestion.fingerprint import compute_fingerprint
from finance.public import TransactionStatus

__all__ = [
    "LegBuilder",
    "PostingRefused",
    "ReferenceNotFound",
    "default_leg_builder",
    "finish_import_batch",
    "leg_count",
    "open_import_batch",
    "resolve_contra_account",
    "write_card_payment",
    "write_posted_transaction",
    "write_unposted_transaction",
]


# ---------------------------------------------------------------------------
# Failures, as values rather than status codes
# ---------------------------------------------------------------------------


class ReferenceNotFound(ValueError):  # noqa: N818
    """A row this write depends on names no existing row. → HTTP 404.

    Not named `ReferenceNotFoundError`, unlike most exceptions. The pair it
    belongs to reads as a single phrase at every call site —
    `raise ReferenceNotFound("account", account_id)` says which lookup failed and
    `except (ReferenceNotFound, PostingRefused)` reads as a list of refusals — and
    the codebase's own rule (ruff `N818` asks for an `Error` suffix) is stylistic,
    deliberately silenced here rather than allowed to rename a name that is read
    more often than it is written.

    An `account_id` or a `category_id` that names nothing is a caller mistake, and
    the `ForeignKeyViolation` the INSERT would raise instead is a 500 that says
    less about it. Carries `missing_id` so a caller can name the id in its own
    message without parsing this one.
    """

    def __init__(self, what: str, missing_id: int) -> None:
        self.missing_id: int = missing_id
        super().__init__(f"No {what} {missing_id}")


class PostingRefused(ValueError):  # noqa: N818
    """A transaction cannot be posted, and the reason is known. → HTTP 422.

    Everything the caller could fix by changing what it asked for: an unknown
    currency, a missing FX rate, a currency that contradicts its own account, or
    a counter-leg the domain rules refuse to guess. A `ValueError` to match
    `AdapterParseError` and `ManualPostingError` — every one of these is "the
    input is wrong", not "the system is broken".

    `reason` carries the originating `ManualPostingError` when there was one, so
    a caller can distinguish a refusal the domain rules made from one this module
    made, without string-matching the message.
    """

    def __init__(
        self, message: str, *, reason: ManualPostingError | None = None
    ) -> None:
        self.reason: ManualPostingError | None = reason
        super().__init__(message)


#: `import_batch.status` values this module writes. The list is the migration's
#: own CHECK, restated so a typo is a name error rather than a 23514 at COMMIT.
#: `pending`/`processing` are what a batch carries while it is open.
BATCH_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "processing", "completed", "failed", "partial"}
)


class LegBuilder(Protocol):
    """The leg policy `write_posted_transaction` delegates its arithmetic to.

    Typed as a protocol rather than as `Callable[..., tuple[PostingLeg, ...]]`
    because the keyword arguments are the contract and a plain callable type would
    erase them — which is precisely the part a caller has to get right.
    """

    def __call__(
        self,
        *,
        funding_account: PostingAccount,
        counter_account: PostingAccount,
        amount: Money,
        rate: Decimal,
        counter_decimals: int | None = None,
    ) -> Sequence[PostingLeg]: ...


def default_leg_builder(
    *,
    funding_account: PostingAccount,
    counter_account: PostingAccount,
    amount: Money,
    rate: Decimal,
    counter_decimals: int | None = None,
) -> Sequence[PostingLeg]:
    """`absorb_fx_residual(build_expense_legs(...))` — the production policy.

    The ONLY implementation of this arithmetic in the API. It is a named function
    rather than an inline pair of calls so that "which legs get built" has one
    name in this module, the same way `fingerprint` and `_fingerprint` are two
    names for one rule.
    """
    return absorb_fx_residual(
        build_expense_legs(
            funding_account=funding_account,
            counter_account=counter_account,
            amount=amount,
            rate=rate,
            counter_decimals=counter_decimals,
        )
    )


# ---------------------------------------------------------------------------
# Reading, without an HTTP vocabulary
# ---------------------------------------------------------------------------


def _all_accounts(session: Session) -> list[PostingAccount]:
    """Every account as the posting rules see it.

    All of them, inactive ones included, because the message a caller gets for
    "no counter-leg" is only actionable if it can say "it exists but is
    inactive" rather than "not found". Active-and-inactive is a single indexed
    scan, so this is deliberately not cached across rows: a cache would be a
    second source of truth about which accounts exist, invalidated by nobody.
    """
    rows: Sequence[tuple[int, str, str, str, bool, str | None]] = session.execute(
        select(
            Account.id,
            Account.name,
            Account.currency,
            Account.account_nature,
            Account.is_active,
            Account.system_role,
        ).order_by(Account.id)
    ).all()
    return [
        PostingAccount(
            id=account_id,
            name=name,
            currency=currency.strip(),
            account_nature=nature,
            is_active=is_active,
            system_role=system_role,
        )
        for account_id, name, currency, nature, is_active, system_role in rows
    ]


def _require_account(session: Session, account_id: int) -> Account:
    """Load one account, or refuse. → `ReferenceNotFound`."""
    account = session.get(Account, account_id)
    if account is None:
        raise ReferenceNotFound("account", account_id)
    return account


def _require_currency(session: Session, code: str) -> CurrencyRow:
    """Load `finance.currency` for `code`, or refuse. → `PostingRefused`.

    The TABLE is the authority for `decimals`, and that is the whole point:
    `ingestion.normalize.normalize_currency` hardcodes 2 because it is the
    pre-database path, so JPY (0) and BTC (8) are rows rather than special cases.
    A code with no row has no exponent and therefore nothing to convert with, so
    it is refused rather than defaulted to 2 — defaulting would be the exact bug
    the currency table exists to prevent, arriving through the front door.
    """
    row = session.get(CurrencyRow, code)
    if row is None:
        raise PostingRefused(
            f"Unknown currency {code!r}; it must exist in the currency table, "
            "because currency.decimals is the authority for its minor units"
        )
    return row


def resolve_contra_account(
    session: Session, *, requested_id: int | None
) -> PostingAccount:
    """The contra-account: the designated system one, or the one the caller named.

    Public because a caller needs it for something the writer cannot do: a manual
    entry's `raw_data` records the account id that was ACTUALLY used, not the one
    that was asked for. When no id is asked for, the default resolver picks by
    role, so "None" would be a claim the entry does not back up.

    Raises:
        PostingRefused: with the resolver's own message, which names the role
            that was expected. A refusal that says only "no counter-leg" is not
            actionable; one that says "no account is designated as the system
            expense account" is.
    """
    accounts = _all_accounts(session)
    try:
        if requested_id is None:
            return resolve_default_counter_account(accounts)
        return resolve_counter_account_by_id(accounts, requested_id)
    except CounterAccountUnresolvedError as exc:
        raise PostingRefused(str(exc), reason=exc) from exc


def _base_rate(session: Session, currency_code: str, on_date: dt.date) -> Decimal:
    """Units of `currency_code` per 1 EUR on `on_date`, from `exchange_rate`.

    `amount_base` is EUR by definition, so a transaction in another currency
    cannot be stated without a rate, and inventing one would be worse than
    refusing. `1.0` for EUR itself, which needs no row.
    """
    if currency_code == BASE_CURRENCY:
        return Decimal(1)
    rate: Decimal | None = session.scalar(
        select(ExchangeRate.rate).where(
            ExchangeRate.date == on_date,
            ExchangeRate.base_currency == BASE_CURRENCY,
            ExchangeRate.quote_currency == currency_code,
        )
    )
    if rate is None:
        raise PostingRefused(
            f"No {BASE_CURRENCY}/{currency_code} exchange rate for "
            f"{on_date.isoformat()}. A transaction in a currency other than "
            f"{BASE_CURRENCY} cannot be stated without one, and the ledger "
            "does not guess a rate."
        )
    return rate


# ---------------------------------------------------------------------------
# The batch
# ---------------------------------------------------------------------------


def open_import_batch(
    session: Session,
    *,
    provider: str,
    import_method: str,
    status: str,
    account_id: int | None = None,
    source_filename: str | None = None,
    source_checksum: str | None = None,
    raw_payload: object | None = None,
    stats: dict[str, object] | None = None,
) -> int:
    """Open an `import_batch` and return its id.

    `account_id` is a convenience, not the owner of the rows: one file can cover
    two accounts, and `source_record.account_id` is authoritative per row. So
    `None` here is honest — an unattributed batch whose rows each name their own
    account, or a batch of rows that could not be attributed at all.

    `status` is supplied rather than defaulted because the two callers want
    different things: a manual entry opens `completed` (it is one transaction
    that is already fully written by the time the batch is asked for), while a
    file import opens `processing` and is closed by `finish_import_batch` with
    what actually persisted. A default would hide that difference.

    Args:
        session: The caller's session. Not begun, committed or rolled back here.
        provider: An `import_batch.provider` CHECK value.
        import_method: An `import_batch.import_method` CHECK value.
        status: An `import_batch.status` CHECK value.
        account_id: The account this batch is nominally about, or None.
        source_filename: The uploaded file's name.
        source_checksum: SHA-256 of the uploaded bytes.
        raw_payload: What a replay reads. JSONB, so a caller that stores a file
            gzips and base64s it first — see `routes/imports.py`.
        stats: JSONB counters. Defaults to `{}` at the column level.

    Returns:
        The new batch's id, flushed so a caller can reference it immediately.

    Raises:
        ValueError: If `status` is not a value the migration's CHECK accepts.
            Checked here so a typo is a name error rather than a 23514 surfacing
            at COMMIT, where nothing but the trigger's message survives.
    """
    if status not in BATCH_STATUSES:
        allowed = ", ".join(sorted(BATCH_STATUSES))
        msg = f"import_batch.status must be one of {allowed}, got {status!r}"
        raise ValueError(msg)
    batch = ImportBatch(
        account_id=account_id,
        provider=provider,
        import_method=import_method,
        status=status,
        source_filename=source_filename,
        source_checksum=source_checksum,
        raw_payload=raw_payload,
        stats=dict(stats or {}),
        completed_at=func.now(),
    )
    session.add(batch)
    session.flush()
    return batch.id


def finish_import_batch(
    session: Session,
    *,
    batch_id: int,
    status: str,
    stats: dict[str, object],
) -> None:
    """Close an `import_batch` with what actually persisted.

    `status` describes WHAT WAS WRITTEN, never what the parser thought it read.
    Those two are different claims: a parser can return 40 rows with no failures
    and the writer can post 39 of them, because one was a card payment that needs
    an account it does not have (`docs/adr/0007-imported-card-payment-is-a
    -transfer.md`). Copying the parse result onto the batch is how a short
    statement gets labelled `completed`.

    Raises:
        ValueError: If `status` is not a value the migration's CHECK accepts, or
            no batch carries `batch_id`.
    """
    if status not in BATCH_STATUSES:
        allowed = ", ".join(sorted(BATCH_STATUSES))
        msg = f"import_batch.status must be one of {allowed}, got {status!r}"
        raise ValueError(msg)
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        msg = f"No import_batch {batch_id} to finish"
        raise ValueError(msg)
    batch.status = status
    batch.stats = dict(stats)
    batch.completed_at = func.now()
    session.flush()


# ---------------------------------------------------------------------------
# The transaction
# ---------------------------------------------------------------------------


def _fingerprint(
    *,
    description: str,
    amount_minor: int,
    currency_code: str,
    booked_date: dt.date,
    account_id: int,
    occurrence_index: int,
) -> bytes:
    """The stored 32 raw bytes of this row's Tier-3 fingerprint.

    `compute_fingerprint` is SHA-256 hash-pinned and returns a 64-character hex
    digest; the column is BYTEA, so the digest is stored as its 32 raw bytes —
    half the index width, and byte-for-byte reproducible on replay. `.hex()` on
    the way out is the exact inverse.

    `occurrence_index` is the CONTENT-ONLY within-batch rank from
    `assign_occurrence_indices`, not a count of what is already stored. That
    choice is the dedup invariant, and it is spelled out where it is made (see
    `routes/imports.py`) because the alternative looks reasonable and is wrong:
    making the index depend on table state means every re-import shifts the
    indices of everything after it, so re-importing the same file rewrites the
    whole file instead of recognising it.
    """
    digest = compute_fingerprint(
        raw_description=description,
        raw_amount=amount_minor,
        raw_currency=currency_code,
        raw_date=booked_date.isoformat(),
        account_id=fingerprint_account_scope(account_id),
        occurrence_index=occurrence_index,
    )
    return bytes.fromhex(digest)


def _add_source_record(
    session: Session,
    *,
    batch_id: int,
    account_id: int,
    description: str,
    amount: Money,
    booked_date: dt.date,
    raw_data: dict[str, object],
    raw_posting_date: dt.date | None,
    occurrence_index: int,
    provider_txn_id: str | None,
    status: TransactionStatus,
    journal_entry_id: int | None,
    error_message: str | None,
) -> int:
    """Insert the raw side of one transaction and return its id.

    The single place a `source_record` is constructed in this API, whether or not
    it ended up posted. Everything above it decides WHICH of the two it is; this
    function only writes, so the fingerprint, the `raw_data` verbatim rule and
    the occurrence index cannot be honoured on one path and forgotten on the other.
    """
    record = SourceRecord(
        import_batch_id=batch_id,
        account_id=account_id,
        provider_txn_id=provider_txn_id,
        fingerprint=_fingerprint(
            description=description,
            amount_minor=amount.amount,
            currency_code=amount.currency.code,
            booked_date=booked_date,
            account_id=account_id,
            occurrence_index=occurrence_index,
        ),
        occurrence_index=occurrence_index,
        raw_data=raw_data,
        raw_description=description,
        raw_amount=amount.amount,
        raw_currency=amount.currency.code,
        raw_date=booked_date,
        raw_posting_date=raw_posting_date,
        status=status.value,
        journal_entry_id=journal_entry_id,
        error_message=error_message,
    )
    session.add(record)
    session.flush()
    return record.id


def _attach_source_record(
    session: Session,
    *,
    record: SourceRecord,
    journal_entry_id: int | None,
    status: TransactionStatus,
    error_message: str | None,
) -> int:
    """Point an existing `source_record` at a (re-)built entry and return its id.

    The replay path (`finance.api.replay`): the raw side already exists, so no
    new row is inserted and no fingerprint is computed — computing one would hit
    `uq_sr_fingerprint` on the row it came from. Only the three columns the
    immutability trigger does NOT protect are written: `journal_entry_id`,
    `status` and `error_message`. Everything else on the record is evidence and
    stays exactly as the import left it.
    """
    record.journal_entry_id = journal_entry_id
    record.status = status.value
    record.error_message = error_message
    session.flush()
    return record.id


def write_posted_transaction(
    session: Session,
    *,
    batch_id: int,
    funding_account_id: int,
    description: str,
    amount: Money,
    booked_date: dt.date,
    raw_data: dict[str, object],
    raw_posting_date: dt.date | None = None,
    counter_account_id: int | None = None,
    occurrence_index: int = 1,
    provider_txn_id: str | None = None,
    status: TransactionStatus = TransactionStatus.POSTED,
    leg_builder: LegBuilder | None = None,
    source_record_id: int | None = None,
) -> int:
    """Write one `source_record` and post it to the ledger. Return its id.

    One `journal_entry` with its legs, one `source_record` already carrying the
    entry's id, in the caller's transaction. Two flushes, because the entry
    needs its id before the lines can reference it and the record needs the
    entry's id.

    `amount.amount` is MINOR UNITS and is stored verbatim. The currency's
    exponent is re-read from the `currency` table rather than taken from
    `amount.currency`, because a `Money` built by the pre-database path carries
    the hardcoded default of 2 (`normalize_currency`) and that is 100x wrong for
    JPY and 10^-6 wrong for BTC. Re-deriving the `Money` from the table is what
    keeps `amount_base` correct; the integer itself is never rescaled, because by
    the time a caller has a `Money` the scaling decision has already been made
    once, at the adapter's edge.

    Args:
        session: The caller's session. Never begun, committed or rolled back here.
        batch_id: An `import_batch` id from `open_import_batch`.
        funding_account_id: The account the money left. Its currency MUST equal
            the transaction currency — `journal_line.currency` is a snapshot of
            `account.currency`, so a mismatch is a caller mistake, not a
            conversion.
        description: The row's description, stored verbatim in `raw_description`
            AND used to fingerprint. Not re-normalised here: an adapter already
            normalised it and a manual entry is the user's own text, and the two
            must not diverge.
        amount: Signed minor units. Positive is a credit.
        booked_date: The accounting date.
        raw_data: The row exactly as the provider gave it. Never mutated, never
            re-rendered — it is what a replay reads and the database freezes it
            on INSERT.
        raw_posting_date: A second date the provider stated (Amex's "Datum
            verwerkt"). Kept separate because it differs from `booked_date` on
            about a third of the rows in a real statement.
        counter_account_id: An explicit contra-account, or None for the seeded
            system expense account. Explicit is validated exactly as the default
            is; it is not a way around the equity-only rule.
        occurrence_index: This row's rank among content-identical rows in its
            batch. See `_fingerprint`.
        provider_txn_id: The provider's own id, or None. NULL on every v1 PDF
            path, which is precisely why Tier 3 exists.
        status: `POSTED` writes the entry and links it. Any other state
            DELEGATES to `write_unposted_transaction` — the entry is never built,
            because a row the caller has already decided not to post must not go
            anywhere near the expense resolver.
        leg_builder: The leg POLICY, injectable only so a caller can replace it.
            Defaults to `default_leg_builder`, which is `absorb_fx_residual(
            build_expense_legs(...))` — the same two functions, and the only
            implementation of that arithmetic anywhere in the API. A caller that
            substitutes something else is running a test, and says so in a comment
            where it does it.
        source_record_id: An existing `source_record` to re-link instead of
            inserting one. The replay path: no fingerprint is computed and
            `uq_sr_fingerprint` is never touched. None is the normal path — a
            new row for a new transaction — and every current caller passes it.

    Returns:
        The new `source_record` id.

    Raises:
        ReferenceNotFound: `funding_account_id` names no account → 404.
            `source_record_id` names no record, when one is given.
        PostingRefused: unknown currency, missing FX rate, a currency that
            contradicts its account, or a counter-leg the domain rules refuse →
            422.
    """
    if status is not TransactionStatus.POSTED:
        raise PostingRefused(
            f"{status.value!r} is not a posted state, so this writer will not "
            "post one; use write_unposted_transaction, which never reaches the "
            "expense resolver"
        )

    funding = _require_account(session, funding_account_id)
    currency_row = _require_currency(session, amount.currency.code)

    # The account/currency agreement is checked HERE, before the rate lookup, and
    # the order is load-bearing: asking for a EUR rate on a EUR/USD transaction is
    # a legitimate question with a legitimate answer, so looking the rate up first
    # would report a missing rate when the actual mistake is that the transaction
    # is in the wrong currency for its own account. The specific error first.
    if funding.currency.strip() != amount.currency.code:
        raise PostingRefused(
            f"Account {funding.id} holds {funding.currency.strip()} but the "
            f"transaction is in {amount.currency.code}. "
            "`journal_line.currency` is a snapshot of the account's own "
            "currency, so this is a mistake in the request, not a conversion."
        )

    counter = resolve_contra_account(session, requested_id=counter_account_id)
    counter_currency = _require_currency(session, counter.currency)

    # The exponent comes from the TABLE. A `scaleb(2)` here would store JPY 1,234
    # as 123,400: off by a factor of 100, and balanced, so nothing downstream
    # would ever notice.
    currency = Currency(code=currency_row.code, decimals=currency_row.decimals)
    stored = Money(amount=amount.amount, currency=currency)
    rate = _base_rate(session, currency_row.code, booked_date)
    build = default_leg_builder if leg_builder is None else leg_builder

    try:
        legs = build(
            funding_account=PostingAccount(
                id=funding.id,
                name=funding.name,
                currency=funding.currency.strip(),
                account_nature=funding.account_nature,
                is_active=funding.is_active,
            ),
            counter_account=counter,
            amount=stored,
            rate=rate,
            counter_decimals=counter_currency.decimals,
        )
    except ManualPostingError as exc:
        raise PostingRefused(str(exc), reason=exc) from exc

    entry = JournalEntry(entry_date=booked_date, description=description)
    session.add(entry)
    session.flush()

    for leg in legs:
        session.add(
            JournalLine(
                journal_entry_id=entry.id,
                account_id=leg.account_id,
                amount=leg.amount,
                currency=leg.currency,
                amount_base=leg.amount_base,
                exchange_rate=leg.exchange_rate,
                sort_order=leg.sort_order,
            )
        )

    if source_record_id is None:
        return _add_source_record(
            session,
            batch_id=batch_id,
            account_id=funding.id,
            description=description,
            amount=stored,
            booked_date=booked_date,
            raw_data=raw_data,
            raw_posting_date=raw_posting_date,
            occurrence_index=occurrence_index,
            provider_txn_id=provider_txn_id,
            status=status,
            journal_entry_id=entry.id,
            error_message=None,
        )
    record = session.get(SourceRecord, source_record_id)
    if record is None:
        raise ReferenceNotFound("source_record", source_record_id)
    return _attach_source_record(
        session,
        record=record,
        journal_entry_id=entry.id,
        status=status,
        error_message=None,
    )


def write_unposted_transaction(
    session: Session,
    *,
    batch_id: int,
    account_id: int,
    description: str,
    amount: Money,
    booked_date: dt.date,
    raw_data: dict[str, object],
    error_message: str,
    raw_posting_date: dt.date | None = None,
    occurrence_index: int = 1,
    provider_txn_id: str | None = None,
    status: TransactionStatus = TransactionStatus.PENDING,
    source_record_id: int | None = None,
) -> int:
    """Store one parsed row WITHOUT posting it, and return its id.

    This function never calls `build_expense_legs`, never resolves a counter-leg,
    and never touches `journal_entry` at all. That is not an omission; it is the
    entire reason it exists.

    A monthly card payment credits a liability and debits an asset. It is a
    TRANSFER, and the one contra-leg rule this codebase enforces is
    `resolve_default_counter_account`'s — an `equity` account, or nothing. Send a
    card payment down the manual default and it does **not** reliably raise:
    `build_expense_legs` never checks `account_nature`, so the equity requirement
    lives in the RESOLVER, one layer up. With an active `Expenses (system)`
    account present — the normal case for anyone who has ever posted a manual
    transaction — the payment books AGAINST EQUITY. It balances, so the balance
    trigger accepts it; `raw_data` is immutable, so it is permanent; and the
    statement still reconciles while the balance sheet is simply wrong. Paying off
    a debt, recorded as spending.

    4 of the 121 rows in the real Amex statements are card payments
    (`docs/adr/0007-imported-card-payment-is-a-transfer.md`). So the row is stored
    — the parse was correct and the evidence is worth keeping — but unposted, with
    a message that says which account is missing, and the batch is `partial`.

    Args:
        session: The caller's session.
        batch_id: An `import_batch` id from `open_import_batch`.
        account_id: The account the row belongs to. An `int`, never None:
            `source_record.account_id` is NOT NULL, and a row that cannot be
            attributed is not this writer's problem to solve.
        description: The row's description, verbatim.
        amount: Signed minor units. NOT rescaled — see `write_posted_transaction`.
            The currency exponent is deliberately not read from the table here,
            because an unposted row stores no `amount_base` for it to be wrong
            about; the integer is the provider's own.
        booked_date: The accounting date.
        raw_data: The row exactly as the provider gave it.
        error_message: Required, and must be actionable. An unposted row with no
            reason is a mystery, and a mystery in a financial ledger is worse
            than a refusal, because nothing looks wrong.
        raw_posting_date: A second date the provider stated.
        occurrence_index: This row's rank among content-identical rows in its
            batch. See `_fingerprint`.
        provider_txn_id: The provider's own id, or None.
        status: `PENDING` puts the row in the uncategorised queue's sibling index
            (`idx_sr_open_pending`); `IMPORTED` leaves it raw and unclaimed.
        source_record_id: An existing `source_record` to re-link instead of
            inserting one. The replay path: the unposted state is restored onto
            the same row. None inserts, as before.

    Returns:
        The new `source_record` id, with no `journal_entry_id`.

    Raises:
        ReferenceNotFound: `account_id` names no account → 404.
            `source_record_id` names no record, when one is given.
    """
    if status is TransactionStatus.POSTED:
        msg = (
            "write_unposted_transaction cannot write a posted row; use "
            "write_posted_transaction"
        )
        raise ValueError(msg)
    _require_account(session, account_id)
    if source_record_id is None:
        return _add_source_record(
            session,
            batch_id=batch_id,
            account_id=account_id,
            description=description,
            amount=amount,
            booked_date=booked_date,
            raw_data=raw_data,
            raw_posting_date=raw_posting_date,
            occurrence_index=occurrence_index,
            provider_txn_id=provider_txn_id,
            status=status,
            journal_entry_id=None,
            error_message=error_message,
        )
    record = session.get(SourceRecord, source_record_id)
    if record is None:
        raise ReferenceNotFound("source_record", source_record_id)
    return _attach_source_record(
        session,
        record=record,
        journal_entry_id=None,
        status=status,
        error_message=error_message,
    )


# ---------------------------------------------------------------------------
# Card payments — a transfer between a card and the account that pays it
# ---------------------------------------------------------------------------
#
# `docs/adr/0007-imported-card-payment-is-a-transfer.md`. A monthly card payment
# credits a liability and debits an asset, so it never reaches
# `resolve_contra_account` (which is unconditionally equity) and it never goes
# through the `LegBuilder` protocol (which has no notion of a synthesized leg).
# It gets its own writer, for the same reason `build_expense_legs` is not
# widened: a manual expense must not acquire a transfer-shaped hole.
#
# The writer is deliberately NOT `write_posted_transaction` with a different
# builder. It owns the two facts the generic writer has no room for: WHICH leg
# is synthesized (the side no statement printed), and whether an earlier import
# already created the other side. That second fact is the double-booking guard.


@dataclass(frozen=True)
class _CardPaymentLegRef:
    """A `journal_line` a card-payment write might attach to."""

    journal_line_id: int
    journal_entry_id: int
    entry_date: dt.date
    account_id: int
    amount: int


def _as_posting_account(account: Account) -> PostingAccount:
    """The `PostingAccount` view of an ORM row, for the pure leg builder."""
    return PostingAccount(
        id=account.id,
        name=account.name,
        currency=account.currency.strip(),
        account_nature=account.account_nature,
        is_active=account.is_active,
        system_role=account.system_role,
    )


def _find_synthesized_card_payment_legs(
    session: Session,
    *,
    account_id: int,
    amount: int,
    earliest: dt.date,
    latest: dt.date,
) -> list[_CardPaymentLegRef]:
    """Unmatched synthesized `card_payment` legs on `account_id`.

    A synthesized leg is the side no statement printed, so finding one means the
    other statement was imported first and this row is its counterpart. The
    query keys on `is_synthesized` AND `synthesized_reason`, never on one alone:
    migration 0003 makes them total, and a matcher that trusted only the boolean
    could be contradicted by the reason.
    """
    rows = session.execute(
        select(
            JournalLine.id,
            JournalLine.journal_entry_id,
            JournalEntry.entry_date,
            JournalLine.account_id,
            JournalLine.amount,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(JournalLine.account_id == account_id)
        .where(JournalLine.is_synthesized.is_(True))
        .where(JournalLine.synthesized_reason == CARD_PAYMENT_REASON)
        .where(JournalLine.transfer_match_id.is_(None))
        .where(JournalLine.amount == amount)
        .where(JournalEntry.entry_date >= earliest)
        .where(JournalEntry.entry_date <= latest)
        .order_by(JournalLine.id)
    ).all()
    return [_CardPaymentLegRef(*row) for row in rows]


def _find_posted_expense_counterparts(
    session: Session,
    *,
    account_id: int,
    amount: int,
    earliest: dt.date,
    latest: dt.date,
) -> list[_CardPaymentLegRef]:
    """Already-posted expense entries in `account_id` a card payment may rewrite.

    This is the checking-first order: the paying statement was imported before
    the card's, so its row was posted through the ordinary expense path and now
    carries an equity contra-leg. The equity leg is the marker that this entry
    is a candidate to rewrite, and requiring it is what keeps the query from
    matching an unrelated transaction that merely has the same amount.
    """
    candidates = session.execute(
        select(
            JournalLine.id,
            JournalLine.journal_entry_id,
            JournalEntry.entry_date,
            JournalLine.account_id,
            JournalLine.amount,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(JournalLine.account_id == account_id)
        .where(JournalLine.is_synthesized.is_(False))
        .where(JournalLine.transfer_match_id.is_(None))
        .where(JournalLine.amount == amount)
        .where(JournalEntry.entry_date >= earliest)
        .where(JournalEntry.entry_date <= latest)
        .order_by(JournalLine.id)
    ).all()
    entry_ids = {row[1] for row in candidates}
    if not entry_ids:
        return []
    equity_entries = set(
        session.scalars(
            select(JournalLine.journal_entry_id)
            .join(Account, Account.id == JournalLine.account_id)
            .where(JournalLine.journal_entry_id.in_(entry_ids))
            .where(Account.account_nature == "equity")
        )
    )
    return [_CardPaymentLegRef(*row) for row in candidates if row[1] in equity_entries]


def _real_line_in_entry(session: Session, *, entry_id: int, account_id: int) -> int:
    """The one real (non-synthesized) line of `entry_id` on `account_id`."""
    line_id = session.scalar(
        select(JournalLine.id)
        .where(JournalLine.journal_entry_id == entry_id)
        .where(JournalLine.account_id == account_id)
        .order_by(JournalLine.id)
        .limit(1)
    )
    if line_id is None:
        raise PostingRefused(
            f"Journal entry {entry_id} has no line on account {account_id}, so "
            "the card payment has nothing to match against."
        )
    return int(line_id)


def _refuse_ambiguous(candidates: Sequence[_CardPaymentLegRef]) -> PostingRefused:
    """Refuse when more than one leg could be the other side, naming them."""
    ids = ", ".join(str(candidate.journal_line_id) for candidate in candidates)
    return PostingRefused(
        f"{len(candidates)} unmatched card-payment legs could be the other side "
        f"(journal_line ids {ids}); the match is refused rather than guessed, "
        "because choosing one silently decides where the money went. Attach the "
        "source row to the right entry by hand, or remove the extra match."
    )


def _insert_transfer_match(
    session: Session,
    *,
    out_line_id: int,
    in_line_id: int,
    match_method: str,
    confidence: Decimal,
    confirmed: bool,
) -> int:
    """INSERT the `transfer_match` row and stamp both lines with its id.

    Nothing else in this codebase writes this table; before this function the
    word "matched" on a card payment was a comment. Both `journal_line` rows get
    `transfer_match_id` so the pair is discoverable from either side, and the
    unique constraint on the ordered pair makes a second link impossible.
    """
    match = TransferMatch(
        journal_line_id_out=out_line_id,
        journal_line_id_in=in_line_id,
        match_method=match_method,
        confidence=confidence,
        confirmed_at=func.now() if confirmed else None,
    )
    session.add(match)
    session.flush()
    for line_id in (out_line_id, in_line_id):
        line = session.get(JournalLine, line_id)
        if line is None:  # pragma: no cover - the ids came from this session
            raise PostingRefused(f"journal_line {line_id} vanished mid-match")
        line.transfer_match_id = match.id
    return match.id


def _attach_card_payment_source(
    session: Session,
    *,
    batch_id: int,
    account_id: int,
    entry_id: int,
    out_line_id: int,
    in_line_id: int,
    match_method: str,
    confidence: Decimal,
    confirmed: bool,
    description: str,
    amount: Money,
    booked_date: dt.date,
    raw_data: dict[str, object],
    raw_posting_date: dt.date | None,
    occurrence_index: int,
    provider_txn_id: str | None,
    source_record_id: int | None = None,
) -> int:
    """Attach the incoming row to an EXISTING entry and store the match."""
    _insert_transfer_match(
        session,
        out_line_id=out_line_id,
        in_line_id=in_line_id,
        match_method=match_method,
        confidence=confidence,
        confirmed=confirmed,
    )
    if source_record_id is None:
        return _add_source_record(
            session,
            batch_id=batch_id,
            account_id=account_id,
            description=description,
            amount=amount,
            booked_date=booked_date,
            raw_data=raw_data,
            raw_posting_date=raw_posting_date,
            occurrence_index=occurrence_index,
            provider_txn_id=provider_txn_id,
            status=TransactionStatus.POSTED,
            journal_entry_id=entry_id,
            error_message=None,
        )
    record = session.get(SourceRecord, source_record_id)
    if record is None:
        raise ReferenceNotFound("source_record", source_record_id)
    return _attach_source_record(
        session,
        record=record,
        journal_entry_id=entry_id,
        status=TransactionStatus.POSTED,
        error_message=None,
    )


def _write_card_payment_legs(
    session: Session,
    *,
    entry_id: int,
    legs: Sequence[CardPaymentLeg],
) -> None:
    """Insert the legs of a card-payment entry, flags included."""
    for leg in legs:
        session.add(
            JournalLine(
                journal_entry_id=entry_id,
                account_id=leg.account_id,
                amount=leg.amount,
                currency=leg.currency,
                amount_base=leg.amount_base,
                exchange_rate=leg.exchange_rate,
                sort_order=leg.sort_order,
                is_synthesized=leg.is_synthesized,
                synthesized_reason=leg.synthesized_reason,
            )
        )


def _rewrite_posted_expense_as_card_payment(
    session: Session,
    *,
    batch_id: int,
    card: Account,
    paying: Account,
    expense_line: _CardPaymentLegRef,
    card_credit: Money,
    rate: Decimal,
    description: str,
    amount: Money,
    booked_date: dt.date,
    raw_data: dict[str, object],
    raw_posting_date: dt.date | None,
    occurrence_index: int,
    provider_txn_id: str | None,
    source_record_id: int | None = None,
) -> int:
    """Turn an already-posted expense entry into the card-payment transfer.

    Checking-first order. The paying row was imported before the card's and was
    posted as an expense (the only thing the code could do without the card),
    so its entry carries a real paying line and an equity contra-leg. The
    rewrite deletes the equity leg and inserts the card leg, leaving the paying
    line in place: the entry becomes the transfer it always was. `journal_line`
    is freely editable — only `source_record.raw_data` is frozen by trigger —
    and the balance trigger is DEFERRABLE, so the transient one-line state is
    legal until COMMIT.
    """
    entry_id = expense_line.journal_entry_id
    equity_line_ids = session.scalars(
        select(JournalLine.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(JournalLine.journal_entry_id == entry_id)
        .where(Account.account_nature == "equity")
    ).all()
    if not equity_line_ids:  # pragma: no cover - the finder required one
        raise PostingRefused(
            f"Journal entry {entry_id} was expected to carry an equity "
            "contra-leg and does not, so rewriting it would not balance."
        )
    for line_id in equity_line_ids:
        session.delete(session.get(JournalLine, line_id))
    session.flush()

    card_leg, _paying_leg = build_card_payment_legs(
        card_account=_as_posting_account(card),
        paying_account=_as_posting_account(paying),
        amount=card_credit,
        rate=rate,
    )
    # The paying line already occupies sort_order 0; the card leg takes the
    # equity leg's slot.
    card_leg = replace(card_leg, sort_order=1)
    _write_card_payment_legs(session, entry_id=entry_id, legs=(card_leg,))
    session.flush()

    entry = session.get(JournalEntry, entry_id)
    if entry is None:  # pragma: no cover - the line's FK guarantees it
        raise PostingRefused(f"Journal entry {entry_id} vanished mid-rewrite")
    entry.is_transfer = True

    card_line_id = _real_line_in_entry(session, entry_id=entry_id, account_id=card.id)
    return _attach_card_payment_source(
        session,
        batch_id=batch_id,
        account_id=card.id,
        entry_id=entry_id,
        out_line_id=expense_line.journal_line_id,
        in_line_id=card_line_id,
        match_method="user_confirmed",
        confidence=Decimal("1.00"),
        confirmed=True,
        description=description,
        amount=amount,
        booked_date=booked_date,
        raw_data=raw_data,
        raw_posting_date=raw_posting_date,
        occurrence_index=occurrence_index,
        provider_txn_id=provider_txn_id,
        source_record_id=source_record_id,
    )


@dataclass(frozen=True)
class _CardPaymentContext:
    """Everything one card-payment write needs, once the accounts are loaded."""

    batch_id: int
    card: Account
    paying: Account
    card_credit: Money
    stored: Money
    rate: Decimal
    description: str
    booked_date: dt.date
    raw_data: dict[str, object]
    raw_posting_date: dt.date | None
    occurrence_index: int
    provider_txn_id: str | None

    @property
    def window(self) -> dt.timedelta:
        return dt.timedelta(days=CARD_PAYMENT_WINDOW_DAYS_AFTER)


def _attach_auto(
    session: Session,
    ctx: _CardPaymentContext,
    *,
    account_id: int,
    entry_id: int,
    out_line_id: int,
    in_line_id: int,
    source_record_id: int | None = None,
) -> int:
    """Attach the row to an existing entry with the automatic match method."""
    return _attach_card_payment_source(
        session,
        batch_id=ctx.batch_id,
        account_id=account_id,
        entry_id=entry_id,
        out_line_id=out_line_id,
        in_line_id=in_line_id,
        match_method="auto_card_payment",
        confidence=Decimal("0.95"),
        confirmed=False,
        description=ctx.description,
        amount=ctx.stored,
        booked_date=ctx.booked_date,
        raw_data=ctx.raw_data,
        raw_posting_date=ctx.raw_posting_date,
        occurrence_index=ctx.occurrence_index,
        provider_txn_id=ctx.provider_txn_id,
        source_record_id=source_record_id,
    )


def _build_new_card_payment_entry(
    session: Session,
    ctx: _CardPaymentContext,
    *,
    statement_account_id: int,
    synthesized_account_id: int,
    source_record_id: int | None = None,
) -> int:
    """The first import for a payment: real leg + synthesized missing leg."""
    legs = build_card_payment_legs(
        card_account=_as_posting_account(ctx.card),
        paying_account=_as_posting_account(ctx.paying),
        amount=ctx.card_credit,
        rate=ctx.rate,
    )
    flagged = tuple(
        replace(leg, is_synthesized=True, synthesized_reason=CARD_PAYMENT_REASON)
        if leg.account_id == synthesized_account_id
        else leg
        for leg in legs
    )
    entry = JournalEntry(
        entry_date=ctx.booked_date, description=ctx.description, is_transfer=True
    )
    session.add(entry)
    session.flush()
    _write_card_payment_legs(session, entry_id=entry.id, legs=flagged)
    if source_record_id is None:
        return _add_source_record(
            session,
            batch_id=ctx.batch_id,
            account_id=statement_account_id,
            description=ctx.description,
            amount=ctx.stored,
            booked_date=ctx.booked_date,
            raw_data=ctx.raw_data,
            raw_posting_date=ctx.raw_posting_date,
            occurrence_index=ctx.occurrence_index,
            provider_txn_id=ctx.provider_txn_id,
            status=TransactionStatus.POSTED,
            journal_entry_id=entry.id,
            error_message=None,
        )
    record = session.get(SourceRecord, source_record_id)
    if record is None:
        raise ReferenceNotFound("source_record", source_record_id)
    return _attach_source_record(
        session,
        record=record,
        journal_entry_id=entry.id,
        status=TransactionStatus.POSTED,
        error_message=None,
    )


def _write_card_statement_row(
    session: Session,
    ctx: _CardPaymentContext,
    *,
    source_record_id: int | None = None,
) -> int:
    """The card statement's row: match a synthesized card leg, rewrite, or build."""
    candidates = _find_synthesized_card_payment_legs(
        session,
        account_id=ctx.card.id,
        amount=ctx.card_credit.amount,
        earliest=ctx.booked_date,
        latest=ctx.booked_date + ctx.window,
    )
    if len(candidates) > 1:
        raise _refuse_ambiguous(candidates)
    if len(candidates) == 1:
        entry_id = candidates[0].journal_entry_id
        return _attach_auto(
            session,
            ctx,
            account_id=ctx.card.id,
            entry_id=entry_id,
            out_line_id=_real_line_in_entry(
                session, entry_id=entry_id, account_id=ctx.paying.id
            ),
            in_line_id=candidates[0].journal_line_id,
            source_record_id=source_record_id,
        )

    expenses = _find_posted_expense_counterparts(
        session,
        account_id=ctx.paying.id,
        amount=-ctx.card_credit.amount,
        earliest=ctx.booked_date,
        latest=ctx.booked_date + ctx.window,
    )
    if len(expenses) > 1:
        raise _refuse_ambiguous(expenses)
    if len(expenses) == 1:
        return _rewrite_posted_expense_as_card_payment(
            session,
            batch_id=ctx.batch_id,
            card=ctx.card,
            paying=ctx.paying,
            expense_line=expenses[0],
            card_credit=ctx.card_credit,
            rate=ctx.rate,
            description=ctx.description,
            amount=ctx.stored,
            booked_date=ctx.booked_date,
            raw_data=ctx.raw_data,
            raw_posting_date=ctx.raw_posting_date,
            occurrence_index=ctx.occurrence_index,
            provider_txn_id=ctx.provider_txn_id,
            source_record_id=source_record_id,
        )
    return _build_new_card_payment_entry(
        session,
        ctx,
        statement_account_id=ctx.card.id,
        synthesized_account_id=ctx.paying.id,
        source_record_id=source_record_id,
    )


def _write_paying_statement_row(
    session: Session,
    ctx: _CardPaymentContext,
    *,
    source_record_id: int | None = None,
) -> int:
    """The paying statement's row: match a synthesized paying leg, or build."""
    candidates = _find_synthesized_card_payment_legs(
        session,
        account_id=ctx.paying.id,
        amount=-ctx.card_credit.amount,
        earliest=ctx.booked_date - ctx.window,
        latest=ctx.booked_date,
    )
    if len(candidates) > 1:
        raise _refuse_ambiguous(candidates)
    if len(candidates) == 1:
        entry_id = candidates[0].journal_entry_id
        return _attach_auto(
            session,
            ctx,
            account_id=ctx.paying.id,
            entry_id=entry_id,
            out_line_id=candidates[0].journal_line_id,
            in_line_id=_real_line_in_entry(
                session, entry_id=entry_id, account_id=ctx.card.id
            ),
            source_record_id=source_record_id,
        )
    return _build_new_card_payment_entry(
        session,
        ctx,
        statement_account_id=ctx.paying.id,
        synthesized_account_id=ctx.card.id,
        source_record_id=source_record_id,
    )


def write_card_payment(
    session: Session,
    *,
    batch_id: int,
    card_account_id: int,
    paying_account_id: int,
    statement_account_id: int,
    amount: Money,
    description: str,
    booked_date: dt.date,
    raw_data: dict[str, object],
    raw_posting_date: dt.date | None = None,
    occurrence_index: int = 1,
    provider_txn_id: str | None = None,
    source_record_id: int | None = None,
) -> int:
    """Write one flagged card payment, matching the other side if it exists.

    A card payment is a transfer, and whichever statement is imported second
    must attach to the synthesized leg the first import left rather than create
    a second entry. Three shapes, decided in this order:

    * **The other side was already synthesized** (this statement came second):
      attach to that entry and store the `transfer_match`.
    * **The paying side was already posted as an expense** (checking first, card
      second, with no synthesized leg): rewrite that entry into the transfer.
    * **Neither exists** (this statement came first): build the entry, marking
      the side no statement printed as synthesized, and leave it unmatched.

    More than one candidate at either step is a REFUSAL, never a guess: a wrong
    link silently changes a card payment into something else.

    Args:
        session: The caller's session. Never begun, committed or rolled back.
        batch_id: The open `import_batch`.
        card_account_id: The liability the payment reduces.
        paying_account_id: The asset that pays it.
        statement_account_id: Which of the two this row is on. Decides which leg
            is real and which is synthesized.
        amount: The row as printed, SIGNED. A card statement prints it positive
            (a credit); a checking statement prints it negative (a debit).
        description: The row's description, verbatim.
        booked_date: The row's accounting date.
        raw_data: The row exactly as the provider gave it.
        raw_posting_date: A second date the provider stated, when any.
        occurrence_index: This row's content-only rank in its batch.
        provider_txn_id: The provider's own id, or None on the v1 PDF paths.
        source_record_id: An existing `source_record` to re-link instead of
            inserting one. The replay path. None inserts, as before.

    Returns:
        The new `source_record` id, linked to a posted `journal_entry`.

    Raises:
        ReferenceNotFound: An account id names no account → 404.
            `source_record_id` names no record, when one is given.
        PostingRefused: A missing rate, a currency mismatch, or two candidates
            for the other side → 422. The caller stores the row unposted.
    """
    card = _require_account(session, card_account_id)
    paying = _require_account(session, paying_account_id)
    if statement_account_id not in (card.id, paying.id):
        raise PostingRefused(
            f"statement_account_id={statement_account_id} is neither the card "
            f"({card.id}) nor the paying account ({paying.id})."
        )

    currency_row = _require_currency(session, amount.currency.code)
    if card.currency.strip() != currency_row.code:
        raise PostingRefused(
            f"Card account {card.id} holds {card.currency.strip()} but the "
            f"payment is in {currency_row.code}."
        )
    if paying.currency.strip() != currency_row.code:
        raise PostingRefused(
            f"Paying account {paying.id} holds {paying.currency.strip()} but "
            f"the payment is in {currency_row.code}."
        )

    currency = Currency(code=currency_row.code, decimals=currency_row.decimals)
    stored = Money(amount=amount.amount, currency=currency)
    card_credit = Money(amount=abs(amount.amount), currency=currency)
    if card_credit.amount == 0:
        raise PostingRefused("A zero-amount card payment has no double-entry meaning.")
    rate = _base_rate(session, currency_row.code, booked_date)

    ctx = _CardPaymentContext(
        batch_id=batch_id,
        card=card,
        paying=paying,
        card_credit=card_credit,
        stored=stored,
        rate=rate,
        description=description,
        booked_date=booked_date,
        raw_data=raw_data,
        raw_posting_date=raw_posting_date,
        occurrence_index=occurrence_index,
        provider_txn_id=provider_txn_id,
    )
    if statement_account_id == card.id:
        return _write_card_statement_row(
            session, ctx, source_record_id=source_record_id
        )
    return _write_paying_statement_row(session, ctx, source_record_id=source_record_id)


def leg_count(session: Session, *, source_record_id: int) -> int:
    """How many `journal_line` rows the entry behind `source_record_id` has.

    Exists for `stats` only. A manual batch has always recorded its leg count,
    and this keeps that number exact without the writer having to return a second
    value or the router re-deriving it from a rule it does not own. Zero for a
    record with no entry.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(JournalLine)
            .join(
                SourceRecord,
                SourceRecord.journal_entry_id == JournalLine.journal_entry_id,
            )
            .where(SourceRecord.id == source_record_id)
        )
        or 0
    )
