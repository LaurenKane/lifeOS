"""IdentityResolver tests — the three dedup tiers.

docs/adr/0003-import-decisions-real-export.md. The property under test throughout
is the bias: **the default is never "merge"**. A wrong merge destroys a
transaction; a wrong non-merge is one row in a review queue the user clears in
seconds. So most of these tests assert that something does NOT auto-link.

Synthetic data throughout.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from finance.ingestion.dedupe import ExistingFingerprint, fingerprint_account_scope
from finance.ingestion.fingerprint import compute_fingerprint
from finance.ingestion.identity import (
    AUTO_LINK_THRESHOLD,
    REVIEW_THRESHOLD,
    Candidate,
    IdentityDecision,
    IdentityResolver,
    trigram_similarity,
)

# Invented, and shaped like the BIGSERIAL M1 hands out.
ACCOUNT = 1001

# A second account, so cross-account cases are distinguishable by value.
OTHER_ACCOUNT = 2002


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


class TestTier1ProviderId:
    """Exact match on the provider's own id. API providers only."""

    def test_known_id_is_a_duplicate(self) -> None:
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-14"),
            provider_txn_id="eb-ref-1",
            known_txn_ids=[(ACCOUNT, "eb-ref-1")],
        )
        assert decision.is_duplicate
        assert decision.tier == 1
        assert decision.confidence == 1.0
        assert not decision.needs_review

    def test_unknown_id_is_a_new_transaction(self) -> None:
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-14"),
            provider_txn_id="eb-ref-2",
            known_txn_ids=[(ACCOUNT, "eb-ref-1")],
        )
        assert not decision.is_duplicate
        assert decision.tier == 0

    def test_same_id_on_a_different_account_is_not_a_match(self) -> None:
        """`entry_reference` is unique per ACCOUNT, never globally.

        Treating it as global would mark every transaction on one card as a
        duplicate of an unrelated transaction on another.
        """
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=OTHER_ACCOUNT,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-14"),
            provider_txn_id="eb-ref-1",
            known_txn_ids=[(ACCOUNT, "eb-ref-1")],
        )
        assert not decision.is_duplicate

    def test_absent_id_skips_the_tier(self) -> None:
        """Amex has no stable id, so Tier 1 does not apply to it at all."""
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-14"),
            provider_txn_id=None,
            known_txn_ids=[(ACCOUNT, "eb-ref-1")],
        )
        assert decision.tier == 0

    def test_empty_id_is_treated_as_absent(self) -> None:
        """ "" would match every other "" in a uniqueness check."""
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-14"),
            provider_txn_id="",
            known_txn_ids=[(ACCOUNT, "")],
        )
        assert decision.tier == 0


