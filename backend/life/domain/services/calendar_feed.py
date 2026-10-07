"""compose_feed — RFC 5545 text for the fenced ICS calendar feed.

Pure function; the route gathers the facts and passes in `now_utc` so this
helper stays out of any clock's business. Per ADR 0013 the feed carries dates
and titles and nothing else: every open Action with a `due_date` becomes one
all-day `VEVENT` (DTSTART on the due day, DTEND the next, per RFC 5545's
all-day convention), ordered however the caller lists them.

DESCRIPTION is withheld on purpose (ADR 0013): dates live in a calendar, the
pile does not — no thoughts, no goals' why, no upkeep cadence reaches the
wire. SUMMARY is the title as written, escaped per RFC 5545 section 3.3.11.

Line endings are CRLF, not the module's usual LF: RFC 5545 names CRLF the
delimiter on the wire and the exact bytes are asserted by the unit test.
Physical lines fold at 75 OCTETS (section 3.1) into continuation lines that
begin with one space, cutting only at UTF-8 character boundaries so a
multibyte char is never split mid-sequence.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["FeedFact", "compose_feed"]

#: The fold limit in encoded octets (RFC 5545 section 3.1). Bytes, not chars
#: — a title with non-ASCII letters folds earlier than it looks.
FOLD_OCTETS: int = 75

#: CRLF, per RFC 5545's wire grammar.
CRLF: str = "\r\n"


@dataclass(frozen=True)
class FeedFact:
    """One dated Action, stripped to exactly what the calendar may say."""

    action_id: int
    title: str
    due_date: dt.date


def _escape(text: str) -> str:
    """Escape TEXT per RFC 5545 section 3.3.11, backslash first."""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def _fold(line: str) -> list[str]:
    """Split one logical line into physical lines of at most 75 octets.

    The count is over the line's UTF-8 encoding, and the cut moves back to a
    character boundary when a multibyte char straddles the limit; each
    continuation line adds one leading space, which counts against its own
    75 (section 3.1 — so a continuation carries at most 74 content octets).
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= FOLD_OCTETS:
        return [line]

    physical: list[str] = []
    start = 0
    first = True
    total = len(encoded)
    while start < total:
        budget = FOLD_OCTETS if first else FOLD_OCTETS - 1
        cut = start + budget
        if cut >= total:
            cut = total
        else:
            # Do not split a character in the middle of its encoding: while
            # `encoded[cut]` is a continuation octet, it sits inside a
            # character that begins before it — move the cut back.
            while cut > start and (encoded[cut] & 0b1100_0000) == 0b1000_0000:
                cut -= 1
            if cut == start:  # single char longer than the budget: impossible
                cut = start + budget
        physical.append(encoded[start:cut].decode("utf-8"))
        if not first:  # RFC 3.1: every continuation line begins with one space.
            physical[-1] = " " + physical[-1]
        start = cut
        first = False
    return physical


def _physical_lines(facts: Sequence[FeedFact], *, now_utc: dt.datetime) -> list[str]:
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//lifeos//calendar feed 1//EN",
        "X-WR-CALNAME:LifeOS",
    ]
    for fact in facts:
        due = fact.due_date
        next_day = due + dt.timedelta(days=1)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:feed-action-{fact.action_id}@lifeos",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{due:%Y%m%d}",
                f"DTEND;VALUE=DATE:{next_day:%Y%m%d}",
                f"SUMMARY:{_escape(fact.title)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return lines


def compose_feed(facts: Sequence[FeedFact], *, now_utc: dt.datetime) -> str:
    """Render the facts as one ICS document with CRLF line endings."""
    physical: list[str] = []
    for logical in _physical_lines(facts, now_utc=now_utc):
        physical.extend(_fold(logical))
    return CRLF.join(physical) + CRLF
