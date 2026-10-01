"""Categorization engine tests — the seven layers.

Section I. Two invariants are asserted throughout:

- **Layer order is precedence.** Layer 4 is deliberately fuzzy, so it must never
  pre-empt an exact rule the user wrote in layer 1.
- **Declining is a success.** Layer 7 with no category is a correct answer, not a
  failure, and nothing here guesses past its confidence.

Synthetic payees only.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from finance.domain.services.categorize import (
    CategorizeResult,
    CategoryRule,
    MerchantAlias,
    categorize_transaction,
    learn_from_correction,
    stable_payee_pattern,
    trigram_similarity,
    word_similarity,
)
from finance.ingestion.fingerprint import normalize_description

# Invented merchant seeds, shaped like the real NL ones the proposal names.
KNOWN = {"albert heijn": 10, "jumbo": 11, "ns intercity": 12}


def classify(description: str, **overrides: object) -> CategorizeResult:
    """Classify with the pinned normalisation, as the pipeline does.

    Without this the fuzzy layer would be scoring descriptions that still carry
    their REF blocks, which is not what production ever sees.
    """
    params: dict[str, object] = {
        "known_merchants": KNOWN,
        "normalize": normalize_description,
    }
    params.update(overrides)
    return categorize_transaction(description, **params)  # type: ignore[arg-type]


class TestLayer1UserRules:
    def test_rule_matches(self) -> None:
        result = classify(
            "ALBERT HEIJN 1234",
            rules=[CategoryRule(category_id=99, description_pattern="albert heijn")],
        )
        assert result.category_id == 99
        assert result.layer_reached == 1
        assert result.confidence == Decimal("1.00")
        assert result.is_auto

    def test_rule_is_case_insensitive(self) -> None:
        result = classify(
            "Albert Heijn 1234",
            rules=[CategoryRule(category_id=99, description_pattern="ALBERT HEIJN")],
        )
        assert result.category_id == 99

    def test_empty_pattern_never_matches(self) -> None:
        """An empty pattern matching everything is the worst possible rule."""
        result = classify(
            "anything at all",
            rules=[CategoryRule(category_id=99, description_pattern="   ")],
        )
        assert result.category_id != 99

    def test_priority_order_decides(self) -> None:
        """A specific rule above a general one.

        Without priority, dict ordering would decide, and the user's careful
        ordering would be silently ignored.
        """
        rules = [
            CategoryRule(category_id=1, description_pattern="heijn", priority=50),
            CategoryRule(
                category_id=2, description_pattern="albert heijn", priority=10
            ),
        ]
        assert classify("albert heijn 1234", rules=rules).category_id == 2
        assert (
            classify("albert heijn 1234", rules=list(reversed(rules))).category_id == 2
        )

    def test_layer1_beats_a_fuzzy_layer4_match(self) -> None:
        """The reason the layers are ordered.

        A fuzzy trigram match is a guess; a rule the user wrote is a decision.
        """
        result = classify(
            "albert helijn amsterdam",  # a typo the trigram layer would catch
            rules=[CategoryRule(category_id=99, description_pattern="albert helijn")],
        )
        assert result.layer_reached == 1
        assert result.category_id == 99


class TestLayer2MerchantAlias:
    def test_alias_matches(self) -> None:
        result = classify(
            "PAYPAL XYZ 1234",
            merchant_aliases=[
                MerchantAlias(
                    raw_string="paypal xyz", category_id=50, confidence=Decimal("0.90")
                )
            ],
        )
        assert result.category_id == 50
        assert result.layer_reached == 2

    def test_alias_confidence_is_carried_through(self) -> None:
        """A low-confidence alias stays visibly low-confidence.

        The user can then see which categorizations came from a shaky match.
        """
        result = classify(
            "PAYPAL XYZ",
            merchant_aliases=[
                MerchantAlias(
                    raw_string="paypal", category_id=50, confidence=Decimal("0.50")
                )
            ],
        )
        assert result.confidence == Decimal("0.50")
        assert not result.is_auto


class TestLayer3KnownMerchants:
    def test_known_merchant_matches(self) -> None:
        result = classify("JUMBO 4321 AMSTERDAM")
        assert result.category_id == KNOWN["jumbo"]
        assert result.layer_reached == 3

    def test_merchant_inside_a_longer_description(self) -> None:
        result = classify("BETAAL JUMBO 4321 AMSTERDAM REF:1")
        assert result.category_id == KNOWN["jumbo"]

    def test_unknown_merchant_reaches_layer7(self) -> None:
        result = classify("SOME COMPLETELY UNKNOWN SHOP XYZ")
        assert result.category_id is None
        assert result.layer_reached == 7
        assert result.needs_review
        assert not result.is_auto


class TestLayer4Fuzzy:
    def test_typo_still_matches(self) -> None:
        """The trigram layer's whole purpose.

        Without it, every misspelled payee lands in the review queue forever.
        """
        result = classify("albert hejin amsterdam")
        assert result.category_id == KNOWN["albert heijn"]
        assert result.layer_reached == 4

    def test_fuzzy_confidence_is_lower_than_exact(self) -> None:
        """A guess must look like a guess in the UI.

        The typo has to break the substring match as well as the trigram one, or
        layer 3 would claim it and the comparison would be vacuous.
        """
        exact = classify("albert heijn 1234")
        fuzzy = classify("albert hejin 1234")
        assert exact.layer_reached == 3
        assert fuzzy.layer_reached == 4
        assert fuzzy.confidence < exact.confidence
        assert not fuzzy.is_auto

    def test_a_typo_too_severe_to_cue_misses_entirely(self) -> None:
        """Declining is the right answer for "jmb" against "jumbo".

        A 3-letter fragment shares no trigrams with a 5-letter word, so there is
        nothing to match on. Fabricating a match here would be the exact failure
        layer 7 exists to prevent.
        """
        assert classify("jmb 4321").layer_reached == 7

    def test_unrelated_text_does_not_fuzzy_match(self) -> None:
        result = classify("zzzz qqqq wwww vvvv")
        assert result.layer_reached == 7


class TestLayer7ManualReview:
    def test_no_match_is_a_valid_outcome(self) -> None:
        result = classify("WHATEVER THIS IS")
        assert result.category_id is None
        assert result.layer_reached == 7
        assert result.confidence == Decimal("0.00")
        assert "review" in result.reason.lower()

    def test_empty_description(self) -> None:
        """Bad data, not a crash."""
        assert classify("").layer_reached == 7

    def test_no_known_merchants_supplied(self) -> None:
        result = categorize_transaction("jumbo 4321")
        assert result.layer_reached == 7


class TestLayer5Learning:
    """The layer the reference implementations lack."""

    def test_correction_creates_a_full_confidence_alias(self) -> None:
        alias = learn_from_correction("PAYPAL XYZ 1234", category_id=77)
        assert alias.category_id == 77
        assert alias.confidence == Decimal("1.00")

    def test_alias_stores_the_payee_not_the_whole_description(self) -> None:
        """The order number must not become part of the learned pattern.

        An alias built on "PAYPAL XYZ 1234" matches that exact string once. The
        next occurrence is "PAYPAL XYZ 9999" and it misses — which the user reads
        as the learning not working.
        """
        alias = learn_from_correction("PAYPAL XYZ 1234", category_id=77)
        assert alias.raw_string == "paypal xyz"
        assert "1234" not in alias.raw_string

    def test_stable_prefix_is_enough_to_identify(self) -> None:
        """Four leading tokens: enough to name a payee, short enough to survive a
        changed terminal or city suffix."""
        assert stable_payee_pattern("ALBERT HEIJN AMSTERDAM 1234 TERM9") == (
            "albert heijn amsterdam 1234"
        )
        # The trailing store number is a reference, so it goes too.
        assert stable_payee_pattern("JUMBO 4321") == "jumbo"

    def test_trailing_reference_number_is_dropped(self) -> None:
        """The number is the order reference, not the merchant.

        "PAYPAL XYZ 1234" and "PAYPAL XYZ 9999" have to produce the same pattern,
        which is the whole reason the numeric strip exists.
        """
        assert stable_payee_pattern("PAYPAL XYZ 1234") == "paypal xyz"
        assert stable_payee_pattern("PAYPAL XYZ 9999") == "paypal xyz"

    def test_short_description_is_kept_whole(self) -> None:
        """Nothing to truncate."""
        assert stable_payee_pattern("Jumbo") == "jumbo"

    def test_correction_updates_an_existing_alias(self) -> None:
        """The payee is already known but at low confidence.

        Re-correcting it raises confidence rather than creating a duplicate row.
        """
        existing = [
            MerchantAlias(
                raw_string="paypal xyz", category_id=50, confidence=Decimal("0.50")
            )
        ]
        alias = learn_from_correction(
            "PAYPAL XYZ 1234", category_id=77, existing_aliases=existing
        )
        assert alias.category_id == 77
        assert alias.confidence == Decimal("1.00")
        assert alias.raw_string == "paypal xyz"

    def test_empty_description_cannot_be_learned(self) -> None:
        """An empty alias would match every description."""
        with pytest.raises(ValueError, match="empty description"):
            learn_from_correction("   ", category_id=77)

    def test_learned_alias_resolves_the_next_occurrence(self) -> None:
        """The queue drains itself.

        Correcting one occurrence means the same payee with a different order
        number resolves next time without asking again — the property the whole
        review workflow depends on, and the one the reference implementations
        lack.
        """
        alias = learn_from_correction("PAYPAL XYZ 1234", category_id=77)
        again = classify("PAYPAL XYZ 9999", merchant_aliases=[alias])
        assert again.category_id == 77
        assert again.is_auto


class TestNormalisationInjection:
    def test_default_normaliser_collapses_and_lowercases(self) -> None:
        result = categorize_transaction(
            "  ALBERT   HEIJN  1234 ",
            rules=[
                CategoryRule(category_id=1, description_pattern="albert heijn 1234")
            ],
        )
        assert result.category_id == 1

    def test_injected_normaliser_is_used(self) -> None:
        """The pipeline injects the pinned fingerprint normalisation.

        Passing it in is how the matcher and the dedup rule stay in agreement
        without `finance.domain` importing `finance.ingestion`.
        """
        result = categorize_transaction(
            "ALBERT HEIJN 1234 *REF:000",
            rules=[
                CategoryRule(category_id=1, description_pattern="albert heijn 1234")
            ],
            normalize=normalize_description,
        )
        assert result.category_id == 1

    def test_default_normaliser_does_not_strip_markers(self) -> None:
        """Documented difference from the injected one.

        A rule matching the reference token itself only fires under the default
        normaliser, which does not know about provider bookkeeping. That is why
        the pipeline always injects the pinned normaliser — without it, a rule
        could be written against a REF block and match a different bank's block.
        """
        raw = "ALBERT HEIJN 1234 *REF:000999"
        matches_marker = [CategoryRule(category_id=1, description_pattern="ref:")]
        assert categorize_transaction(raw, rules=matches_marker).category_id == 1
        assert (
            categorize_transaction(
                raw, rules=matches_marker, normalize=normalize_description
            ).category_id
            != 1
        )

    def test_injected_normaliser_still_matches_the_payee(self) -> None:
        """Stripping the marker must not stop the payee from matching."""
        rule = CategoryRule(category_id=1, description_pattern="albert heijn 1234")
        assert (
            categorize_transaction(
                "ALBERT HEIJN 1234 *REF:0",
                rules=[rule],
                normalize=normalize_description,
            ).category_id
            == 1
        )


class TestTrigramSimilarity:
    def test_identical(self) -> None:
        assert trigram_similarity("jumbo", "jumbo") == 1.0

    def test_short_strings(self) -> None:
        assert trigram_similarity("ab", "ab") == 1.0
        assert trigram_similarity("ab", "cd") == 0.0

    def test_typo_scores_above_threshold(self) -> None:
        assert trigram_similarity("albert heijn", "albert hejin") > 0.6

    def test_unrelated_scores_below(self) -> None:
        assert trigram_similarity("albert heijn", "zzzz yyyy xxxx") < 0.6


class TestWordSimilarity:
    """Windowed scoring, the layer-4 fix.

    A merchant name is always shorter than the bank description containing it, so
    a whole-string comparison punishes the merchant for the description's extra
    words. Scoring the best window instead is what makes the fuzzy layer usable.
    """

    def test_beats_whole_string_on_a_long_description(self) -> None:
        whole = trigram_similarity("albert hejin amsterdam", "albert heijn")
        windowed = word_similarity("albert hejin amsterdam", "albert heijn")
        assert windowed > whole
        assert windowed > 0.6

    def test_identical_merchant_name(self) -> None:
        assert word_similarity("albert heijn 1234 amsterdam", "albert heijn") == 1.0

    def test_unrelated_text(self) -> None:
        assert word_similarity("zzzz qqqq wwww", "albert heijn") == 0.0

    def test_description_shorter_than_merchant_falls_back(self) -> None:
        """Nothing to slide a window over, so compare whole."""
        assert word_similarity("ns", "ns intercity") == trigram_similarity(
            "ns", "ns intercity"
        )

    def test_empty_inputs(self) -> None:
        assert word_similarity("", "albert heijn") == 0.0
        assert word_similarity("albert heijn", "") == 0.0


class TestDeterminism:
    def test_repeated_classification_agrees(self) -> None:
        """No dict-order dependence.

        `known_merchants` is iterated in sorted order precisely so two merchants
        matching the same description cannot produce different answers on
        different runs.
        """
        rules = [
            CategoryRule(category_id=1, description_pattern="a", priority=10),
            CategoryRule(category_id=2, description_pattern="ab", priority=10),
        ]
        first = categorize_transaction("abc", rules=rules, known_merchants=KNOWN)
        second = categorize_transaction(
            "abc", rules=list(reversed(rules)), known_merchants=KNOWN
        )
        assert first == second


@pytest.mark.parametrize("layer", [1, 2, 3, 4, 7])
def test_every_reachable_layer_reports_its_number(layer: int) -> None:
    """The layer number in the result matches the proposal's table."""
    match layer:
        case 1:
            result = categorize_transaction(
                "jumbo 1",
                rules=[CategoryRule(category_id=1, description_pattern="jumbo")],
            )
        case 2:
            result = categorize_transaction(
                "widget co",
                merchant_aliases=[MerchantAlias(raw_string="widget", category_id=2)],
            )
        case 3:
            result = categorize_transaction("jumbo 1", known_merchants=KNOWN)
        case 4:
            result = categorize_transaction(
                "albert hejin amsterdam",
                known_merchants=KNOWN,
                normalize=normalize_description,
            )
        case _:
            result = categorize_transaction("zzzz nothing here")
    assert result.layer_reached == layer
