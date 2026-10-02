"""Money arithmetic tests — signed minor units, never floats.

The project's loudest rule (ARCHITECTURE.md section 6): an amount is an integer
number of minor units and `currency.decimals` is the authoritative exponent.
These tests cover both `core.money` and the domain-layer re-export of it,
because a second Money class would be two answers to "what is a cent".

Synthetic amounts throughout. No real financial data appears in this repository.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

import pytest
from core.money import Currency, Money

from finance.domain.value_objects import Currency as DomainCurrency
from finance.domain.value_objects import Money as DomainMoney

EUR = Currency(code="EUR")
JPY = Currency(code="JPY", decimals=0)
BTC = Currency(code="BTC", decimals=8)
ETH = Currency(code="ETH", decimals=18)


class TestCurrency:
    def test_exponent_is_authoritative(self) -> None:
        """`decimals` is never inferred from the code.

        A historical redefinition of a currency must not silently reinterpret
        every stored amount, so the exponent is data, not a lookup.
        """
        assert EUR.decimals == 2
        assert Currency(code="XYZ").decimals == 2
        assert Currency(code="XYZ", decimals=4).decimals == 4

    def test_scale(self) -> None:
        assert EUR.scale == 100
        assert JPY.scale == 1
        assert BTC.scale == 100_000_000

    def test_has_minor_units(self) -> None:
        assert EUR.has_minor_units
        assert not JPY.has_minor_units

    @pytest.mark.parametrize("code", ["EU", "EURO", "E1R", "", "EU "])
    def test_rejects_malformed_codes(self, code: str) -> None:
        with pytest.raises(ValueError, match="3 letters"):
            Currency(code=code)

    def test_rejects_absurd_exponent(self) -> None:
        with pytest.raises(ValueError, match="decimals must be"):
            Currency(code="EUR", decimals=19)


class TestMoneyIsMinorUnits:
    def test_amount_is_an_int(self) -> None:
        """A float cannot even be constructed.

        This is the rule made structural: `Money(amount=10.0)` is not a value
        that rounds, it is a type error.
        """
        money = Money(amount=1250, currency=EUR)
        assert money.amount == 1250
        assert isinstance(money.amount, int)

    def test_float_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="int minor units"):
            Money(amount=12.5, currency=EUR)  # type: ignore[arg-type]

    def test_bool_is_rejected(self) -> None:
        """bool subclasses int; True must not silently become 1 cent."""
        with pytest.raises(TypeError, match="int minor units"):
            Money(amount=True, currency=EUR)

    def test_sign_conveys_direction(self) -> None:
        negative = Money(amount=-1250, currency=EUR)
        positive = Money(amount=1250, currency=EUR)
        assert negative.is_negative
        assert not negative.is_positive
        assert positive.is_positive
        assert not negative.is_zero

    def test_is_zero(self) -> None:
        assert Money.zero(EUR).is_zero
        assert not Money(amount=1, currency=EUR).is_zero

    def test_abs(self) -> None:
        assert Money(amount=-1250, currency=EUR).abs() == Money(
            amount=1250, currency=EUR
        )


class TestArithmetic:
    def test_add_and_subtract(self) -> None:
        a = Money(amount=-1250, currency=EUR)
        b = Money(amount=-320, currency=EUR)
        assert (a + b).amount == -1570
        assert (a - b).amount == -930

    def test_cross_currency_is_refused(self) -> None:
        """Never silently convert.

        A conversion needs a rate and a date; guessing one produces a wrong
        balance that nobody notices until the numbers do not add up.
        """
        with pytest.raises(ValueError, match="different currencies"):
            Money(amount=100, currency=EUR) + Money(amount=100, currency=JPY)

    def test_negate(self) -> None:
        assert (-Money(amount=1250, currency=EUR)).amount == -1250

    def test_integer_multiplier(self) -> None:
        assert (Money(amount=100, currency=EUR) * 3).amount == 300
        assert (3 * Money(amount=100, currency=EUR)).amount == 300

    def test_float_multiplier_is_refused(self) -> None:
        """`Money * 0.5` is the classic way a float gets into a ledger."""
        with pytest.raises(TypeError, match="factor must be int"):
            Money(amount=100, currency=EUR) * 0.5  # type: ignore[operator]

    def test_exactness_over_many_additions(self) -> None:
        """The reason for minor units: no drift.

        A cent is an integer, so a hundred additions of 3.20 are exactly 3.20.
        The same arithmetic in float cents loses this.
        """
        total = Money.zero(EUR)
        for _ in range(100):
            total = total + Money(amount=320, currency=EUR)
        assert total.amount == 32_000
        assert str(total) == "320.00"

    def test_float_accumulation_would_drift(self) -> None:
        """The comparison that justifies the whole design.

        Ten additions of 0.1 in binary floating point do not sum to 1.0. In minor
        units the same ten additions are exactly 100 cents, which is why a ledger
        cannot be built on floats.
        """
        as_float = 0.0
        for _ in range(10):
            as_float += 0.1
        assert as_float != 1.0

        in_minor_units = Money.zero(EUR)
        for _ in range(10):
            in_minor_units = in_minor_units + Money(amount=10, currency=EUR)
        assert in_minor_units.amount == 100
        assert in_minor_units == Money(amount=100, currency=EUR)


class TestDecimalConversion:
    """The only sanctioned crossing between major and minor units."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("12.34", 1234), ("-5.67", -567), ("0.00", 0), ("0.01", 1), ("-0.01", -1)],
    )
    def test_from_decimal_eur(self, value: str, expected: int) -> None:
        assert Money.from_decimal(Decimal(value), EUR).amount == expected

    @pytest.mark.parametrize(
        ("value", "currency", "expected"),
        [
            ("1234", JPY, 1234),
            ("0", JPY, 0),
            ("-500", JPY, -500),
            ("0.00000001", BTC, 1),
            ("1", BTC, 100_000_000),
        ],
    )
    def test_per_currency_exponent(
        self, value: str, currency: Currency, expected: int
    ) -> None:
        """`currency.decimals` decides the scale, per currency."""
        assert Money.from_decimal(Decimal(value), currency).amount == expected

    def test_rounds_half_up(self) -> None:
        """What a human reading a bank statement expects.

        Python's default rounding is banker's rounding (ROUND_HALF_EVEN), which
        sends 0.125 to 0.12. A statement says 0.13, and a provider that rounds
        half-up would disagree with us — so the tie is broken toward the larger
        magnitude explicitly rather than inherited from a default.
        """
        assert Money.from_decimal(Decimal("0.125"), EUR).amount == 13
        assert Money.from_decimal(Decimal("0.135"), EUR).amount == 14

    def test_differs_from_bankers_rounding(self) -> None:
        """The distinction, stated as an assertion rather than a comment.

        Under ROUND_HALF_EVEN these two cases would give 12 and 14; under
        ROUND_HALF_UP they give 13 and 14. The first differing case is the one
        that matters, because a mismatch here is a one-cent disagreement with the
        provider on every such amount.
        """
        bankers = Decimal("0.125").quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        half_up = Decimal("0.125").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert bankers != half_up
        assert Money.from_decimal(Decimal("0.125"), EUR).amount == int(
            half_up.scaleb(2)
        )

    def test_rounding_matches_decimal_module(self) -> None:
        value = Decimal("12.345")
        expected = int(
            value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).scaleb(2)
        )
        assert Money.from_decimal(value, EUR).amount == expected

    def test_round_trips(self) -> None:
        for minor in (0, 1, -1, 99, 1234, -1234):
            money = Money(amount=minor, currency=EUR)
            assert Money.from_decimal(money.to_decimal(), EUR) == money

    def test_to_decimal_is_exact(self) -> None:
        assert Money(amount=1250, currency=EUR).to_decimal() == Decimal("12.50")
        assert Money(amount=1, currency=BTC).to_decimal() == Decimal("0.00000001")
        assert Money(amount=1, currency=JPY).to_decimal() == Decimal("1")


