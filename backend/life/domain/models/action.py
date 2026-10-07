"""Action — a step doable in one sitting.

Glossary: "A step doable in one sitting. May carry a date, an urgent flag,
subactions and membership of a Goal — none of which are required."

Model notes carrying real weight:

- **`planned_date` vs `due_date` are different facts.** `due_date` is a date
  the real world attached (dentist on the 14th). `planned_date` is the Do-now
  list membership: it is set when the user pulls an Action into a day or the
  capture parser sees "tomorrow". The Do-now board derives from both; conflating
  them would make "due tomorrow" mean "queued for tomorrow" — a promise the
  user did not choose.
- **`parent_action_id` is one level deep** ("packing" holds "clothes",
  "charger", "laptop"; a subaction can never hold subactions). Enforced by the
  `fn_action_depth` trigger in migration 0001 — a CHECK cannot read another
  row, and this is a data-integrity rule, not a UI preference.
- **Done-ness is a pair.** `is_done = (done_at IS NOT NULL)` by CHECK; `done_at`
  is kept precise so the pixel graph can date completions without a second
  "done on" column, which would be the same fact twice.
- Every top-level Do-now entry counts as **one** item however many subactions
  it carries (Q12) — that is a display rule of the board, not a column here;
  the board counts `parent_action_id IS NULL`.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from life.domain.models import Base, created_at_column

__all__ = ["Action"]


class Action(Base):
    """One concrete step. A subaction is also an Action, one level deep."""

    __tablename__ = "action"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goal.id"), nullable=True)
    #: One level. The trigger in migration 0001 refuses a subaction whose
    #: parent is itself a subaction.
    parent_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("action.id"), nullable=True
    )
    #: The real world's date (appointment); no default rolls it forward.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Do-now membership for a chosen day; NULL when not on any list.
    planned_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The only priority bit in this schema (Q24: "urgent" is a manual flag).
    #: Urgent Actions surface in their own Dashboard block and do not consume
    #: the Do-now list's cap.
    urgent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Precise so the pixel graph can date completions; kept in one pair with
    #: `is_done` so "done" is never in two minds.
    done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("is_done = (done_at IS NOT NULL)", name="done_pair"),
        CheckConstraint(
            "due_date IS NULL OR due_date >= DATE '2020-01-01'",
            name="sane_due_date",
        ),
    )
