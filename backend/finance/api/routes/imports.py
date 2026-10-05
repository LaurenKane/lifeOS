"""imports router — run an import, and persist what it parsed.

`POST /imports/file` is the real one: it accepts an upload, runs the adapter, and
writes every row it parsed through `finance.api.writers`. `POST /imports` is the
older M0 dry run and is kept, because a caller who wants to see what a file
contains before committing to it is a legitimate question — but it persists
nothing, and its summary carries zeros for `created`/`duplicated`/`failed`
precisely so nobody can mistake it for an import that happened.

The endpoint is `async def` and does its database work in a threadpool. A sync
SQLAlchemy `Session` cannot be awaited, and running its statements directly on the
event loop would block every other request for the length of a 7-year PDF parse.
Same reason `finance.api.deps` exists.
"""

from __future__ import annotations

import base64
import datetime as dt
import gzip
from collections.abc import Sequence
from typing import Annotated, Final, Protocol

from core.datetime import parse_date
from core.money import Currency, Money
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from finance.api.deps import get_session
from finance.api.schemas import ImportSummary, Provider, ProviderInfo
from finance.api.transfer_linker import link_transfers
from finance.api.writers import (
    PostingRefused,
    ReferenceNotFound,
    finish_import_batch,
    open_import_batch,
    write_card_payment,
    write_posted_transaction,
    write_unposted_transaction,
)
from finance.domain.models.accounts import Account
from finance.domain.models.importer import SourceRecord
from finance.ingestion.adapters import (
    AmexPdfAdapter,
    RabobankPdfAdapter,
    RevolutPdfAdapter,
)
from finance.ingestion.adapters.base import ImportResult
from finance.ingestion.dedupe import (
    OccurrenceKey,
    count_existing_occurrences,
    fingerprint_account_scope,
)
from finance.ingestion.fingerprint import compute_fingerprint
from finance.public import RawRecord, TransactionStatus

router = APIRouter(tags=["finance"], prefix="/imports")

#: Annotated rather than a default argument, for the reason stated in
#: `routes/transactions.py`: ruff runs with `B` selected and `Depends(...)` in a
#: default value is exactly what that rule is for. It also keeps `account_id` and
#: `provider` free to be the query parameters they are on the wire.
SessionDep = Annotated[Session, Depends(get_session)]

# Largest statement we accept. A 7-year Amex PDF is a few MB; 64 MiB is generous
# and keeps one request from exhausting the API container's memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class FileAdapter(Protocol):
    """The adapter shape an upload needs: parse a file for one account.

    Narrower than `ImportAdapter` on purpose. `ManualAdapter` and
    `EnableBankingAdapter` implement `ImportAdapter` but are not uploadable, and
    typing the table against the full interface would push that distinction into
    runtime checks instead of the type system.
    """

    def parse(
        self, payload: bytes, account_id: int | None = None, filename: str | None = None
    ) -> ImportResult: ...


# Canonical v1 provider list. Source of truth for the backend schema, the
# `/imports/providers` response, and the frontend `provider` enum.
# See docs/adr/0002-import-provider-enum.md for the decision record.
V1_PROVIDERS: tuple[str, ...] = (
    "enable_banking",
    "amex_pdf",
    "rabobank_pdf",
    "revolut_pdf",
    "manual",
)

# provider -> the adapter and the file extension it accepts.
#
# `enable_banking` and `manual` are absent on purpose: the first arrives over its
# API and the second is POSTed as JSON, and neither is an upload. Every remaining
# v1 provider has an adapter, so nothing is advertised as importable that the app
# cannot actually read (docs/adr/0002-import-provider-enum.md).
#
# All three PDF adapters share one extractor: `pdftotext -layout` from
# poppler-utils, which the runtime image installs (backend/Dockerfile). Without
# that binary an upload fails with an explicit "pdftotext is not installed"
# reason rather than a zero-row import.
_FILE_ADAPTERS: dict[str, tuple[type[FileAdapter], str]] = {
    "amex_pdf": (AmexPdfAdapter, ".pdf"),
    "rabobank_pdf": (RabobankPdfAdapter, ".pdf"),
    "revolut_pdf": (RevolutPdfAdapter, ".pdf"),
}

_IMPORT_METHODS: dict[str, str] = {
    "enable_banking": "api",
    "amex_pdf": "pdf",
    "rabobank_pdf": "pdf",
    "revolut_pdf": "pdf",
    "manual": "manual",
}


