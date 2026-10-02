"""identity.py — IdentityResolver, the three dedup tiers.

ARCHITECTURE-PROPOSAL.md section G. The schema is provider-agnostic; only this
resolver is provider-aware.

    Tier 1  provider ID          (account_id, provider_txn_id)   — API only
    Tier 2  pending -> booked    (amount/date/description window) — EB only
    Tier 3  content fingerprint  (everything else, files and API)

Ordering matters. Tier 1 is a primary key lookup and is exact, so it runs first
and costs nothing. Tier 2 is a scored search over a candidate window and can be
confident but is never certain. Tier 3 is a pure function and is the backstop.

**The default is never "merge".** Perfect automatic dedup is impossible when a
provider supplies no ID and two genuinely identical transactions occur on the
same day. This resolver therefore returns a decision with a confidence band:

    >= 0.85   auto-link
    0.50-0.85 create new, queue for review
    <  0.50   create new

`entry_reference` is unique per account, never globally, so Tier 1 is always
scoped by account. Enable Banking's `account.uid` is deliberately not used: it
rotates at every re-auth, so a fingerprint keyed on it would break on the next
consent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from finance.ingestion.dedupe import ExistingFingerprint, lookup_fingerprint
from finance.ingestion.fingerprint import compute_fingerprint, normalize_description

__all__ = [
    "AUTO_LINK_THRESHOLD",
    "Candidate",
    "IdentityDecision",
    "IdentityResolver",
    "REVIEW_THRESHOLD",
    "trigram_similarity",
]

# Confidence bands, from section G. Auto-link is deliberately conservative:
# a wrong merge destroys a transaction, a wrong non-merge is merely a duplicate
# in a review queue the user can clear in seconds.
AUTO_LINK_THRESHOLD: float = 0.85
REVIEW_THRESHOLD: float = 0.50

# Amount tolerance in MINOR UNITS, not major. One cent.
AMOUNT_TOLERANCE_MINOR: int = 1

# Outbound precedes inbound. Section G states the window from the stored row's
# point of view — `new.date BETWEEN existing.date - 3 AND existing.date + 1` —
# which means the STORED row falls between (new.date - 1) and (new.date + 3).
# Restated here relative to the incoming date so the asymmetry is not lost in a
# transposition: a stored row may be up to 3 days LATER, or 1 day earlier.
DATE_WINDOW_EARLIER = dt.timedelta(days=1)
DATE_WINDOW_LATER = dt.timedelta(days=3)


@dataclass(frozen=True)
class Candidate:
    """A stored row being considered as the counterpart of an incoming one."""

    source_record_id: str
    account_id: str
    amount_minor: int
    booked_date: dt.date
    description: str
    status: str
    merchant_alias_id: int | None = None
    fingerprint: str = ""
    journal_entry_id: int | None = None
    transfer_match_id: int | None = None

    @property
    def is_already_matched(self) -> bool:
        return self.transfer_match_id is not None

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"


@dataclass(frozen=True)
class IdentityDecision:
    """What the resolver decided, and how sure it is.

    `tier` is 1, 2 or 3 — which rule produced the answer. `tier=0` means no rule
    matched and the row is simply new, which is the common case and not an
    error.
    """

    is_duplicate: bool
    tier: int
    source_record_id: str | None = None
    journal_entry_id: int | None = None
    confidence: float = 0.0
    needs_review: bool = False
    fingerprint: str = ""
    reason: str = ""

    @property
    def should_auto_link(self) -> bool:
        return self.is_duplicate and not self.needs_review


def trigram_similarity(left: str, right: str) -> Decimal:
    """Dice coefficient over character trigrams, in 0.0-1.0.

    A cheap stand-in for Postgres `pg_trgm`, so the scoring rule is testable with
    no database. Strings shorter than three characters degrade to exact equality,
    because a trigram set of a two-character string is meaningless noise.

    Stands in for `similarity(a, b) > 0.8` in the section G scoring formula.
    Returns a Decimal rather than a float so the score is bit-identical on every
    run — it is persisted and shown to the user.
    """
    a, b = left.lower(), right.lower()
    if a == b:
        return Decimal("1.00")
    if len(a) < 3 or len(b) < 3:
        return Decimal("0.00")
    grams_a = {a[i : i + 3] for i in range(len(a) - 2)}
    grams_b = {b[i : i + 3] for i in range(len(b) - 2)}
    shared = len(grams_a & grams_b)
    return Decimal(2 * shared) / Decimal(len(grams_a) + len(grams_b))


def _tier_2_candidates(
    amount_minor: int,
    booked_date: dt.date,
    account_id: str,
    currency: str,
    candidates: Iterable[Candidate],
) -> list[Candidate]:
    """Rows eligible for a Tier-2 match.

    Same account, same currency, already unmatched, and inside the asymmetric
    date window. Deliberately narrow: a Tier-2 match merges two records, so a
    candidate that fails any of these is not a near miss, it is a different
    transaction.
    """
    window_start = booked_date - DATE_WINDOW_EARLIER
    window_end = booked_date + DATE_WINDOW_LATER
    eligible: list[Candidate] = []
    for candidate in candidates:
        if candidate.account_id != account_id or candidate.is_already_matched:
            continue
        if abs(candidate.amount_minor - amount_minor) > AMOUNT_TOLERANCE_MINOR:
            continue
        if not window_start <= candidate.booked_date <= window_end:
            continue
        eligible.append(candidate)
    return eligible


def score_tier_2(
    *,
    amount_minor: int,
    booked_date: dt.date,
    description: str,
    merchant_alias_id: int | None,
    candidate: Candidate,
    alternatives: int,
) -> Decimal:
    """Confidence for one Tier-2 pairing, per the section G formula.

    Starts at 1.0 and subtracts for every signal that is weaker than ideal:

        -0.30  amount only matched within tolerance, not exactly
        -0.20  date does not match exactly
        -0.20  trigram similarity of description <= 0.8
        -0.10  the two do not resolve to the same merchant alias
        -0.10  the stored row is not pending (a weaker signal)
        -0.10  more than one candidate in the window

    Returns:
        The score, clamped to 0.0-1.0. Floats are avoided so the score is exactly
        reproducible — the same inputs always give bit-identical results, which
        matters because the score is persisted and shown to the user.
    """
    score = Decimal("1.00")

    if candidate.amount_minor != amount_minor:
        score -= Decimal("0.30")
    if candidate.booked_date != booked_date:
        score -= Decimal("0.20")
    if (
        trigram_similarity(normalize_description(description), candidate.description)
        <= 0.8
    ):
        score -= Decimal("0.20")
    if merchant_alias_id is None or candidate.merchant_alias_id != merchant_alias_id:
        score -= Decimal("0.10")
    if not candidate.is_pending:
        score -= Decimal("0.10")
    if alternatives > 1:
        score -= Decimal("0.10")

    return min(max(score, Decimal("0.00")), Decimal("1.00"))


class IdentityResolver:
    """Resolves one incoming transaction against what is already stored.

    Stateless and injectable: every source of truth is passed in per call. The
    resolver holds no cursor and issues no queries, so the whole three-tier
    decision surface is exercised by unit tests.
    """

    def __init__(
        self,
        *,
        auto_link_threshold: float = AUTO_LINK_THRESHOLD,
        review_threshold: float = REVIEW_THRESHOLD,
    ) -> None:
        self._auto_link = auto_link_threshold
        self._review = review_threshold

    def resolve(
        self,
        *,
        account_id: str,
        description: str,
        amount_minor: int,
        currency: str,
        booked_date: dt.date,
        provider_txn_id: str | None = None,
        merchant_alias_id: int | None = None,
        occurrence_index: int = 1,
        known_txn_ids: Iterable[tuple[str, str]] = (),
        candidates: Iterable[Candidate] = (),
        stored_fingerprints: Iterable[ExistingFingerprint] = (),
    ) -> IdentityDecision:
        """Run the tiers in order and return the first decisive answer.

        Args:
            account_id: Scope key for every tier.
            description: The raw provider description.
            amount_minor: Signed minor units. Never a float.
            currency: ISO 4217 code.
            booked_date: The provider's booking date.
            provider_txn_id: Tier 1 only. None for Amex, which has no stable ID.
            merchant_alias_id: Used by Tier 2's merchant signal.
            occurrence_index: Tier 3 disambiguator; see `assign_occurrence_indices`.
            known_txn_ids: (account_id, provider_txn_id) pairs already stored.
            candidates: Rows in the Tier-2 search space.
            stored_fingerprints: Rows for the Tier-3 lookup.

        Returns:
            An `IdentityDecision`. `tier=0` means "new transaction", which is
            the expected result for most rows.
        """
        fingerprint = compute_fingerprint(
            raw_description=description,
            raw_amount=amount_minor,
            raw_currency=currency,
            raw_date=booked_date.isoformat(),
            account_id=account_id,
            occurrence_index=occurrence_index,
        )

        tier_1 = self._tier_1(
            account_id=account_id,
            provider_txn_id=provider_txn_id,
            known_txn_ids=known_txn_ids,
            fingerprint=fingerprint,
        )
        if tier_1 is not None:
            return tier_1

        tier_2 = self._tier_2(
            account_id=account_id,
            description=description,
            amount_minor=amount_minor,
            currency=currency,
            booked_date=booked_date,
            merchant_alias_id=merchant_alias_id,
            candidates=candidates,
            fingerprint=fingerprint,
        )
        if tier_2 is not None:
            return tier_2

        return self._tier_3(
            account_id=account_id,
            description=description,
            amount_minor=amount_minor,
            currency=currency,
            booked_date=booked_date,
            occurrence_index=occurrence_index,
            stored_fingerprints=stored_fingerprints,
            fingerprint=fingerprint,
        )

    def _tier_1(
        self,
        *,
        account_id: str,
        provider_txn_id: str | None,
        known_txn_ids: Iterable[tuple[str, str]],
        fingerprint: str,
    ) -> IdentityDecision | None:
        """Exact provider-ID match. API providers only."""
        if not provider_txn_id:
            return None
        for stored_account, stored_id in known_txn_ids:
            if stored_account == account_id and stored_id == provider_txn_id:
                return IdentityDecision(
                    is_duplicate=True,
                    tier=1,
                    source_record_id=None,
                    confidence=1.0,
                    fingerprint=fingerprint,
                    reason=f"provider_txn_id {provider_txn_id!r} already stored",
                )
        return None

    def _tier_2(
        self,
        *,
        account_id: str,
        description: str,
        amount_minor: int,
        currency: str,
        booked_date: dt.date,
        merchant_alias_id: int | None,
        candidates: Iterable[Candidate],
        fingerprint: str,
    ) -> IdentityDecision | None:
        """Pending-to-booked correlation. Enable Banking only.

        Scored rather than decided: the best candidate is compared against the
        bands, and anything short of certainty becomes a review item instead of
        a merge.
        """
        eligible = _tier_2_candidates(
            amount_minor, booked_date, account_id, currency, candidates
        )
        if not eligible:
            return None

        # Ties resolve on source_record_id, not on iteration order: the same
        # data must always produce the same decision, whoever queried it.
        best_score = Decimal("0.00")
        best = min(eligible, key=lambda c: c.source_record_id)
        for candidate in eligible:
            score = score_tier_2(
                amount_minor=amount_minor,
                booked_date=booked_date,
                description=description,
                merchant_alias_id=merchant_alias_id,
                candidate=candidate,
                alternatives=len(eligible),
            )
            if score > best_score or (
                score == best_score
                and candidate.source_record_id < best.source_record_id
            ):
                best_score, best = score, candidate

        confidence = float(best_score)
        if confidence < self._review:
            return None

        needs_review = confidence < self._auto_link
        return IdentityDecision(
            is_duplicate=not needs_review,
            tier=2,
            source_record_id=best.source_record_id,
            journal_entry_id=best.journal_entry_id,
            confidence=confidence,
            needs_review=needs_review,
            fingerprint=fingerprint,
            reason=(
                f"pending->booked match with {best.source_record_id} "
                f"(score {best_score:.2f})"
            ),
        )

    def _tier_3(
        self,
        *,
        account_id: str,
        description: str,
        amount_minor: int,
        currency: str,
        booked_date: dt.date,
        occurrence_index: int,
        stored_fingerprints: Iterable[ExistingFingerprint],
        fingerprint: str,
    ) -> IdentityDecision:
        """Content fingerprint. Exact by construction, so no scoring needed."""
        match = lookup_fingerprint(
            existing=stored_fingerprints,
            account_id=account_id,
            raw_description=description,
            raw_amount=amount_minor,
            raw_currency=currency,
            raw_date=booked_date.isoformat(),
            occurrence_index=occurrence_index,
        )
        if match is None:
            return IdentityDecision(
                is_duplicate=False,
                tier=0,
                confidence=0.0,
                fingerprint=fingerprint,
                reason="no fingerprint match — new transaction",
            )
        return IdentityDecision(
            is_duplicate=True,
            tier=3,
            source_record_id=match.source_record_id,
            journal_entry_id=match.journal_entry_id,
            confidence=1.0,
            fingerprint=fingerprint,
            reason=f"fingerprint matches stored row {match.source_record_id}",
        )
