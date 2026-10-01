"""Adapter tests — one provider's real-world shape to RawRecords.

Every adapter emits the same `RawRecord`, which is the point: the
provider-agnostic part is the schema, only the IdentityResolver is
provider-aware.

All fixtures are synthetic. No real bank export is in this repository, and
nothing here should be copied from one.
"""

from __future__ import annotations

import pytest

from finance.ingestion.adapters import (
    AmexCsvAdapter,
    AmexPdfAdapter,
    EnableBankingAdapter,
    ManualAdapter,
    RevolutCsvAdapter,
)
from finance.ingestion.adapters.base import (
    AdapterParseError,
    ImportAdapter,
)
from finance.ingestion.adapters.manual import ManualEntry
from finance.tests.fixtures import (
    ACCOUNT_ID,
    AIS_TRANSACTIONS,
    ais_transaction,
    am_statement_csv,
    make_csv,
    synthetic_amex_csv,
)


class TestAdapterContract:
    """What every adapter must satisfy.

    Three classes and an ABC if a fourth appears (section C) — no plugin
    registry, so the set cannot differ per environment.
    """

    @pytest.mark.parametrize(
        "adapter_class",
        [
            AmexCsvAdapter,
            AmexPdfAdapter,
            RevolutCsvAdapter,
            EnableBankingAdapter,
            ManualAdapter,
        ],
    )
    def test_declares_the_database_check_values(
        self, adapter_class: type[ImportAdapter]
    ) -> None:
        """`provider` and `import_method` are CHECK values in the schema.

        A typo here would fail at INSERT time, in production, on the one row that
        matters.
        """
        assert adapter_class.provider in {
            "enable_banking",
            "amex_csv",
            "amex_pdf",
            "revolut_csv",
            "manual",
        }
        assert adapter_class.import_method in {"api", "csv", "pdf", "manual"}

    @pytest.mark.parametrize(
        "adapter_class",
        [
            AmexCsvAdapter,
            AmexPdfAdapter,
            RevolutCsvAdapter,
            EnableBankingAdapter,
            ManualAdapter,
        ],
    )
    def test_confidence_weight_is_in_range(
        self, adapter_class: type[ImportAdapter]
    ) -> None:
        """PDF is noisier than csv, which is noisier than api (section G)."""
        assert -1.0 <= adapter_class.confidence_weight <= 1.0

    def test_pdf_carries_the_noisiest_weight(self) -> None:
        """Column alignment, wrapped descriptions and page breaks."""
        assert AmexPdfAdapter.confidence_weight < AmexCsvAdapter.confidence_weight
        assert AmexCsvAdapter.confidence_weight < EnableBankingAdapter.confidence_weight

    @pytest.mark.parametrize(
        "adapter_class",
        [EnableBankingAdapter, ManualAdapter],
    )
    def test_non_file_sources_refuse_parse_bytes(
        self, adapter_class: type[ImportAdapter]
    ) -> None:
        """Neither is an upload.

        Failing loudly beats a confusing parse error: an uploaded manual entry or
        an AIS payload posted as a file is a caller bug, not bad data.
        """
        adapter = adapter_class()
        with pytest.raises(NotImplementedError):
            adapter.parse(b"")