def _provider_info(kind: str) -> Provider:
    """Build a Provider entry from the canonical v1 list."""
    return Provider(
        kind=kind,
        import_method=_IMPORT_METHODS[kind],
        accepts_upload=kind in _FILE_ADAPTERS,
    )


# The full provider list, including providers that are not yet uploadable.
_PROVIDERS: tuple[Provider, ...] = tuple(_provider_info(kind) for kind in V1_PROVIDERS)


async def _read_upload(file: UploadFile, *, provider: str) -> tuple[bytes, str]:
    """Validate the provider/extension pair and read the bytes.

    Shared by the dry run and the persisting import so the two cannot disagree
    about what counts as an acceptable upload. The provider checks come FIRST:
    "manual entries are POSTed as JSON" is a more useful answer than a confusing
    parse failure, and saying so before reading 60 MB out of the request is also
    cheaper.
    """
    # Manual entries are typed field by field, and Enable Banking arrives over
    # its API. Neither is an upload, and saying so is more useful than a
    # confusing parse failure.
    if provider == "enable_banking":
        raise HTTPException(
            status_code=400,
            detail="Enable Banking is an API source, not an upload",
        )
    if provider == "manual":
        raise HTTPException(
            status_code=400, detail="Manual entries are POSTed as JSON, not uploaded"
        )
    if provider not in _FILE_ADAPTERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider {provider!r}")

    _, extension = _FILE_ADAPTERS[provider]
    filename = file.filename or ""
    if extension and not filename.lower().endswith(extension):
        raise HTTPException(
            status_code=400, detail=f"Expected a {extension} file, got {filename!r}"
        )

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    return payload, filename


@router.post(
    "",
    summary="Parse a statement WITHOUT writing it",
    response_model=ImportSummary,
)
async def dry_run_import(
    file: UploadFile = File(...),
    provider: str = "amex_pdf",
    account_id: int | None = None,
) -> ImportSummary:
    """Accept a statement file, parse it, and write NOTHING.

    Args:
        file: The uploaded statement.
        provider: Which adapter to run. Must match the file extension.
        account_id: The local account to attribute rows to. A file names no
            account, so the user picks one and the caller passes it in. None
            when the upload has not been attributed to an account yet — which is
            honest, where the previous `""` default claimed to be an id.

    Returns:
        A summary of what the file CONTAINS. Nothing is written: `created`,
        `duplicated` and `failed` are all zero, and this endpoint does not open an
        `import_batch`. Use `POST /imports/file` to persist.

    Raises:
        HTTPException: 400 for an unknown provider, a mismatched extension, or a
            provider that is not uploadable; 413 when the upload is too large.
    """
    payload, filename = await _read_upload(file, provider=provider)
    adapter = _FILE_ADAPTERS[provider][0]()
    result = adapter.parse(payload, account_id=account_id, filename=filename)
    return ImportSummary(
        provider=result.provider,
        import_method=result.import_method,
        status=result.status,
        record_count=result.record_count,
        source_checksum=result.source_checksum,
        failures=[str(failure) for failure in result.failed],
    )


# ---------------------------------------------------------------------------
# Persisting an import
# ---------------------------------------------------------------------------

#: The message every unattributable row carries. `source_record.account_id` is
#: NOT NULL (migration 0001), so a row nobody can attribute cannot be written at
#: all — and Revolut's PDF emits `account_id=None` for rows from a section other
#: than the caller's, by design (`revolut_pdf.py` says so). The row is counted as
#: failed rather than dropped, because a silently dropped row is a statement that
#: does not reconcile and a user with no way to tell which one.
_UNATTRIBUTED = (
    "Not written: this row names no account, and source_record.account_id is "
    "NOT NULL. Pass account_id on the upload, or supply "
    "section_account_ids so the adapter can attribute it."
)

#: A flagged card payment's message. Actionable, and deliberately does NOT guess
#: a paying account: picking an arbitrary asset account is a coin flip that
#: decides where the user's money went, which is the same reason
#: `resolve_default_counter_account` refuses two same-named accounts
#: (`docs/adr/0007-imported-card-payment-is-a-transfer.md`).
_CARD_PAYMENT = (
    "This row is a monthly card payment, so it is a TRANSFER (a card liability "
    "paid from an asset) rather than an expense, and the statement does not say "
    "which account paid it. It is stored unposted until that account is "
    "registered; posting it as an expense would record debt repayment as "
    "spending, and that entry would balance, commit and be permanent. See "
    "docs/adr/0007-imported-card-payment-is-a-transfer.md."
)


