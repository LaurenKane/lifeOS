"""Unit tests for the reflection shaping: window math and max-3 shaping.

Pure function tests — no session, no HTTP. The route's SQL pre-filter
delegates the window edges to `shape_reflection` on purpose, so the boundary
moments (exactly 30 days back, one second older) are provable here first.
"""

from __future__ import annotations

import datetime as dt

from life.domain.services.reflection import (
    DoneActionFact,
    GoalFact,
    ReceiptFact,
    UpkeepFact,
    reflection_window,
    shape_reflection,
)

NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)


def ago(**units: int) -> dt.datetime:
    return NOW - dt.timedelta(**units)


def goal_fact(
    goal_id: int,
    *,
    is_active: bool = True,
    created_at: dt.datetime | None = None,
) -> GoalFact:
    return GoalFact(
        goal_id=goal_id,
        title=f"goal {goal_id}",
        area=None,
        created_at=created_at or NOW,
        is_active=is_active,
    )


def done(goal_id: int, text: str, at: dt.datetime) -> DoneActionFact:
    return DoneActionFact(goal_id=goal_id, text=text, done_at=at)


class TestWindowMath:
    def test_the_window_is_thirty_rolling_days(self) -> None:
        """Rolling, not calendar: "last 30 days" means now minus 30 days,
        whatever today's date is called."""
        assert reflection_window(NOW) == ago(days=30)

    def test_done_exactly_at_the_window_start_is_in(self) -> None:
        """Inclusive lower edge: a step done the moment the window opens
        still counts."""
        row = shape_reflection(
            [goal_fact(1)],
            [],
            [done(1, "old but in", reflection_window(NOW))],
            [],
            now_local=NOW,
        )
        assert row.goals[0].count == 1

    def test_done_one_second_older_is_out(self) -> None:
        row = shape_reflection(
            [goal_fact(1)],
            [],
            [
                done(
                    1,
                    "just outside",
                    reflection_window(NOW) - dt.timedelta(seconds=1),
                )
            ],
            [],
            now_local=NOW,
        )
        assert row.goals == []

    def test_done_after_now_is_out(self) -> None:
        """A future-dated done (a backdate that overshoots) does not belong
        to a window that ends at now — the same historiography as
        `away_days`' 0 floor."""
        row = shape_reflection(
            [goal_fact(1)],
            [],
            [done(1, "from tomorrow", ago(days=-1))],
            [],
            now_local=NOW,
        )
        assert row.goals == []

    def test_done_29_days_back_is_in(self) -> None:
        row = shape_reflection(
            [goal_fact(1)],
            [],
            [done(1, "well inside", ago(days=29))],
            [],
            now_local=NOW,
        )
        assert row.goals[0].count == 1


class TestGoalShaping:
    def test_only_entries_with_count_at_least_one_appear(self) -> None:
        """Zero is not a data point here: a goal with nothing done in the
        window is absent, and its absence is the whole answer about it."""
        actionless = 4
        row = shape_reflection(
            [goal_fact(1), goal_fact(actionless)],
            [],
            [done(1, "walked", ago(days=1))],
            [],
            now_local=NOW,
        )
        assert [entry.goal_id for entry in row.goals] == [1]

    def test_a_paused_goal_is_resting_and_absent(self) -> None:
        """Paused is resting: even with activity in the window a paused goal
        keeps off the reflection board; it stays visible on its own page."""
        resting = 2
        row = shape_reflection(
            [goal_fact(resting, is_active=False)],
            [],
            [done(resting, "done while awake", ago(days=1))],
            [],
            now_local=NOW,
        )
        assert row.goals == []

    def test_at_most_three_texts_newest_first(self) -> None:
        five_done = [
            done(1, "oldest", ago(hours=5)),
            done(1, "older", ago(hours=4)),
            done(1, "middle", ago(hours=3)),
            done(1, "second", ago(hours=2)),
            done(1, "youngest", ago(hours=1)),
        ]
        row = shape_reflection([goal_fact(1)], [], five_done, [], now_local=NOW)
        assert row.goals[0].count == 5
        assert row.goals[0].done_texts == ["youngest", "second", "middle"]

    def test_texts_are_words_only_dates_stay_out(self) -> None:
        """The examples are quiet reference for the count; when a thing was
        done is an input fact, not part of the shaped headline."""
        row = shape_reflection(
            [goal_fact(1)],
            [],
            [done(1, "cleaned the jars", ago(days=2))],
            [],
            now_local=NOW,
        )
        assert row.goals[0].done_texts == ["cleaned the jars"]
        assert all(isinstance(text_part, str) for text_part in row.goals[0].done_texts)

    def test_equal_counts_keep_creation_order(self) -> None:
        first = goal_fact(7, created_at=ago(days=10))
        second = goal_fact(3, created_at=ago(days=5))
        row = shape_reflection(
            [first, second],
            [],
            [
                done(7, "the older goal", ago(hours=1)),
                done(3, "the newer goal", ago(hours=1)),
            ],
            [],
            now_local=NOW,
        )
        assert [entry.goal_id for entry in row.goals] == [7, 3]

    def test_bigger_count_comes_first(self) -> None:
        late_created = goal_fact(1, created_at=ago(days=1))
        early_created = goal_fact(2, created_at=ago(days=9))
        row = shape_reflection(
            [late_created, early_created],
            [],
            [
                done(1, "the slow goal", ago(hours=1)),
                done(2, "a", ago(hours=2)),
                done(2, "b", ago(hours=3)),
            ],
            [],
            now_local=NOW,
        )
        assert [entry.goal_id for entry in row.goals] == [2, 1]


class TestUpkeepShaping:
    def test_receipts_count_per_active_upkeep(self) -> None:
        row = shape_reflection(
            [],
            [UpkeepFact(9, "water the plants", NOW, True)],
            [],
            [ReceiptFact(9), ReceiptFact(9), ReceiptFact(9)],
            now_local=NOW,
        )
        assert [
            (entry.upkeep_id, entry.title, entry.count) for entry in row.upkeeps
        ] == [(9, "water the plants", 3)]

    def test_receipts_for_a_resting_upkeep_do_not_count_as_its_row(
        self,
    ) -> None:
        row = shape_reflection(
            [],
            [UpkeepFact(9, "resting upkeep", NOW, False)],
            [],
            [ReceiptFact(9)],
            now_local=NOW,
        )
        assert row.upkeeps == []

    def test_a_receipt_for_nothing_known_cannot_conjure_a_row(self) -> None:
        """A receipt whose upkeep the route did not pass (an archive that
        raced the read) must not invent an entry out of nothing."""
        row = shape_reflection([], [], [], [ReceiptFact(9)], now_local=NOW)
        assert row.upkeeps == []
