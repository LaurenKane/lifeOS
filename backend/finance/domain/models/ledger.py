"""The canonical ledger: journal_entry, journal_line.

Double entry, and nothing else is canonical. A transaction is one
`journal_entry` (the fact: when, what) and two or more `journal_line` rows (the
arithmetic: which account, how much, in which currency).

The balance rule is enforced by the DATABASE, not by this module: migration
0001 creates `assert_journal_entry_balances()` as a DEFERRABLE INITIALLY
DEFERRED constraint trigger, so the check runs at COMMIT when every line of the
entry exists. An application-side check would reject the first line of every
entry, which is the ordinary way an entry is written.

Sign carries direction. Debits negative, credits positive. There is no
`direction` column, and adding one would contradict that.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base, created_at_column, updated_at_column

__all__ = ["JournalEntry", "JournalLine"]


class JournalEntry(Base):
    """One accounting fact, and the header every line hangs off.

    Deliberately free of amounts. Every number lives on `journal_line`, so
    `entry_date` cannot disagree with a line's own idea of when the transaction
    happened, and no query has to reconcile a header total against its detail.
    """

    __tablename__ = "journal_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set by the transfer-matching sweep, not by a user. An entry that is a
    # transfer is shown differently and excluded from spending, and getting
    # that wrong is the single most misleading thing this application can do.
    is_transfer: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    is_split: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # When a human confirmed the entry as correct. Not `is_verified`: a NULL
    # here means "nobody has said", which is different from "nobody said yes".
    # TIMESTAMPTZ explicitly - a bare `Mapped[datetime]` renders TIMESTAMP
    # WITHOUT TIME ZONE and drops the offset.
    user_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()
    # Server-maintained by trg_journal_entry_updated_at. See
    # updated_at_column() for why there is no Python-side onupdate.
    updated_at: Mapped[datetime] = updated_at_column()

    # NOT modelled: idx_je_date, a plain btree on (entry_date DESC). It is
    # created by migration 0001. `DESC` is not expressible as a column list
    # without reaching for a text() expression, and an index definition is not
    # worth duplicating in two places.


class JournalLine(Base):
    """One side of an entry: an amount against one account, in one currency.

    The numeric columns are the only NUMERIC in the schema, and they are not
    money. `amount` is money and it is a signed BIGINT of minor units.
    `amount_base` is the EUR value of that amount, and it exists because a
    balance has to be addable across currencies; storing it at import time is
    what makes history immune to a rate feed changing its mind.
    """

    __tablename__ = "journal_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: a line is meaningless without its entry, and the balance trigger
    # has to see the parent disappear so a deleted entry is not left holding an
    # orphaned unbalanced line.
    journal_entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), nullable=False)

    # Account currency, minor units, SIGNED.
    amount: Mapped[int] = mapped_column(nullable=False)
    # A snapshot of `account.currency` as it was when the line was written.
    # Not a live view of the account's current currency: an account whose
    # currency is corrected must not silently re-denominate its history.
    currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    # EUR, signed, converted at import time. NUMERIC(18,4) explicitly: a bare
    # `Mapped[Decimal]` renders an unconstrained NUMERIC, which is not the
    # column the migration created.
    amount_base: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    # The rate that produced amount_base, kept so the conversion is
    # reproducible and auditable. NUMERIC(20,10): enough for crypto pairs,
    # which need far more than the 8 decimals a fiat rate does.
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, server_default=text("1.0")
    )
    # The original foreign amount, when this line IS the foreign one. Amex
    # reports in USD and books in EUR, and this is what lets the PDF be
    # replayed without re-deriving the original figure.
    foreign_amount: Mapped[int | None] = mapped_column(nullable=True)
    foreign_currency: Mapped[str | None] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=True
    )

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("category.id"), nullable=True
    )
    merchant_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant.id"), nullable=True
    )
    # NOT NULL-looking but nullable, because `transfer_match` points back at
    # this table: the pair is cyclic, so migration 0001 creates this table
    # first, then ALTERs it in the foreign key once `transfer_match` exists,
    # then VALIDATEs it - a real validated constraint, not a permanently NOT
    # VALID one. Modelling it here needs no such ceremony: SQLAlchemy resolves
    # an FK string lazily, when DDL is emitted, and by then both tables are
    # registered. The two modules never import each other's class.
    transfer_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfer_match.id"), nullable=True
    )

    # The Amex card-payment leg. A payment is two legs in two currencies, and
    # the synthesised one is the side no statement ever printed; without it the
    # card balance and the bank balance disagree by the full amount every
    # month.
    is_synthesized: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    synthesized_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Investment seam, same reasoning as account.security_id: the table does not
    # exist yet, and a cross-schema FK is impossible under no_cross_schema_fk.
    security_id: Mapped[int | None] = mapped_column(nullable=True)
    # For an investment line: how much of the security, and at what price.
    # NUMERIC(24,12) because ETH quantities need 18 decimals and BTC needs 8;
    # (18,4) would truncate a satoshi.
    units: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    price_per_unit: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    # INT, explicitly - `Mapped[int]` would render BIGINT here.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        # The Amex SOURCE convention is the inverse of the ledger's ("charges
        # positive"), and the Amex PDF carries no sign at all - direction is a
        # separate CR marker line. So an adapter must flip EXPLICITLY and emit
        # already-signed minor units; the normalizer must not flip twice. The
        # flip is a decision, not an implication
        # (docs/adr/0003-import-decisions-real-export.md Decision 1).
        CheckConstraint(
            "synthesized_reason IN "
            "('card_payment','sepa_dd','investment','opening_balance')",
            name="synthesized_reason",
        ),
        # `is_synthesized` and `synthesized_reason` are two columns carrying one
        # fact, and migration 0001 let them disagree: a leg could claim to be
        # synthesized for no stated reason. A matcher that keys on one column can
        # then be contradicted by the other, so the pair is made total (migration
        # 0003): a synthesized leg always says why, and a real leg never does.
        CheckConstraint(
            "(is_synthesized AND synthesized_reason IS NOT NULL)"
            " OR (NOT is_synthesized AND synthesized_reason IS NULL)",
            name="synthesized_reason_present",
        ),
    )

    # NOT modelled: idx_jl_entry (journal_entry_id), idx_jl_acct
    # (account_id, journal_entry_id) - deliberately NOT a denormalised
    # entry_date, because section G Tier 2 mutates entry_date in place when a
    # pending entry merges into a posted one, and a copied date would then
    # misstate which month a transaction falls in; and the four partial indexes
    # idx_jl_cat, idx_jl_uncat (the uncategorised review queue),
    # idx_jl_unmatched, idx_jl_transfer. All created by migration 0001.
