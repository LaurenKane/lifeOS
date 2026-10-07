"""Upkeep cadence math and the next-opportunity rule.

Everything the Dashboard says about aging is computed HERE, from raw facts,
so the wording ("last done 6 days ago · usually every ~7 days") matches the
data by construction.

`earned_cadence_days` is a MEDIAN of the gaps, not a mean: a single twice-a-
week burst beside two missed weeks would make a mean lie high while a median
stays at the typical rhythm.

The next-opportunity rule is discovery Q26(a) literally: an Upkeep appears on
the today screen only when its next opportunity is today, or it was missed by
AT MOST ~2 days. Longer misses stay on the Upkeep page — never on the
Dashboard, never as an overdue state (ADR 0011)."""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Final, Sequence

__all__ = [
    "MAX_OVERSIGHT_CATCH_UP_DAYS",
    "earnable",
    "earned_cadence_days",
    "last_done_days",
    "next_opportunity",
    "visible_on_today",
]

#: Q26: "today, or was missed by ~2 days". Longer misses never surface on the
#: Dashboard — they stay on the Upkeep page as information, not accusation.
MAX_OVERSIGHT_CATCH_UP_DAYS: Final[int] = 2


def earnable(cadence_days: int | None) -> int | None:
    """A cadence is only meaningful from 1 up; anything else is None."""
    return cadence_days if cadence_days is not None and cadence_days >= 1 else None


def earned_cadence_days(moments: Sequence[dt.datetime]) -> int | None:
    """The median gap between consecutive receipts, as whole days.

    Args:
        moments: Times when the Upkeep was receipted (any order; duplicates
            allowed — a double-tap is one gap of zero).

    Returns:
        The median gap in whole days, rounded UP (`~7 days` reads as 7, not
        6.3), or None when fewer than two receipts exist. Gaps below one day
        contribute but the result never drops below 1: an upkeep done twice
        in an afternoon is still "every day" at best.
    """
    if len(moments) < 2:
        return None
    ordered = sorted(moments)
    gaps_days = [
        (later - earlier).total_seconds() / 86_400.0
        # strict= is wrong here by construction: the second list is one
        # shorter, and truncating IS the pairing contract (consecutive rows).
        for earlier, later in zip(ordered, ordered[1:])  # noqa: B905
    ]
    return max(1, round(statistics.median(gaps_days)))


def last_done_days(last_done_at: dt.datetime | None, *, now: dt.datetime) -> int | None:
    """Days since the last receipt — the number the Dashboard speaks as
    "last done N days ago". None (never done) is a state, not zero."""
    if last_done_at is None:
        return None
    return max(0, (now - last_done_at).days)


def next_opportunity(
    last_done_at: dt.datetime | None, cadence_days: int | None
) -> dt.date | None:
    """When this Upkeep next becomes today's business.

    No cadence (no aim, no earned one yet) → None: it stays on the upkeep
    page, where the dot ages with the user's eye. We do NOT invent a default
    cadence — an unsupported number is the exact thing this module refuses
    (the product voice: no number without evidence).
    """
    usable = earnable(cadence_days)
    if last_done_at is None or usable is None:
        return None
    return last_done_at.date() + dt.timedelta(days=usable)


def visible_on_today(opportunity: dt.date | None, *, today: dt.date) -> bool:
    """The Dashboard rule: next opportunity today, or missed by at most 2
    days — the window is [today − 2, today]. Older misses fade to the Upkeep
    page — quiet, never gone."""
    if opportunity is None:
        return False
    return (
        today - dt.timedelta(days=MAX_OVERSIGHT_CATCH_UP_DAYS) <= opportunity <= today
    )


def opac_status(days_since: int | None, aim_days: int | None) -> str:
    """Fresh → aging dot state, for the dot color on the Dashboard.

    'fresh' : within the aim (or no aim yet — no judgment is possible).
    'aging' : past the aim by up to the catch-up window.
    'aged'  : beyond that — still a dot, never an accusation.
    """
    if days_since is None or aim_days is None:
        return "fresh"
    if days_since <= aim_days:
        return "fresh"
    if days_since <= aim_days + MAX_OVERSIGHT_CATCH_UP_DAYS:
        return "aging"
    return "aged"
