"""Unit tests for away_days: whole days of silence, edge first."""

from __future__ import annotations

import datetime as dt

from life.domain.services.away import away_days

NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)


def ago(**units: int) -> dt.datetime:
    return NOW - dt.timedelta(**units)


class TestAwayDays:
    def test_no_activity_ever_is_zero(self) -> None:
        """None means "never showed up" — and the strip only speaks from
        ~2 days of silence, never from a fact like "nothing recorded yet".
        The 0 keeps the Dashboard's half empty rather than inventing a gap."""
        assert away_days(None, now_local=NOW) == 0

    def test_less_than_one_day_is_zero(self) -> None:
        assert away_days(ago(hours=5), now_local=NOW) == 0
        assert away_days(ago(minutes=1), now_local=NOW) == 0

    def test_exactly_one_day_is_one(self) -> None:
        assert away_days(ago(days=1), now_local=NOW) == 1

    def test_whole_days_count_up(self) -> None:
        assert away_days(ago(days=3), now_local=NOW) == 3
        assert away_days(ago(days=30), now_local=NOW) == 30

    def test_a_day_and_leftovers_floor_to_the_day(self) -> None:
        assert away_days(ago(days=3, hours=2), now_local=NOW) == 3

    def test_the_same_moment_is_zero_not_negative(self) -> None:
        assert away_days(NOW, now_local=NOW) == 0

    def test_backwards_activity_does_not_read_as_away(self) -> None:
        """A timestamp the clock says is later than `now` (skew, a backdate
        that overshoots) is 0, never a negative count."""
        assert away_days(ago(days=-1), now_local=NOW) == 0
