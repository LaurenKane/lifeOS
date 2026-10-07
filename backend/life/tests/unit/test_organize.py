"""Unit tests for the organize contract — prompt text and parse boundary.

No network, no provider: `build_prompt` is asserted as the string it is,
and `parse_suggestions` is asserted against the kinds of answer a model
actually produces (fences, prose, hallucinated ids, invented dates).
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from life.domain.services.organize import (
    Suggestion,
    ThoughtFact,
    build_prompt,
    parse_suggestions,
)

TODAY = dt.date(2026, 10, 7)

FACTS = [
    ThoughtFact(
        thought_id=7,
        text="call the dentist tomorrow",
        created_at=dt.datetime(2026, 10, 6, 9, 0, tzinfo=dt.UTC),
    ),
    ThoughtFact(
        thought_id=9,
        text="water the plants",
        created_at=dt.datetime(2026, 10, 5, 20, 0, tzinfo=dt.UTC),
    ),
]

ID7 = {"thought_id": 7, "due_date": "", "urgent": False, "why": ""}


def _entry(choice: object, **extra: object) -> str:
    entry: dict[str, object] = dict(ID7)
    entry["choice"] = choice
    entry.update(extra)
    return json.dumps([entry])


def _action_entry(**extra: object) -> str:
    return _entry("action", **extra)


@pytest.fixture
def prompt() -> str:
    return build_prompt(FACTS, today_local=TODAY)


class TestPrompt:
    def test_every_thought_text_and_id_appear_verbatim(self, prompt: str) -> None:
        assert '"call the dentist tomorrow"' in prompt
        assert '"water the plants"' in prompt
        assert "- id 7:" in prompt
        assert "- id 9:" in prompt

    def test_the_four_choices_appear_verbatim(self, prompt: str) -> None:
        for choice in ("action", "upkeep", "keep", "dismiss"):
            assert f'"{choice}"' in prompt

    def test_today_is_named_so_dates_are_derivable(self, prompt: str) -> None:
        assert "Today is 2026-10-07" in prompt

    def test_the_prompt_contains_no_judgment_words(self, prompt: str) -> None:
        """Vocabulary law, exact: the words the tracker refuses are absent
        from the only text the helper ever sees."""
        for banned in ("overdue", "streak", "habit", "backlog"):
            assert banned not in prompt.lower()


class TestParse:
    def test_a_plain_array_parses_to_suggestions(self) -> None:
        raw = _action_entry(due_date="2026-10-08", urgent=True, why="named")
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions == [
            Suggestion(
                thought_id=7,
                choice="action",
                due_date=dt.date(2026, 10, 8),
                urgent=True,
                why="named",
            )
        ]

    def test_a_fenced_answer_parses(self) -> None:
        raw = "```json\n" + _entry("upkeep", why="recurring") + "\n```"
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert len(suggestions) == 1
        assert suggestions[0].choice == "upkeep"
        assert suggestions[0].due_date is None

    def test_prose_wrapped_json_parses(self) -> None:
        raw = "Here is what I think:\n" + _entry("dismiss") + "\nHope that helps."
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert [s.choice for s in suggestions] == ["dismiss"]

    def test_unknown_thought_ids_are_dropped(self) -> None:
        raw = json.dumps([{"thought_id": 99, "choice": "keep"}])
        assert parse_suggestions(raw, FACTS, today_local=TODAY) == []

    def test_off_vocabulary_choices_are_dropped(self) -> None:
        assert parse_suggestions(_entry("goal"), FACTS, today_local=TODAY) == []
        assert parse_suggestions(_entry(3), FACTS, today_local=TODAY) == []

    def test_choice_case_is_normalized(self) -> None:
        raw = _entry("Keep")
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert [s.choice for s in suggestions] == ["keep"]

    def test_a_date_before_today_is_dropped_not_kept(self) -> None:
        raw = _action_entry(due_date="2026-10-01")
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions[0].due_date is None
        assert suggestions[0].choice == "action"

    def test_a_date_of_today_itself_parses(self) -> None:
        raw = _action_entry(due_date=TODAY.isoformat())
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions[0].due_date == TODAY

    def test_an_unparseable_date_is_dropped(self) -> None:
        raw = _action_entry(due_date="the 20th")
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions[0].due_date is None

    def test_a_long_why_is_truncated_to_120_characters(self) -> None:
        raw = _action_entry(why="w" * 300)
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions[0].why == "w" * 120

    def test_urgent_as_a_string_only_reads_true_literally(self) -> None:
        raw = _action_entry(urgent="true")
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions[0].urgent is True
        raw = _action_entry(urgent=1)
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert suggestions[0].urgent is False

    def test_nothing_parses_to_no_suggestions(self) -> None:
        assert parse_suggestions("no JSON at all", FACTS, today_local=TODAY) == []
        assert parse_suggestions("", FACTS, today_local=TODAY) == []
        assert parse_suggestions("[]", FACTS, today_local=TODAY) == []

    def test_non_dicts_in_the_array_are_dropped(self) -> None:
        raw = json.dumps([17, {"thought_id": 7, "choice": "keep"}])
        suggestions = parse_suggestions(raw, FACTS, today_local=TODAY)
        assert [s.choice for s in suggestions] == ["keep"]
