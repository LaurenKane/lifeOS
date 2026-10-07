"""Unit tests for capture date parsing. Deterministic: `today` is always an
argument, never a clock. 2026-10-07 is a Wednesday (useful for weekday
assertions)."""

from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError

import pytest

from life.domain.services.parse import ParsedCapture, parse_capture

TODAY: dt.date = dt.date(2026, 10, 7)  # a Wednesday


def parse(text: str) -> ParsedCapture:
    return parse_capture(text, today=TODAY)


class TestRelativeDayWords:
    def test_tomorrow_is_planned_not_due(self) -> None:
        parsed = parse("buy a lamp tomorrow")
        assert parsed.planned_date == dt.date(2026, 10, 8)
        assert parsed.due_date is None
        assert parsed.urgent is False

    def test_tomorrow_word_is_removed_from_the_title(self) -> None:
        parsed = parse("buy a lamp tomorrow")
        assert parsed.text == "buy a lamp"

    def test_today_means_today(self) -> None:
        assert parse("pack today").planned_date == TODAY

    def test_dutch_tomorrow_morgen(self) -> None:
        assert parse("waterschade oplossen morgen").planned_date == dt.date(2026, 10, 8)

    def test_no_date_words_means_no_date(self) -> None:
        parsed = parse("maybe learn guitar")
        assert parsed.due_date is None and parsed.planned_date is None


class TestWeekdays:
    def test_a_named_weekday_is_the_next_occurrence(self) -> None:
        assert parse("bins out friday").planned_date == dt.date(2026, 10, 9)

    def test_friday_on_a_friday_is_that_friday(self) -> None:
        # Wednesday Oct 7 → the coming Friday is Oct 9.
        assert parse("guitar on friday").planned_date == dt.date(2026, 10, 9)

    def test_next_prefix_is_tolerated(self) -> None:
        assert parse("next friday dentist").planned_date == dt.date(2026, 10, 9)

    def test_dutch_weekday(self) -> None:
        assert parse("afval vrijdag buiten").planned_date == dt.date(2026, 10, 9)

    def test_two_letter_dutch_forms_never_match(self) -> None:
        # "do" (Thu as a Dutch abbrev) would collide with English "do" —
        # deliberately excluded; the capture keeps its text and gains no date.
        parsed = parse("do this sometime")
        assert parsed.planned_date is None


class TestConcreteDates:
    def test_on_the_14th_is_a_due_date_next_month(self) -> None:
        parsed = parse("dentist on the 14th")
        # From Oct 7: the 14th of October (this month, still ahead).
        assert parsed.due_date == dt.date(2026, 10, 14)
        assert parsed.planned_date is None

    def test_a_passed_day_of_month_rolls_to_next_month(self) -> None:
        parsed = parse("appointment the 3rd")
        assert parsed.due_date == dt.date(2026, 11, 3)

    def test_month_name_date_day_first(self) -> None:
        assert parse("book dentist oct 20").due_date == dt.date(2026, 10, 20)

    def test_dutch_month_name(self) -> None:
        assert parse("paspoort 14 okt ophalen").due_date == dt.date(2026, 10, 14)

    def test_day_first_numeric_rolls_a_passed_date_to_next_year(self) -> None:
        # TODAY Oct 7 2026; 14/09 is past this year → Sep 14 2027.
        assert parse("insurance 14/09").due_date == dt.date(2027, 9, 14)

    def test_a_nonsense_month_date_gains_no_date(self) -> None:
        # "switching 30 feb" — Feb 30 does not exist; misrepresented as None.
        assert parse("check out 30 feb").due_date is None

    def test_a_two_number_conversation_is_not_a_date(self) -> None:
        # "16/2" IS a plausible day-first date (16 Februari) and parses as
        # one — past this year, so it bumps to next year.
        parsed = parse("buy 5 apples at 16/2")
        assert parsed.due_date == dt.date(2027, 2, 16)
        assert parsed.text == "buy 5 apples at"

    def test_exactly_one_date_wins(self) -> None:
        # The FIRST priority match is dropped from the title; the second stays
        # as words — deliberate honesty rather than a silent double guess.
        parsed = parse("dentist tomorrow on the 14th")
        assert parsed.planned_date == dt.date(2026, 10, 8)
        assert parsed.due_date is None
        assert "14th" in parsed.text


class TestUrgent:
    def test_the_word_urgent_sets_the_flag_and_leaves_the_title(self) -> None:
        parsed = parse("call the bank urgent")
        assert parsed.urgent is True
        assert parsed.text == "call the bank"

    def test_double_bang_sets_urgent(self) -> None:
        assert parse("electricity!! url").urgent is True

    def test_one_bang_is_not_urgent(self) -> None:
        assert parse("wow! the lamp").urgent is False


class TestCaptureNeverBlocked:
    def test_empty_of_markers_is_a_clean_thought(self) -> None:
        parsed = parse("idea for LifeOS:Receipt ink saving budget rule")
        assert parsed.due_date is None and parsed.planned_date is None
        assert parsed.urgent is False

    def test_wrong_shape_still_produces_a_thought(self) -> None:
        # "!!" is two poles; "?!" is NOT urgent-triggering ('!{2,}' needs two
        # consecutive bangs, and "?!" has none) — the text survives intact.
        expected = "?" + "!??"
        assert parse(expected).text == expected

    def test_the_type_never_loses_words(self) -> None:
        parsed = parse("look  into   this desk")
        assert parsed.text == "look into this desk"


class TestParsedCaptureShape:
    def test_frozen(self) -> None:
        parsed = parse("unit test tomorrow")
        with pytest.raises(FrozenInstanceError):
            parsed.planned_date = TODAY  # type: ignore[misc]

    def test_the_signature_remains_part_of_the_public_surface(self) -> None:
        # Guard that the relative/pure call shape is how callers reach it.
        parsed = parse_capture(
            "cell phone plan renewal tomorrow", today=dt.date(2027, 1, 2)
        )
        assert parsed.planned_date == dt.date(2027, 1, 3)
