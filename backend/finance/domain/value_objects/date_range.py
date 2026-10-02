"""DateRange - an inclusive [start, end] window.

PRIVATE - only finance.public may import from this module.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class DateRange:
    """A closed interval of dates. `start == end` is a valid single-day range."""

    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.start > self.end:
            msg = f"DateRange start ({self.start}) must not be after end ({self.end})"
            raise ValueError(msg)

    def __contains__(self, value: dt.date) -> bool:
        return self.start <= value <= self.end

    def contains(self, value: dt.date) -> bool:
        return self.start <= value <= self.end

    @property
    def days(self) -> int:
        """Inclusive day count: a one-day range is 1, not 0."""
        return (self.end - self.start).days + 1

    def intersect(self, other: DateRange) -> DateRange | None:
        """The overlapping range, or None when the two do not overlap."""
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if start > end:
            return None
        return DateRange(start=start, end=end)

    def shift(self, days: int) -> DateRange:
        """Move both bounds by `days`. Used by the dedup candidate windows."""
        delta = dt.timedelta(days=days)
        return DateRange(start=self.start + delta, end=self.end + delta)

    def __repr__(self) -> str:
        return f"DateRange({self.start}..{self.end})"
