"""replay.py — rebuild a batch's journal side from its stored file.

An import batch keeps the uploaded bytes in `import_batch.raw_payload`
(gzipped, base64'd), so the journal side of a batch can be rebuilt without the
original file: detach the batch's `source_record` rows from their entries,
delete the entries left orphaned, and re-book every parsed row through the same
writers the import used. Categorization rules are then re-applied from the
`category_rule` table, so a replay also picks up rules added since the import.

The caller owns the transaction (`with session.begin():` around the call), for
the same reason every writer's caller does: the balance trigger fires at
COMMIT, and a rejection has to surface while the caller can still react to it.

A divergence RAISES. `ReplayError` aborts before anything is detached, so no
partial rebuild is ever left behind — and reports from `replay_batch` are
therefore always `ok=True` with empty `divergences`. The field exists so the
report shape can carry findings if a future caller wants them collected rather
than raised; today any divergence aborts before a report is built.

THREE DEVIATIONS FROM THE NAIVE READING, each documented where it bites:

* Deletion is bounded to entries this batch pointed at. A global "delete every
  orphan" would, on a batch with no entries at all, delete the whole ledger's
  journal. In normal operation the two are identical.
* A Revolut multi-section statement needs `section_account_ids` to attribute
  its rows, and the batch does not store them. Replay passes only
  `batch.account_id`, so a multi-account Revolut batch diverges at the
  fingerprint check — loudly, rather than partially.
* A manual category edit is restored when no current rule claims the line
  (see `_snapshot_categories`). Without that, every replay would destroy
  exactly the edits the review queue exists to produce, while a rule that
  matches still always wins — which is what lets updating a rule change the
  ledger on the next replay.
"""

from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass
from typing import Final

from core.money import Money
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from finance.api.routes.imports import (
    _CARD_PAYMENT,
    _FILE_ADAPTERS,
    _content_only_ranks,
    _money,
    _occurrence_keys,
    _posting_date,
    _resolve_card_payment_roles,
)
from finance.api.transfer_linker import link_transfers
from finance.api.writers import (
    PostingRefused,
    ReferenceNotFound,
    _fingerprint,
    write_card_payment,
    write_posted_transaction,
    write_unposted_transaction,
)
from finance.domain.models.importer import ImportBatch, SourceRecord
from finance.domain.models.ledger import JournalEntry, JournalLine
from finance.domain.services.categorize import categorize_transaction
from finance.ingestion.fingerprint import normalize_description
from finance.ingestion.rules import load_rules
from finance.public import RawRecord, TransactionStatus

__all__ = ["ReplayError", "ReplayReport", "replay_batch"]


#: Batch statuses a replay will touch. `pending`/`processing` are refused: the
#: batch is still open (or was never closed), so rebuilding under it would race
#: the import that owns it.
_REPLAYABLE_STATUSES: Final = frozenset({"completed", "partial", "failed"})


@dataclass
class ReplayError(Exception):
    """A replay that refuses to run or to continue. The message says why.

    Raised — never returned — so the caller's transaction rolls back and no
    partial rebuild is left behind.
    """

    message: str

    def __str__(self) -> str:
        """The message, so `str(exc)` says what the batch refused."""
        return self.message


@dataclass(frozen=True)
class ReplayReport:
    """What one replay rebuilt.

    `journal_entry` ids are deliberately absent: they are BIGSERIAL and are
    not stable across a rebuild, so asserting on them would pin a new fact
    rather than the ledger's facts. Counts and fingerprints are the stable
    claims.
    """

    batch_id: int
    parsed_rows: int
    reposted: int
    restored_pending: int
    category_rules_applied: int
    transfer_links_created: int
    divergences: list[str]
    ok: bool


def _load_batch(session: Session, *, batch_id: int) -> ImportBatch:
    """The batch, or the reason there is nothing to replay."""
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise ReplayError(f"No import_batch {batch_id}")
    if batch.status not in _REPLAYABLE_STATUSES:
        raise ReplayError(
            f"import_batch {batch_id} has status {batch.status!r}; only "
            "completed, partial or failed batches can be replayed"
        )
    return batch


def _decode_payload(batch: ImportBatch) -> bytes:
    """The uploaded bytes back out of `import_batch.raw_payload`."""
    raw = batch.raw_payload
    if raw is None:
        raise ReplayError(f"import_batch {batch.id} stores no raw_payload")
    if not isinstance(raw, str):
        raise ReplayError(
            f"import_batch {batch.id} raw_payload is not gzipped base64 text"
        )
    try:
        return gzip.decompress(base64.b64decode(raw))
    except (ValueError, OSError) as exc:
        raise ReplayError(
            f"import_batch {batch.id} raw_payload does not decode: {exc}"
        ) from exc


