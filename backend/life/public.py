"""life's public API — the ONLY export surface.

Other modules may depend on this module and nothing else below it. What may
appear here: Protocols and read-only, frozen Pydantic schemas. Business logic
is `life.domain.services`; database access is `life.api.routes`.

Identifiers are `int` for the same reason finance's public surface gives: the
tables hand out BIGSERIAL and every boundary is type-honest.

Vocabulary note (docs/adr/0011): there is no "habit", "streak", or
"overdue" concept exported from this module — the read models carry raw facts
(last done, earned cadence) and never a computed judgment like "you are
behind on X".
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ActionSummary",
    "Area",
    "GoalSummary",
    "PixelDay",
    "ThoughtResolution",
    "ThoughtSummary",
    "UpkeepSummary",
    "VisionKind",
    "VisionItemSummary",
    "WishlistItemSummary",
]


class Area(StrEnum):
    """The eight areas of the Mandala (discovery Q28) — the fixed life
    taxonomy. Mirrors the CHECK constraint on `life.goal.area`."""

    HOME = "home"
    HOBBIES = "hobbies"
    BODY = "body"
    CAREER = "career"
    MONEY = "money"
    PEOPLE = "people"
    GROWTH = "growth"
    EXPERIENCES = "experiences"


class ThoughtResolution(StrEnum):
    """What a Thought became — or that it rests by decision."""

    ACTION = "action"
    GOAL = "goal"
    UPKEEP = "upkeep"
    #: "I'll keep it": resolved out of the Inbox nudge, still a Thought.
    RESTS = "rests"
    #: "No longer needed": kept, invisible, never auto-surfaced again.
    DISMISSED = "dismissed"


class VisionKind(StrEnum):
    """What sits on the Vision board. Mirrors the CHECK on kind."""

    IMAGE = "image"
    PHRASE = "phrase"


class _ReadOnly(BaseModel):  # type: ignore[explicit-any]
    """Base for every exported schema: frozen, strict, closed — same
    rationale as `finance.public._ReadOnly`."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ActionSummary(_ReadOnly):  # type: ignore[explicit-any]
    """An Action as other layers see it."""

    id: int
    text: str
    goal_id: int | None = None
    parent_action_id: int | None = None
    due_date: date | None = None
    planned_date: date | None = None
    urgent: bool = False
    is_done: bool = False
    done_at: datetime | None = None
    created_at: datetime


class GoalSummary(_ReadOnly):  # type: ignore[explicit-any]
    """A Goal. No progress percent exists anywhere in this contract."""

    id: int
    title: str
    why: str | None = None
    area: Area | None = None
    minimum: str | None = None
    current_focus: str | None = None
    is_active: bool = True
    created_at: datetime


class UpkeepSummary(_ReadOnly):  # type: ignore[explicit-any]
    """An Upkeep: raw facts only.

    `last_done_at` comes from receipts; `earned_cadence_days` is None until at
    least two receipts exist; `next_opportunity` is the cadence arithmetic
    (None when no cadence is known — never an invented default). The
    Dashboard derives its green→amber dot from these; no "overdue" flag
    exists on purpose (ADR 0011).
    """

    id: int
    title: str
    aim_days: int | None = None
    last_done_at: datetime | None = None
    earned_cadence_days: int | None = None
    next_opportunity: date | None = None
    created_at: datetime


class ThoughtSummary(_ReadOnly):  # type: ignore[explicit-any]
    """A Thought, with whatever it has become (if anything)."""

    id: int
    text: str
    resolved_kind: ThoughtResolution | None = None
    action_id: int | None = None
    goal_id: int | None = None
    upkeep_id: int | None = None
    created_at: datetime


class VisionItemSummary(_ReadOnly):  # type: ignore[explicit-any]
    """One Vision-board image (`media_path`) or phrase (`text`)."""

    id: int
    kind: VisionKind
    text: str | None = None
    media_path: str | None = None
    area: Area | None = None
    is_active: bool = True


class WishlistItemSummary(_ReadOnly):  # type: ignore[explicit-any]
    """A want attached to a Goal — not an Action, carries no date."""

    id: int
    goal_id: int
    text: str
    is_done: bool = False


class PixelDay(_ReadOnly):  # type: ignore[explicit-any]
    """One day of the activity grid: how many things were done.

    `count` is kept raw so the frontend renders intensity; the fill rule is
    `count >= 1`, and an empty day is rendered faintly, never red.
    """

    date: date
    count: int