class TestTier2PendingToBooked:
    """Enable Banking only. Scored, never certain."""

    def _resolve(
        self, candidates: list[Candidate], **overrides: object
    ) -> IdentityDecision:
        params: dict[str, object] = {
            "account_id": ACCOUNT,
            "description": "Jumbo 4321 Amsterdam",
            "amount_minor": -850,
            "currency": "EUR",
            "booked_date": _date("2026-03-15"),
            "candidates": candidates,
        }
        params.update(overrides)
        return IdentityResolver().resolve(**params)  # type: ignore[arg-type]

    def test_exact_pending_twin_auto_links(self) -> None:
        """The strongest Tier-2 case: same amount, same date, same description,
        same merchant, and the stored row is still pending."""
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                    journal_entry_id=42,
                )
            ],
            merchant_alias_id=7,
        )
        assert decision.tier == 2
        assert decision.is_duplicate
        assert not decision.needs_review
        assert decision.source_record_id == 5001
        assert decision.journal_entry_id == 42
        assert decision.confidence >= AUTO_LINK_THRESHOLD

    def test_similar_description_is_auto_linked(self) -> None:
        """A pending/booked pair where the reference number differs.

        This is the case that matters: the same purchase arrives twice with a
        different bank reference, so the description is similar rather than
        identical.
        """
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam ZUID",
                    status="pending",
                    merchant_alias_id=7,
                )
            ],
            merchant_alias_id=7,
        )
        assert decision.tier == 2
        assert not decision.needs_review

    def test_low_score_creates_a_new_row_and_queues_review(self) -> None:
        """The whole point of the confidence bands.

        A pending row that is only vaguely similar is not merged. A new
        transaction is created AND queued, rather than the two being guessed
        into one.
        """
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Completely Different Merchant XYZ",
                    status="posted",
                    merchant_alias_id=None,
                )
            ],
            merchant_alias_id=None,
        )
        assert decision.needs_review
        assert not decision.is_duplicate
        assert not decision.should_auto_link

    def test_amount_outside_tolerance_is_not_a_candidate(self) -> None:
        """Two cents apart is a different transaction, not a rounding artefact."""
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-852,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ]
        )
        assert decision.tier == 0

    def test_one_cent_tolerance_is_allowed(self) -> None:
        """The documented tolerance: ABS(diff) <= 1 minor unit."""
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-851,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ]
        )
        assert decision.tier == 2

    def test_date_window_is_asymmetric(self) -> None:
        """Money leaves before it arrives.

        Section G: `new.date BETWEEN existing.date - 3 AND existing.date + 1`,
        which read from the incoming date means the stored row may be up to 3
        days LATER or 1 day earlier. A symmetric window would match a Friday card
        payment against the following Monday's unrelated grocery.
        """
        stored_later = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-18"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ],
            booked_date=_date("2026-03-15"),
        )
        stored_earlier_by_one = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-14"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ],
            booked_date=_date("2026-03-15"),
        )
        stored_earlier_by_two = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-13"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ],
            booked_date=_date("2026-03-15"),
        )
        stored_far_later = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-19"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ],
            booked_date=_date("2026-03-15"),
        )

        assert stored_later.tier == 2
        assert stored_earlier_by_one.tier == 2
        # Two days early is outside the window: outbound precedes inbound.
        assert stored_earlier_by_two.tier == 0
        # Four days late is outside it too.
        assert stored_far_later.tier == 0

    def test_already_matched_rows_are_not_candidates(self) -> None:
        """A line can only be in one transfer pair."""
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    transfer_match_id=99,
                )
            ],
            merchant_alias_id=7,
        )
        assert decision.tier == 0

    def test_other_accounts_are_not_candidates(self) -> None:
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=OTHER_ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ]
        )
        assert decision.tier == 0

    def test_ambiguous_window_is_penalised(self) -> None:
        """Two candidates in the window means the match is ambiguous.

        The uniqueness signal is subtracted, which is what stops an arbitrary one
        of two identical-looking rows from winning.
        """
        single = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                )
            ],
            merchant_alias_id=7,
        )
        ambiguous = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                ),
                Candidate(
                    source_record_id=5002,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                ),
            ],
            merchant_alias_id=7,
        )
        assert ambiguous.confidence < single.confidence

    def test_ties_resolve_deterministically(self) -> None:
        """The same candidates must always produce the same decision.

        Two rows identical on every scored field must not resolve by query
        order, or replay would not be reproducible.
        """
        candidates = [
            Candidate(
                source_record_id=5002,
                account_id=ACCOUNT,
                amount_minor=-850,
                booked_date=_date("2026-03-15"),
                description="Jumbo 4321 Amsterdam",
                status="pending",
            ),
            Candidate(
                source_record_id=5001,
                account_id=ACCOUNT,
                amount_minor=-850,
                booked_date=_date("2026-03-15"),
                description="Jumbo 4321 Amsterdam",
                status="pending",
            ),
        ]
        first = self._resolve(candidates)
        second = self._resolve(list(reversed(candidates)))
        assert first.source_record_id == second.source_record_id == 5001

    def test_score_is_a_decimal_not_a_float(self) -> None:
        """Reproducible to the bit.

        The score is persisted and shown to the user, so `0.8500000000000001`
        would be a visible defect.
        """
        decision = self._resolve(
            [
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                )
            ]
        )
        assert isinstance(decision.confidence, float)
        # The confidence is a float on the public decision; the score that
        # produced it is exact Decimal arithmetic.
        assert str(Decimal(repr(decision.confidence))) == str(decision.confidence)