def _gzip_b64(payload: bytes) -> str:
    """The uploaded bytes, gzipped and base64'd, for `import_batch.raw_payload`.

    JSONB cannot hold bytes, and `raw_payload` is documented as "gzipped if
    large; NEVER removed" — it is what a replay reads. base64 is used rather than
    hex because a PDF compresses to roughly a third and hex would double that
    again; the column is text, so both are equally lossy on nothing.

    `mtime=0` so the SAME file produces the SAME stored value. Without it gzip
    embeds the current time, so two imports of identical bytes would differ, and
    "compare `raw_payload` to the file the user uploaded" — the obvious
    verification — would report a difference for no reason at all.
    """
    return base64.b64encode(gzip.compress(payload, mtime=0)).decode("ascii")


#: The `raw_data` keys that carry a provider's SECOND date, in the order they
#: are tried.
#:
#: `raw_posting_date` is the documented one and the only one Amex writes
#: (`amex_pdf.py`, an ISO string). Rabobank writes its processing date under
#: `processing_date` instead, in `DD-MM-YYYY`, and it differs from the booking
#: date on 35 of the 37 rows of one real statement — so reading only the Amex key
#: would leave the column NULL for every Rabobank row, which is the same silent
#: loss as not having the column at all.
#:
#: Two keys rather than a per-provider table because the SECOND date is the same
#: concept under both names. A provider that starts writing a third spelling
#: belongs here, once.
_POSTING_DATE_KEYS: Final[tuple[str, ...]] = ("raw_posting_date", "processing_date")


def _posting_date(record: RawRecord) -> dt.date | None:
    """The provider's SECOND date, lifted out of `raw_data` and parsed.

    `raw_posting_date` differs from `raw_date` on 42 of the 121 rows in the real
    Amex statements, so collapsing them loses a real fact. It lives in `raw_data`
    as a STRING and the column is a DATE — so it has to be parsed on the way in, or
    it is silently NULL. `parse_date` handles both the ISO form Amex writes and
    the `DD-MM-YYYY` form Rabobank writes.

    A value that will not parse is None, not an exception: one malformed row in a
    7-year PDF must not abandon the other 4000, and the raw string is already
    preserved verbatim in `raw_data`.

    A date equal to `raw_date` is still returned rather than collapsed to None. The
    column records what the provider SAID, and "it said the same thing twice" is
    not the same claim as "it said it once"; deciding otherwise here would make the
    column a derived value rather than evidence.
    """
    for key in _POSTING_DATE_KEYS:
        raw = record.raw_data.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        # `RawRecord.raw_data` is declared `str | int | float | bool | None`, so
        # mypy narrows a `dt.date` out of that union as unreachable and
        # `warn_unreachable` complains about the `return`. The declared type is
        # narrower than what an adapter may actually put in a JSON object, so the
        # value is re-widened to `object` before the check rather than the check
        # being silenced — which makes the runtime behaviour the type describes.
        wide: object = raw
        if isinstance(wide, dt.date):
            return wide
        try:
            return parse_date(str(wide))
        except ValueError:
            continue
    return None


def _money(record: RawRecord) -> Money:
    """This row as `Money`, with the exponent from the CURRENCY TABLE.

    `RawRecord.amount_minor` is already minor units, so the integer is taken
    verbatim and never rescaled here — the adapter made that decision once, at
    parse time, where it had the statement in front of it.

    What is NOT taken verbatim is the exponent. `normalize_currency` hardcodes
    `decimals=2`, so a JPY or BTC `Money` built on the pre-database path is 100x
    (or 10^6x) wrong before it reaches a writer, and `build_expense_legs` divides
    by it to produce `amount_base`. The table is the authority; this is spelled
    out because it is the one number in this file that is a lookup rather than a
    value, and a reader will otherwise assume it came from the statement.
    """
    return Money(amount=record.amount_minor, currency=Currency(code=record.currency))


def _occurrence_keys(records: Sequence[RawRecord]) -> list[OccurrenceKey]:
    """Each row's dedup key, in the row's own order.

    Built from `record.description`, which the adapters have ALREADY normalised
    (`normalize_description`) — so this normalises nothing again. Normalising twice
    is not idempotent in general and a second pass here would make the key disagree
    with the fingerprint the stored rows were written under.
    """
    return [
        OccurrenceKey(
            account_id=record.account_id,
            normalized_description=record.description,
            amount_minor=record.amount_minor,
            currency=record.currency,
            booked_date=record.booked_date,
        )
        for record in records
    ]


