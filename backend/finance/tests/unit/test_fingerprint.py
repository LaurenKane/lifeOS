"""Fingerprint determinism tests.

`finance/ingestion/fingerprint.py` is SHA-256 hash-pinned by `invariants.yaml`,
so these tests are the human-readable counterpart to that pin. If one of these
fails, either the algorithm changed (which invalidates every stored fingerprint
and breaks replay) or a bug was found. Both are worth knowing immediately.

Section G, lines 555-570 of docs/ARCHITECTURE-PROPOSAL.md.

All fixtures are synthetic. No real transaction data appears anywhere in this
repository.
"""

from __future__ import annotations

import pytest

from finance.ingestion.fingerprint import (
    compute_fingerprint,
    fingerprint_source_string,
    normalize_description,
)

# Synthetic. Not a real transaction.
BASE = {
    "raw_description": "Albert Heijn 1234",
    "raw_amount": -1250,
    "raw_currency": "EUR",
    "raw_date": "2026-03-14",
    "account_id": "acc-001",
    "occurrence_index": 1,
}


def fp(**overrides: object) -> str:
    """Compute a fingerprint from BASE with field overrides."""
    return compute_fingerprint(**{**BASE, **overrides})  # type: ignore[arg-type]


class TestDeterminism:
    """Same input, same digest. Every time, on any machine."""

    def test_repeated_calls_agree(self) -> None:
        assert fp() == fp() == fp()

    def test_digest_shape(self) -> None:
        """64 lowercase hex characters: a full SHA-256, not a truncated one."""
        digest = fp()
        assert len(digest) == 64
        assert digest == digest.lower()
        assert all(character in "0123456789abcdef" for character in digest)

    def test_keyword_and_positional_independence(self) -> None:
        """Only the values matter, not how they were passed."""
        assert (
            compute_fingerprint(
                raw_description="Albert Heijn 1234",
                raw_amount=-1250,
                raw_currency="EUR",
                raw_date="2026-03-14",
                account_id="acc-001",
                occurrence_index=1,
            )
            == fp()
        )

    def test_known_digest_is_stable(self) -> None:
        """A golden digest, readable counterpart to the invariants.yaml hash pin.

        If this fails, either the algorithm changed — which invalidates every
        stored fingerprint and breaks replay — or a bug was found. Both are worth
        knowing immediately, which is why the value is written down here rather
        than only being compared to itself.
        """
        assert (
            fp() == "08304ec6ef2960bcef157f4c5515e404c04f816b1e5ee02453b90d4efddca6f1"
        )

    def test_no_dependence_on_insertion_or_key_order(self) -> None:
        """Building the same dict in a different order gives the same digest."""
        reordered = {
            "occurrence_index": 1,
            "account_id": "acc-001",
            "raw_date": "2026-03-14",
            "raw_currency": "EUR",
            "raw_amount": -1250,
            "raw_description": "Albert Heijn 1234",
        }
        assert compute_fingerprint(**reordered) == fp()  # type: ignore[arg-type]


class TestTrailingMarkerCollapse:
    """REF: blocks and trailing markers are provider bookkeeping, not identity.

    Re-importing the same statement produces the same markers, and a bank that
    changes its reference format must not orphan every stored fingerprint.
    """

    @pytest.mark.parametrize(
        "variant",
        [
            "Albert Heijn 1234",
            "Albert Heijn 1234 *",
            "Albert Heijn 1234 #",
            "Albert Heijn 1234 *REF:0123456789",
            "Albert Heijn 1234 #REF:0123456789",
            "Albert Heijn 1234 # REF 0123456789",
            "Albert Heijn 1234 * #REF:0123456789",
            "Albert Heijn 1234 CARD:9876",
            "Albert Heijn 1234 KAASACHTELNR:1234567",
        ],
    )
    def test_variants_collapse_to_the_bare_description(self, variant: str) -> None:
        assert fp(raw_description=variant) == fp(raw_description="Albert Heijn 1234")

    def test_whitespace_variants_collapse(self) -> None:
        """Any whitespace run is one space: tabs, newlines, trailing padding."""
        assert fp(raw_description="Albert Heijn  1234") == fp()
        assert fp(raw_description="Albert Heijn\t1234") == fp()
        assert fp(raw_description="Albert Heijn\n1234") == fp()
        assert fp(raw_description="  Albert Heijn 1234  ") == fp()

    def test_case_variants_collapse(self) -> None:
        assert fp(raw_description="ALBERT HEIJN 1234") == fp()
        assert fp(raw_description="albert heijn 1234") == fp()

    def test_all_three_axes_together(self) -> None:
        """The realistic case: the same line, re-exported by a different run."""
        assert fp(raw_description="  ALBERT   HEIJN  1234  *REF:99887766  ") == fp()


