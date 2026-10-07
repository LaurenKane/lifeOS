"""Goal — the thing you are becoming or finishing.

Glossary: "Something you are becoming or finishing, bigger than one sitting.
A Goal carries a why, a Minimum and a Current thing. Never overdue, never
failing."

What the schema deliberately does NOT hold, and why each absence is
load-bearing:

- **No progress count, no percent, no streak.** Discovery fixed "meaning over
  metrics" as a hard rule: the only numbers the life schema stores are upkeep
  receipts. A progress column would leak into the Dashboard and become a
  score.
- **No failure state.** `is_active` is a switch the user toggles, not a
  judgment: a paused Goal is resting, visible on the Goals page, out of
  suggestions. There is no `failed` value because no state assigns it.
- **No due date.** Goals are examined, not scheduled; the Actions belonging
  to them are.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from life.domain.models import Base, created_at_column

__all__ = ["GOAL_AREAS", "Goal", "WishlistItem"]


#: The eight areas of the discovery-set Mandala (Q28: fixed list, chosen from
#: chips, optional per Goal). Spelled again in the CHECK constraint below and
#: again in `life.public.Area` — one decision, enforced at each layer.
GOAL_AREAS: Final[tuple[str, ...]] = (
    "home",
    "hobbies",
    "body",
    "career",
    "money",
    "people",
    "growth",
    "experiences",
)


class Goal(Base):
    """One life goal. The why is optional; the title is not."""

    __tablename__ = "goal"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    area: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: "Pick up the guitar for five minutes." Honest free text, no tier column —
    #: the 🟢🟡🔵 tiers are a fast follow (discovery Q15) and this schema must
    #: not grow a stub column for them.
    minimum: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: One focus per Goal (glossary: "Current thing"); choosing a new one
    #: replaces the old.
    current_focus: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            f"area IS NULL OR area IN ({', '.join(repr(a) for a in GOAL_AREAS)})",
            name="area",
        ),
    )


class WishlistItem(Base):
    """A desire attached to a Goal — wanted eventually, not scheduled.

    Glossary: "Not an Action; carries no date and no pressure." It has a tick
    because that is the only interaction a want supports ("bought it"); the
    tick is a memory, not a completed obligation.
    """

    __tablename__ = "wishlist_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goal.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = created_at_column()
