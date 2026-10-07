"""budget: one spending limit on one category, for one period.

The stored side of `finance.domain.services.budget`, which is pure arithmetic
over these rows — the comparison lives there, never here.

There is deliberately no `name` column: a budget is a limit ON A CATEGORY, so
the category's own name is the label, and a second copy stored on this row
would go stale the moment somebody renames the category. There are no
start/end dates either: `check_budget` compares `spent` against `limit` for
one period the caller has already selected, so a period BOUNDARY is not
something this arithmetic has ever seen — the `period` column records how
often the limit repeats, nothing more.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base, created_at_column

__all__ = ["Budget"]


class Budget(Base):
    """A limit on one category's spending, per period.

    `amount` is BIGINT minor units and positive by CHECK — a zero limit is not
    a smaller budget, it is a budget that is already broken, and the service's
    `used_ratio` zero-division guard exists for arithmetic robustness rather
    than because zero is a value this table admits.

    No ON DELETE clause on `category_id`: a budget whose category vanished is
    a limit on nothing, so deleting a budgeted category is refused outright
    rather than silently orphaning or cascading the limit away.
    """

    __tablename__ = "budget"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The category this limits. Unqualified FK target — the metadata carries
    # the schema (see the package docstring); a qualified one is what the
    # `no_cross_schema_fk` event trigger reads as cross-schema.
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"), nullable=False)
    # BIGINT, explicitly — `Mapped[int]` would also render BIGINT through the
    # annotation map, but every column in this package states its own type.
    # Never `Numeric`: minor units are integers, and a float budget is off by
    # a cent per transaction (see `domain/services/budget.py`).
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # CHAR(3), blank-padded by PostgreSQL — strip on read, the way
    # `routes/accounts.py` reads its own currency.
    currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    # 'monthly' | 'quarterly' | 'yearly'. Plain TEXT with a CHECK rather than
    # a Postgres enum: adding a value is then an ALTER, not a type rewrite —
    # same reasoning as `category.kind`.
    period: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "period IN ('monthly','quarterly','yearly')",
            name="period",
        ),
    )