def _parse_records(batch: ImportBatch, payload: bytes) -> list[RawRecord]:
    """The adapter's rows for the stored file, under the batch's account.

    Only `batch.account_id` is passed: a Revolut multi-section statement needs
    `section_account_ids` to attribute its rows, and the batch does not store
    them. Single-account batches replay exactly; multi-account Revolut batches
    diverge at the fingerprint check instead of replaying half-attributed.
    """
    if batch.provider not in _FILE_ADAPTERS:
        raise ReplayError(
            f"import_batch {batch.id} provider {batch.provider!r} has no file "
            "adapter, so there is nothing to re-parse it with"
        )
    adapter = _FILE_ADAPTERS[batch.provider][0]()
    result = adapter.parse(
        payload, account_id=batch.account_id, filename=batch.source_filename
    )
    return list(result.records)


def _parsed_fingerprints(
    records: list[RawRecord],
) -> tuple[list[bytes], list[int]]:
    """Each row's Tier-3 fingerprint, by the same rank rule the import used.

    Ranks are the content-only within-batch ranks from `_content_only_ranks`
    (which builds `_occurrence_keys` internally; both are imported, neither is
    reimplemented), and the digest is `writers._fingerprint` — byte-identical
    to the `compute_fingerprint` + `fingerprint_account_scope` composition the
    import route spells out, because it IS that composition. Returns the
    digests alongside their ranks, positionally matching `records`.
    """
    ranks = _content_only_ranks(records)
    keys = _occurrence_keys(records)
    if len(ranks) != len(records) or len(keys) != len(records):
        raise ReplayError("the occurrence ranks do not align with the rows")
    digests: list[bytes] = []
    for record, rank in zip(records, ranks, strict=True):
        if record.account_id is None:
            raise ReplayError(
                f"line {record.line_number} names no account, so it has no "
                "fingerprint scope and was never stored; replay cannot "
                "restore a row the import could not write"
            )
        digests.append(
            _fingerprint(
                description=record.description,
                amount_minor=record.amount_minor,
                currency_code=record.currency,
                booked_date=record.booked_date,
                account_id=record.account_id,
                occurrence_index=rank,
            )
        )
    return digests, ranks


def _stored_fingerprints(session: Session, *, batch_id: int) -> dict[bytes, int]:
    """This batch's stored fingerprints, each mapped to its `source_record`."""
    found: dict[bytes, int] = {}
    rows = session.execute(
        select(SourceRecord.id, SourceRecord.fingerprint).where(
            SourceRecord.import_batch_id == batch_id
        )
    ).all()
    for row in rows:
        found[bytes(row[1])] = int(row[0])
    return found


def _check_divergence(parsed: list[bytes], stored: dict[bytes, int]) -> None:
    """The frozen-fingerprint check: the two sets must be identical.

    Any symmetric difference raises, before a single row is detached. A replay
    that continued past a divergence would attach new entries to the wrong
    rows — a partial apply with no honest report, which is exactly what raising
    here prevents.
    """
    if len(set(parsed)) != len(parsed):
        raise ReplayError("the re-parsed file has duplicate fingerprints")
    parsed_set = set(parsed)
    stored_set = set(stored)
    if parsed_set != stored_set:
        only_parsed = len(parsed_set - stored_set)
        only_stored = len(stored_set - parsed_set)
        raise ReplayError(
            f"fingerprint divergence: {only_parsed} re-parsed rows have no "
            f"stored row and {only_stored} stored rows have no re-parsed row"
        )


def _snapshot_categories(
    session: Session, *, batch_id: int
) -> dict[tuple[int, int], int]:
    """Pre-replay `(source_record id, account id)` to category, for set lines.

    Manual categorization edits live on `journal_line`, and the rebuild below
    deletes every orphaned entry — including an edited one. The snapshot is
    what lets a manual edit survive: after re-booking, a line no current rule
    claims gets its previous category back. A line a rule DOES claim follows
    the rule, which is what lets updating a rule change the ledger on replay.
    """
    snapshot: dict[tuple[int, int], int] = {}
    rows = session.execute(
        select(SourceRecord.id, JournalLine.account_id, JournalLine.category_id)
        .join(
            JournalLine,
            JournalLine.journal_entry_id == SourceRecord.journal_entry_id,
        )
        .where(SourceRecord.import_batch_id == batch_id)
        .where(SourceRecord.journal_entry_id.is_not(None))
        .where(JournalLine.category_id.is_not(None))
    ).all()
    for row in rows:
        if row[2] is not None:
            snapshot[(int(row[0]), int(row[1]))] = int(row[2])
    return snapshot


