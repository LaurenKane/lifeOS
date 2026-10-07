"""Unit tests for compose_digest: grammar, order, and the empty day."""

from __future__ import annotations

import datetime as dt

from life.domain.services.digest import (
    DigestFacts,
    UpcomingFact,
    compose_digest,
)

TODAY = dt.date(2026, 10, 7)  # a Wednesday, like the title example

# The vocabulary law (GLOSSARY.md): none of these words may appear in a
# pushed message — the digest carries facts, never a judgment.
BANNED_WORDS = ("overdue", "streak", "habit", "backlog")


def facts(
    *,
    do_now: list[str] | None = None,
    urgent: list[str] | None = None,
    kept_back: int = 0,
    upkeep: list[str] | None = None,
    upcoming: list[UpcomingFact] | None = None,
) -> DigestFacts:
    return DigestFacts(
        do_now=do_now if do_now is not None else [],
        urgent=urgent if urgent is not None else [],
        kept_back=kept_back,
        upkeep_opportunities=upkeep if upkeep is not None else [],
        upcoming=upcoming if upcoming is not None else [],
    )


def compose(**kwargs: object) -> str:
    """body of compose_digest on the given facts."""
    f = facts(**kwargs)  # type: ignore[arg-type]
    return compose_digest(f, today_local=TODAY).body


class TestTitle:
    def test_the_title_names_the_app_and_the_local_day(self) -> None:
        message = compose_digest(facts(), today_local=TODAY)
        assert message.title == "LifeOS · Wed 7 Oct"

    def test_the_title_is_not_the_empty_day_talk(self) -> None:
        message = compose_digest(facts(), today_local=dt.date(2026, 12, 1))
        assert message.title.startswith("LifeOS · ")

    def test_upcoming_wording_names_the_day_textually(self) -> None:
        body = compose(
            upcoming=[
                UpcomingFact(text="register the bike", due_date=dt.date(2026, 10, 14))
            ]
        )
        assert 'upcoming: "register the bike" on Wed 14 Oct' in body


class TestEmptyDay:
    def test_an_empty_day_composes_the_dashboard_line_exactly(self) -> None:
        assert compose() == "Nothing assigned to today — good."


class TestCountLine:
    def test_one_thing_reads_singled(self) -> None:
        assert compose(do_now=["put the desk together"]).startswith(
            "1 thing on today\n\n- put the desk together"
        )

    def test_several_things_read_plural(self) -> None:
        lines = compose(do_now=["a", "b", "c"])
        assert lines.startswith("3 things on today\n\n- a\n- b\n- c")

    def test_kept_back_is_appended_to_the_count(self) -> None:
        body = compose(do_now=["a", "b", "c"], kept_back=2)
        assert body.startswith("3 things on today · 2 kept back\n\n- a")

    def test_zero_kept_back_is_not_mentioned(self) -> None:
        body = compose(do_now=["a"])
        assert "kept back" not in body


class TestSections:
    def test_urgent_is_its_own_section(self) -> None:
        body = compose(do_now=["a"], urgent=["fix the door"])
        assert "urgent: fix the door" in body

    def test_upkeep_open_carries_the_aim_the_caller_baked_in(self) -> None:
        body = compose(do_now=["a"], upkeep=["water the plants (aim 3 days)"])
        assert "upkeep open: water the plants (aim 3 days)" in body

    def test_sections_appear_in_the_fixed_order(self) -> None:
        body = compose(
            do_now=["a"],
            urgent=["fix the door"],
            upkeep=["water the plants (aim 3 days)"],
            upcoming=[
                UpcomingFact(text="register the bike", due_date=dt.date(2026, 10, 14))
            ],
        )
        assert body.index("1 thing on today") < body.index("urgent:")
        assert body.index("urgent:") < body.index("upkeep open:")
        assert body.index("upkeep open:") < body.index("upcoming:")

    def test_sections_are_separated_by_blank_lines(self) -> None:
        body = compose(
            do_now=["a"],
            urgent=["fix the door"],
        )
        assert "\n\nurgent:" in body

    def test_urgent_alone_composes_without_a_count_line(self) -> None:
        body = compose(urgent=["fix the door"])
        assert body == "urgent: fix the door"


class TestVocabulary:
    def test_no_judgment_word_appears_in_any_composed_body(self) -> None:
        bodies = [
            compose(),
            compose(do_now=["put the desk together"], kept_back=2),
            compose(urgent=["fix the door"]),
            compose(upkeep=["water the plants (aim 3 days)"]),
            compose(
                upcoming=[
                    UpcomingFact(
                        text="register the bike", due_date=dt.date(2026, 10, 14)
                    )
                ]
            ),
        ]
        for body in bodies:
            lowered = body.lower()
            for word in BANNED_WORDS:
                assert word not in lowered
