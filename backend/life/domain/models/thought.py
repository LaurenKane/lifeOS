"""Thought and the capture-side transitions.

**Thought** (glossary): "Anything captured in one line, before any sorting. …
It stays a Thought until it becomes an Action, a Goal or an Upkeep — or rests
in the Inbox indefinitely, which is a normal state, not debt."

`resolved_kind` + `resolved_at` + the three target-FK columns encode that
transition. The CHECKs make the legal states exactly:

- NULL kind                → NULL resolved_at, no target   — resting in the Inbox
- 'rests' | 'dismissed'    → resolved_at, no target        — resting by decision
- 'action' | 'goal' | 'upkeep' → resolved_at plus the one matching FK

A dismissed Thought is KEPT, never deleted: the user's words are their data,
and the resolution record exists so the 14-day Inbox nudge (discovery Q14)
can quietly stop nagging without losing anything.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from life.domain.models import Base, created_at_column

__all__ = ["Thought"]


class Thought(Base):
    """One captured line. Nothing about it is required except the text."""

    __tablename__ = "thought"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: NULL while resting in the Inbox; one of the five values below after that.
    resolved_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_id: Mapped[int | None] = mapped_column(
        ForeignKey("action.id"), nullable=True
    )
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goal.id"), nullable=True)
    upkeep_id: Mapped[int | None] = mapped_column(
        ForeignKey("upkeep.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN action_id IS NOT NULL THEN 1 ELSE 0 END +"
            " CASE WHEN goal_id IS NOT NULL THEN 1 ELSE 0 END +"
            " CASE WHEN upkeep_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name="one_target",
        ),
        CheckConstraint(
            "(resolved_kind IS NULL) = (resolved_at IS NULL)",
            name="resolution_pair",
        ),
        CheckConstraint(
            "resolved_kind IS NULL OR resolved_kind IN "
            "('action','goal','upkeep','dismissed','rests')",
            name="resolved_kind",
        ),
        CheckConstraint(
            "(resolved_kind = 'action' AND action_id IS NOT NULL) OR"
            " (resolved_kind = 'goal' AND goal_id IS NOT NULL) OR"
            " (resolved_kind = 'upkeep' AND upkeep_id IS NOT NULL) OR"
            " resolved_kind IS NULL OR resolved_kind IN ('dismissed','rests')",
            name="kind_matches_target",
        ),
    )
