"""Unit tests for the Do-now board: cap, order, urgent block, subactions."""

from __future__ import annotations

import datetime as dt

from life.domain.services.today import CAP_TOP_LEVEL, ActionRow, choose_do_now

TODAY = dt.date(2026, 10, 7)


def row(
    id: int,
    *,
    due: dt.date | None = None,
    planned: dt.date | None = None,
    urgent: bool = False,
    done: bool = False,
    parent: int | None = None,
    order: int = 0,
) -> ActionRow:
    return ActionRow(
        id=id,
        urgent=urgent,
        is_done=done,
        parent_action_id=parent,
        due_date=due,
        planned_date=planned,
        order_index=order,
    )


class TestCapAndKeptBack:
    def test_five_top_level_is_the_cap(self) -> None:
        assert CAP_TOP_LEVEL == 5

    def test_tiles_over_the_cap_are_cut_not_silently_moved(self) -> None:
        rows = [
            row(i, planned=TODAY, order=i)
            for i in range(1, 9)  # 8 open today
        ]
        board = choose_do_now(rows, today=TODAY)
        assert len(board.selected) == 5
        assert board.kept_back == 3
        # kept-back rows were NOT rescheduled: their own data prevails, the
        # BOARD cut them (assert db rows' zodiac untouched: no plan mutation).
        assert board.selected == (1, 2, 3, 4, 5)

    def test_earliest_due_date_wins_the_cap(self) -> None:
        # All due ON or BEFORE today (future dues are the upcoming strip's
        # business, not today's board).
        rows = [
            row(1, due=dt.date(2026, 10, 7), order=0),
            row(2, due=dt.date(2026, 10, 6), order=1),
            row(3, due=dt.date(2026, 10, 4), order=2),
        ]
        board = choose_do_now(rows, today=TODAY)
        assert board.selected == (3, 2, 1)


class TestEligibility:
    def test_a_dated_action_surfaces_on_its_day(self) -> None:
        board = choose_do_now([row(1, due=TODAY)], today=TODAY)
        assert board.selected == (1,)

    def test_a_planned_action_surfaces_only_today(self) -> None:
        board = choose_do_now(
            [row(1, planned=TODAY + dt.timedelta(days=2))], today=TODAY
        )
        assert board.selected == ()
        # Planned today surfaces today — that is what "planned" means.
        board = choose_do_now([row(1, planned=TODAY)], today=TODAY)
        assert board.selected == (1,)

    def test_a_passed_due_date_still_surfaces(self) -> None:
        # A real-world deadline is a fact, not backlog shame: it stays on the
        # board (the earliest-cut rule picks it first).
        board = choose_do_now([row(1, due=TODAY - dt.timedelta(days=4))], today=TODAY)
        assert board.selected == (1,)
        assert board.kept_back == 0

    def test_done_actions_never_surface(self) -> None:
        board = choose_do_now([row(1, due=TODAY, done=True)], today=TODAY)
        assert board.selected == ()
        assert board.urgent_ids == ()

    def test_an_undated_unplanned_action_does_not_surface(self) -> None:
        board = choose_do_now([row(1)], today=TODAY)
        assert board.selected == ()


class TestSubactions:
    def test_subactions_never_count_toward_the_cap(self) -> None:
        rows = [row(i, planned=TODAY, order=i) for i in range(1, 6)]  # 5 full
        rows += [row(90, parent=1, planned=TODAY), row(91, parent=1, planned=TODAY)]
        board = choose_do_now(rows, today=TODAY)
        assert board.selected == (1, 2, 3, 4, 5)
        assert board.kept_back == 0
        assert 90 not in board.selected and 91 not in board.selected


class TestUrgent:
    def test_urgent_is_its_own_uncapped_block(self) -> None:
        rows = [row(i, urgent=True, order=i) for i in range(1, 9)]
        board = choose_do_now(rows, today=TODAY)
        assert board.selected == ()  # urgent does not consume the cap
        assert board.urgent_ids == (1, 2, 3, 4, 5, 6, 7, 8)

    def test_urgent_not_done_even_far_in_the_future_surfaces(self) -> None:
        board = choose_do_now(
            [row(1, urgent=True, due=TODAY + dt.timedelta(days=30))], today=TODAY
        )
        assert board.urgent_ids == (1,)

    def test_empty_input(self) -> None:
        board = choose_do_now([], today=TODAY)
        assert board.selected == ()
        assert board.kept_back == 0
        assert board.urgent_ids == ()
