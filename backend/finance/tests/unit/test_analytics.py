"""analytics service — the rules that decide what a chart shows.

These are the tests that matter most in the whole feature, because every one of
them describes a way the numbers come out plausible and wrong. A chart that is
silently off by a sign does not announce itself; a user has to notice it against
their own bank statement.

The SQL-level rules — that equity is excluded from net worth and that the
liability column is never subtracted — live in `finance.api.routes.analytics` and
need a real database to exercise. **They are currently untested.** They are the
two rules that would silently report a purchase as wealth, so they are the first
thing to cover with a `db`-marked test once Postgres is reachable; until then they
are held in place by review and by the arithmetic written out in the route's
docstring, not by a test that would catch a regression.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finance.domain.services.analytics import (
    PERIODS,
    CashflowPoint,
    bucket_key,
    date_range,
    net_worth_series,
    quantise,
    root_ancestor,
    summarise_cashflow,
)

EUR = Decimal


class TestQuantise:
    def test_rounds_half_up_like_a_bank_statement(self) -> None:
        # Half-up is what `Money.from_decimal` does and is what a human comparing
        # this to a statement expects; bankers' rounding would make a .005 land
        # the other way and quietly disagree with every invoice.
        assert quantise(EUR("12.345")) == 1235
        assert quantise(EUR("-12.345")) == -1235

    def test_keeps_four_decimal_input_without_truncating_it(self) -> None:
        # `amount_base` carries 4 dp (FX rates need them). A figure that survives
        # the ledger and dies at the API boundary is worse than a rounding rule.
        assert quantise(EUR("0.0049")) == 0
        assert quantise(EUR("0.0051")) == 1

    def test_zero_is_zero_in_both_directions(self) -> None:
        assert quantise(EUR("0")) == 0


class TestNetWorthSeries:
    def test_a_liability_makes_net_worth_fall(self) -> None:
        """The sign rule, stated as a number.

        £5,000 lands in an asset account, then a £2,000 card charge posts to the
        liability. Net worth must read £3,000 — NOT £7,000, which is what a
        second subtraction of the liability column would produce, and NOT £5,000,
        which is what leaving equity in would produce.
        """
        series = net_worth_series(
            {date(2026, 1, 2): EUR("5000"), date(2026, 1, 4): EUR("-2000")},
            date(2026, 1, 1),
            date(2026, 1, 5),
        )
        assert [minor for _, minor in series] == [0, 500000, 500000, 300000, 300000]

    def test_a_quiet_day_repeats_the_previous_figure(self) -> None:
        """A gap must carry forward, not fall to zero.

        A chart that drops to £0 on a Sunday reads as bankruptcy, not as a day
        nothing happened.
        """
        series = net_worth_series(
            {date(2026, 1, 2): EUR("100")}, date(2026, 1, 1), date(2026, 1, 4)
        )
        assert [minor for _, minor in series] == [0, 10000, 10000, 10000]

    def test_every_day_in_the_range_is_present(self) -> None:
        series = net_worth_series({}, date(2026, 1, 1), date(2026, 1, 3))
        assert len(series) == 3
        assert all(minor == 0 for _, minor in series)

    def test_balance_before_the_range_is_carried_into_it(self) -> None:
        """Asking for last week must not report last week's opening as nothing.

        The running total is seeded from postings that predate `start`, otherwise
        a window that begins after the first transaction reports a net worth
        built only from what happened inside it.
        """
        series = net_worth_series(
            {date(2026, 1, 1): EUR("900")}, date(2026, 1, 10), date(2026, 1, 11)
        )
        assert [minor for _, minor in series] == [90000, 90000]

    def test_inverted_range_is_empty_rather_than_negative(self) -> None:
        series = net_worth_series(
            {date(2026, 1, 3): EUR("1")}, date(2026, 1, 5), date(2026, 1, 1)
        )
        assert series == []


class TestDateRange:
    def test_inclusive_of_both_ends(self) -> None:
        assert date_range(date(2026, 1, 1), date(2026, 1, 3)) == [
            date(2026, 1, 1),
            date(2026, 1, 2),
            date(2026, 1, 3),
        ]

    def test_a_single_day_is_one_point(self) -> None:
        assert date_range(date(2026, 1, 1), date(2026, 1, 1)) == [date(2026, 1, 1)]


class TestRootAncestor:
    def test_walks_to_the_top(self) -> None:
        assert root_ancestor(3, {3: 2, 2: 1, 1: None}) == 1

    def test_a_root_is_its_own_ancestor(self) -> None:
        assert root_ancestor(1, {1: None}) == 1

    def test_a_self_referencing_category_terminates(self) -> None:
        """A hang is worse than a wrong answer here: nothing reports it.

        `parent_id` is a plain self-referencing foreign key with no constraint
        against a loop, so this is representable and unguarded it never returns.
        """
        assert root_ancestor(7, {7: 7}) == 7

    def test_two_categories_pointing_at_each_other_terminate(self) -> None:
        assert root_ancestor(1, {1: 2, 2: 1}) in (1, 2)


class TestCashflow:
    def test_income_and_expense_are_both_positive_and_net_keeps_its_sign(self) -> None:
        """The one asymmetry in the whole feature, so it gets a test.

        A chart wants "£42 spent", not "-42", but a deficit still has to read as
        a deficit. Flipping all three together would make every period balance.
        """
        points = summarise_cashflow(
            [(date(2026, 1, 5), EUR("3000")), (date(2026, 1, 20), EUR("-425"))],
            "month",
        )
        assert len(points) == 1
        assert points[0].income == 300000
        assert points[0].expense == 42500
        assert points[0].net == 257500

    def test_a_spending_period_has_a_negative_net(self) -> None:
        (point,) = summarise_cashflow([(date(2026, 2, 3), EUR("-100"))], "month")
        assert point.net == -10000

    def test_a_period_with_no_movement_is_omitted_not_zero_filled(self) -> None:
        points = summarise_cashflow(
            [(date(2026, 1, 5), EUR("1000")), (date(2026, 3, 5), EUR("2000"))],
            "month",
        )
        assert [point.period for point in points] == ["2026-01", "2026-03"]

    def test_months_are_bucketed_by_calendar_month(self) -> None:
        points = summarise_cashflow(
            [
                (date(2026, 1, 31), EUR("1000")),
                (date(2026, 2, 1), EUR("2000")),
            ],
            "month",
        )
        assert [point.period for point in points] == ["2026-01", "2026-02"]

    def test_weeks_are_bucketed_by_their_monday(self) -> None:
        """A stable label, not 'the seven days since the request'."""
        assert bucket_key(date(2026, 1, 7), "week") == "2026-01-05"
        assert bucket_key(date(2026, 1, 11), "week") == "2026-01-05"
        assert bucket_key(date(2026, 1, 12), "week") == "2026-01-12"

    @pytest.mark.parametrize("period", PERIODS)
    def test_every_declared_period_is_accepted(self, period: str) -> None:
        assert summarise_cashflow([(date(2026, 1, 5), EUR("1"))], period)

    def test_an_unknown_period_is_refused_rather_than_defaulted(self) -> None:
        """Silently bucketing on a typo returns a series that looks right and is
        on the wrong axis, which is the failure this guard exists to prevent."""
        with pytest.raises(ValueError, match="Unknown period"):
            summarise_cashflow([(date(2026, 1, 5), EUR("1"))], "quater")

    def test_exactly_zero_is_not_income_or_expense(self) -> None:
        """A reversed entry nets to zero and belongs in neither column."""
        assert summarise_cashflow([(date(2026, 1, 5), EUR("0"))], "month") == []


def test_cashflow_point_from_signed_negates_expense() -> None:
    point = CashflowPoint.from_signed("2026-01", EUR("100"), EUR("-40"))
    assert point == CashflowPoint(
        period="2026-01", income=10000, expense=4000, net=6000
    )
