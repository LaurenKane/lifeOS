"""The Do-now board — which Actions surface today.

Discovery rules implemented here (Q4, Q24, Q26):

- **The cap counts TOP-LEVEL entries only.** An action like "pack for the
  trip" with its subactions is one item on the board; its subactions render
  inside it and never inflate the count.
- **Hard cap of 5.** Order decides the cut: the earliest-scheduled entries
  survive, the rest are `kept_back` — shown on the Dashboard as "N more wait
  for tomorrow", not rescheduled behind the user's back (data keeps its own
  planned/due dates; the *board* is the view that decides).
- **Urgent is its own block and does not consume the cap** — the user set the
  flag, not the system, and Q24 wants "actions that need my most immediate
  attention" visible.
- **A dated Action whose due date passed remains on the board** (it is a real
  fact, not backlog shame) and is rendered with its own "was due" label; it is
  NOT recounted as overdue — no such state exists.

Pure function; routes gather the rows and pass them in."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, Sequence

__all__ = ["CAP_TOP_LEVEL", "ActionRow", "DoNowSelection", "choose_do_now"]

#: The hard top-level cap (Q4).
CAP_TOP_LEVEL: Final[int] = 5


@dataclass(frozen=True)
class ActionRow:
    """The slice of an Action the board decision needs.

    `order_index` breaks ties in capture order, so two actions on the same
    day resolve deterministically (i assertable, not clock-dependent).
    """

    id: int
    urgent: bool
    is_done: bool
    parent_action_id: int | None
    due_date: dt.date | None
    planned_date: dt.date | None
    order_index: int


@dataclass(frozen=True)
class DoNowSelection:
    """What the board shows for one day."""

    #: The top-level Action ids on the board, board-ordered.
    selected: tuple[int, ...]
    #: How many eligible top-level entries were cut by the cap.
    kept_back: int
    #: Urgent, open, top-level ids — its own block, uncapped.
    urgent_ids: tuple[int, ...]


def choose_do_now(
    rows: Sequence[ActionRow], *, today: dt.date, cap: int = CAP_TOP_LEVEL
) -> DoNowSelection:
    """Select today's board from open top-level actions.

    Eligibility: an Action is on the board when **(due_date <= today) OR
    (planned_date == today)** and it is not done and not urgent. Urgent open
    top-level actions surface in `urgent_ids` regardless of dates (they are
    the user's own "do first" flags).
    """
    urgent: list[ActionRow] = []
    eligible: list[ActionRow] = []

    for row in rows:
        if row.is_done or row.parent_action_id is not None:
            continue
        if row.urgent:
            urgent.append(row)
            continue
        if (row.due_date is not None and row.due_date <= today) or (
            row.planned_date == today
        ):
            eligible.append(row)

    # Earliest real-world first; ties keep capture order (stable sort on the
    # caller's ordering).
    def sort_key(row: ActionRow) -> tuple[dt.date, int]:
        return (row.due_date or row.planned_date or today, row.order_index)

    eligible.sort(key=sort_key)
    urgent.sort(key=sort_key)

    selected = tuple(row.id for row in eligible[:cap])
    return DoNowSelection(
        selected=selected,
        kept_back=max(0, len(eligible) - cap),
        urgent_ids=tuple(row.id for row in urgent),
    )
