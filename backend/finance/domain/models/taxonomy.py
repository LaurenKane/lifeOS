"""Merchant normalisation and categorisation: merchant, merchant_alias,
category, category_rule.

The merchant layer answers "who is this?" from a string the bank chose freely;
the category layer answers "what kind of thing is this?" from a rule. They are
separate tables because they change for different reasons and are edited by
different people: a user fixing "ALBERT HEIJN 1234 AMSTERDAM" -> "Albert Heijn"
is teaching a name, not a category.

`category` is a self-referencing tree, and the UNIQUE constraint is on
(parent_id, name) rather than on name alone: two different branches are
entitled to their own "Other".
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base, created_at_column

__all__ = ["Category", "CategoryRule", "Merchant", "MerchantAlias"]


class Merchant(Base):
    """A canonical merchant name: 'Albert Heijn'.

    One row per real merchant, so that eleven different spellings of a
    supermarket collapse onto one identity and one set of transactions.
    """

    __tablename__ = "merchant"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_column()


class MerchantAlias(Base):
    """One observed raw string, and how sure we are it means that merchant.

    `raw_string` is the whole point of the table, and it is globally UNIQUE:
    one string maps to one merchant or the mapping is not a mapping. `confidence`
    is what the matcher believed at the time, kept rather than overwritten, so a
    decision that turns out wrong can be found by querying for low confidence
    instead of by hoping somebody notices.
    """

    __tablename__ = "merchant_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: an alias with no merchant is a string that matches nothing.
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("merchant.id", ondelete="CASCADE"), nullable=False
    )
    # 'ALBERT HEIJN 1234 AMSTERDAM' - the bank's string, verbatim and
    # unnormalised. This is the trigram-search key (idx_alias_raw).
    raw_string: Mapped[str] = mapped_column(Text, nullable=False)
    # NUMERIC(3,2), explicitly. A bare `Mapped[Decimal]` renders an
    # unconstrained NUMERIC and (3,2) is what makes 1.00 the maximum expressible.
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.50")
    )
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_seen: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )

    __table_args__ = (UniqueConstraint("raw_string"),)

    # NOT modelled: idx_alias_raw, a GIN index using gin_trgm_ops on
    # raw_string. Created by migration 0001, and it needs the pg_trgm
    # extension that the bootstrap installs.


class Category(Base):
    """A spending category, in a tree.

    `kind` is what the ledger asks about, not what the user calls it: an
    'expense' category reduces the balance and an 'income' category raises it,
    and a 'transfer' category is neither. A tree node with the wrong kind would
    silently invert a balance.
    """

    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Self-referencing. Nullable, not NOT NULL: a top-level category has no
    # parent. No ON DELETE clause, so deleting a category with children is
    # refused rather than silently promoting its children to the root.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # INT, explicitly - `Mapped[int]` would render BIGINT through the
    # declarative annotation map.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Marks the categories this application ships with, so a user's rename can
    # be told apart from a user-defined category and restored.
    is_system: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('expense','income','transfer','investment')",
            name="kind",
        ),
        # Per parent, not globally: two branches are each entitled to their own
        # 'Other'. Under Postgres' default NULL semantics a NULL parent_id does
        # NOT collide with another NULL, so top-level categories are unconstrained
        # here; the root-level case is a known gap, not an oversight (see the
        # NULLS NOT DISTINCT note in recurring.py for how this project handles
        # that case when it matters).
        UniqueConstraint("parent_id", "name"),
    )


class CategoryRule(Base):
    """A rule that assigns a category to a line, and what it outranks.

    `priority` is the ordering: lower wins. The value is a plain INT, not a
    CHECK-constrained enum, because adding a new tier in the middle of the
    seven-layer engine must not require rewriting every existing rule's
    priority - the default of 100 is what lets a new layer slot in above the
    defaults without touching them.
    """

    __tablename__ = "category_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )
    # All three match criteria are optional and are ANDed when present. A rule
    # with only a description_pattern is a general rule; one with all three is
    # the most specific rule in the system.
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), nullable=True
    )
    merchant_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant.id"), nullable=True
    )
    # Trigram / ILIKE match against `source_record.raw_description`, which is
    # why that column is indexed with gin_trgm_ops.
    description_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"), nullable=False)
    # True for a rule the user taught the system by correcting a line. Kept
    # distinct from a shipped rule so that a correction can be undone without
    # losing the user's own learning.
    is_learned: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("1.00")
    )
    created_at: Mapped[datetime] = created_at_column()

    # NOT modelled: idx_rule_lookup, a btree on
    # (merchant_id, description_pattern, priority). Created by migration 0001 -
    # it is the evaluation order written into the index.
