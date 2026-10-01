"""datetime.py — Date/datetime utilities for the finance module.

Mypy‑safe: uses `import datetime` rather than `from datetime import ...`
to avoid "module does not explicitly export" errors.
"""

from __future__ import annotations

import datetime as dt


def parse_date(value: str | dt.date | dt.datetime) -> dt.date:
    """Parse a date from various input types."""
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, str):
        # Try common formats
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return dt.datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    msg = f"Cannot parse date from string: {value}"
    raise ValueError(msg)


def parse_datetime(value: str | dt.datetime) -> dt.datetime:
    """Parse a datetime from various input types."""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M",
        ):
            try:
                return dt.datetime.strptime(value, fmt)
            except ValueError:
                continue
    msg = f"Cannot parse datetime from string: {value}"
    raise ValueError(msg)


def days_between(start: dt.date, end: dt.date) -> int:
    """Return the number of days from start to end (can be negative)."""
    return (end - start).days


def month_start(d: dt.date) -> dt.date:
    """Return the first day of the month containing d."""
    return dt.date(d.year, d.month, 1)


def month_end(d: dt.date) -> dt.date:
    """Return the last day of the month containing d."""
    from calendar import monthrange
    _, last_day = monthrange(d.year, d.month)
    return dt.date(d.year, d.month, last_day)


def weeks_between(start: dt.date, end: dt.date) -> int:
    """Return the number of whole weeks between two dates."""
    return days_between(start, end) // 7


def next_occurrence(d: dt.date, frequency: str) -> dt.date:
    """Compute the next occurrence of a date given a frequency string."""
    if frequency == "weekly":
        return d + dt.timedelta(weeks=1)
    if frequency == "biweekly":
        return d + dt.timedelta(weeks=2)
    if frequency == "monthly":
        if d.month == 12:
            return dt.date(d.year + 1, 1, 1) - dt.timedelta(days=1)
        return dt.date(d.year, d.month + 1, d.day)
    if frequency == "quarterly":
        next_q = (d.month - 1) // 3 + 1 + 1  # Q1→2, Q2→3, Q3→4, Q4→1(next year)
        start_next_q = (next_q - 1) * 3 + 1
        try:
            return dt.date(d.year, start_next_q, d.day)
        except ValueError:
            _, last = monthrange(d.year, start_next_q)
            return dt.date(d.year, start_next_q, last)
    if frequency == "yearly":
        try:
            return dt.date(d.year + 1, d.month, d.day)
        except ValueError:
            return dt.date(d.year + 1, 3, 1)
    msg = f"Unknown frequency: {frequency}"
    raise ValueError(msg)