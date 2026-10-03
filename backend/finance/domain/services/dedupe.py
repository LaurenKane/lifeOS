"""dedupe.py — Tier-3 dedup decisions, in the domain layer.

Pure: no I/O, no database, no provider awareness. The ingestion pipeline owns
*where* a fingerprint comes from; this module owns the rule for deciding whether
two rows are the same purchase.

The fingerprint arrives as an argument. It is computed by
`finance.ingestion.fingerprint`, which is SHA-256 hash-pinned by
`invariants.yaml` and is the single definition of "the same row". This module
takes that value rather than computing it, for two reasons:

- `finance.domain` must not depend on `finance.ingestion`. The layering is
  api -> ingestion -> domain -> core; a domain service that reached back up into
  the pipeline would invert it (enforced by `.importlinter` contract 6).
- A second copy of the normalisation rule is how a dedup logic starts
  disagreeing with itself. The hash pin only covers one file.

So the caller normalises and computes; this module decides.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["DedupeCandidate", "DedupeResult", "dedupe_check", "find_duplicate"]


@dataclass(frozen=True)
class DedupeCandidate:
    """A stored row that an incoming transaction might duplicate."""

    account_id: int | None
    fingerprint: str
    source_record_id: int
    journal_entry_id: int | None = None

    @property
    def is_normalised(self) -> bool:
        """Whether this row has reached the ledger yet.

        A duplicate of an un-normalised row is still a duplicate: the raw record
        already landed, so re-importing the same line must not create a second
        one.
        """
        return self.journal_entry_id is not None


@dataclass(frozen=True)
class DedupeResult:
    """The outcome of one dedup check.

    `fingerprint` is echoed back so the caller can store it on a row it decides
    to create.
    """

    is_duplicate: bool
    fingerprint: str
    occurrence_index: int = 1
    matched: DedupeCandidate | None = None

    @property
    def canonical_entry_id(self) -> int | None:
        """The journal entry that should stand for this transaction.

        None means "not a duplicate", never "we lost track of it".
        """
        return None if self.matched is None else self.matched.journal_entry_id


def find_duplicate(
    candidates: Iterable[DedupeCandidate],
    *,
    account_id: int | None,
    fingerprint: str,
) -> DedupeCandidate | None:
    """The first candidate on the same account with this fingerprint.

    Scoped by account: the same EUR 3.20 coffee on two different cards is two
    real transactions, and cross-account dedup would silently delete one.
    """
    for candidate in candidates:
        if candidate.account_id == account_id and candidate.fingerprint == fingerprint:
            return candidate
    return None


def dedupe_check(
    *,
    account_id: int | None,
    fingerprint: str,
    candidates: Iterable[DedupeCandidate] = (),
    occurrence_index: int = 1,
) -> DedupeResult:
    """Decide whether an incoming transaction duplicates a stored one.

    Args:
        account_id: Scope key. Never matched across accounts.
        fingerprint: The Tier-3 digest, from `finance.ingestion.fingerprint`.
            Passed in rather than computed here — see the module docstring.
        candidates: Rows already stored, for the caller to have loaded.
        occurrence_index: 1-based position among rows identical on every other
            component. Two identical EUR 3.20 coffees get 1 and 2.

    Returns:
        A `DedupeResult`. `is_duplicate=False` is the common case and carries
        the fingerprint so the caller can store it.
    """
    matched = find_duplicate(
        candidates,
        account_id=account_id,
        fingerprint=fingerprint,
    )
    return DedupeResult(
        is_duplicate=matched is not None,
        fingerprint=fingerprint,
        occurrence_index=occurrence_index,
        matched=matched,
    )