def _already_stored(session: Session, digest: bytes) -> bool:
    """Is there a `source_record` with exactly this Tier-3 fingerprint?

    A POINT LOOKUP, and it has to be one. `lookup_fingerprint` takes an iterable
    of candidates and walks it in Python, so using it here would mean reading every
    stored fingerprint on the account (or worse, in the whole table) for every row
    of the file — O(rows x stored), which on a 121-row statement against seven
    years of history is tens of thousands of comparisons inside one transaction.

    `uq_sr_fingerprint` is a UNIQUE index on the digest alone, so a point lookup
    against it is both the fast answer and the exact one.

    **No `IdentityResolver` here, and that is deliberate.** Tier 2 merges at
    >= 0.85 and is Enable-Banking-only (`identity.py:381-393`); running it over a
    PDF path would merge genuine separate purchases into whatever pending row
    happened to be nearby, and a merge is silent. Tier 1 needs a provider key and
    there is none on any v1 PDF path, so the resolver's `resolve()` collapses to
    Tier 3 here anyway — this is that Tier 3, said plainly rather than reached by
    accident.
    """
    found = session.scalar(
        select(SourceRecord.id).where(SourceRecord.fingerprint == digest).limit(1)
    )
    return found is not None


def _stored_occurrence_keys(
    session: Session, keys: Sequence[OccurrenceKey]
) -> list[OccurrenceKey]:
    """Content-keys for every stored row on the accounts in this batch.

    **A CONSISTENCY ASSERTION ONLY. It is never used to compute an occurrence
    index**, and that is not an oversight — see the note below. Its one job is to
    be compared against the content-only ranks the writer used, so a drift between
    the two shows up as a failure instead of as silently renumbered rows.

    The index has to be the CONTENT-ONLY within-batch rank from
    `assign_occurrence_indices`, and both of these were simulated against
    re-importing `[K, K, L]`:

        stored_count + rank:  re-import -> created=2, duplicated=1  WRONG
        content-only rank:     re-import -> created=0, duplicated=3  RIGHT

    The difference is what `stored_count` does. Adding the number of stored rows
    makes the index depend on TABLE STATE rather than on the file's contents, so
    the first re-import shifts the index of every later row and the second one
    shifts them again — which means re-importing the same statement keeps
    rewriting it instead of recognising it. The index has to be a function of the
    file alone.

    The cost of that choice is that `count_existing_occurrences` CANNOT reproduce
    a global ROW_NUMBER: a probe that is content-only has no way to know which of
    two identical stored rows an incoming row "would have" been. That is why this
    is an assertion and not a computation. The known consequence is documented on
    the writer below.
    """
    accounts = {key.account_id for key in keys if key.account_id is not None}
    if not accounts:
        return []
    rows = session.execute(
        select(
            SourceRecord.account_id,
            SourceRecord.raw_description,
            SourceRecord.raw_amount,
            SourceRecord.raw_currency,
            SourceRecord.raw_date,
        ).where(SourceRecord.account_id.in_(accounts))
    ).all()
    return [
        OccurrenceKey(
            account_id=account_id,
            normalized_description=description,
            amount_minor=amount,
            currency=currency.strip(),
            booked_date=booked,
        )
        for account_id, description, amount, currency, booked in rows
    ]


# ---------------------------------------------------------------------------
# Persisting an import
# ---------------------------------------------------------------------------


def _resolve_card_payment_roles(
    session: Session,
    *,
    row_account_id: int,
    explicit_id: int | None,
) -> tuple[Account, Account] | None:
    """The `(card, paying)` accounts for a flagged card-payment row, or None.

    Never by name and never guessed (`docs/adr/0007-imported-card-payment-is-a
    -transfer.md`). Resolution is explicit only, in this order:

    * An explicit request id names the OTHER side; which role it plays follows
      from the two accounts' natures.
    * `account.payment_from_account_id` on the row account names the paying
      account, when the row IS the card.
    * A row on a paying account is recognised by the cards that point at it. Two
      cards pointing at one account is ambiguous and resolves to None.

    Every unresolvable or wrong-shaped case returns None: the caller stores the
    row unposted rather than posting it as an expense.
    """
    row = session.get(Account, row_account_id)
    if row is None:
        return None
    if explicit_id is not None:
        counterpart = session.get(Account, explicit_id)
        if counterpart is None:
            return None
        return _roles_from_explicit(row, counterpart)
    return _roles_from_saved_mapping(session, row)


