"""VisionItem — one image or phrase on the Vision board.

`media_path` names a file under the configured media directory — "uploads
become links to local media, not copies in the database" was a discovery
commitment — so no image bytes are stored in this schema. `area` is the
optional Mandala tag (discovery Q10) used to pick the few items the Dashboard
strip shows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from life.domain.models import Base, created_at_column
from life.domain.models.goal import GOAL_AREAS

__all__ = ["VisionItem"]


class VisionItem(Base):
    """One image or phrase. An image references a file; a phrase is text."""

    __tablename__ = "vision_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: File name under the media directory; required for an image, never set
    #: for a phrase (both halves enforced by the CHECKs below).
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    area: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("kind IN ('image', 'phrase')", name="kind"),
        CheckConstraint(
            f"area IS NULL OR area IN ({', '.join(repr(a) for a in GOAL_AREAS)})",
            name="area",
        ),
        CheckConstraint(
            "(kind = 'image') = (media_path IS NOT NULL)",
            name="kind_media_pair",
        ),
        CheckConstraint(
            "kind <> 'phrase' OR text IS NOT NULL",
            name="phrase_has_text",
        ),
    )
