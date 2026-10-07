"""Unit tests for upkeep cadence math and the next-opportunity rule.

The Dashboard's aging dot is computed from these pure functions — the shapes
and thresholds (Q26) live here so the Dashboard cannot drift from them."""

from __future__ import annotations

import datetime as dt

from life.domain.services.upkeep_state import (
    MAX_OVERSIGHT_CATCH_UP_DAYS,
    earned_cadence_days,
    last_done_days,
    next_opportunity,
    visible_on_today,
)

THU_DATA = dt.datetime(2026, 10, 8, 9, 5, tzinfo=dt.timezone.utc)


class TestEarnedCadence:
    def test_fewer_than_two_receipts_is_no_evidence(self) -> None:
        assert earned_cadence_days([]) is None
        assert earned_cadence_days([THU_DATA]) is None

    def test_median_not_mean(self) -> None:
        # Gaps of 7, 7, 3, 7: mean 6 — but the typical gap is 7.
        moments = [
            THU_DATA,
            THU_DATA + dt.timedelta(days=7),
            THU_DATA + dt.timedelta(days=14),
            THU_DATA + dt.timedelta(days=17),
            THU_DATA + dt.timedelta(days=24),
        ]
        assert earned_cadence_days(moments) == 7

    def test_double_tap_is_one_gap_of_zero(self) -> None:
        # Two receipts in the same minute; the result rounds up to 1 day.
        moments = [THU_DATA, THU_DATA]
        assert earned_cadence_days(moments) == 1

    def test_never_below_one(self) -> None:
        moments = [THU_DATA, THU_DATA + dt.timedelta(hours=2)]
        assert earned_cadence_days(moments) == 1

    def test_moments_may_be_out_of_order(self) -> None:
        # Receipts at day 0, 7, 14 in scrambled order: gaps 7, 7 → median 7.
        moments = [
            THU_DATA + dt.timedelta(days=14),
            THU_DATA,
            THU_DATA + dt.timedelta(days=7),
        ]
        assert earned_cadence_days(moments) == 7


class TestNextOpportunity:
    def test_no_cadence_no_invention(self) -> None:
        # Without an aim or an earned cadence, the answer is None — never a
        # made-up default (product voice: no number without evidence).
        assert next_opportunity(THU_DATA, None) is None
        assert next_opportunity(None, 7) is None

    def test_last_done_plus_cadence(self) -> None:
        next_day = next_opportunity(THU_DATA - dt.timedelta(days=7), 7)
        assert next_day == THU_DATA.date()


class TestVisibleOnToday:
    TODAY = dt.date(2026, 10, 6)

    def test_opportunity_today_is_visible(self) -> None:
        assert visible_on_today(self.TODAY, today=self.TODAY)

    def test_missed_by_two_days_is_still_visible(self) -> None:
        assert visible_on_today(
            self.TODAY - dt.timedelta(days=MAX_OVERSIGHT_CATCH_UP_DAYS),
            today=self.TODAY,
        )

    def test_missed_longer_fades_to_the_upkeep_page(self) -> None:
        miss = MAX_OVERSIGHT_CATCH_UP_DAYS + 1
        assert not visible_on_today(
            self.TODAY - dt.timedelta(days=miss), today=self.TODAY
        )

    def test_no_opportunity_never_appears(self) -> None:
        assert not visible_on_today(None, today=self.TODAY)

    def test_a_future_opportunity_is_not_today_business(self) -> None:
        assert not visible_on_today(self.TODAY + dt.timedelta(days=3), today=self.TODAY)


class TestLastDoneDays:
    def test_none_is_a_state_not_zero(self) -> None:
        assert last_done_days(None, now=THU_DATA) is None

    def test_days_since(self) -> None:
        assert (
            last_done_days(THU_DATA - dt.timedelta(days=6, hours=3), now=THU_DATA) == 6
        )

    def test_never_negative(self) -> None:
        assert last_done_days(THU_DATA + dt.timedelta(hours=1), now=THU_DATA) == 0