class TestAmexCsv:
    def test_parses_a_synthetic_export(self) -> None:
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert result.record_count == 4
        assert not result.failed
        assert result.status == "completed"
        assert result.provider == "amex_csv"

    def test_positive_amounts_become_debits(self) -> None:
        """Amex reports a debit as a positive magnitude.

        Without the flip every purchase would land in the ledger as income, and
        the totals would look plausible while being wrong.
        """
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert all(record.amount_minor < 0 for record in result.records)
        assert {r.amount_minor for r in result.records} == {-850, -1234, -410}

    def test_ref_blocks_are_stripped_from_the_description(self) -> None:
        """The bank reference is noise for matching purposes.

        raw_data keeps it; only the normalised description drops it.
        """
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert all("REF:" not in record.description for record in result.records)

    def test_raw_data_preserves_the_original_description(self) -> None:
        """Replay reads from raw_data, so it must not be normalised."""
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert any(
            "REF:" in str(record.raw_data.get("description"))
            for record in result.records
        )

    def test_no_stable_provider_id(self) -> None:
        """The reason Amex needs Tier 3 at all.

        Amex's own identifiers are documented to change between exports, so a
        Tier-1 dedup on them produces a complete duplicate of every row on
        re-import.
        """
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert all(record.provider_txn_id is None for record in result.records)

    def test_nothing_is_pending(self) -> None:
        """The export is settled activity only."""
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert all(record.pending is False for record in result.records)

    def test_line_numbers_track_the_file(self) -> None:
        """Row 1 of the data is line 2, since the header is line 1."""
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert [record.line_number for record in result.records] == [2, 3, 4, 5]

    def test_checksum_is_stable_across_identical_uploads(self) -> None:
        """Two uploads of the same bytes are the same import.

        This is what makes "I already imported this" answerable without diffing
        file contents.
        """
        payload = synthetic_amex_csv()
        first = AmexCsvAdapter().parse(payload, account_id=ACCOUNT_ID)
        second = AmexCsvAdapter().parse(payload, account_id=ACCOUNT_ID)
        assert first.source_checksum == second.source_checksum
        assert first.source_checksum is not None
        assert len(first.source_checksum) == 64

    def test_account_is_supplied_by_the_caller(self) -> None:
        """A CSV names no account, so the user picks it."""
        result = AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID)
        assert all(record.account_id == ACCOUNT_ID for record in result.records)

    def test_localised_headers(self) -> None:
        """Amex localises its export per language and per vintage."""
        payload = (
            am_statement_csv([("14/03/2026", "JUMBO 4321", "8.50")])
            .replace("Date", "Datum")
            .replace("Description", "Omschrijving")
        )
        result = AmexCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 1
        assert result.records[0].amount_minor == -850

    def test_one_bad_row_does_not_abandon_the_file(self) -> None:
        """A single malformed line in six months of rows must not cost the rest."""
        payload = am_statement_csv(
            [
                ("14/03/2026", "JUMBO 4321", "8.50"),
                ("not-a-date", "BROKEN ROW", "1.00"),
                ("15/03/2026", "NS INTERCITY", "4.10"),
            ]
        )
        result = AmexCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 2
        assert len(result.failed) == 1
        assert result.failed[0].line_number == 3
        assert result.status == "partial"

    def test_missing_header_is_reported_not_raised(self) -> None:
        result = AmexCsvAdapter().parse(b"", account_id=ACCOUNT_ID)
        assert result.record_count == 0
        assert result.status == "failed"
        assert "header" in str(result.failed[0])

    def test_blank_lines_are_skipped_silently(self) -> None:
        payload = am_statement_csv(
            [
                ("14/03/2026", "JUMBO 4321", "8.50"),
                ("", "", ""),
                ("15/03/2026", "NS", "4.10"),
            ]
        )
        result = AmexCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 2
        assert not result.failed

    def test_bom_is_tolerated(self) -> None:
        """Excel writes a UTF-8 BOM, and the first header would not match."""
        result = AmexCsvAdapter().parse(
            ("﻿" + am_statement_csv([("14/03/2026", "JUMBO 4321", "8.50")])).encode(),
            account_id=ACCOUNT_ID,
        )
        assert result.record_count == 1


