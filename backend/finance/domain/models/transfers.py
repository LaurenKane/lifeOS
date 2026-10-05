"""Transfer matching: transfer_match.

A transfer is two journal lines in different accounts that are the same money
moving. The match is STORED, never recomputed: `transfer_match` is the
authorisation to treat two lines as one event, which is what stops a bank
transfer being counted as a withdrawal in one account and a deposit in the
other.

Why it is a table rather than a derived join: the matching sweep is a search
over hundreds of thousands of lines that runs nightly, and a decision made once
and recorded is both cheaper and inspectable. A user who disagrees edits the row.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base, created_at_column

__all__ = ["TransferMatch", "TransferReview", "TransferSkip"]


class TransferMatch(Base):
    """One stored decision that two journal lines are the same movement.

    Both sides point at `journal_line`. This table and `journal_line` are
    mutually referential, which is why `journal_line.transfer_match_id`'s
    foreign key is created by migration 0001 after both tables exist and then
    VALIDATEd, instead of inline. The module imports `journal_line`'s table by
    STRING, so neither module has to import the other's class.
    """

    __tablename__ = "transfer_match"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_line_id_out: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id", ondelete="CASCADE"), nullable=False
    )
    journal_line_id_in: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id", ondelete="CASCADE"), nullable=False
    )
    match_method: Mapped[str] = mapped_column(Text, nullable=False)
    # NUMERIC(3,2), explicitly: a bare `Mapped[Decimal]` renders an
    # unconstrained NUMERIC. 0.00 to 1.00 - a confidence over 1.00 is a bug in
    # the matcher, and (3,2) refuses to store it.
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("1.00")
    )
    created_at: Mapped[datetime] = created_at_column()
    # NULL means "the matcher decided"; non-NULL means "a human agreed". Those
    # are different claims and the review queue needs to tell them apart.
    # TIMESTAMPTZ explicitly - a bare `Mapped[datetime]` renders TIMESTAMP
    # WITHOUT TIME ZONE and drops the offset.
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # One match per ordered pair. Matching is directional - money leaves one
        # account and arrives in another - and the asymmetry is load-bearing:
        # the sweep looks for the unmatched outgoing leg, so an unordered pair
        # would be ambiguous about which side is which.
        UniqueConstraint("journal_line_id_out", "journal_line_id_in"),
        # A line cannot be its own counterparty. Uniqueness above does not imply
        # this: ('x','x') is still a legal unique pair, and it would cancel the
        # line out of every balance while leaving no transfer visible.
        CheckConstraint(
            "journal_line_id_out <> journal_line_id_in",
            name="distinct",
        ),
    )

    # NOT modelled: idx_tm_out, idx_tm_in. Created by migration 0001.
    #
    # 'auto_amount_date' pairs on amount and date alone; 'auto_card_payment' is
    # the Amex monthly settlement, which must additionally be separated by
    # DESCRIPTION (`HARTELIJK BEDANKT VOOR UW BETALING`) because a statement's
    # credit section mixes that payment with real refunds. The migration's
    # CHECK carries the full list, verbatim.


class TransferReview(Base):
    """One outbound leg that needs a human to pick its counterparty.

    A question, not a link: the matcher queues it and a human confirms,
    rejects, or ignores it. At most one `pending` row per outbound (the
    migration's partial unique index); resolving sets `status` and
    `resolved_at`. `candidate_journal_line_ids` is the matched candidate set
    the human chooses from.
    """

    __tablename__ = "transfer_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: a review is meaningless without its outbound line, and a replay
    # deletes entries freely — the question must go with the line.
    outbound_journal_line_id: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id", ondelete="CASCADE"), nullable=False
    )
    candidate_journal_line_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = created_at_column()
    # NULL while pending; set when the review is confirmed, rejected, or
    # ignored. TIMESTAMPTZ explicitly — see `TransferMatch.confirmed_at`.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # NOT modelled: uq_transfer_review_pending_outbound, a PARTIAL unique
    # index ON transfer_review (outbound_journal_line_id)
    # WHERE status = 'pending'. Created by migration 0004. A partial unique
    # index is an `Index(...)` with a `postgresql_where` clause rather than a
    # table argument, so it lives with the migration that owns the `WHERE`,
    # not in the model. The `reason`/`status` CHECK vocabularies are the
    # migration's, verbatim, like `TransferMatch.match_method`.


class TransferSkip(Base):
    """An outbound/inbound pair the sweep must stop suggesting.

    Written by `ignore_review`, one row per candidate on the ignored review.
    The linker excludes these pairs from candidates, so ignoring is permanent
    until the rows themselves are deleted (which cascades).
    """

    __tablename__ = "transfer_skip"

    id: Mapped[int] = mapped_column(primary_key=True)
    outbound_journal_line_id: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id", ondelete="CASCADE"), nullable=False
    )
    inbound_journal_line_id: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        # One skip per ordered pair. Directional like the match itself: the
        # sweep looks for the unmatched outgoing leg.
        UniqueConstraint("outbound_journal_line_id", "inbound_journal_line_id"),
    )