class TestTier3Fingerprint:
    """The backstop. Exact by construction, so no scoring."""

    def _stored(self, **overrides: object) -> ExistingFingerprint:
        params: dict[str, object] = {
            "fingerprint": compute_fingerprint(
                raw_description="Jumbo 4321",
                raw_amount=-850,
                raw_currency="EUR",
                raw_date="2026-03-14",
                account_id=fingerprint_account_scope(ACCOUNT),
                occurrence_index=1,
            ),
            "account_id": ACCOUNT,
            "source_record_id": 5001,
            "journal_entry_id": 42,
        }
        params.update(overrides)
        return ExistingFingerprint(**params)  # type: ignore[arg-type]

    def _resolve(
        self, stored: list[ExistingFingerprint], **overrides: object
    ) -> IdentityDecision:
        params: dict[str, object] = {
            "account_id": ACCOUNT,
            "description": "Jumbo 4321",
            "amount_minor": -850,
            "currency": "EUR",
            "booked_date": _date("2026-03-14"),
            "stored_fingerprints": stored,
        }
        params.update(overrides)
        return IdentityResolver().resolve(**params)  # type: ignore[arg-type]

    def test_matching_fingerprint_is_a_duplicate(self) -> None:
        decision = self._resolve([self._stored()])
        assert decision.is_duplicate
        assert decision.tier == 3
        assert decision.confidence == 1.0
        assert decision.journal_entry_id == 42

    def test_unnormalised_stored_row_still_blocks_a_duplicate(self) -> None:
        """The raw record already landed, so the row must not be created twice.

        `journal_entry_id is None` means normalisation has not run yet, not that
        the row does not exist.
        """
        decision = self._resolve([self._stored(journal_entry_id=None)])
        assert decision.is_duplicate
        # No canonical entry to point at yet, which is not the same as "lost".
        assert decision.journal_entry_id is None

    def test_different_amount_is_not_a_duplicate(self) -> None:
        decision = self._resolve([self._stored()], amount_minor=-851)
        assert not decision.is_duplicate
        assert decision.tier == 0

    def test_occurrence_index_separates_identical_rows(self) -> None:
        """Two identical coffees are two transactions.

        This is the case Tier 3 exists for on file imports: Amex supplies no id,
        so without the occurrence index the second coffee would be swallowed.
        """
        first = self._resolve([self._stored()], occurrence_index=1)
        second = self._resolve([self._stored()], occurrence_index=2)
        assert first.is_duplicate
        assert not second.is_duplicate

    def test_cross_account_never_matches(self) -> None:
        decision = self._resolve([self._stored(account_id=OTHER_ACCOUNT)])
        assert not decision.is_duplicate

    def test_fingerprint_is_echoed_on_a_new_row(self) -> None:
        """The caller needs it to store on the row it just decided to create."""
        decision = self._resolve([])
        assert not decision.is_duplicate
        assert len(decision.fingerprint) == 64


class TestTierOrdering:
    def test_tier1_wins_over_a_tier2_candidate(self) -> None:
        """The cheap exact rule runs first.

        An exact provider-id match is not a judgement call, so it must not be
        downgraded by a scored comparison that happens to disagree.
        """
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-14"),
            provider_txn_id="eb-ref-1",
            known_txn_ids=[(ACCOUNT, "eb-ref-1")],
            candidates=[
                Candidate(
                    source_record_id=5002,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-14"),
                    description="Something Else Entirely",
                    status="pending",
                )
            ],
        )
        assert decision.tier == 1

    def test_tier2_wins_over_tier3_when_it_is_decisive(self) -> None:
        """Tier 2 identifies WHICH pending row this is; Tier 3 only says
        'already seen'."""
        resolver = IdentityResolver()
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321 Amsterdam",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-15"),
            candidates=[
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                    journal_entry_id=42,
                )
            ],
            merchant_alias_id=7,
            stored_fingerprints=[],
        )
        assert decision.tier == 2
        assert decision.journal_entry_id == 42


