"""life.api.schemas — the Pydantic contract.

Re-exports `life.public`'s read-only summaries plus the request models and
interaction responses that appear in the OpenAPI spec. The frontend's
TypeScript types are hand-mirrored from this surface (like finance's are);
a change here is a contract change to mirror in `features/life`.

Import style matches finance/api/schemas: absolute, sibling packages under
the backend/ import root.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from life.public import (
    ActionSummary,
    Area,
    GoalSummary,
    PixelDay,
    ThoughtSummary,
    UpkeepSummary,
    VisionItemSummary,
)

__all__ = [
    "ActionCreateRequest",
    "ActionSummary",
    "ActionUpdateRequest",
    "Area",
    "CaptureRequest",
    "CaptureResponse",
    "GoalCreateRequest",
    "GoalSummary",
    "GoalUpdateRequest",
    "PixelDay",
    "ReceiptCreateRequest",
    "ThoughtResolveRequest",
    "ThoughtResolveResponse",
    "ThoughtSummary",
    "TodayBoard",
    "UpkeepChoice",
    "UpkeepCreateRequest",
    "UpkeepSummary",
    "UpkeepUpdateRequest",
    "VisionItemSummary",
    "VisionPhraseRequest",
    "WishlistItemCreateRequest",
    "WishlistItemUpdateRequest",
    "ParsedCaptureInfo",
    "InboxStatus",
]


class _ReadOnly(BaseModel):  # type: ignore[explicit-any]
    """Frozen base for every response contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ── Capture ──────────────────────────────────────────────────────────


class CaptureRequest(BaseModel):  # type: ignore[explicit-any]
    """One captured line. Nothing else is required — that is the point."""

    text: str = Field(
        min_length=1,
        max_length=500,
        description="What's on your mind, verbatim. Parsing only, never "
        "blocking: an unparseable line is simply a Thought.",
    )


class ParsedCaptureInfo(_ReadOnly):  # type: ignore[explicit-any]
    """What the parser saw in the line, echoed for the retappable label."""

    due_date: date | None = None
    planned_date: date | None = None
    urgent: bool = False


class UpkeepChoice(_ReadOnly):  # type: ignore[explicit-any]
    """An upkeep walk the parser matched but could not interpret intent on.

    The client asks the ONE question (receipt, or an action reminder?); the
    answer arrives at `POST /life/capture/{thought_id}/resolve` as
    `choice=receipt` (upkeep_id carries) or `choice=action`.
    """

    upkeep_id: int
    title: str


class CaptureResponse(_ReadOnly):  # type: ignore[explicit-any]
    """The single response of a capture, always.

    Exactly one of `created_action`, `created_receipt_id`, `upkeep_choice`
    is set; when none is, the Thought rests in the Inbox — and that is a
    normal outcome, not an error.
    """

    thought: ThoughtSummary
    parsed: ParsedCaptureInfo
    created_action: ActionSummary | None = None
    created_receipt_id: int | None = None
    receipted_upkeep: UpkeepSummary | None = None
    upkeep_choice: UpkeepChoice | None = None


class ThoughtResolveRequest(BaseModel):  # type: ignore[explicit-any]
    """The one decision a Thought may eventually involve — always explicit.

    - `action` / `goal` / `upkeep`: promote; the new row gets
      `life.thought.text` as its title.
    - `upkeep` + `upkeep_id`: the accepted receipt match (discovery Q21);
      `create_receipt` records the receipt in the same transaction.
    - `rests` / `dismissed`: no target; the Inbox nudge goes quiet.
    """

    choice: Literal["action", "goal", "upkeep", "rests", "dismissed"]
    upkeep_id: int | None = None
    create_receipt: bool = False
    aim_days: int | None = Field(
        default=None, ge=1, le=365, description="When promoting to a NEW upkeep."
    )


class ThoughtResolveResponse(_ReadOnly):  # type: ignore[explicit-any]
    """The Thought after resolving, plus whatever was created."""

    thought: ThoughtSummary
    created_action: ActionSummary | None = None
    created_goal: GoalSummary | None = None
    created_upkeep: UpkeepSummary | None = None


class InboxStatus(_ReadOnly):  # type: ignore[explicit-any]
    """The Inbox line on the Dashboard deliberately reads like this:
    "17 things in the pile. Don't worry about these." `stale` is the subset
    older than fourteen days — the only subset the nudge counts."""

    count: int
    stale: int


class TodayBoard(_ReadOnly):  # type: ignore[explicit-any]
    """The Dashboard's life half. `kept_back` carries the cap's cut honestly:
    N eligible things exist beyond the five shown; data wasn't rescheduled."""

    do_now: list[ActionSummary]
    kept_back: int
    cap: int
    urgent: list[ActionSummary]
    upcoming: list[ActionSummary]
    upkeep_opportunities: list[UpkeepSummary]
    inbox: InboxStatus


# ── Requests ─────────────────────────────────────────────────────────


class ActionCreateRequest(BaseModel):  # type: ignore[explicit-any]
    text: str = Field(min_length=1, max_length=500)
    goal_id: int | None = None
    due_date: date | None = None
    planned_date: date | None = None
    urgent: bool = False


class ActionUpdateRequest(BaseModel):  # type: ignore[explicit-any]
    """Partial update; None means leave-as-is (not clear). Detaching a Goal
    is not needed from the dashboard in v1."""

    text: str | None = Field(default=None, min_length=1, max_length=500)
    goal_id: int | None = None
    due_date: date | None = None
    planned_date: date | None = None
    urgent: bool | None = None


class GoalCreateRequest(BaseModel):  # type: ignore[explicit-any]
    title: str = Field(min_length=1, max_length=200)
    why: str | None = Field(default=None, max_length=2000)
    area: Area | None = None
    minimum: str | None = Field(default=None, max_length=500)
    current_focus: str | None = Field(default=None, max_length=500)


class GoalUpdateRequest(BaseModel):  # type: ignore[explicit-any]
    """Partial; None means leave-as-is."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    why: str | None = Field(default=None, max_length=2000)
    area: Area | None = None
    minimum: str | None = Field(default=None, max_length=500)
    current_focus: str | None = Field(default=None, max_length=500)


class UpkeepCreateRequest(BaseModel):  # type: ignore[explicit-any]
    title: str = Field(min_length=1, max_length=200)
    aim_days: int | None = Field(default=None, ge=1, le=365)


class UpkeepUpdateRequest(BaseModel):  # type: ignore[explicit-any]
    """Partial; None means leave-as-is."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    aim_days: int | None = Field(default=None, ge=1, le=365)
    is_active: bool | None = None


class ReceiptCreateRequest(BaseModel):  # type: ignore[explicit-any]
    """One receipt. `receipted_at` backdates ("I did this yesterday"); absent
    means now — and now is the only auto value, never an invention."""

    receipted_at: datetime | None = None


class WishlistItemCreateRequest(BaseModel):  # type: ignore[explicit-any]
    text: str = Field(min_length=1, max_length=300)


class WishlistItemUpdateRequest(BaseModel):  # type: ignore[explicit-any]
    """None means leave-as-is."""

    text: str | None = Field(default=None, min_length=1, max_length=300)
    is_done: bool | None = None


class VisionPhraseRequest(BaseModel):  # type: ignore[explicit-any]
    text: str = Field(min_length=1, max_length=500)
    area: Area | None = None


class VisionUpdateRequest(BaseModel):  # type: ignore[explicit-any]
    """Partial; None means leave-as-is. media_path is set only by upload."""

    text: str | None = Field(default=None, min_length=1, max_length=500)
    area: Area | None = None
    is_active: bool | None = None
