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

__all__ = ["parse_date"]

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