def _roles_from_explicit(
    row: Account, counterpart: Account
) -> tuple[Account, Account] | None:
    """`(card, paying)` from an explicitly named counterpart, by nature."""
    if counterpart.id == row.id:
        return None
    if row.account_nature == "liability" and counterpart.account_nature == "asset":
        return (row, counterpart)
    if row.account_nature == "asset" and counterpart.account_nature == "liability":
        return (counterpart, row)
    return None


def _roles_from_saved_mapping(
    session: Session, row: Account
) -> tuple[Account, Account] | None:
    """`(card, paying)` from `payment_from_account_id`, forward or reverse."""
    if row.payment_from_account_id is not None:
        paying = session.get(Account, row.payment_from_account_id)
        if paying is None or paying.id == row.id:
            return None
        if row.account_nature != "liability" or paying.account_nature != "asset":
            return None
        return (row, paying)

    cards = session.scalars(
        select(Account).where(Account.payment_from_account_id == row.id)
    ).all()
    if (
        len(cards) == 1
        and cards[0].account_nature == "liability"
        and row.account_nature == "asset"
    ):
        return (cards[0], row)
    return None


def _store_card_payment_unposted(
    session: Session,
    *,
    record: RawRecord,
    money: Money,
    batch_id: int,
    row_account: int,
    rank: int,
    reason: str,
) -> None:
    """Store one flagged card payment as a row with a reason, never as an expense."""
    write_unposted_transaction(
        session,
        batch_id=batch_id,
        account_id=row_account,
        description=record.description,
        amount=money,
        booked_date=record.booked_date,
        raw_data=dict(record.raw_data),
        error_message=reason,
        raw_posting_date=_posting_date(record),
        occurrence_index=rank,
        provider_txn_id=record.provider_txn_id,
        status=TransactionStatus.PENDING,
    )


def _persist_card_payment_row(
    session: Session,
    *,
    record: RawRecord,
    money: Money,
    batch_id: int,
    row_account: int,
    rank: int,
    card_payment_account_id: int | None,
) -> str | None:
    """Post one flagged card payment. None on success, else the reason.

    Raises `HTTPException(404)` when an account id names nothing, matching the
    ordinary row path; every other per-row refusal is a reason string, so the
    caller can count it and name the line.
    """
    roles = _resolve_card_payment_roles(
        session,
        row_account_id=row_account,
        explicit_id=card_payment_account_id,
    )
    if roles is not None and not (roles[0].is_active and roles[1].is_active):
        # An inactive mapping is as unusable as a missing one; using it would
        # move money through a closed account.
        roles = None
    if roles is None:
        _store_card_payment_unposted(
            session,
            record=record,
            money=money,
            batch_id=batch_id,
            row_account=row_account,
            rank=rank,
            reason=_CARD_PAYMENT,
        )
        return _CARD_PAYMENT

    card, paying = roles
    try:
        write_card_payment(
            session,
            batch_id=batch_id,
            card_account_id=card.id,
            paying_account_id=paying.id,
            statement_account_id=row_account,
            amount=money,
            description=record.description,
            booked_date=record.booked_date,
            raw_data=dict(record.raw_data),
            raw_posting_date=_posting_date(record),
            occurrence_index=rank,
            provider_txn_id=record.provider_txn_id,
        )
    except ReferenceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PostingRefused as exc:
        reason = f"Not posted: {exc}"
        _store_card_payment_unposted(
            session,
            record=record,
            money=money,
            batch_id=batch_id,
            row_account=row_account,
            rank=rank,
            reason=reason,
        )
        return reason
    return None


