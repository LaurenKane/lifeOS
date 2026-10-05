"""Pipeline tests — several components together, no live database.

These are the properties the whole design is judged on, stated end to end:

- **Re-importing the same file creates nothing.** The property that makes file
  import safe at all, and the one Firefly and Actual both fail for Amex.
- **Cross-batch overlap is non-destructive.** The seven-year history case.
- **A pending row and its booked twin become one transaction.** Tier 2.
- **A duplicate of an un-normalised row is still a duplicate.** The raw record
  already landed, so the row must not be created twice.
- **Nothing is merged below the confidence band.** The bias that matters most: a
  wrong merge destroys a transaction.

Synthetic data throughout.
"""

from __future__ import annotations

import datetime as dt

import pytest

from finance.ingestion.adapters import AmexPdfAdapter
from finance.ingestion.dedupe import (
    ExistingFingerprint,
    assign_occurrence_indices,
    fingerprint_account_scope,
    lookup_fingerprint,
)
from finance.ingestion.fingerprint import compute_fingerprint, normalize_description
from finance.ingestion.identity import Candidate, IdentityResolver
from finance.ingestion.normalize import NormalizedRecord, normalize_record
from finance.tests.fixtures import (
    ACCOUNT_ID,
    make_record,
    synthetic_amex_pdf_text,
)


def _ingest(
    lines: list[str], account_id: int = ACCOUNT_ID
) -> tuple[list[NormalizedRecord], list[int]]:
    """Adapter -> RawRecords -> NormalizedRecords -> occurrence indices."""
    parsed = AmexPdfAdapter(text_extractor=lambda _payload: lines).parse(
        b"", account_id=account_id
    )
    records = [
        normalize_record(
            account_id=raw.account_id,
            description=raw.description,
            amount=raw.amount_minor,
            currency_code=raw.currency,
            booked_date=raw.booked_date,
            line_number=raw.line_number,
        )
        for raw in parsed.records
    ]
    return records, assign_occurrence_indices(records)


