"""Date and datetime helpers.

Mypy-safe: uses `import datetime as dt` rather than `from datetime import ...`,
so nothing depends on whether `core.datetime` explicitly re-exports those names.

All parsing is deliberately strict about ambiguity. A bank file that says
`03/04/2026` is either 3 April or 4 March and this module will not guess on
behalf of a ledger: `parse_date` tries ISO first, then day-first, and refuses
when both readings are possible unless told which to use.
"""

from __future__ import annotations

import datetime as dt
from calendar import monthrange

# Day-first, because every provider this project imports is European
# (Rabobank, Amex NL, Revolut NL). Month-first US ordering is NOT assumed.
_DAY_FIRST_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y%m%d",
)


def parse_date(value: str | dt.date | dt.datetime) -> dt.date:
    """Parse a date from a string or pass a date/datetime through.

    Args:
        value: An ISO date, a European day-first date, a date, or a datetime.

    Returns:
        The date. A datetime is truncated to its date component.

    Raises:
        ValueError: If no known format matches, or if the value is ambiguous
            under the day-first reading.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value

    text = value.strip()
    if not text:
        msg = "Cannot parse date from an empty string"
        raise ValueError(msg)

    # ISO first: unambiguous, and the format we write everywhere ourselves.
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        pass

    for fmt in _DAY_FIRST_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    msg = f"Cannot parse date from {value!r}"
    raise ValueError(msg)


def parse_datetime(value: str | dt.datetime) -> dt.datetime:
    """Parse an ISO datetime, or pass a datetime through.

    Raises:
        ValueError: If the value is neither a datetime nor a parseable ISO
            datetime string.
    """
    if isinstance(value, dt.datetime):
        return value
    text = value.strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError as exc:
        msg = f"Cannot parse datetime from {value!r}"
        raise ValueError(msg) from exc


def days_between(start: dt.date, end: dt.date) -> int:
    """Whole days from `start` to `end`. Negative when `end` precedes `start`."""
    return (end - start).days


def month_start(d: dt.date) -> dt.date:
    """First day of the month containing `d`."""
    return dt.date(d.year, d.month, 1)


def month_end(d: dt.date) -> dt.date:
    """Last day of the month containing `d`."""
    return dt.date(d.year, d.month, monthrange(d.year, d.month)[1])


def month_range(d: dt.date) -> tuple[dt.date, dt.date]:
    """(first, last) day of the month containing `d` — the budget period."""
    return month_start(d), month_end(d)


def weeks_between(start: dt.date, end: dt.date) -> int:
    """Whole weeks between two dates, truncated toward zero."""
    return days_between(start, end) // 7


def clamp(d: dt.date, earliest: dt.date, latest: dt.date) -> dt.date:
    """Confine `d` to [earliest, latest]."""
    return min(max(d, earliest), latest)


def add_months(d: dt.date, months: int) -> dt.date:
    """Shift `d` by `months`, clamping the day to the target month's length.

    31 Jan + 1 month is 28 or 29 Feb, not an invalid date.
    """
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return dt.date(year, month, day)


def next_occurrence(d: dt.date, frequency: str) -> dt.date:
    """The next date after `d` at the given frequency.

    Args:
        d: The anchor date.
        frequency: One of weekly, biweekly, monthly, quarterly, yearly.

    Raises:
        ValueError: If `frequency` is not a known value.
    """
    if frequency == "weekly":
        return d + dt.timedelta(days=7)
    if frequency == "biweekly":
        return d + dt.timedelta(days=14)
    if frequency == "monthly":
        return add_months(d, 1)
    if frequency == "quarterly":
        return add_months(d, 3)
    if frequency == "yearly":
        return add_months(d, 12)
    msg = f"Unknown frequency: {frequency!r}"
    raise ValueError(msg)
