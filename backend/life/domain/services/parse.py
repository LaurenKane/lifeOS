"""Capture parsing — the deterministic interpreter of a quick thought.

Rules fixed in discovery (rounds 3–6 of the interviews):

- A Thought carries **no** required decisions: the parser never blocks a
  capture, it only *suggests*. Every suggestion travels to the client inside
  an echo (Q22: retap to change the parsed bits, undo to reverse) — nothing
  here is final, and a wrong guess composes a Thought, which is always legal.
- Dates come from natural language so the user structures nothing. Pure
  functions only: no LLM, no new dependency.
- Timezone boundary: the user is in NL. Callers pass today's LOCAL date in
  (`today`), so "tomorrow" resolves against the user's day, not the server's
  UTC clock, and a unit test is a fact rather than a race.

One date per capture, first match by priority — a parser that guesses between
two dates in one sentence writes a wrong promise silently, and the echo's
retap exists precisely so the user can supply the structure no parser could
infer.

Reading convention: relative day words ("tomorrow", "vrijdag") become
**planned** dates — a Do-now choice; concrete dates ("on the 14th", "14/10")
become **due** dates — a real-world fact the sentence attributes. The echo
lets the user correct a wrong reading.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

__all__ = ["ParsedCapture", "parse_capture"]


_URGENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\b\w*urgent\w*\b|\basap\b|!{2,})", re.IGNORECASE
)

# Full names and common abbreviations, Dutch ones included (a Dutch user types
# "vrijdag" between two English thoughts without switching keyboards).
# Two-letter Dutch forms ("ma", "do") are deliberately NOT included: as bare
# words they collide with English ("do this sometime") and would false-fire.
_WEEKDAYS: Final[dict[str, int]] = {
    "monday": 0,
    "mandag": 0,
    "mon": 0,
    "tuesday": 1,
    "tues": 1,
    "tue": 1,
    "dinsdag": 1,
    "wednesday": 2,
    "wed": 2,
    "woensdag": 2,
    "thursday": 3,
    "thurs": 3,
    "thur": 3,
    "thu": 3,
    "donderdag": 3,
    "friday": 4,
    "fri": 4,
    "vrijdag": 4,
    "saturday": 5,
    "sat": 5,
    "zaterdag": 5,
    "sunday": 6,
    "sun": 6,
    "zondag": 6,
}

# Dutch "morgen" = tomorrow; "vandaag" = today.
_RELATIVE_DAYS: Final[dict[str, int]] = {
    "today": 0,
    "tonight": 0,
    "vandaag": 0,
    "tomorrow": 1,
    "tmrw": 1,
    "tmr": 1,
    "morgen": 1,
}

_RELATIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:" + "|".join(_RELATIVE_DAYS) + r")\b", re.IGNORECASE
)

_WEEKDAY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\bon\s+)?\b(?:next\s+)?(?:" + "|".join(_WEEKDAYS) + r")\b",
    re.IGNORECASE,
)

_ORDINAL_DAY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:on\s+|by\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE
)

_MONTHS: Final[dict[str, str]] = {
    "january": "1",
    "januari": "1",
    "jan": "1",
    "february": "2",
    "februari": "2",
    "feb": "2",
    "march": "3",
    "maart": "3",
    "mar": "3",
    "april": "4",
    "apr": "4",
    "may": "5",
    "mei": "5",
    "june": "6",
    "juni": "6",
    "jun": "6",
    "july": "7",
    "juli": "7",
    "jul": "7",
    "august": "8",
    "augustus": "8",
    "aug": "8",
    "september": "9",
    "sept": "9",
    "sep": "9",
    "october": "10",
    "oktober": "10",
    "oct": "10",
    "okt": "10",
    "november": "11",
    "nov": "11",
    "december": "12",
    "dec": "12",
}

_MONTH_NAMES: Final[str] = "|".join(_MONTHS)

_MONTH_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_NAMES + r")\b"
    r"|(" + _MONTH_NAMES + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b)",
    re.IGNORECASE,
)

# Day-first numeric ("14/10") — the same convention `core.datetime.parse_date`
# fixes for this project's European providers.
_NUMERIC_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{1,2})[/.-](\d{1,2})(?![\d])\b"
)

_WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedCapture:
    """What one captured line resolves to. Zero decisions required."""

    #: The capture text with date and urgent markers removed — the title the
    #: row stores. Whitespace is collapsed; nothing else is rewritten.
    text: str
    #: A real-world date ("dentist on the 14th", "14/10").
    due_date: dt.date | None
    #: A day the user chose for the Do-now list ("buy lamp tomorrow").
    planned_date: dt.date | None
    urgent: bool


def _strip_matches(text: str, pattern: re.Pattern[str]) -> str:
    """Remove every match of `pattern` from `text` and normalise whitespace."""
    return " ".join(pattern.sub(" ", text).split())


def _next_weekday(target: int, from_day: dt.date) -> dt.date:
    """The next occurrence of `target` weekday, `from_day` included: a capture
    that says "friday" ON a friday means this friday."""
    days_ahead = (target - from_day.weekday()) % 7
    return from_day + dt.timedelta(days=days_ahead)


def _next_nth_of_month(day: int, from_day: dt.date) -> dt.date:
    """The next occurrence of day-of-month `day` on or after `from_day`.

    Months that do not have the day (February 30th) are skipped rather than
    approximated to their last day — an invented date is worse than none.
    """
    candidate = from_day
    for _ in range(13):
        try:
            with_day = candidate.replace(day=day)
        except ValueError:
            pass
        else:
            if with_day >= from_day:
                return with_day
        if candidate.month == 12:
            candidate = candidate.replace(year=candidate.year + 1, month=1, day=1)
        else:
            candidate = candidate.replace(month=candidate.month + 1, day=1)
    msg = f"no occurrence of day {day} within 13 months"
    raise ValueError(msg)


def _extract_planned_date(work: str, today: dt.date) -> tuple[dt.date | None, str]:
    """A relative day word ("tomorrow") or a weekday, as Do-now membership."""
    match = _RELATIVE_PATTERN.search(work)
    if match is not None:
        offset = _RELATIVE_DAYS[match.group(0).lower()]
        replaced = _strip_matches(work, _RELATIVE_PATTERN)
        return today + dt.timedelta(days=offset), replaced
    weekday_match = _WEEKDAY_PATTERN.search(work)
    if weekday_match is not None:
        name = weekday_match.group()
        for prefix in ("on ", "next "):
            name = name.removeprefix(prefix)
        target = _WEEKDAYS[name.strip().lower()]
        return _next_weekday(target, today), _strip_matches(work, _WEEKDAY_PATTERN)
    return None, work


def _extract_due_date(work: str, today: dt.date) -> tuple[dt.date | None, str]:
    """A concrete real-world date: month-name date, day-of-month ordinal or a
    day-first numeric. THE FIRST that matches wins."""
    month_match = _MONTH_DATE_PATTERN.search(work)
    if month_match is not None:
        first_date = _month_style_date(month_match, today)
        if first_date is not None:
            return first_date, _strip_matches(work, _MONTH_DATE_PATTERN)
    ordinal_match = _ORDINAL_DAY_PATTERN.search(work)
    if ordinal_match is not None:
        day = int(ordinal_match.group(1))
        if 1 <= day <= 31:
            return _next_nth_of_month(day, today), _strip_matches(
                work, _ORDINAL_DAY_PATTERN
            )
    numeric_match = _NUMERIC_DATE_PATTERN.search(work)
    if numeric_match is not None:
        day, month = int(numeric_match.group(1)), int(numeric_match.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            numeric_date = _numeric_style_date(day, month, today)
            if numeric_date is not None:
                return numeric_date, _strip_matches(work, _NUMERIC_DATE_PATTERN)
    return None, work


def _month_style_date(match: re.Match[str], today: dt.date) -> dt.date | None:
    """A month-name date's concrete date, past ones loyal to next year."""
    if match.group(2) is not None:
        day, month_text = int(match.group(1)), match.group(2)
    else:
        day, month_text = int(match.group(4)), match.group(3)
    month = int(_MONTHS[month_text.lower()])
    if day < 1 or day > 31:
        return None
    try:
        candidate = today.replace(month=month, day=day)
    except ValueError:
        return None
    return candidate if candidate >= today else dt.date(candidate.year + 1, month, day)


def _numeric_style_date(day: int, month: int, today: dt.date) -> dt.date | None:
    """A day-first numeric (`14/10`) reading. Past this year → next year."""
    try:
        candidate = today.replace(month=month, day=day)
    except ValueError:
        return None
    if candidate >= today:
        return candidate
    try:
        return candidate.replace(year=candidate.year + 1)
    except ValueError:
        return None  # February 29th with no leap year ahead.


def parse_capture(text: str, *, today: dt.date) -> ParsedCapture:
    """Parse one captured line.

    Args:
        text: Raw capture text (the API rejects empty text before this).
        today: The caller's LOCAL date, so "tomorrow" means tomorrow in the
            user's timezone.

    Returns:
        The cleaned title plus zero or one date and the urgent flag.
    """
    work = text
    urgent = _URGENT_PATTERN.search(work) is not None
    if urgent:
        work = _strip_matches(work, _URGENT_PATTERN)

    planned_date, work = _extract_planned_date(work, today)
    due_date: dt.date | None = None
    if planned_date is None:
        due_date, work = _extract_due_date(work, today)

    return ParsedCapture(
        text=_strip_matches(work, _WHITESPACE_PATTERN),
        due_date=due_date,
        planned_date=planned_date,
        urgent=urgent,
    )