def _persist(
    session: Session,
    *,
    result: ImportResult,
    payload: bytes,
    filename: str,
    account_id: int | None,
    records: Sequence[RawRecord],
    card_payment_account_id: int | None = None,
) -> tuple[str, int, int, int, list[str]]:
    """Write every row the adapter produced.

    Returns:
        `(status, created, duplicated, failed, reasons)` — see the Returns section.

    ONE `session.begin()` around everything, and it is the handler's job — see
    `finance.api.deps` for why the COMMIT that the deferrable balance trigger can
    reject has to happen while a status code can still be produced. So a refusal
    on row 40 rolls back rows 1-39 rather than leaving half a statement in the
    ledger.

    **KNOWN LIMITATION, inherited and not fixable here.** If batch A holds two
    identical rows and an overlapping batch B holds one of them, B's row is
    treated as the FIRST. There is no way to tell which of A's rows B meant: the
    rows are identical, so the question has no answer in the data. This is a
    property of content-only indexing, not a defect introduced by it, and
    resolving it would need a provider id that the v1 PDF paths do not supply.

    Returns:
        `(status, created, duplicated, failed, reasons)`. `status` is what was
        PERSISTED, never what the parser thought it read — see the note on
        `_batch_status`.

    Raises:
        HTTPException: 404 when `account_id` names no account. Every OTHER
            per-row problem is not raised: it is stored on the row or counted as
            failed, because one bad row must not abandon a 121-row statement.
    """
    reasons: list[str] = []

    # `session.begin()` is the FIRST statement. Not a style preference:
    # `Session.get()` autobegins, and `begin()` refuses when a transaction is
    # already in progress — so an account check placed before the block makes
    # every write below it fail. See `finance.api.deps`, which is where this rule
    # is written down.
    with session.begin():
        if account_id is not None and session.get(Account, account_id) is None:
            # 404 rather than letting the first row's INSERT raise a
            # ForeignKeyViolation as a 500. Before `open_import_batch`, so a
            # request naming no real account leaves no batch behind at all — and
            # the rollback of this block is what guarantees that, since the
            # refusal is raised rather than caught.
            raise HTTPException(status_code=404, detail=f"No account {account_id}")

        batch_id = open_import_batch(
            session,
            provider=result.provider,
            import_method=result.import_method,
            status="processing",
            account_id=account_id,
            source_filename=filename or result.source_filename,
            source_checksum=result.source_checksum,
            raw_payload=_gzip_b64(payload),
            stats={"record_count": result.record_count},
        )

        created = 0
        duplicated = 0
        failed = 0

        # ── The occurrence index, and the one thing it must not depend on ──
        #
        # `assign_occurrence_indices` needs `NormalizedRecord`s, which carry more
        # than this path wants to build (and would re-normalise the description,
        # putting the key at odds with the fingerprint). The ranks are reproduced
        # here by walking the rows in `line_number` order and counting within each
        # content-key — the same algorithm, spelled out, because it is load-bearing
        # and a reader needs to see that it reads CONTENT and not table state.
        #
        # Re-importing [K, K, L] therefore gives K->1, K->2, L->1 both times.
        ranks = _content_only_ranks(records)

        keys = _occurrence_keys(records)

        for record, rank in zip(records, ranks, strict=True):
            money = _money(record)

            # `source_record.account_id` is NOT NULL, so an unattributable row
            # cannot be written at all. That is a per-row failure, not a skip.
            row_account = record.account_id
            if row_account is None:
                failed += 1
                reasons.append(f"line {record.line_number}: {_UNATTRIBUTED}")
                continue

            digest = bytes.fromhex(
                compute_fingerprint(
                    raw_description=record.description,
                    raw_amount=record.amount_minor,
                    raw_currency=record.currency,
                    raw_date=record.booked_date.isoformat(),
                    account_id=fingerprint_account_scope(row_account),
                    occurrence_index=rank,
                )
            )

            # ── Tier 3, as a POINT LOOKUP, before anything else ──
            #
            # Before the card-payment dispatch on purpose: a card payment that was
            # already stored unposted MUST count as a duplicate on re-import, not
            # be stored a second time. Re-importing a statement has to be
            # non-destructive in every state, not only the posted one.
            if _already_stored(session, digest):
                duplicated += 1
                continue

            # ── A card payment NEVER reaches the expense resolver ──
            #
            # `docs/adr/0007-imported-card-payment-is-a-transfer.md`. The equity
            # requirement lives in `resolve_default_counter_account`, NOT in
            # `build_expense_legs`, so a card payment sent through the manual
            # default does NOT reliably raise — with an active `Expenses (system)`
            # account present it books against equity, which balances, commits and
            # is permanent. 4 of the 121 real Amex rows are card payments.
            if record.raw_data.get("is_card_payment") is True:
                card_reason = _persist_card_payment_row(
                    session,
                    record=record,
                    money=money,
                    batch_id=batch_id,
                    row_account=row_account,
                    rank=rank,
                    card_payment_account_id=card_payment_account_id,
                )
                if card_reason is None:
                    created += 1
                else:
                    failed += 1
                    reasons.append(f"line {record.line_number}: {card_reason}")
                continue

            try:
                write_posted_transaction(
                    session,
                    batch_id=batch_id,
                    funding_account_id=row_account,
                    description=record.description,
                    amount=money,
                    booked_date=record.booked_date,
                    raw_data=dict(record.raw_data),
                    raw_posting_date=_posting_date(record),
                    occurrence_index=rank,
                    provider_txn_id=record.provider_txn_id,
                )
            except ReferenceNotFound as exc:
                # The account existed a moment ago and does not now. Refusing the
                # whole import is the honest answer: continuing would write a
                # statement that is silently shorter.
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except PostingRefused as exc:
                # Per row, not per batch: one unpostable row must not abandon the
                # other hundred. The row is still STORED — the parse was right and
                # the evidence is worth keeping — with the reason on it.
                write_unposted_transaction(
                    session,
                    batch_id=batch_id,
                    account_id=row_account,
                    description=record.description,
                    amount=money,
                    booked_date=record.booked_date,
                    raw_data=dict(record.raw_data),
                    error_message=f"Not posted: {exc}",
                    raw_posting_date=_posting_date(record),
                    occurrence_index=rank,
                    provider_txn_id=record.provider_txn_id,
                    status=TransactionStatus.PENDING,
                )
                failed += 1
                reasons.append(f"line {record.line_number}: {exc}")
                continue
            created += 1

        # The consistency assertion, INSIDE this transaction. After the writes,
        # deliberately: `count_existing_occurrences` is checked and NEVER used to
        # compute an index — it cannot reproduce a global ROW_NUMBER while the
        # probe is content-only (see `_stored_occurrence_keys` for what was
        # simulated, and why the alternative is wrong). What it can do is prove
        # the ranks this import used are reproducible from the rows now stored, so
        # a future change to `_content_only_ranks` fails here instead of quietly
        # renumbering every stored fingerprint.
        #
        # Inside the block rather than after it: the COMMIT is what makes these
        # rows durable, and an assertion that ran afterwards would be reading a
        # different connection's view of a batch that might never commit.
        link_transfers(session, batch_id=batch_id)
        _assert_ranks_agree(records, ranks, _stored_occurrence_keys(session, keys))

        status = _batch_status(
            record_count=result.record_count, created=created, duplicated=duplicated
        )
        stats: dict[str, object] = {
            "record_count": result.record_count,
            "created": created,
            "duplicated": duplicated,
            "failed": failed,
            "parse_failed": len(result.failed),
        }
        finish_import_batch(session, batch_id=batch_id, status=status, stats=stats)

    return status, created, duplicated, failed, reasons


