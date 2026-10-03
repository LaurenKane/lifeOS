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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base, created_at_column

__all__ = ["TransferMatch"]


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
        ForeignKey("journal_line.id"), nullable=False
    )
    journal_line_id_in: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id"), nullable=False
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