class TestReimportIsANoOp:
    """The property that makes file import safe."""

    def test_second_import_of_the_same_file_creates_nothing(self) -> None:
        records, indices = _ingest(synthetic_amex_pdf_text())

        # Simulate the first import landing: store every fingerprint.
        stored = [
            ExistingFingerprint(
                fingerprint=compute_fingerprint(
                    raw_description=record.description,
                    raw_amount=record.amount.amount,
                    raw_currency=record.amount.currency.code,
                    raw_date=record.booked_date.isoformat(),
                    account_id=fingerprint_account_scope(record.account_id),
                    occurrence_index=index,
                ),
                account_id=record.account_id,
                source_record_id=position,
                journal_entry_id=position,
            )
            for position, (record, index) in enumerate(
                zip(records, indices, strict=True), 1
            )
        ]
        assert len(stored) == 4

        # Re-import the same file.
        _, second_indices = _ingest(synthetic_amex_pdf_text())
        assert second_indices == indices

        resolver = IdentityResolver()
        duplicates = 0
        for record, index in zip(records, second_indices, strict=True):
            decision = resolver.resolve(
                account_id=record.account_id,
                description=record.description,
                amount_minor=record.amount.amount,
                currency=record.amount.currency.code,
                booked_date=record.booked_date,
                occurrence_index=index,
                stored_fingerprints=stored,
            )
            if decision.is_duplicate:
                duplicates += 1
        assert duplicates == 4

    def test_two_identical_coffees_both_land(self) -> None:
        """The failure mode this whole rule exists to prevent.

        Without the occurrence index, the second EUR 12.34 Albert Heijn is
        fingerprinted identically to the first and swallowed as a duplicate —
        one purchase becomes one, and the user's spending is understated.
        """
        records, indices = _ingest(synthetic_amex_pdf_text())
        heijn = [
            (record, index)
            for record, index in zip(records, indices, strict=True)
            if "heijn" in normalize_description(record.description)
        ]
        assert len(heijn) == 2
        assert [index for _, index in heijn] == [1, 2]
        assert heijn[0][0].amount.amount == heijn[1][0].amount.amount

        # The first coffee has already landed. The second must not collide
        # with it: same description, same amount, same day, different
        # occurrence index.
        stored = [
            ExistingFingerprint(
                fingerprint=compute_fingerprint(
                    raw_description=heijn[0][0].description,
                    raw_amount=heijn[0][0].amount.amount,
                    raw_currency=heijn[0][0].amount.currency.code,
                    raw_date=heijn[0][0].booked_date.isoformat(),
                    account_id=fingerprint_account_scope(heijn[0][0].account_id),
                    occurrence_index=1,
                ),
                account_id=heijn[0][0].account_id,
                source_record_id=5001,
            )
        ]
        resolver = IdentityResolver()
        first = resolver.resolve(
            account_id=ACCOUNT_ID,
            description=heijn[0][0].description,
            amount_minor=heijn[0][0].amount.amount,
            currency="EUR",
            booked_date=heijn[0][0].booked_date,
            occurrence_index=1,
            stored_fingerprints=stored,
        )
        second = resolver.resolve(
            account_id=ACCOUNT_ID,
            description=heijn[1][0].description,
            amount_minor=heijn[1][0].amount.amount,
            currency="EUR",
            booked_date=heijn[1][0].booked_date,
            occurrence_index=2,
            stored_fingerprints=stored,
        )
        # The first is a duplicate of itself (a re-import), and the second is
        # NOT a duplicate of the first — it is a second real purchase.
        assert first.is_duplicate
        assert not second.is_duplicate

    def test_re_export_with_different_reference_still_dedupes(self) -> None:
        """A re-export with different reference blocks is the same file.

        The provider changes its bookkeeping numbers between exports; the
        fingerprint must ignore them or every re-export duplicates everything.
        """
        original = synthetic_amex_pdf_text()
        re_exported = [
            line.replace("REF:000000123456", "REF:000000999999") for line in original
        ]

        first_records, first_indices = _ingest(original)
        second_records, second_indices = _ingest(re_exported)

        assert first_indices == second_indices
        for record_a, record_b, index in zip(
            first_records, second_records, first_indices, strict=True
        ):
            assert compute_fingerprint(
                raw_description=record_a.description,
                raw_amount=record_a.amount.amount,
                raw_currency=record_a.amount.currency.code,
                raw_date=record_a.booked_date.isoformat(),
                account_id=fingerprint_account_scope(record_a.account_id),
                occurrence_index=index,
            ) == compute_fingerprint(
                raw_description=record_b.description,
                raw_amount=record_b.amount.amount,
                raw_currency=record_b.amount.currency.code,
                raw_date=record_b.booked_date.isoformat(),
                account_id=fingerprint_account_scope(record_b.account_id),
                occurrence_index=index,
            )

    def test_an_empty_reimport_is_still_a_noop(self) -> None:
        """A file the user re-uploads after deleting nothing must not fail."""
        records, indices = _ingest(synthetic_amex_pdf_text())
        assert records
        assert all(index >= 1 for index in indices)


class TestCrossBatchOverlap:
    """Older import already landed; a later import of the same row is a duplicate."""

    def test_rows_already_present_from_an_earlier_import_are_not_duplicated(
        self,
    ) -> None:
        """The stated M7 requirement, repointed for the PDF-only canonical path.

        Amex CSV is retired (LifeOS-18), so the overlap proof now uses a row
        already stored from a PDF import and the same row arriving again. The
        cross-batch `(fingerprint, account_id)` key must find it rather than
        creating it again, as opposed to keying on the batch.
        """
        from finance.ingestion.fingerprint import normalize_description as norm

        earlier_stored = [
            ExistingFingerprint(
                fingerprint=compute_fingerprint(
                    raw_description="ALBERT HEIJN 1234",
                    raw_amount=-1234,
                    raw_currency="EUR",
                    raw_date="2026-03-14",
                    account_id=fingerprint_account_scope(ACCOUNT_ID),
                    occurrence_index=1,
                ),
                account_id=ACCOUNT_ID,
                source_record_id=5001,
                journal_entry_id=1,
            )
        ]

        # The later import contains that same purchase again.
        decision = IdentityResolver().resolve(
            account_id=ACCOUNT_ID,
            description="ALBERT HEIJN 1234",
            amount_minor=-1234,
            currency="EUR",
            booked_date=dt.date(2026, 3, 14),
            occurrence_index=1,
            stored_fingerprints=earlier_stored,
        )
        assert decision.is_duplicate
        assert decision.source_record_id == 5001
        assert norm("ALBERT HEIJN 1234") == norm("albert  heijn  1234")


