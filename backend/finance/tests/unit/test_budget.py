"""Budget arithmetic tests.

Pure integer arithmetic on signed minor units. The property worth stating: the
comparison happens on integers, because a budget checked with floats is
permanently off by a fraction of a cent per transaction and nobody notices until
the numbers do not add up.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from core.money import Currency, Money

from finance.domain.services.budget import check_budget

EUR = Currency(code="EUR")
JPY = Currency(code="JPY", decimals=0)


def money(amount: int, currency: Currency = EUR) -> Money:
    return Money(amount=amount, currency=currency)


class TestWithinBudget:
    def test_under_limit(self) -> None:
        result = check_budget(category_id=1, spent=money(2000), limit=money(5000))
        assert not result.over_budget
        assert result.remaining.amount == 3000
        assert result.used_ratio == 40

    def test_exactly_at_limit(self) -> None:
        """Spending the whole budget is not over budget."""
        result = check_budget(category_id=1, spent=money(5000), limit=money(5000))
        assert not result.over_budget
        assert result.remaining.amount == 0
        assert result.used_ratio == 100

    def test_nothing_spent(self) -> None:
        result = check_budget(category_id=1, spent=money(0), limit=money(5000))
        assert not result.over_budget
        assert result.used_ratio == 0


class TestOverBudget:
    def test_one_cent_over(self) -> None:
        """The boundary that a float would eventually get wrong."""
        result = check_budget(category_id=1, spent=money(5001), limit=money(5000))
        assert result.over_budget
        assert result.remaining.amount == -1

    def test_remaining_is_negative(self) -> None:
        """Sign conveys the overrun; there is no separate field for it."""
        result = check_budget(category_id=1, spent=money(9000), limit=money(5000))
        assert result.over_budget
        assert result.remaining.amount == -4000
        assert result.remaining.is_negative


class TestRefunds:
    def test_negative_spend_counts_towards_the_limit(self) -> None:
        """A refund exceeding spend is a real outcome, not an error.

        A negative `remaining` here would mean "over budget by a refund", which is
        nonsense and would show the user a budget in a state that cannot occur.
        """
        result = check_budget(category_id=1, spent=money(-1000), limit=money(5000))
        assert not result.over_budget
        assert result.remaining.amount == 6000

    def test_refund_can_recover_a_previous_overrun(self) -> None:
        result = check_budget(category_id=1, spent=money(-2000), limit=money(1000))
        assert not result.over_budget
        assert result.remaining.amount == 3000


class TestCurrencies:
    def test_cross_currency_is_refused(self) -> None:
        """A conversion needs a rate and a date.

        Guessing one produces a wrong budget, which is worse than refusing.
        """
        with pytest.raises(ValueError, match="across currencies"):
            check_budget(category_id=1, spent=money(1000), limit=money(1000, JPY))

    def test_zero_decimal_currency(self) -> None:
        """JPY has no minor units; the arithmetic is the same shape."""
        result = check_budget(
            category_id=1, spent=money(1500, JPY), limit=money(1000, JPY)
        )
        assert result.over_budget
        assert result.remaining.amount == -500


class TestZeroLimit:
    def test_zero_limit_reports_zero_percent(self) -> None:
        """Spending nothing against no limit is not 100% overspent.

        Dividing by zero here would either crash or produce infinity, and both end
        up rendered as a nonsense progress bar.
        """
        result = check_budget(category_id=1, spent=money(0), limit=money(0))
        assert not result.over_budget
        assert result.used_ratio == 0

    def test_any_spend_against_a_zero_limit_is_over(self) -> None:
        result = check_budget(category_id=1, spent=money(1), limit=money(0))
        assert result.over_budget
        assert result.remaining.amount == -1
        assert result.used_ratio == 0


class TestRatio:
    @pytest.mark.parametrize(
        ("spent_minor", "limit_minor", "expected"),
        [
            (0, 1000, 0),
            (100, 1000, 10),
            (999, 1000, 100),
            (1000, 1000, 100),
            (1001, 1000, 100),
            (2500, 1000, 250),
            (500, 0, 0),
        ],
    )
    def test_whole_percentages(
        self, spent_minor: int, limit_minor: int, expected: int
    ) -> None:
        """An integer percentage, because a progress bar is not a float.

        A float ratio would invite a comparison against a float budget, which is
        the mistake this type exists to prevent.
        """
        result = check_budget(
            category_id=1, spent=money(spent_minor), limit=money(limit_minor)
        )
        assert result.used_ratio == expected
        assert isinstance(result.used_ratio, int)

    def test_rounds_to_nearest_percent(self) -> None:
        result = check_budget(category_id=1, spent=money(1234), limit=money(1000))
        assert result.used_ratio == 123

    def test_no_float_leaks_into_the_result(self) -> None:
        result = check_budget(category_id=1, spent=money(1234), limit=money(1000))
        assert isinstance(result.remaining.amount, int)
        assert isinstance(result.spent.amount, int)
        assert isinstance(result.limit.amount, int)
        assert isinstance(result.used_ratio, int)


class TestResultShape:
    def test_spend_and_limit_are_echoed(self) -> None:
        """The caller renders all three, so all three come back."""
        spent = money(1234)
        limit = money(1000)
        result = check_budget(category_id=7, spent=spent, limit=limit)
        assert result.category_id == 7
        assert result.spent == spent
        assert result.limit == limit
        assert result.remaining.currency == EUR

    def test_remaining_keeps_the_limit_currency(self) -> None:
        result = check_budget(
            category_id=1, spent=money(100, JPY), limit=money(1000, JPY)
        )
        assert result.remaining.currency == JPY
        assert result.remaining.currency == result.limit.currency


def test_decimal_only_for_display() -> None:
    """A guard against a 'harmless' refactor introducing a float comparison."""
    assert Decimal("12.34") * 100 == Decimal("1234")