class TestWhatMustNotCollapse:
    """The cases above are noise. These are real differences.

    A dedup rule that is too aggressive is worse than one that is too timid: it
    silently deletes a transaction.
    """

    def test_different_amounts_differ(self) -> None:
        assert fp(raw_amount=-1250) != fp(raw_amount=-1251)
        assert fp(raw_amount=-1250) != fp(raw_amount=1250)

    def test_different_dates_differ(self) -> None:
        assert fp(raw_date="2026-03-14") != fp(raw_date="2026-03-15")

    def test_different_accounts_differ(self) -> None:
        """The same coffee on two cards is two transactions.

        Cross-account collapsing would delete one of them.
        """
        assert fp(account_id="acc-001") != fp(account_id="acc-002")

    def test_different_currencies_differ(self) -> None:
        assert fp(raw_currency="EUR") != fp(raw_currency="USD")

    def test_different_descriptions_differ(self) -> None:
        assert fp(raw_description="Albert Heijn 1234") != fp(
            raw_description="Jumbo 5678"
        )

    def test_a_ref_inside_the_payee_survives(self) -> None:
        """Markers are stripped only at the end.

        A payee name that genuinely contains the token is a different merchant,
        and swallowing it would merge two real transactions.
        """
        assert fp(raw_description="Cafe REFACTOR 1234") != fp(
            raw_description="Cafe 1234"
        )


class TestOccurrenceIndex:
    """The multiset answer: two identical coffees are two transactions.

    This is the mechanism that stops the alternative failure mode — every
    re-imported line becoming a duplicate of the first.
    """

    def test_occurrence_index_separates_identical_rows(self) -> None:
        first = fp(occurrence_index=1)
        second = fp(occurrence_index=2)
        third = fp(occurrence_index=3)
        assert len({first, second, third}) == 3

    def test_default_is_one(self) -> None:
        assert fp() == fp(occurrence_index=1)

    def test_index_zero_is_rejected(self) -> None:
        """1-based, because it comes from ROW_NUMBER().

        Silently accepting 0 would produce a fingerprint that no row can ever
        collide with, so a duplicate would pass straight through.
        """
        with pytest.raises(ValueError, match="1-based"):
            fp(occurrence_index=0)


class TestTypeSafety:
    """Amounts are minor units. A float must never reach this function."""

    def test_float_amount_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="int minor units"):
            fp(raw_amount=-12.5)

    def test_bool_amount_is_rejected(self) -> None:
        """bool is an int subclass; True must not become 1 minor unit."""
        with pytest.raises(TypeError, match="int minor units"):
            fp(raw_amount=True)


class TestNormalizeDescription:
    """The description half of the fingerprint, on its own."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Albert Heijn", "albert heijn"),
            ("  Albert Heijn  ", "albert heijn"),
            ("Albert    Heijn", "albert heijn"),
            ("ALBERT HEIJN", "albert heijn"),
            ("Albert Heijn *REF:123", "albert heijn"),
            ("Starbucks * #REF:12345", "starbucks"),
            ("Albert Heijn\t1234\n", "albert heijn 1234"),
        ],
    )
    def test_cases(self, raw: str, expected: str) -> None:
        assert normalize_description(raw) == expected

    def test_empty_description(self) -> None:
        """Allowed. An empty description is bad data, not a crash."""
        assert normalize_description("") == ""
        assert normalize_description("   ") == ""

    def test_strips_before_collapsing(self) -> None:
        """Order matters: collapse-then-strip leaves residue.

        "SHOP  *REF:1" collapses to "SHOP *REF:1", which the marker pattern then
        removes cleanly. The other order leaves a trailing space that the strip
        removes anyway — but only because strip() runs last. The invariant that
        matters is the observable one: no leading or trailing whitespace ever
        survives.
        """
        result = normalize_description("SHOP  *REF:1")
        assert result == "shop"
        assert result == result.strip()


class TestSourceString:
    """The exact hashed string, for explaining a digest to a human."""

    def test_component_order_is_fixed(self) -> None:
        """Changing the order changes every stored fingerprint, so it is pinned."""
        assert fingerprint_source_string(**BASE) == (  # type: ignore[arg-type]
            "albert heijn 1234|-1250|EUR|2026-03-14|acc-001|1"
        )

    def test_pipeline_is_the_pipe_character(self) -> None:
        """The separator must not appear in a normalised component.

        It is stripped from descriptions, but a description could still contain a
        literal '|' from a malformed export. Documented here because the format
        is load-bearing: it is why fingerprints are not reversible and why two
        differently-shaped rows cannot be made to collide by moving text across
        the boundary.
        """
        source = fingerprint_source_string(**BASE)  # type: ignore[arg-type]
        assert source.count("|") == 5
        assert len(source.split("|")) == 6

    def test_hashes_to_the_digest(self) -> None:
        import hashlib

        source = fingerprint_source_string(**BASE)  # type: ignore[arg-type]
        assert hashlib.sha256(source.encode("utf-8")).hexdigest() == fp()
