"""Occurrence-index tests — the multiset answer.

`ROW_NUMBER() OVER (PARTITION BY account_id, normalized_description, amount,
currency, date ORDER BY import_batch_id, raw_line_number)`.

This is the mechanism that stops the two failure modes of content-based dedup at
once: eating a real transaction (two coffees collapse into one) and creating a
phantom one (the second coffee becomes a "duplicate" of the first).

Synthetic data only.
"""

from __future__ import annotations

import pytest

from finance.ingestion.dedupe import (
    ExistingFingerprint,
    OccurrenceKey,
    assign_occurrence_indices,
    count_existing_occurrences,
    lookup_fingerprint,
)
from finance.ingestion.fingerprint import compute_fingerprint
from finance.ingestion.normalize import NormalizedRecord
from finance.tests.fixtures import make_record


class TestAssignOccurrenceIndex:
    def test_two_identical_coffees_get_one_and_two(self) -> None:
        """The case the whole rule exists for."""
        records = [
            make_record(description="Albert Heijn 1234", amount_minor=-320),
            make_record(description="Albert Heijn 1234", amount_minor=-320),
        ]
        assert assign_occurrence_indices(records) == [1, 2]

    def test_different_amounts_are_unrelated(self) -> None:
        records = [
            make_record(description="Albert Heijn 1234", amount_minor=-320),
            make_record(description="Albert Heijn 1234", amount_minor=-450),
        ]
        assert assign_occurrence_indices(records) == [1, 1]

    def test_different_days_are_unrelated(self) -> None:
        records = [
            make_record(description="Albert Heijn 1234", booked_date="2026-03-14"),
            make_record(description="Albert Heijn 1234", booked_date="2026-03-15"),
        ]
        assert assign_occurrence_indices(records) == [1, 1]

    def test_different_accounts_are_unrelated(self) -> None:
        """The same coffee on two cards is two transactions."""
        records = [
            make_record(description="Albert Heijn 1234", account_id="acc-a"),
            make_record(description="Albert Heijn 1234", account_id="acc-b"),
        ]
        assert assign_occurrence_indices(records) == [1, 1]

    def test_marker_and_case_variants_share_a_bucket(self) -> None:
        """Same purchase, re-exported with a different reference block.

        Without normalisation these would be two buckets, so the second export
        would be a fresh transaction rather than the second coffee.
        """
        records = [
            make_record(description="Albert Heijn 1234"),
            make_record(description="ALBERT  HEIJN  1234 *REF:999"),
        ]
        assert assign_occurrence_indices(records) == [1, 2]

    def test_five_identical_rows_number_one_to_five(self) -> None:
        records = [
            make_record(description="Jumbo 4321", amount_minor=-850, line_number=index)
            for index in range(5)
        ]
        assert assign_occurrence_indices(records) == [1, 2, 3, 4, 5]

    def test_indexing_is_stable_under_reordering_of_distinct_rows(self) -> None:
        """Anchored on the row's line number, not on arrival order.

        The same three rows in any order get the same index each. An
        implementation that counted arrival order would renumber everything when
        the batch was filtered or sorted differently, and every stored
        fingerprint would become irreproducible.
        """
        base = [
            make_record(description="Albert Heijn 1234", line_number=1),
            make_record(description="Jumbo 4321", line_number=2),
            make_record(description="Albert Heijn 1234", line_number=3),
        ]
        indices = assign_occurrence_indices(base)

        def index_by_line(rows: list[NormalizedRecord]) -> dict[int, int]:
            return dict(
                zip(
                    [row.line_number for row in rows],
                    assign_occurrence_indices(rows),
                    strict=True,
                )
            )

        for permutation in ([base[2], base[0], base[1]], [base[1], base[2], base[0]]):
            assert index_by_line(permutation) == index_by_line(base)

        # The two identical rows are lines 1 and 3, so they number 1 and 2 and
        # the distinct one at line 2 numbers 1.
        assert indices == [1, 1, 2]

    def test_empty_input(self) -> None:
        assert assign_occurrence_indices([]) == []