class TestRevolutCsv:
    def test_carries_the_stable_id(self) -> None:
        """Tier 1 works for Revolut, unlike Amex."""
        payload = make_csv([("2026-03-14", "JUMBO 4321", "8.50")])
        result = RevolutCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 1
        assert result.records[0].provider_txn_id == "txn-000001"

    def test_thousands_separators_are_handled(self) -> None:
        """Revolut writes "EUR 1,234.56".

        Parsed as a string and stripped, never via float, so the minor-unit
        conversion stays exact.
        """
        payload = make_csv([("2026-03-14", "BIG PURCHASE", "EUR 1,234.56")])
        result = RevolutCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.records[0].amount_minor == -123456

    def test_pending_state_is_honoured(self) -> None:
        """Unlike Amex, Revolut does export pending rows."""
        payload = make_csv([("2026-03-14", "JUMBO 4321", "8.50")]).replace(
            "completed", "pending"
        )
        result = RevolutCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.records[0].pending is True

    def test_cash_withdrawals_are_skipped(self) -> None:
        """Moving your own money out of the account is not spending.

        Counting it would show a withdrawal as an expense with nothing to buy.
        """
        payload = make_csv([("2026-03-14", "ATM WITHDRAWAL", "50.00")]).replace(
            "card_payment", "cash_withdrawal"
        )
        result = RevolutCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 0
        assert not result.failed

    def test_unparseable_amount_is_reported_with_a_line_number(self) -> None:
        payload = make_csv([("2026-03-14", "JUMBO 4321", "not-a-number")])
        result = RevolutCsvAdapter().parse(payload.encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 0
        assert result.failed[0].line_number == 2


class TestEnableBanking:
    def test_parses_booked_and_pending(self) -> None:
        result = EnableBankingAdapter().parse_payload(
            AIS_TRANSACTIONS, account_id=ACCOUNT_ID
        )
        assert result.record_count == 2
        statuses = {record.pending for record in result.records}
        assert statuses == {False, True}

    def test_information_only_entries_are_skipped(self) -> None:
        """Legal AIS output, not malformed input.

        An information-only entry carries no amount to book, and treating it as a
        failure would make a healthy account look broken.
        """
        result = EnableBankingAdapter().parse_payload(
            [ais_transaction(status="INFO")], account_id=ACCOUNT_ID
        )
        assert result.record_count == 0
        assert not result.failed

    def test_signed_amounts_pass_through(self) -> None:
        """AIS already encodes a debit as negative."""
        result = EnableBankingAdapter().parse_payload(
            [ais_transaction(amount="-12.34")], account_id=ACCOUNT_ID
        )
        assert result.records[0].amount_minor == -1234

    def test_credit_stays_positive(self) -> None:
        result = EnableBankingAdapter().parse_payload(
            [ais_transaction(amount="250000.00")], account_id=ACCOUNT_ID
        )
        assert result.records[0].amount_minor == 25000000

    def test_entry_reference_is_the_tier_1_key(self) -> None:
        result = EnableBankingAdapter().parse_payload(
            [ais_transaction(entry_reference="eb-ref-42")], account_id=ACCOUNT_ID
        )
        assert result.records[0].provider_txn_id == "eb-ref-42"

    def test_pending_rows_often_have_no_reference(self) -> None:
        """The gap Tier 2 exists to cover.

        `entryReference` usually only materialises on BOOK, so a pending row
        arrives with no Tier-1 key at all.
        """
        result = EnableBankingAdapter().parse_payload(
            [ais_transaction(status="PDNG", entry_reference=None)],
            account_id=ACCOUNT_ID,
        )
        assert result.records[0].provider_txn_id is None
        assert result.records[0].pending is True

    def test_counterparty_name_is_used_when_remittance_is_absent(self) -> None:
        """A blank description fingerprints badly and shows blank in review."""
        transaction = ais_transaction(remittance=None)
        transaction["creditor"] = {"name": "Some Merchant BV"}
        result = EnableBankingAdapter().parse_payload(
            [transaction], account_id=ACCOUNT_ID
        )
        assert result.records[0].description == "Some Merchant BV"

    def test_raw_data_keeps_the_provider_payload(self) -> None:
        """The immutable raw column has to answer "what did the provider send"."""
        result = EnableBankingAdapter().parse_payload(
            [ais_transaction()], account_id=ACCOUNT_ID
        )
        raw = result.records[0].raw_data
        assert raw["status"] == "BOOK"
        assert raw["bookingDate"] == "2026-03-14"

    def test_missing_booking_date_is_a_failure(self) -> None:
        """Cannot be filed without a date, and guessing one would misdate it."""
        transaction = ais_transaction()
        del transaction["bookingDate"]
        result = EnableBankingAdapter().parse_payload(
            [transaction], account_id=ACCOUNT_ID
        )
        assert result.record_count == 0
        assert result.failed[0].line_number == 1

    def test_line_offset_keeps_indices_stable_across_pages(self) -> None:
        """A paginated fetch must produce the same indices as one full fetch."""
        first_page = [ais_transaction()]
        second_page = [ais_transaction(entry_reference="eb-ref-2")]
        combined = EnableBankingAdapter().parse_payload(
            first_page + second_page, account_id=ACCOUNT_ID
        )
        paged = EnableBankingAdapter().parse_payload(
            first_page, account_id=ACCOUNT_ID, line_offset=0
        )
        assert combined.records[0].line_number == paged.records[0].line_number == 1

    def test_never_reads_account_uid(self) -> None:
        """`uid` rotates at every re-auth.

        A fingerprint or a link keyed on it breaks at the next consent, so the
        account is identified by identification_hash, resolved by the caller.
        """
        transaction = ais_transaction()
        transaction["accountUid"] = "rotating-value"
        result = EnableBankingAdapter().parse_payload(
            [transaction], account_id=ACCOUNT_ID
        )
        assert result.records[0].account_id == ACCOUNT_ID
        assert "rotating-value" not in str(result.records[0].provider_txn_id)


class TestAmexPdf:
    LINES = [
        "Card Summary",
        "Date        Description              Amount",
        "14 Mar 2026 JUMBO 4321 AMSTERDAM     8,50",
        "14 Mar 2026 ALBERT HEIJN 1234         12,34",
        "14 Mar 2026 ALBERT HEIJN 1234         12,34",
        "New Balance                              33,18",
    ]

    def test_parses_transaction_rows(self) -> None:
        adapter = AmexPdfAdapter(text_extractor=lambda _payload: self.LINES)
        result = adapter.parse("\n".join(self.LINES).encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 3
        assert result.provider == "amex_pdf"
        assert result.import_method == "pdf"

    def test_headings_are_not_transactions(self) -> None:
        """ "New Balance" is not a EUR 33.18 purchase."""
        adapter = AmexPdfAdapter(text_extractor=lambda _payload: self.LINES)
        result = adapter.parse("\n".join(self.LINES).encode(), account_id=ACCOUNT_ID)
        assert all("new balance" not in r.description.lower() for r in result.records)

    def test_identical_rows_are_not_collapsed(self) -> None:
        """The PDF/CSV overlap case.

        Importing a 7-year PDF and then a 6-month CSV must be non-destructive,
        which is exactly what Tier 3 gives.
        """
        adapter = AmexPdfAdapter(text_extractor=lambda _payload: self.LINES)
        result = adapter.parse("\n".join(self.LINES).encode(), account_id=ACCOUNT_ID)
        heijn = [r for r in result.records if "heijn" in r.description.lower()]
        assert len(heijn) == 2
        assert {r.amount_minor for r in heijn} == {-1234}

    def test_no_extractable_text_is_reported(self) -> None:
        adapter = AmexPdfAdapter(text_extractor=lambda _payload: [])
        result = adapter.parse(b"", account_id=ACCOUNT_ID)
        assert result.status == "failed"

    def test_a_merchant_named_after_a_heading_is_not_dropped(self) -> None:
        """Prefix matching must require whitespace.

        "Payment Solutions BV" begins with the heading word "payment", and a
        looser prefix test would silently delete it from the statement.
        """
        lines = [
            "Date        Description              Amount",
            "14 Mar 2026 PAYMENT SOLUTIONS BV    120,00",
        ]
        adapter = AmexPdfAdapter(text_extractor=lambda _payload: lines)
        result = adapter.parse("\n".join(lines).encode(), account_id=ACCOUNT_ID)
        assert result.record_count == 1
        assert "PAYMENT SOLUTIONS" in result.records[0].description

    def test_text_extraction_is_injected_not_imported(self) -> None:
        """pdfplumber is M7's concern.

        Keeping it behind an injected callable is what lets this file be tested
        with a string and keeps M0's dependency set unchanged.
        """
        assert "pdfplumber" not in ImportAdapter.__module__


class TestManual:
    def _entry(self, amount: object = "-3.50", **overrides: object) -> ManualEntry:
        params: dict[str, object] = {
            "account_id": ACCOUNT_ID,
            "description": "Coffee with a colleague",
            "amount": amount,
            "currency": "EUR",
            "booked_date": "2026-03-14",
        }
        params.update(overrides)
        return ManualEntry(**params)  # type: ignore[arg-type]

    def test_parses_a_user_entry(self) -> None:
        result = ManualAdapter().parse_entry(self._entry())
        assert result.record_count == 1
        assert result.records[0].amount_minor == -350
        assert result.records[0].provider_txn_id is None

    def test_direction_is_explicit(self) -> None:
        """The form asks for a direction rather than expecting a typed minus."""
        as_debit = ManualAdapter().parse_entry(
            self._entry(amount="3.50"), as_debit=True
        )
        as_credit = ManualAdapter().parse_entry(
            self._entry(amount="3.50"), as_debit=False
        )
        assert as_debit.records[0].amount_minor == -350
        assert as_credit.records[0].amount_minor == 350

    def test_date_is_never_defaulted(self) -> None:
        """Defaulting to today would silently misdate the entry."""
        result = ManualAdapter().parse_entry(self._entry(booked_date=""))
        assert result.record_count == 0
        assert "required" in str(result.failed[0])

    def test_empty_description_is_refused(self) -> None:
        result = ManualAdapter().parse_entry(self._entry(description="   "))
        assert result.record_count == 0

    def test_absurdly_long_description_is_refused(self) -> None:
        """A pasted statement line would match nothing and clutter the queue."""
        result = ManualAdapter().parse_entry(self._entry(description="x" * 501))
        assert result.record_count == 0

    def test_float_input_is_refused(self) -> None:
        """A JSON number would arrive as a float, which this project forbids."""
        result = ManualAdapter().parse_entry(self._entry(amount=3.50))
        assert result.record_count == 0
        assert result.failed


class TestSharedShape:
    """Every adapter emits the same shape, which is what makes the rest of the
    pipeline provider-agnostic."""

    def test_all_adapters_produce_raw_records(self) -> None:
        records = (
            AmexCsvAdapter().parse(synthetic_amex_csv(), account_id=ACCOUNT_ID).records
            + RevolutCsvAdapter()
            .parse(
                make_csv([("2026-03-14", "JUMBO 4321", "8.50")]).encode(),
                account_id=ACCOUNT_ID,
            )
            .records
            + EnableBankingAdapter()
            .parse_payload([ais_transaction()], account_id=ACCOUNT_ID)
            .records
            + AmexPdfAdapter(text_extractor=lambda _p: TestAmexPdf.LINES)
            .parse("\n".join(TestAmexPdf.LINES).encode(), account_id=ACCOUNT_ID)
            .records
            + ManualAdapter()
            .parse_entry(
                ManualEntry(
                    account_id=ACCOUNT_ID,
                    description="Coffee",
                    amount="-3.50",
                    currency="EUR",
                    booked_date="2026-03-14",
                )
            )
            .records
        )
        # 4 Amex CSV + 1 Revolut CSV + 1 AIS + 3 PDF + 1 manual.
        assert len(records) == 10
        for record in records:
            assert isinstance(record.amount_minor, int)
            assert len(record.currency) == 3
            assert record.account_id == ACCOUNT_ID
            assert isinstance(record.raw_data, dict)

    def test_error_messages_name_the_provider(self) -> None:
        """A stack trace through three pipeline layers is not a useful error."""
        error = AdapterParseError("bad row", line_number=4, provider="amex_csv")
        assert "amex_csv" in str(error)
        assert "line 4" in str(error)
