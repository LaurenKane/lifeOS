"""Upkeep and Receipt — recurring maintenance, never overdue.

Glossary: "Recurring household or self maintenance, defined by a usual
cadence and the moment it was last done. Never overdue and never
accumulating backlog." The Aim (this schema) is the user's intent; the earned
cadence comes from receipts and is computed at read time (`life.domain.
services.upkeep_state`), so there is no stored "actual cadence" column that a
stale cache could contradict.

What the schema deliberately does NOT hold:

- **No `due_date` / `next_due` column.** The Upkeep data shape is a product
  decision recorded in ADR 0011 — cadence plus last-done, deliberately no due
  date. Skipping only moves the next opportunity; nothing here ever renders
  "late".
- **No streak, no completion count.** Receipts are facts about moments, not a
  chain to keep alive.
- **`last_done_at` is not a column.** It is `MAX(receipt.receipted_at)` per
  upkeep, computed in queries. A denormalised copy would be a second clock
  and the two would eventually disagree (the same reasoning as
  `finance.domain.models.updated_at_column`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from life.domain.models import Base, created_at_column

__all__ = ["Receipt", "Upkeep"]


class Upkeep(Base):
    """One named recurring thing (dishes, bathroom, bins)."""

    __tablename__ = "upkeep"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: UNIQUE on purpose: capture resolves a receipt only when the text names
    #: exactly one Upkeep (discovery Q21b), so an ambiguous name would flap.
    #: Enforced by the constraint here and named `uq_upkeep_title` in the
    #: migration via the naming convention.
    title: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: The Aim (glossary): how often the user intends it, in days. NULL means
    #: no aim yet — a thing you know you do but cannot put a number on; the
    #: earned cadence still fills in once receipts accumulate.
    aim_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: An archived Upkeep rests like a paused Goal: visible on the Upkeep
    #: page on request, never matched by capture, never a receipt target.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            "aim_days IS NULL OR aim_days BETWEEN 1 AND 365", name="aim_range"
        ),
    )


class Receipt(Base):
    """A receipt: one fact that an Upkeep was done, at a moment.

    `receipted_at` is when the thing was DONE (backdatable — "I actually did
    this yesterday"); `created_at` is when the receipt entered the system.
    Backdating is allowed; inventing a cadence is not, so there is no field a
    caller could set to claim a cadence directly.

    `thought_id` is capture provenance: the undo of an auto-recorded receipt
    (Q22's undo) deletes exactly the receipt that capture created, never a
    manually record one from the Upkeep page.
    """

    __tablename__ = "receipt"

    id: Mapped[int] = mapped_column(primary_key=True)
    upkeep_id: Mapped[int] = mapped_column(ForeignKey("upkeep.id"), nullable=False)
    thought_id: Mapped[int | None] = mapped_column(
        ForeignKey("thought.id"), nullable=True
    )
    receipted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = created_at_column()