def _detach_and_delete_orphans(session: Session, *, batch_id: int) -> None:
    """Detach this batch's rows, then delete the entries left orphaned.

    Detach writes only `journal_entry_id` — one of the columns the immutability
    trigger deliberately leaves writable. Deletion is bounded to entries this
    batch pointed at (see the module docstring for why a global orphan sweep
    would be unsafe). `journal_line` cascades; entries still referenced by
    another batch's `source_record` survive, because they are not orphans.
    """
    entry_ids = session.scalars(
        select(SourceRecord.journal_entry_id).where(
            SourceRecord.import_batch_id == batch_id,
            SourceRecord.journal_entry_id.is_not(None),
        )
    ).all()
    session.execute(
        update(SourceRecord)
        .where(
            SourceRecord.import_batch_id == batch_id,
            SourceRecord.journal_entry_id.is_not(None),
        )
        .values(journal_entry_id=None)
    )
    own = [int(entry_id) for entry_id in entry_ids if entry_id is not None]
    if not own:
        return
    live = select(SourceRecord.journal_entry_id).where(
        SourceRecord.journal_entry_id.is_not(None)
    )
    session.execute(
        delete(JournalEntry).where(JournalEntry.id.in_(own), ~JournalEntry.id.in_(live))
    )


def _replay_card_payment_row(
    session: Session,
    *,
    record: RawRecord,
    money: Money,
    batch_id: int,
    row_account: int,
    rank: int,
    source_record_id: int,
) -> str | None:
    """Post one flagged card payment onto its existing row, or return why not.

    Mirrors `_persist_card_payment_row` in the import route — same role
    resolution, same inactive-mapping refusal, same unposted fallback — except
    every write carries `source_record_id`, so nothing is inserted. There is
    no explicit `card_payment_account_id` on a replay: the batch stores no
    per-upload override, so only the saved mapping can resolve.
    `ReferenceNotFound` propagates: a vanished account aborts the replay, the
    way a 404 aborts an import.
    """
    roles = _resolve_card_payment_roles(
        session, row_account_id=row_account, explicit_id=None
    )
    if roles is not None and not (roles[0].is_active and roles[1].is_active):
        # An inactive mapping is as unusable as a missing one; using it would
        # move money through a closed account.
        roles = None
    if roles is None:
        write_unposted_transaction(
            session,
            batch_id=batch_id,
            account_id=row_account,
            description=record.description,
            amount=money,
            booked_date=record.booked_date,
            raw_data=dict(record.raw_data),
            error_message=_CARD_PAYMENT,
            raw_posting_date=_posting_date(record),
            occurrence_index=rank,
            provider_txn_id=record.provider_txn_id,
            status=TransactionStatus.PENDING,
            source_record_id=source_record_id,
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
            source_record_id=source_record_id,
        )
    except PostingRefused as exc:
        reason = f"Not posted: {exc}"
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
            source_record_id=source_record_id,
        )
        return reason
    return None


def _rebook(
    session: Session,
    *,
    records: list[RawRecord],
    digests: list[bytes],
    ranks: list[int],
    stored: dict[bytes, int],
    batch_id: int,
) -> tuple[int, int]:
    """Re-book every row onto its existing `source_record`.

    The same writer chain the import used: `write_posted_transaction` for
    ordinary rows with its `PostingRefused`-to-unposted fallback, the
    card-payment path for flagged rows. Returns `(reposted, restored_pending)`.
    The rank is each row's content-only occurrence index, recomputed from the
    file — the same number the fingerprint was checked under, so the stored
    fingerprint stays reproducible.
    """
    reposted = 0
    restored_pending = 0
    for record, digest, rank in zip(records, digests, ranks, strict=True):
        source_record_id = stored.get(digest)
        if source_record_id is None:
            # Unreachable after `_check_divergence`; a guard, not a branch.
            raise ReplayError("a re-parsed fingerprint has no stored row")
        row_account = record.account_id
        if row_account is None:
            # Same: `_parsed_fingerprints` already refused these.
            raise ReplayError(f"line {record.line_number} names no account")
        money = _money(record)
        try:
            if record.raw_data.get("is_card_payment") is True:
                reason = _replay_card_payment_row(
                    session,
                    record=record,
                    money=money,
                    batch_id=batch_id,
                    row_account=row_account,
                    rank=rank,
                    source_record_id=source_record_id,
                )
                if reason is None:
                    reposted += 1
                else:
                    restored_pending += 1
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
                    source_record_id=source_record_id,
                )
            except PostingRefused as exc:
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
                    source_record_id=source_record_id,
                )
                restored_pending += 1
                continue
            reposted += 1
        except ReferenceNotFound as exc:
            raise ReplayError(f"line {record.line_number}: {exc}") from exc
    return reposted, restored_pending