class TestOccurrenceKey:
    def test_key_equality_is_component_equality(self) -> None:
        record = make_record(description="Albert Heijn 1234")
        assert OccurrenceKey.from_normalized(record) == OccurrenceKey.from_normalized(
            make_record(description="albert  heijn  1234 *REF:1")
        )

    def test_keys_are_hashable_and_sortable(self) -> None:
        """Determinism depends on a stable ordering."""
        first = OccurrenceKey.from_normalized(make_record())
        second = OccurrenceKey.from_normalized(
            make_record(description="Jumbo 4321", amount_minor=-1)
        )
        assert len({first, second}) == 2
        assert sorted([second, first]) == [first, second]

    def test_count_matches(self) -> None:
        key = OccurrenceKey.from_normalized(make_record())
        other = OccurrenceKey.from_normalized(make_record(amount_minor=-1))
        assert count_existing_occurrences([key, key, other], key) == 2


class TestFingerprintLookup:
    def _fp(self, **overrides: object) -> str:
        params: dict[str, object] = {
            "raw_description": "Albert Heijn 1234",
            "raw_amount": -320,
            "raw_currency": "EUR",
            "raw_date": "2026-03-14",
            "account_id": "acc-synthetic-001",
            "occurrence_index": 1,
        }
        params.update(overrides)
        return compute_fingerprint(**params)  # type: ignore[arg-type]

    def test_match_is_found(self) -> None:
        stored = ExistingFingerprint(
            fingerprint=self._fp(),
            account_id="acc-synthetic-001",
            source_record_id="sr-1",
        )
        found = lookup_fingerprint(
            existing=[stored],
            account_id="acc-synthetic-001",
            raw_description="Albert Heijn 1234",
            raw_amount=-320,
            raw_currency="EUR",
            raw_date="2026-03-14",
        )
        assert found is not None
        assert found.source_record_id == "sr-1"

    def test_second_occurrence_does_not_match_the_first(self) -> None:
        """The property that makes re-importing a file non-destructive.

        Re-importing the same CSV gives the same rows the same occurrence
        indices, so every row matches its stored fingerprint and nothing is
        created twice.
        """
        stored = ExistingFingerprint(
            fingerprint=self._fp(occurrence_index=1),
            account_id="acc-synthetic-001",
            source_record_id="sr-1",
        )
        second = lookup_fingerprint(
            existing=[stored],
            account_id="acc-synthetic-001",
            raw_description="Albert Heijn 1234",
            raw_amount=-320,
            raw_currency="EUR",
            raw_date="2026-03-14",
            occurrence_index=2,
        )
        assert second is None

    def test_cross_account_never_matches(self) -> None:
        stored = ExistingFingerprint(
            fingerprint=self._fp(), account_id="acc-other", source_record_id="sr-1"
        )
        found = lookup_fingerprint(
            existing=[stored],
            account_id="acc-synthetic-001",
            raw_description="Albert Heijn 1234",
            raw_amount=-320,
            raw_currency="EUR",
            raw_date="2026-03-14",
        )
        assert found is None

    def test_no_stored_rows(self) -> None:
        assert (
            lookup_fingerprint(
                existing=[],
                account_id="acc-synthetic-001",
                raw_description="x",
                raw_amount=-1,
                raw_currency="EUR",
                raw_date="2026-03-14",
            )
            is None
        )


@pytest.mark.parametrize("index", [1, 2, 3])
def test_indices_are_one_based_and_contiguous(index: int) -> None:
    """1-based because it comes from ROW_NUMBER().

    A 0-based index would produce a fingerprint no row could ever collide with,
    so every duplicate would pass straight through.
    """
    records = [
        make_record(description="Jumbo 4321", amount_minor=-850, line_number=position)
        for position in range(index)
    ]
    assert assign_occurrence_indices(records) == list(range(1, index + 1))
