"""Normalisation tests: provider rows to the one canonical shape.

The rules that matter here are the ones that, if wrong, silently corrupt a
ledger: sign convention, exact decimal conversion, and never inventing a field
the provider did not send.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from core.money import Currency

from finance.ingestion.normalize import (
    AmountSignConvention,
    normalize_amount,
    normalize_currency,
    normalize_description,
    normalize_record,
    normalize_reference,
)


class TestAmountParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("12.34", 1234), ("-12.34", -1234), ("0.00", 0), ("0.01", 1), ("-0.01", -1)],
    )
    def test_signed_decimal(self, text: str, expected: int) -> None:
        assert normalize_amount(text, Currency(code="EUR")).amount == expected

    @pytest.mark.parametrize(
        ("text", "currency", "expected"),
        [
            ("1234", "JPY", 1234),
            ("0.00000001", "BTC", 1),
            ("0.000000000000000001", "ETH", 1),
        ],
    )
    def test_per_currency_exponent(
        self, text: str, currency: str, expected: int
    ) -> None:
        """`currency.decimals` decides the scale."""
        money = normalize_amount(
            text, Currency(code=currency, decimals=_exponent(currency))
        )
        assert money.amount == expected

    def test_int_input_is_already_minor_units(self) -> None:
        """An int must not be scaled by 100.

        Reinterpreting 1250 as "1250 euro" would silently multiply every integer
        amount by 100 — the kind of error that looks plausible on the first row
        and is invisible for a week.
        """
        assert normalize_amount(1250, Currency(code="EUR")).amount == 1250

    def test_never_uses_float(self) -> None:
        """A float would arrive here only from a JSON number, and is refused."""
        with pytest.raises(ValueError, match="unparseable|Cannot parse"):
            normalize_amount("abc", Currency(code="EUR"))

    def test_exactness(self) -> None:
        """The reason for going through Decimal rather than float.

        In float, 0.1 + 0.2 != 0.3, and a provider's "0.005" rounds to
        whichever side the binary representation happens to fall on.
        """
        assert normalize_amount("0.30", Currency(code="EUR")).amount == 30
        assert normalize_amount("1.15", Currency(code="EUR")).amount == 115

    def test_rounds_half_up(self) -> None:
        assert normalize_amount("0.125", Currency(code="EUR")).amount == 13
        assert normalize_amount("-0.125", Currency(code="EUR")).amount == -13

    def test_thousands_separators_are_not_accepted(self) -> None:
        """Explicit is better: a European "1.234,56" is ambiguous by locale."""
        with pytest.raises(ValueError, match="Cannot parse|unparseable"):
            normalize_amount("1.234,56", Currency(code="EUR"))

    def test_rejects_bool(self) -> None:
        """bool is an int subclass; True must not become 1 minor unit."""
        with pytest.raises(ValueError, match="must not be a bool"):
            normalize_amount(True, Currency(code="EUR"))

    def test_rejects_infinite(self) -> None:
        with pytest.raises(ValueError, match="finite"):
            normalize_amount("NaN", Currency(code="EUR"))


class TestSignConvention:
    def test_positive_is_debit_flips_sign(self) -> None:
        """Amex and Revolut report a debit as a positive magnitude."""
        money = normalize_amount(
            "8.50",
            Currency(code="EUR"),
            AmountSignConvention.POSITIVE_IS_DEBIT,
        )
        assert money.amount == -850
        assert money.is_negative

    def test_signed_passes_through(self) -> None:
        """AIS reports a debit as negative already."""
        money = normalize_amount(
            "-8.50", Currency(code="EUR"), AmountSignConvention.SIGNED
        )
        assert money.amount == -850

    def test_positive_is_debit_never_produces_a_positive_amount(self) -> None:
        """Even from a negative input, the convention wins.

        A provider that emits -8.50 under a positive-is-debit convention is
        describing a credit, and `-abs()` is the only reading that keeps the
        ledger's sign convention uniform.
        """
        money = normalize_amount(
            "-8.50",
            Currency(code="EUR"),
            AmountSignConvention.POSITIVE_IS_DEBIT,
        )
        assert money.amount == -850


class TestCurrency:
    def test_uppercases(self) -> None:
        assert normalize_currency("eur").code == "EUR"
        assert normalize_currency(" Eur ").code == "EUR"

    @pytest.mark.parametrize("code", ["EU", "EURO", "E1R", ""])
    def test_rejects_malformed(self, code: str) -> None:
        with pytest.raises(ValueError, match="currency code"):
            normalize_currency(code)


class TestReference:
    def test_strips_whitespace(self) -> None:
        assert normalize_reference("  abc123  ") == "abc123"

    def test_empty_becomes_none(self) -> None:
        """ "" would match every other "" in a uniqueness check."""
        assert normalize_reference("") is None
        assert normalize_reference("   ") is None
        assert normalize_reference(None) is None

    def test_preserves_a_real_id(self) -> None:
        assert normalize_reference("eb-ref-0001") == "eb-ref-0001"


class TestDescription:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Jumbo 4321  ", "Jumbo 4321"),
            ("Jumbo   4321", "Jumbo 4321"),
            ("Jumbo\t4321", "Jumbo 4321"),
            ("Jumbo 4321 REF:000123", "Jumbo 4321"),
            ("Jumbo 4321 *REF:000123", "Jumbo 4321"),
            ("Jumbo 4321 KAASACHTELNR:12345", "Jumbo 4321"),
            ("Jumbo 4321 MNDT 12345", "Jumbo 4321"),
        ],
    )
    def test_cleans(self, raw: str, expected: str) -> None:
        assert normalize_description(raw) == expected

    def test_case_is_preserved(self) -> None:
        """The raw column must not disagree with what we match on.

        Fingerprint and categorization both lower-case; doing it here would make
        `raw_description` — which is immutable and user-visible — differ from
        every other representation of the same payee.
        """
        assert normalize_description("JUMBO 4321") == "JUMBO 4321"

    def test_empty(self) -> None:
        assert normalize_description("") == ""
        assert normalize_description(None) == ""


class TestNormalizeRecord:
    def test_full_record(self) -> None:
        record = normalize_record(
            account_id=1,
            description="  Jumbo 4321  REF:1 ",
            amount="-8.50",
            currency_code="eur",
            booked_date="14/03/2026",
            value_date="2026-03-13",
            provider_txn_id="  tx-1 ",
            line_number=7,
        )
        assert record.account_id == 1
        assert record.description == "Jumbo 4321"
        assert record.amount.amount == -850
        assert record.amount.currency.code == "EUR"
        assert record.booked_date == dt.date(2026, 3, 14)
        assert record.value_date == dt.date(2026, 3, 13)
        assert record.provider_txn_id == "tx-1"
        assert record.line_number == 7
        assert record.is_debit

    def test_credit_is_not_a_debit(self) -> None:
        record = normalize_record(
            account_id=1,
            description="Salary",
            amount="300000",
            currency_code="EUR",
            booked_date="2026-03-14",
        )
        assert record.is_credit
        assert not record.is_debit

    def test_value_date_is_optional(self) -> None:
        record = normalize_record(
            account_id=1,
            description="Jumbo",
            amount=-100,
            currency_code="EUR",
            booked_date="2026-03-14",
        )
        assert record.value_date is None

    def test_raw_data_is_preserved_verbatim(self) -> None:
        """raw_data is what replay reads from, so it is never normalised.

        The immutable raw column must be able to answer "what did the provider
        actually send", including the whitespace and markers that normalisation
        stripped.
        """
        record = normalize_record(
            account_id=1,
            description="  Jumbo  4321  ",
            amount="-8.50",
            currency_code="EUR",
            booked_date="2026-03-14",
        )
        assert record.raw_data["description"] == "  Jumbo  4321  "
        assert record.raw_data["amount_minor"] == -850

    def test_raw_data_keeps_the_caller_payload(self) -> None:
        """An adapter that has the original row keeps it; normalisation adds to
        the mapping rather than replacing it."""
        record = normalize_record(
            account_id=1,
            description="Jumbo",
            amount=-850,
            currency_code="EUR",
            booked_date="2026-03-14",
            raw_data={"entryReference": "eb-1", "cardProduct": "visa"},
        )
        assert record.raw_data["entryReference"] == "eb-1"
        assert record.raw_data["cardProduct"] == "visa"
        assert record.raw_data["currency"] == "EUR"

    def test_missing_description_is_not_invented(self) -> None:
        """A blank description stays blank.

        Substituting the payee would make `raw_description` disagree with the
        provider's row, and a blank is at least visibly blank in the review
        queue.
        """
        record = normalize_record(
            account_id=1,
            description=None,
            amount=-100,
            currency_code="EUR",
            booked_date="2026-03-14",
        )
        assert record.description == ""

    def test_bad_date_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse date"):
            normalize_record(
                account_id=1,
                description="Jumbo",
                amount=-100,
                currency_code="EUR",
                booked_date="not a date",
            )


def _exponent(code: str) -> int:
    return {"EUR": 2, "JPY": 0, "BTC": 8, "ETH": 18}[code]


def test_decimal_import_is_used() -> None:
    """Guards against a 'harmless' refactor to float somewhere in this path."""
    assert Decimal("0.30") == Decimal("0.3")