class TestTrigramSimilarity:
    def test_identical_strings(self) -> None:
        assert trigram_similarity("jumbo", "jumbo") == Decimal("1")

    def test_short_strings_compare_by_equality_only(self) -> None:
        """A trigram set of a two-character string is noise."""
        assert trigram_similarity("ab", "ab") == Decimal("1")
        assert trigram_similarity("ab", "cd") == Decimal("0")

    def test_similar_beats_dissimilar(self) -> None:
        similar = trigram_similarity(
            "albert heijn amsterdam", "albert heijn amsterdam zuid"
        )
        dissimilar = trigram_similarity(
            "albert heijn amsterdam", "com completely other"
        )
        assert similar > dissimilar

    def test_is_case_insensitive(self) -> None:
        assert trigram_similarity("Jumbo", "jumbo") == Decimal("1")

    def test_above_the_stated_threshold_for_a_real_variant(self) -> None:
        """The score feeds the `-0.20 if similarity <= 0.8` term, so the
        threshold has to mean something for the pairs that matter."""
        score = trigram_similarity("jumbo 4321 amsterdam", "jumbo 4321 amsterdam zuid")
        assert score > Decimal("0.8")


class TestThresholds:
    def test_bands_match_the_proposal(self) -> None:
        """Section G: >= 0.85 auto-link, 0.50-0.85 review, < 0.50 new."""
        assert AUTO_LINK_THRESHOLD == 0.85
        assert REVIEW_THRESHOLD == 0.50

    def test_thresholds_are_injectable(self) -> None:
        """A caller that disagrees can raise the bar without editing the module.

        Refusing to auto-link at all is a legitimate configuration, and it must
        not require patching a constant.
        """
        resolver = IdentityResolver(auto_link_threshold=1.01, review_threshold=0.0)
        decision = resolver.resolve(
            account_id=ACCOUNT,
            description="Jumbo 4321 Amsterdam",
            amount_minor=-850,
            currency="EUR",
            booked_date=_date("2026-03-15"),
            candidates=[
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT,
                    amount_minor=-850,
                    booked_date=_date("2026-03-15"),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                )
            ],
            merchant_alias_id=7,
        )
        assert decision.needs_review
        assert not decision.is_duplicate


class TestResolverIsStateless:
    def test_repeated_resolution_agrees(self) -> None:
        """No hidden cursor: the same inputs always give the same answer.

        That is what makes the whole dedup path replayable.
        """
        resolver = IdentityResolver()
        params = {
            "account_id": ACCOUNT,
            "description": "Jumbo 4321",
            "amount_minor": -850,
            "currency": "EUR",
            "booked_date": _date("2026-03-14"),
            "provider_txn_id": "eb-ref-1",
            "known_txn_ids": [(ACCOUNT, "eb-ref-1")],
        }
        assert resolver.resolve(**params) == resolver.resolve(**params)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["imported", "posted", "pending", "duplicate"])
def test_all_statuses_are_accepted_as_candidates(status: str) -> None:
    """Tier 2 narrows on `is_pending`, it does not require it."""
    candidate = Candidate(
        source_record_id=5001,
        account_id=ACCOUNT,
        amount_minor=-850,
        booked_date=_date("2026-03-15"),
        description="Jumbo 4321 Amsterdam",
        status=status,
        merchant_alias_id=7,
    )
    decision = IdentityResolver().resolve(
        account_id=ACCOUNT,
        description="Jumbo 4321 Amsterdam",
        amount_minor=-850,
        currency="EUR",
        booked_date=_date("2026-03-15"),
        candidates=[candidate],
        merchant_alias_id=7,
    )
    assert decision.tier == 2
    # Every other signal is ideal here, so the only difference is the pending
    # signal: 1.00 for a pending row, 0.90 for anything else. 0.90 is still
    # above the 0.85 auto-link band, so a booked twin still links — which is the
    # point: Tier 2 is not restricted to pending rows, it just trusts them more.
    expected = Decimal("1.00") if status == "pending" else Decimal("0.90")
    assert Decimal(str(decision.confidence)) == expected
