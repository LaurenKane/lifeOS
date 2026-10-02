"""dedupe.py — Tier-3 fingerprint dedup and occurrence indexing.

Pure logic over already-stored rows. No database: the caller passes in what
exists and gets back a decision. That is what makes the dedup rules testable
without a Postgres instance, which is why the reference implementations are not
worth studying for behaviour (ARCHITECTURE-PROPOSAL.md section B).

Two responsibilities, kept separate because they fail differently:

- **Occurrence indexing.** Deciding that two EUR 3.20 coffees on the same day are
  two transactions, not one transaction and one duplicate. Getting this wrong
  either eats a real transaction or creates a phantom one.
- **Fingerprint lookup.** Deciding whether an incoming row matches a stored one.

Both delegate the description normalisation to `fingerprint.normalize_description`.
This file is deliberately NOT hash-pinned, so it must never grow a second,
slightly different normalisation: that is how a dedup rule starts disagreeing
with itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from finance.ingestion.fingerprint import compute_fingerprint, normalize_description

if TYPE_CHECKING:
    from finance.ingestion.normalize import NormalizedRecord

__all__ = [
    "ExistingFingerprint",
    "OccurrenceKey",
    "assign_occurrence_indices",
    "count_existing_occurrences",
    "lookup_fingerprint",
]


@dataclass(frozen=True, order=True)
class OccurrenceKey:
    """Everything that makes two rows "the same transaction".

    Two rows differing only in `occurrence_index` are the same purchase seen
    twice. Two rows differing in any other component are different purchases.
    Ordered so a set or sorted() of keys is deterministic.
    """

    account_id: str
    normalized_description: str
    amount_minor: int
    currency: str
    booked_date: date

    @classmethod
    def from_normalized(cls, record: NormalizedRecord) -> OccurrenceKey:
        """Build a key from a `NormalizedRecord`."""
        return cls(
            account_id=record.account_id,
            normalized_description=normalize_description(record.description),
            amount_minor=record.amount.amount,
            currency=record.amount.currency.code,
            booked_date=record.booked_date,
        )


@dataclass(frozen=True)
class ExistingFingerprint:
    """One stored row's dedup key.

    `journal_entry_id` is None for a stored row that has not been normalised
    yet. Such a row still blocks a duplicate: the raw record already landed, so
    re-importing the same line must not create a second one.
    """

    fingerprint: str
    account_id: str
    source_record_id: str
    journal_entry_id: int | None = None


def assign_occurrence_indices(records: Sequence[NormalizedRecord]) -> list[int]:
    """Assign a 1-based occurrence index to each record.

    Implements `ROW_NUMBER() OVER (PARTITION BY account_id,
    normalized_description, amount, currency, date ORDER BY import_batch_id,
    raw_line_number)`.

    The `ORDER BY` is honoured explicitly rather than by assuming the caller
    passed the rows in file order. Rows are ranked by `line_number` within each
    partition, so the result depends only on the row contents and its line
    number — not on the order they arrived in this function. Two consequences
    matter:

    - Re-importing a CSV preserves line order, so the indices come out identical
      and every stored fingerprint stays reproducible.
    - A caller that filtered or sorted the batch before calling gets the same
      indices as one that did not. Counting arrival order instead would renumber
      everything and silently invalidate every fingerprint already in the
      database.

    Args:
        records: Rows from one batch.

    Returns:
        One occurrence index per record, positionally matching `records`.
    """
    keys = [OccurrenceKey.from_normalized(record) for record in records]

    # Walk the rows in line order, counting within each partition, and write each
    # row's rank back to its own position. Ties on line_number fall back to
    # position, which keeps the result defined for synthetic rows that share one.
    order = sorted(range(len(records)), key=lambda i: (records[i].line_number, i))
    counts: dict[OccurrenceKey, int] = {}
    indices = [0] * len(records)
    for position in order:
        key = keys[position]
        counts[key] = counts.get(key, 0) + 1
        indices[position] = counts[key]
    return indices


def count_existing_occurrences(
    existing: Iterable[OccurrenceKey], key: OccurrenceKey
) -> int:
    """How many stored rows already match `key` on every component but index."""
    return sum(1 for candidate in existing if candidate == key)


def lookup_fingerprint(
    *,
    existing: Iterable[ExistingFingerprint],
    account_id: str,
    raw_description: str,
    raw_amount: int,
    raw_currency: str,
    raw_date: str,
    occurrence_index: int = 1,
) -> ExistingFingerprint | None:
    """Find the stored row this incoming transaction duplicates, if any.

    Scoped by `account_id`: the same EUR 3.20 coffee on two different cards is
    two real transactions, and cross-account dedup would silently delete one.

    This is Tier 3 — the backstop that also runs on API rows, because it is what
    makes re-importing a 7-year Amex PDF and then a 6-month CSV non-destructive.

    Returns:
        The matching stored row, or None when this is a new transaction.
    """
    target = compute_fingerprint(
        raw_description=raw_description,
        raw_amount=raw_amount,
        raw_currency=raw_currency,
        raw_date=raw_date,
        account_id=account_id,
        occurrence_index=occurrence_index,
    )
    for candidate in existing:
        if candidate.account_id == account_id and candidate.fingerprint == target:
            return candidate
    return None