class TestRendering:
    @pytest.mark.parametrize(
        ("amount", "currency", "expected"),
        [
            (1234, EUR, "12.34"),
            (-1234, EUR, "-12.34"),
            (0, EUR, "0.00"),
            (5, EUR, "0.05"),
            (50, EUR, "0.50"),
            (1234, JPY, "1234"),
            (-1234, JPY, "-1234"),
            (100_000_000, BTC, "1.00000000"),
        ],
    )
    def test_str(self, amount: int, currency: Currency, expected: str) -> None:
        assert str(Money(amount=amount, currency=currency)) == expected

    def test_repr_names_the_currency(self) -> None:
        """The currency is the first thing you need to see.

        Two amounts of 100 in different currencies are different numbers, and a
        bare "100" in a log is a bug waiting to be misread.
        """
        assert "EUR" in repr(Money(amount=100, currency=EUR))


class TestDomainReExport:
    """`finance.domain.value_objects.money` re-exports the core implementation.

    One Money, two names. A second class here would be two answers to "what is a
    cent", which is precisely what this module exists to prevent.
    """

    def test_same_class(self) -> None:
        assert DomainMoney is Money
        assert DomainCurrency is Currency

    def test_isinstance_works_across_both_paths(self) -> None:
        """The reason it is a re-export rather than a subclass."""
        money: DomainMoney = DomainMoney(
            amount=1250, currency=DomainCurrency(code="EUR")
        )
        assert isinstance(money, Money)
        assert isinstance(money, DomainMoney)

    def test_arithmetic_is_identical(self) -> None:
        assert DomainMoney(amount=100, currency=EUR) + DomainMoney(
            amount=50, currency=EUR
        ) == Money(amount=150, currency=EUR)


class TestEqualityAndHashing:
    def test_value_equality(self) -> None:
        a = Money(amount=1250, currency=EUR)
        b = Money(amount=1250, currency=EUR)
        assert a == b
        assert hash(a) == hash(b)

    def test_currency_is_part_of_identity(self) -> None:
        """100 EUR is not 100 JPY, and equality has to say so."""
        assert Money(amount=100, currency=EUR) != Money(amount=100, currency=JPY)

    def test_usable_as_a_dict_key(self) -> None:
        """Needed for summing and grouping without a round trip."""
        ledger = {
            Money(amount=1250, currency=EUR): "groceries",
            Money(amount=320, currency=EUR): "coffee",
        }
        assert ledger[Money(amount=1250, currency=EUR)] == "groceries"

    def test_inequality_with_other_types(self) -> None:
        """A Money never equals a bare number or a string.

        `Money(amount=100) == 100` being False is what stops an int and a Money
        being confused in a dict or a sum. Compared through a typed `object`
        parameter because `str.__eq__` is typed too narrowly for mypy to accept
        the direct comparison without a lie.
        """

        def equals(left: object, right: object) -> bool:
            return bool(left == right)

        money = Money(amount=100, currency=EUR)
        assert not equals(money, 100)
        assert not equals(money, "12.34")
        assert not equals(money, Money(amount=100, currency=JPY))
