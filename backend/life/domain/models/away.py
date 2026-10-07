"""CatchupAck — the "I've seen this" record behind the Dashboard's away strip.

One row per day, UniqueConstraint on `day`. It is not a domain fact about a
Goal, a Thought or an Upkeep; it is a fact about ATTENTION: "the away strip
was quieted on this day." That is why it lives in its own module, away from
the three tables above it, and why nothing references it — the strip goes
quiet for the day and that is all; no other surface ever reads it (Q8, Q22).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date
from sqlalchemy.orm import Mapped, mapped_column

from life.domain.models import Base, created_at_column

__all__ = ["CatchupAck"]


class CatchupAck(Base):
    """One day's acknowledgment of the away strip."""

    __tablename__ = "catchup_ack"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The local day the strip was quieted (life.local, not UTC).
    day: Mapped[dt.date] = mapped_column(Date, nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = created_at_column()