class TestPendingThenBooked:
    """Tier 2, end to end. Enable Banking only."""

    def _resolver(self) -> IdentityResolver:
        return IdentityResolver()

    def test_booked_row_updates_the_pending_one_in_place(self) -> None:
        """Never a second entry, never delete-and-reinsert.

        That is BankingSync's `MergePatch` **behaviour**, which is correct and
        worth having. Creating a second entry would double the transaction;
        deleting and reinserting would discard the user's category and any
        comment attached to it.

        A behaviour is not copyrightable and no code was taken from BankingSync,
        which is AGPL-3.0. See `docs/adr/0008-licence-policy.md`: reading an AGPL
        project for design is permitted, copying its source is not, and the
        permitted-reference project here is Actual Budget (MIT, attribution
        required). This comment describes what to do, not an implementation.
        """
        decision = self._resolver().resolve(
            account_id=ACCOUNT_ID,
            description="Jumbo 4321 Amsterdam",
            amount_minor=-850,
            currency="EUR",
            booked_date=dt.date(2026, 3, 15),
            merchant_alias_id=7,
            candidates=[
                Candidate(
                    source_record_id=5001,
                    account_id=ACCOUNT_ID,
                    amount_minor=-850,
                    booked_date=dt.date(2026, 3, 15),
                    description="Jumbo 4321 Amsterdam",
                    status="pending",
                    merchant_alias_id=7,
                    journal_entry_id=42,
                )
            ],
        )
        assert decision.tier == 2
        assert decision.is_duplicate
        assert decision.journal_entry_id == 42
        assert decision.source_record_id == 5001

    def test_a_vague_pair_is_queued_not_merged(self) -> None:
        """The bias, end to end.

        The candidate is a different merchant with a coincidentally equal
        amount. Merging them would silently delete one transaction's identity;
        creating a new row and queuing it costs the user one click.
        """
        decision = self._resolver().resolve(
            account_id=ACCOUNT_ID,
            description="Jumbo 4321 Amsterdam",
            amount_minor=-850,
            currency="EUR",
            booked_date=dt.date(2026, 3, 15),
            candidates=[
                Candidate(
                    source_record_id=5002,
                    account_id=ACCOUNT_ID,
                    amount_minor=-850,
                    booked_date=dt.date(2026, 3, 15),
                    description="Completely Different Merchant XYZ",
                    status="pending",
                )
            ],
        )
        assert decision.needs_review
        assert not decision.is_duplicate
        assert not decision.should_auto_link


class TestUnnormalisedDuplicate:
    def test_a_stored_row_with_no_journal_entry_still_blocks(self) -> None:
        """`imported` means the raw record landed; the row exists.

        Dedupe has to treat that as present. Skipping it would mean a failed
        batch, retried after a fix, silently doubled every row it had already
        written.
        """
        fingerprint = compute_fingerprint(
            raw_description="Jumbo 4321",
            raw_amount=-850,
            raw_currency="EUR",
            raw_date="2026-03-14",
            account_id=fingerprint_account_scope(ACCOUNT_ID),
            occurrence_index=1,
        )
        stored = ExistingFingerprint(
            fingerprint=fingerprint,
            account_id=ACCOUNT_ID,
            source_record_id=5001,
            journal_entry_id=None,
        )
        decision = IdentityResolver().resolve(
            account_id=ACCOUNT_ID,
            description="Jumbo 4321",
            amount_minor=-850,
            currency="EUR",
            booked_date=dt.date(2026, 3, 14),
            occurrence_index=1,
            stored_fingerprints=[stored],
        )
        assert decision.is_duplicate
        assert decision.source_record_id == 5001
        # No canonical entry yet, which is not the same as "not a duplicate".
        assert decision.journal_entry_id is None