def _content_only_ranks(records: Sequence[RawRecord]) -> list[int]:
    """The 1-based rank of each row among content-identical rows in THIS file.

    `assign_occurrence_indices` computes exactly this, over `NormalizedRecord`s.
    It is reimplemented here rather than called because building those would mean
    running `normalize_record` — which calls `normalize_description` a second time
    on text the adapter already normalised, and whose `Money` carries the
    hardcoded 2-decimal exponent this path deliberately does not use.

    The algorithm is one loop over the rows in `line_number` order, counting within
    each content key, writing each row's rank back to its own position — which is
    what `assign_occurrence_indices` does, and the reason the indices depend only
    on the file's contents and not on the order the caller happened to pass rows
    in. Re-importing `[K, K, L]` gives `1, 2, 1` every time, which is what makes a
    second import of the same statement recognise every row as a duplicate.

    Ties on `line_number` fall back to position, for the same reason: synthetic
    rows that share one line number must still get a defined answer.
    """
    keys = _occurrence_keys(records)
    order = sorted(range(len(records)), key=lambda i: (records[i].line_number, i))
    counts: dict[OccurrenceKey, int] = {}
    ranks = [0] * len(records)
    for position in order:
        key = keys[position]
        counts[key] = counts.get(key, 0) + 1
        ranks[position] = counts[key]
    return ranks


def _batch_status(*, record_count: int, created: int, duplicated: int) -> str:
    """What the batch status says about what PERSISTED.

    **Never `ImportResult.status`.** That is parse-derived: it says `completed`
    when the parser produced rows and reported no failures, which is exactly the
    claim a silently short statement must not make. A file of 40 rows where 39
    posted and one card payment was stored unposted is `partial`, whatever the
    adapter thought.

    The test is `created + duplicated == record_count`, and nothing else. Notably
    it is NOT "did anything get created": a re-import creates nothing, recognises
    everything, and IS complete, because every row the file named is accounted for
    in the ledger. Conversely a single unpostable row in an otherwise clean file is
    `partial` even though that row was STORED — storing a row is not posting it,
    and `partial` is what sends the user to look
    (`docs/adr/0007-imported-card-payment-is-a-transfer.md`).

    `failed` is left for the case that deserves it: a file that parsed to nothing.
    """
    if record_count == 0:
        return "failed"
    return "completed" if created + duplicated == record_count else "partial"


