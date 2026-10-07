"""shape_reflection — the deterministic read behind "you advanced {goal}".

Pure function; the route passes in the local now (the window is lived in the
user's days, never the server's UTC arm). What it shapes are existing FACTS —
done Actions filed under a Goal, receipts under an Upkeep — counted inside a
ROLLING last-30-days window (label says "last 30 days": a calendar month lies
about a 7-day-old month).

What it refuses to become, and why: no score, no comparison of today against
any yesterday — the only numbers emitted are counts of what actually
happened. Entries with a count of zero do not appear at all (an empty summary
is a normal answer, and the Dashboard renders silence for it — not an
empty-state box).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

__all__ = [
    "DoneActionFact",
    "GoalFact",
    "GoalReflection",
    "ReceiptFact",
    "Reflection",
    "UpkeepFact",
    "UpkeepReflection",
    "WINDOW_DAYS",
    "reflection_window",
    "shape_reflection",
]

#: The rolling window, in days. Fixed here so the route's label, the SQL
#: pre-filter and this shaping agree by construction, not by three copies.
WINDOW_DAYS: int = 30

#: How many example done texts a goal at most carries. Quiet provenance for
#: "{N} small steps", not a list to scroll: three is enough to remember by.
TEXT_CAP: int = 3


def reflection_window(now_local: dt.datetime) -> dt.datetime:
    """The window's start: now minus WINDOW_DAYS, in the same arm."""
    return now_local - dt.timedelta(days=WINDOW_DAYS)


@dataclass(frozen=True)
class GoalFact:
    """A Goal's identity, passed in by the route. `is_active` filters here —
    paused is resting, and the reflection reads active goals only."""

    goal_id: int
    title: str
    area: str | None
    created_at: dt.datetime
    is_active: bool


@dataclass(frozen=True)
class UpkeepFact:
    """An Upkeep's identity. Archived (`is_active=False`) rests like a
    paused Goal: out of the read, on its own page."""

    upkeep_id: int
    title: str
    created_at: dt.datetime
    is_active: bool


@dataclass(frozen=True)
class DoneActionFact:
    """A done Action's provenance: which Goal it advanced, its actual words,
    and the moment it was done. `done_at` is required — this fact does not
    exist for an undone Action."""

    goal_id: int
    text: str
    done_at: dt.datetime


@dataclass(frozen=True)
class ReceiptFact:
    """One maintenance moment: which Upkeep was receipted (when, from the
    row)."""

    upkeep_id: int


@dataclass(frozen=True)
class GoalReflection:
    """One goal's honest month: the count and up to TEXT_CAP example texts,
    newest first."""

    goal_id: int
    title: str
    area: str | None
    count: int
    done_texts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UpkeepReflection:
    """One upkeep's honest month: how many times it was kept going."""

    upkeep_id: int
    title: str
    count: int


@dataclass(frozen=True)
class Reflection:
    """The whole summary. `goals`/`upkeeps` hold only entries with count >= 1,
    ordered count-first, creation-second; empty lists are a normal answer."""

    goals: list[GoalReflection] = field(default_factory=list)
    upkeeps: list[UpkeepReflection] = field(default_factory=list)


def _in_window(
    moment: dt.datetime, *, window_start: dt.datetime, now: dt.datetime
) -> bool:
    """Between the window's edges, inclusive. A future-dated done/receipt
    (a backdate that overshoots, a clock skew) does not belong to a window
    that ends at now — the same historiography as `away_days`' 0 floor."""
    return window_start <= moment <= now


def shape_reflection(
    goals: Sequence[GoalFact],
    upkeeps: Sequence[UpkeepFact],
    done_actions: Sequence[DoneActionFact],
    receipts: Sequence[ReceiptFact],
    *,
    now_local: dt.datetime,
    window_start: dt.datetime | None = None,
) -> Reflection:
    """Count the window's facts and shape the summary.

    `done_actions`/`receipts` are raw rows the route gathered per its SQL
    pre-filter; the window is applied AGAIN here so this helper is the one
    authority on the edges and the unit tests can prove them (`window_start`
    defaults to `reflection_window(now_local)`).
    """
    window = reflection_window(now_local) if window_start is None else window_start

    by_goal: dict[int, list[DoneActionFact]] = {}
    for action in done_actions:
        if _in_window(action.done_at, window_start=window, now=now_local):
            by_goal.setdefault(action.goal_id, []).append(action)

    by_upkeep: dict[int, int] = {}
    for receipt in receipts:
        by_upkeep[receipt.upkeep_id] = by_upkeep.get(receipt.upkeep_id, 0) + 1

    goal_created: dict[int, dt.datetime] = {
        goal.goal_id: goal.created_at for goal in goals
    }
    upkeep_created: dict[int, dt.datetime] = {
        upkeep.upkeep_id: upkeep.created_at for upkeep in upkeeps
    }

    goal_reflections: list[GoalReflection] = []
    for goal in goals:
        if not goal.is_active:
            continue  # paused is resting; the read keeps it off the board.
        done_in_window = sorted(
            by_goal.get(goal.goal_id, []),
            key=lambda action: (action.done_at, action.goal_id),
            reverse=True,
        )
        count = len(done_in_window)
        if count < 1:
            continue  # nothing happened: the goal is absent, not "at 0%".
        goal_reflections.append(
            GoalReflection(
                goal_id=goal.goal_id,
                title=goal.title,
                area=goal.area,
                count=count,
                done_texts=[action.text for action in done_in_window[:TEXT_CAP]],
            )
        )
    goal_reflections.sort(key=lambda row: (-row.count, goal_created[row.goal_id]))

    upkeep_reflections: list[UpkeepReflection] = []
    for upkeep in upkeeps:
        if not upkeep.is_active:
            continue
        count = by_upkeep.get(upkeep.upkeep_id, 0)
        if count < 1:
            continue
        upkeep_reflections.append(
            UpkeepReflection(
                upkeep_id=upkeep.upkeep_id, title=upkeep.title, count=count
            )
        )
    upkeep_reflections.sort(key=lambda row: (-row.count, upkeep_created[row.upkeep_id]))

    return Reflection(goals=goal_reflections, upkeeps=upkeep_reflections)
