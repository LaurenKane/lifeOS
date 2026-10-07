"""Unit tests for upkeep matching: confident receipts, ambiguous asks."""

from __future__ import annotations

from life.domain.services.upkeep_match import match_upkeep


class TestConfidentMatch:
    def test_a_receipt_verb_plus_exactly_one_named_upkeep(self) -> None:
        match = match_upkeep("cleaned the bathroom", [(1, "bathroom"), (2, "laundry")])
        assert match is not None and match.confident
        assert match.upkeep_id == 1

    def test_bare_title_with_a_verb_prefix(self) -> None:
        match = match_upkeep("did laundry", [(1, "laundry")])
        assert match is not None and match.confident

    def test_word_boundary_not_substring(self) -> None:
        # "bathroomtiles" does NOT name the bathroom upkeep.
        assert match_upkeep("cleaned bathroomtiles", [(1, "bathroom")]) is None


class TestAmbiguousMatch:
    def test_an_upkeep_named_without_a_completion_verb_asks(self) -> None:
        match = match_upkeep("bathroom", [(1, "clean bathroom")])
        assert match is not None
        assert not match.confident

    def test_one_word_upkeep_named_alone_confirms_nothing(self) -> None:
        # "bathroom" IS the upkeep's whole name; a bare name cannot
        # distinguish a completion from a reminder intent — it asks (Q20).
        match = match_upkeep("bathroom", [(7, "bathroom")])
        assert match is not None
        assert not match.confident

    def test_an_imperative_read_as_intent_not_a_fact(self) -> None:
        # "clean bathroom" is a request; "cleaned the bathroom" is a fact.
        match = match_upkeep("clean bathroom", [(1, "bathroom")])
        assert match is not None
        assert not match.confident

    def test_an_unknown_leftover_word_breaks_confidence(self) -> None:
        # "did laundry by the sink": the sink is unaccounted — ask, don't
        # record. Conservatism over a lying receipt (Q21b).
        match = match_upkeep("did laundry by the sink", [(1, "laundry")])
        assert match is not None
        assert not match.confident


class TestNoMatch:
    def test_no_upkeep_named_means_none(self) -> None:
        assert match_upkeep("buy a lamp tomorrow", [(1, "laundry")]) is None

    def test_two_upkeeps_named_at_once_is_none(self) -> None:
        # An ambiguous ask is worse than a plain Thought.
        assert (
            match_upkeep(
                "cleaned bathroom and laundry",
                [(1, "bathroom"), (2, "laundry")],
            )
            is None
        )

    def test_empty_text(self) -> None:
        assert match_upkeep("", [(1, "laundry")]) is None