def _assert_ranks_agree(
    records: Sequence[RawRecord],
    ranks: Sequence[int],
    stored_keys: Sequence[OccurrenceKey],
) -> None:
    """Prove the content-only ranks match `count_existing_occurrences`.

    An ASSERTION, not a computation, and the asymmetry is the point: a content-only
    probe can count how many stored rows match a row on every component but index,
    but it cannot know which of two identical stored rows this one "would have"
    been. So this checks the weaker, still-useful claim — that the count of
    identical stored rows is at least the rank this row was given, i.e. no row is
    assigned an index beyond the evidence — and leaves the global `ROW_NUMBER`
    unreproduced, which no probe of this shape can reproduce.

    Failure is a bug in `_content_only_ranks`, and the message says so rather than
    letting a shifted index quietly invalidate every fingerprint already stored.
    """
    keys = _occurrence_keys(records)
    for index, (key, rank) in enumerate(zip(keys, ranks, strict=True)):
        matching = count_existing_occurrences(stored_keys, key)
        if matching < rank:
            msg = (
                f"row {records[index].line_number} was assigned occurrence "
                f"index {rank} but only {matching} identical rows are stored, so "
                "the index is not reproducible from stored data and every "
                "fingerprint from this file on would be wrong"
            )
            raise RuntimeError(msg)


@router.post(
    "/file",
    summary="Upload a statement and persist it",
    response_model=ImportSummary,
)
async def import_file(
    session: SessionDep,
    file: UploadFile = File(...),
    provider: str = "amex_pdf",
    account_id: int | None = None,
    card_payment_account_id: int | None = None,
) -> ImportSummary:
    """Accept a statement file, parse it, and WRITE what it parsed.

    Everything `POST /imports` reports, plus the rows in the ledger: one
    `import_batch`, and per row either a `source_record` with a balanced
    `journal_entry` or a `source_record` with no entry and an `error_message`
    saying why. All in one transaction.

    **Re-importing the same file is safe and is the design, not an accident.**
    Every row's Tier-3 fingerprint is probed before it is written, so a second
    upload of the same statement creates nothing and duplicates everything. That is
    why the occurrence index is derived from the file's CONTENTS and never from how
    many similar rows happen to be stored: an index that moved with table state
    would make every re-import rewrite the file instead of recognising it.

    `async def` with the work in a threadpool. A sync SQLAlchemy `Session` cannot
    be awaited, and issuing its statements on the event loop would stall every
    other request for the length of a 7-year PDF parse.

    Args:
        session: From `finance.api.deps`. The handler owns the transaction.
        file: The uploaded statement.
        provider: Which adapter to run. Must match the file extension.
        account_id: The local account to attribute rows to. Without it, rows
            whose own `account_id` is None cannot be written at all
            (`source_record.account_id` is NOT NULL) and are counted as failed.
        card_payment_account_id: The account on the OTHER side of a flagged
            card payment, when the caller wants to name it for this upload.
            Used in preference to the saved mapping
            (`account.payment_from_account_id`), and never guessed from a name.

    Returns:
        What persisted: `created`, `duplicated` and `failed`, plus the failures
        themselves. `status` is the batch status — `partial` when some row did not
        land, which is the normal outcome for an Amex statement until card
        payments can be attributed.

    Raises:
        HTTPException: 400 for an unknown provider, a mismatched extension, or a
            provider that is not uploadable; 404 for an `account_id` that names no
            account; 413 when the upload is too large.
    """
    payload, filename = await _read_upload(file, provider=provider)
    adapter = _FILE_ADAPTERS[provider][0]()
    result = await run_in_threadpool(
        adapter.parse, payload, account_id=account_id, filename=filename
    )
    status, created, duplicated, failed, reasons = await run_in_threadpool(
        _persist,
        session,
        result=result,
        payload=payload,
        filename=filename,
        account_id=account_id,
        records=result.records,
        card_payment_account_id=card_payment_account_id,
    )
    return ImportSummary(
        provider=result.provider,
        import_method=result.import_method,
        status=status,
        record_count=result.record_count,
        source_checksum=result.source_checksum,
        created=created,
        duplicated=duplicated,
        failed=failed,
        failures=reasons,
    )


@router.get(
    "/providers", summary="Supported import providers", response_model=ProviderInfo
)
def list_providers() -> ProviderInfo:
    """The closed provider list, so a client does not hardcode it.

    Declared as data rather than derived from `_FILE_ADAPTERS`, because that
    table says nothing about import methods and omits providers that are not
    yet uploadable.
    """
    return ProviderInfo(providers=list(_PROVIDERS))
