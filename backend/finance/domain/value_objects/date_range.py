"""DateRange value object for finance module.

PRIVATE — only finance.public may import from this module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DateRange:
    """A range of dates [start, end] inclusive."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            msg = f"DateRange start ({self.start}) must not be after end ({self.end})"
            raise ValueError(msg)

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def intersect(self, other: DateRange) -> DateRange | None:
        """Return the intersection of two DateRanges, or None if they don't overlap."""
        new_start = max(self.start, other.start)
        new_end = min(self.end, other.end)
        if new_start > new_end:
            return None
        return DateRange(start=new_start, end=new_end)

    def __repr__(self) -> str:
        return f"DateRange(start={self.start}, end={self.end})"
