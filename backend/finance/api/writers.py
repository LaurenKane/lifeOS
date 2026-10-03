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

    Returns:
        The new `source_record` id.

    Raises:
        ReferenceNotFound: `funding_account_id` names no account → 404.
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

    Returns:
        The new `source_record` id, with no `journal_entry_id`.

    Raises:
        ReferenceNotFound: `account_id` names no account → 404.
    """
    if status is TransactionStatus.POSTED:
        msg = (
            "write_unposted_transaction cannot write a posted row; use "
            "write_posted_transaction"
        )
        raise ValueError(msg)
    _require_account(session, account_id)
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
