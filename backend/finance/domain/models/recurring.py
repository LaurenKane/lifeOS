"""Recurring transaction detection: recurring_series.

The output of the nightly recurring sweep: a series the detector believes is
the same money moving on a schedule. `status` is what separates a guess from a
fact, and 'detected' is the default - a series arrives unconfirmed and the user
promotes it.

This is the one table where the uniqueness of the deduplication key depends on
PostgreSQL 15's NULLS NOT DISTINCT, so the key is spelled out at the site.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base

__all__ = ["RecurringSeries"]


class RecurringSeries(Base):
    """A detected-or-confirmed repeating movement in one account.

    The detector proposes, the user disposes. Nothing here is written by the
    ledger: a series is an observation about history, so removing one removes
    an observation and not a transaction.
    """

    __tablename__ = "recurring_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)
    # Nullable, and the nullability is load-bearing: an uncategorised recurring
    # charge is entirely normal, and under Postgres' default NULL semantics two
    # NULL merchants are NOT equal to each other. A plain UNIQUE constraint on
    # the key below would therefore permit exactly the duplicate an
    # uncategorised series is most likely to produce - see __table_args__.
    merchant_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant.id"), nullable=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True
    )
    # NUMERIC(18,4), explicitly - a bare `Mapped[Decimal]` renders an
    # unconstrained NUMERIC. Same width and scale as `journal_line.amount_base`
    # so the two are directly comparable, which is the whole point: this is the
    # EUR amount being matched.
    amount_base: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    first_date: Mapped[date] = mapped_column(Date, nullable=False)
    # NULL while the series is still being observed. NOT NULL would mean the
    # detector had to invent an end date on the first sighting.
    last_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # NULL means "not yet scored", which is different from a score of zero.
    detection_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="detected")

    __table_args__ = (
        CheckConstraint(
            "frequency IN ('weekly','biweekly','monthly','quarterly','yearly')",
            name="frequency",
        ),
        CheckConstraint(
            "status IN ('detected','confirmed','dismissed')",
            name="status",
        ),
        # The detector's identity of a series, and it is NULLS NOT DISTINCT.
        #
        # Migration 0001 creates this as a UNIQUE INDEX rather than a table
        # constraint, because `NULLS NOT DISTINCT` is a Postgres 15+ feature and
        # the deployment target is 17 (docker-compose.yml pins
        # postgres:17-alpine). Modelled here as the equivalent constraint rather
        # than left as a comment, because unlike the partial indexes this one
        # SQLAlchemy can express exactly.
        #
        # Without NULLS NOT DISTINCT this constraint is a trap: merchant_id is
        # nullable, and a plain UNIQUE lets any number of series with a NULL
        # merchant and otherwise identical keys coexist - the single most likely
        # duplicate this table could produce.
        UniqueConstraint(
            "account_id",
            "merchant_id",
            "amount_base",
            "frequency",
            "first_date",
            postgresql_nulls_not_distinct=True,
        ),
    )
