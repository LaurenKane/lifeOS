"""transfer_match.py — pipeline-side transfer matching.

Wraps the domain rule (`finance.domain.services.transfer_match`) with the one
thing the pipeline knows and the domain cannot: which rows to consider.

The domain module owns the *rule* and is pure. This module owns the *search*:
given an account's unmatched lines, find the pairs worth scoring. Keeping the
two apart is what lets the rule be tested without a database, and the search
without a ledger.

The interesting case is the Amex card payment, and it is why this exists
(section H). A EUR 50 purchase on the card is an expense on a liability account.
Weeks later the checking account shows -50 "American Express". There is no
second row to link — the liability balance simply falls. That leg is
*synthesized* during normalisation, not discovered here; this module finds the
outbound and the synthesized inbound and links them.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from finance.domain.services.transfer_match import (
    JournalLineRef,
    TransferMatch,
    is_transfer_pair,
    transfer_match,
    transfer_window,
)

__all__ = [
    "TransferCandidate",
    "TransferMatch",
    "find_transfer_matches",
    "is_transfer_pair",
    "transfer_match",
    "transfer_window",
]


@dataclass(frozen=True)
class TransferCandidate:
    """A journal line as the pipeline sees it, ready for the domain rule."""

    entry_id: str
    account_id: str
    amount_minor: int
    currency: str
    booked_date: dt.date
    transfer_match_id: int | None = None

    def to_ref(self) -> JournalLineRef:
        return JournalLineRef(
            entry_id=self.entry_id,
            account_id=self.account_id,
            amount_minor=self.amount_minor,
            currency=self.currency,
            booked_date=self.booked_date,
            already_matched=self.transfer_match_id is not None,
        )


def find_transfer_matches(
    candidates: Sequence[TransferCandidate],
) -> list[TransferMatch]:
    """Pair up transfer candidates greedily, strongest match first.

    Greedy-by-score, not optimal assignment: a card payment has one obvious
    counterpart and spending a search for the globally optimal pairing buys
    nothing a user can observe. Each line is used at most once, so no two
    matches can claim the same leg.

    Args:
        candidates: Unmatched lines from more than one account.

    Returns:
        Matches ordered by descending confidence.
    """
    unmatched: list[TransferCandidate] = [
        c for c in candidates if c.transfer_match_id is None and c.amount_minor != 0
    ]
    # Outbound legs (negative) drive the search; inbound legs are looked up.
    outbound_legs = sorted(
        (c for c in unmatched if c.amount_minor < 0),
        key=lambda c: (c.booked_date, c.entry_id),
    )
    # Inbound legs live in a DIFFERENT account by definition, so this is a flat
    # list rather than a per-account index.
    inbound_legs = sorted(
        (c for c in unmatched if c.amount_minor > 0),
        key=lambda c: (c.booked_date, c.entry_id),
    )

    matches: list[TransferMatch] = []
    claimed: set[str] = set()
    for outbound in outbound_legs:
        best: TransferMatch | None = None
        best_inbound: str | None = None
        for inbound in inbound_legs:
            if inbound.entry_id in claimed:
                continue
            found = transfer_match(outbound.to_ref(), inbound.to_ref())
            if found is None:
                continue
            if best is None or found.confidence > best.confidence:
                best, best_inbound = found, inbound.entry_id
        if best is not None and best_inbound is not None:
            claimed.add(outbound.entry_id)
            claimed.add(best_inbound)
            matches.append(best)

    return sorted(matches, key=lambda m: (-m.confidence, m.outbound, m.inbound))


def unmatched_transfer_legs(
    candidates: Iterable[TransferCandidate],
) -> list[TransferCandidate]:
    """The lines `find_transfer_matches` did not use.

    These are the review queue: a lone -50 with no counterpart is either a
    transfer whose other leg has not arrived yet, or spending. Guessing which is
    exactly what the review queue is for.
    """
    used: set[str] = set()
    for match in find_transfer_matches(list(candidates)):
        used.add(match.outbound)
        used.add(match.inbound)
    return [c for c in candidates if c.entry_id not in used]