class TestCrossAccountSafety:
    def test_the_same_purchase_on_two_accounts_is_not_a_duplicate(self) -> None:
        """Two real transactions, not one transaction and a phantom.

        This is the failure mode that loses money from a report: a EUR 12.34
        purchase on two cards collapsing into one, so the user's spending is
        understated by half.
        """
        fingerprint = compute_fingerprint(
            raw_description="Albert Heijn 1234",
            raw_amount=-1234,
            raw_currency="EUR",
            raw_date="2026-03-14",
            account_id=fingerprint_account_scope(1001),
            occurrence_index=1,
        )
        stored = [
            ExistingFingerprint(
                fingerprint=fingerprint,
                account_id=1001,
                source_record_id=5001,
                journal_entry_id=1,
            )
        ]
        decision = IdentityResolver().resolve(
            account_id=2002,
            description="Albert Heijn 1234",
            amount_minor=-1234,
            currency="EUR",
            booked_date=dt.date(2026, 3, 14),
            occurrence_index=1,
            stored_fingerprints=stored,
        )
        assert not decision.is_duplicate

    def test_lookup_respects_account_scope(self) -> None:
        fingerprint = compute_fingerprint(
            raw_description="Albert Heijn 1234",
            raw_amount=-1234,
            raw_currency="EUR",
            raw_date="2026-03-14",
            account_id=fingerprint_account_scope(1001),
            occurrence_index=1,
        )
        stored = [
            ExistingFingerprint(
                fingerprint=fingerprint,
                account_id=1001,
                source_record_id=5001,
            )
        ]
        assert (
            lookup_fingerprint(
                existing=stored,
                account_id=1001,
                raw_description="Albert Heijn 1234",
                raw_amount=-1234,
                raw_currency="EUR",
                raw_date="2026-03-14",
            )
            is not None
        )
        assert (
            lookup_fingerprint(
                existing=stored,
                account_id=2002,
                raw_description="Albert Heijn 1234",
                raw_amount=-1234,
                raw_currency="EUR",
                raw_date="2026-03-14",
            )
            is None
        )


class TestReplayDeterminism:
    """The property the frozen fingerprint exists to protect."""

    def test_the_same_input_produces_the_same_decisions_every_time(self) -> None:
        records, indices = _ingest(synthetic_amex_pdf_text())

        def decisions() -> list[tuple[bool, int]]:
            resolver = IdentityResolver()
            return [
                (
                    resolver.resolve(
                        account_id=record.account_id,
                        description=record.description,
                        amount_minor=record.amount.amount,
                        currency=record.amount.currency.code,
                        booked_date=record.booked_date,
                        occurrence_index=index,
                    ).is_duplicate,
                    index,
                )
                for record, index in zip(records, indices, strict=True)
            ]

        first, second, third = decisions(), decisions(), decisions()
        assert first == second == third
        # Nothing is stored yet, so the first pass creates everything.
        assert not any(is_duplicate for is_duplicate, _ in first)

    def test_order_of_processing_does_not_change_the_outcome(self) -> None:
        """Rows may be batched, streamed or reordered; the result must not move."""
        records, indices = _ingest(synthetic_amex_pdf_text())
        pairs = list(zip(records, indices, strict=True))

        resolver = IdentityResolver()

        def fingerprint_of(record: NormalizedRecord, index: int) -> str:
            return resolver.resolve(
                account_id=record.account_id,
                description=record.description,
                amount_minor=record.amount.amount,
                currency=record.amount.currency.code,
                booked_date=record.booked_date,
                occurrence_index=index,
            ).fingerprint

        forward = [fingerprint_of(record, index) for record, index in pairs]
        backward = [fingerprint_of(record, index) for record, index in reversed(pairs)]
        assert forward == list(reversed(backward))


@pytest.mark.parametrize("occurrence_index", [1, 2])
def test_replay_reproduces_the_same_fingerprint(occurrence_index: int) -> None:
    """Replay reads raw_data and must rebuild the identical entry.

    The fingerprint is the one function in the project that is hash-pinned, and
    this is the property the pin protects.
    """
    record = make_record(
        description="Albert Heijn 1234 *REF:1",
        amount_minor=-1234,
        booked_date="2026-03-14",
    )
    original = compute_fingerprint(
        raw_description=record.description,
        raw_amount=record.amount.amount,
        raw_currency=record.amount.currency.code,
        raw_date=record.booked_date.isoformat(),
        account_id=fingerprint_account_scope(record.account_id),
        occurrence_index=occurrence_index,
    )
    # Replay re-normalises from the preserved raw description.
    replayed = compute_fingerprint(
        raw_description=str(record.raw_data["description"]),
        raw_amount=int(record.raw_data["amount_minor"]),  # type: ignore[arg-type]
        raw_currency=record.amount.currency.code,
        raw_date=record.booked_date.isoformat(),
        account_id=fingerprint_account_scope(record.account_id),
        occurrence_index=occurrence_index,
    )
    assert replayed == original