def _categorize(
    session: Session,
    *,
    batch_id: int,
    snapshot: dict[tuple[int, int], int],
) -> int:
    """Re-apply the stored rules to this batch's fresh lines.

    Only lines with no category are touched: a line already carrying one — on
    an entry shared with another batch, or set by hand — is left alone. When
    no rule matches a line, the pre-replay snapshot is restored onto it (see
    `_snapshot_categories`); when a rule matches, the rule wins. Returns how
    many lines a rule categorized.
    """
    rules = load_rules(session)
    entry_description: dict[int, str] = {}
    entry_records: dict[int, list[int]] = {}
    rows = session.execute(
        select(
            SourceRecord.id,
            SourceRecord.journal_entry_id,
            SourceRecord.raw_description,
        ).where(
            SourceRecord.import_batch_id == batch_id,
            SourceRecord.journal_entry_id.is_not(None),
        )
    ).all()
    for row in rows:
        if row[1] is None:
            continue
        entry_id = int(row[1])
        entry_description.setdefault(entry_id, str(row[2]))
        entry_records.setdefault(entry_id, []).append(int(row[0]))
    if not entry_description:
        return 0
    lines = session.scalars(
        select(JournalLine).where(
            JournalLine.journal_entry_id.in_(list(entry_description)),
            JournalLine.category_id.is_(None),
        )
    ).all()
    applied = 0
    for line in lines:
        description = entry_description[line.journal_entry_id]
        result = categorize_transaction(
            description, rules=rules, normalize=normalize_description
        )
        if result.category_id is not None:
            line.category_id = result.category_id
            applied += 1
            continue
        for source_record_id in entry_records[line.journal_entry_id]:
            previous = snapshot.get((source_record_id, line.account_id))
            if previous is not None:
                line.category_id = previous
                break
    session.flush()
    return applied


def replay_batch(session: Session, *, batch_id: int) -> ReplayReport:
    """Rebuild one batch's journal side from its stored file.

    All inside the caller's transaction: the caller writes
    `with session.begin():` around this call, so a `ReplayError` — or a
    balance-trigger rejection at COMMIT — rolls back the whole replay.

    The steps, in order: (a) load the batch, refusing a missing batch, a
    missing payload, or a `pending`/`processing` status; (b) gunzip the
    base64 payload; (c) re-parse it with the batch's provider adapter, under
    the batch's account only; (d) recompute every row's Tier-3 fingerprint;
    (e) require the re-parsed set to equal the stored set, raising on any
    symmetric difference; (f) detach the batch's rows; (g) delete the entries
    left orphaned; (h) re-book every row through the import's writer chain,
    counting posted vs unposted; (i) link this batch's unmatched transfer
    legs; (j) re-apply the stored categorization rules.

    Args:
        session: The caller's session. Never begun, committed or rolled back
            here.
        batch_id: The `import_batch` to rebuild.

    Returns:
        What was rebuilt. Always `ok=True`: anything divergent raises
        `ReplayError` before a report is built.

    Raises:
        ReplayError: The batch is missing, unreplayable, undecodable,
            unparsable under its stored account, or divergent from what was
            stored — or a row's account vanished since the import.
    """
    batch = _load_batch(session, batch_id=batch_id)
    payload = _decode_payload(batch)
    records = _parse_records(batch, payload)
    digests, ranks = _parsed_fingerprints(records)
    stored = _stored_fingerprints(session, batch_id=batch_id)
    _check_divergence(digests, stored)
    snapshot = _snapshot_categories(session, batch_id=batch_id)
    _detach_and_delete_orphans(session, batch_id=batch_id)
    reposted, restored_pending = _rebook(
        session,
        records=records,
        digests=digests,
        ranks=ranks,
        stored=stored,
        batch_id=batch_id,
    )
    links = link_transfers(session, batch_id=batch_id)
    applied = _categorize(session, batch_id=batch_id, snapshot=snapshot)
    return ReplayReport(
        batch_id=batch_id,
        parsed_rows=len(records),
        reposted=reposted,
        restored_pending=restored_pending,
        category_rules_applied=applied,
        transfer_links_created=links.auto_matched,
        divergences=[],
        ok=True,
    )
