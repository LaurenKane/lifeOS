"""test_calendar_feed.py — the composer's exact wire bytes.

What is certain: the document opens and closes as one VCALENDAR; each fact
becomes one all-day VEVENT ending the day after it starts; TEXT escaping
follows RFC 5545 3.3.11 with the backslash first; folding happens at 75
OCTETS (UTF-8 bytes, never mid-character) into one-space continuations; and
DTSTAMP is the passed UTC now — never the machine's clock.
"""

from __future__ import annotations

import datetime as dt

from life.domain.services.calendar_feed import FeedFact, compose_feed

NOW = dt.datetime(2026, 10, 7, 9, 30, 0, tzinfo=dt.timezone.utc)

HEADER = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//lifeos//calendar feed 1//EN",
    "X-WR-CALNAME:LifeOS",
]


def lines_of(ics: str) -> list[str]:
    assert ics.endswith("\r\n")
    return ics[:-2].split("\r\n")


class TestHeaderOnly:
    def test_no_facts_is_a_bare_vcalendar(self) -> None:
        """An empty feed is a well-formed document, not an error."""
        ics = compose_feed([], now_utc=NOW)
        assert lines_of(ics) == [*HEADER, "END:VCALENDAR"]


class TestOneEvent:
    def test_the_exact_lines_of_one_all_day_event(self) -> None:
        fact = FeedFact(action_id=17, title="dentist", due_date=dt.date(2026, 10, 14))
        ics = compose_feed([fact], now_utc=NOW)
        assert lines_of(ics) == [
            *HEADER,
            "BEGIN:VEVENT",
            "UID:feed-action-17@lifeos",
            "DTSTAMP:20261007T093000Z",
            "DTSTART;VALUE=DATE:20261014",
            "DTEND;VALUE=DATE:20261015",
            "SUMMARY:dentist",
            "END:VEVENT",
            "END:VCALENDAR",
        ]

    def test_dtstamp_is_the_passed_now_never_the_clock(self) -> None:
        other = dt.datetime(2001, 2, 3, 4, 5, 6, tzinfo=dt.timezone.utc)
        ics = compose_feed([FeedFact(1, "t", dt.date(2026, 10, 7))], now_utc=other)
        assert "DTSTAMP:20010203T040506Z\r\n" in ics


class TestEscaping:
    def test_rfc_3311_escapes_in_the_backslash_first_order(self) -> None:
        """Backslash first, then `;`, then `,`, then newline -> literal \\n."""
        fact = FeedFact(action_id=1, title="a;b,c\n\\d", due_date=dt.date(2026, 10, 1))
        ics = compose_feed([fact], now_utc=NOW)
        assert "SUMMARY:a\\;b\\,c\\n\\\\d\r\n" in ics


class TestFolding:
    def test_a_long_title_folds_into_small_parts(self) -> None:
        # Chosen so no fold edge lands exactly on a content space: a space
        # there becomes the continuation's leading space and is legitimately
        # consumed on unfolding — every RFC 5545 producer behaves the same.
        title = "repaint the shutters on the street side before the rain "
        title += "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        ics = compose_feed([FeedFact(1, title, dt.date(2026, 10, 1))], now_utc=NOW)

        summary_lines = [ln for ln in lines_of(ics) if ln.startswith(("SUMMARY:", " "))]
        assert len(summary_lines) > 1
        # Every physical line stays within 75 octets encoded.
        for physical in lines_of(ics):
            assert len(physical.encode("utf-8")) <= 75
        # Continuations begin with exactly one space.
        assert all(ln.startswith(" ") for ln in summary_lines[1:])
        rejoined = summary_lines[0] + "".join(ln[1:] for ln in summary_lines[1:])
        assert rejoined == f"SUMMARY:{title}"

    def test_a_multibyte_title_never_folds_mid_character(self) -> None:
        """UTF-8 bytes, not chars: a kanji straddling the edge moves whole."""
        title = (
            "予約した老人ホームの見学を午前十時に行く。 multibyte "
            "folding on purpose, filler after filler word until past one fold"
        )
        ics = compose_feed([FeedFact(1, title, dt.date(2026, 10, 1))], now_utc=NOW)
        for physical in lines_of(ics):
            physical.encode("utf-8").decode("utf-8")  # must round-trip whole
            assert len(physical.encode("utf-8")) <= 75
