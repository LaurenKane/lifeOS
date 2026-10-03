"""Accounts: account, provider_account_link.

`account` is the asset-vs-liability split made first-class, which is what lets
one balance query serve a checking account and a credit card without a CASE
expression per account type. `provider_account_link` is the seam between a
provider's opaque account identifier and our row, kept in its own table so the
identity resolver has one place to look and no adapter has to own a column.
"""

from __future__ import annotations

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base

__all__ = ["Account", "ProviderAccountLink"]


class Account(Base):
    """One real-world account: a checking account, a card, a bond, a loan.

    `account_type` is the provider's own taxonomy, because an adapter has to be
    able to write what the statement said. `account_nature` is ours: it is what
    the ledger asks when it needs to know which way an amount runs, and it is
    the only column that decides it.
    """

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable, not NOT NULL: a manually created account has no institution
    # until the user says which bank it is at.
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institution.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    account_type: Mapped[str] = mapped_column(Text, nullable=False)
    account_nature: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    # Free text, never normalised, never used as an identifier. `masked_pan`
    # exists so a card can be displayed without a full PAN ever entering the
    # system; neither column is a lookup key.
    iban: Mapped[str | None] = mapped_column(Text, nullable=True)
    masked_pan: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    is_hidden: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    # INT, not BIGINT. Explicitly, because `Mapped[int]` renders BIGINT through
    # `Base.type_annotation_map` and the migration created INT.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Investment seam. No FK, and deliberately so: the `security` table does not
    # exist yet, and an FK to a table in another schema is impossible anyway
    # under `no_cross_schema_fk`. A column that cannot be joined is the honest
    # state of an unfinished feature.
    security_id: Mapped[int | None] = mapped_column(nullable=True)
    # Canonical system-purpose account. NULL means "not designated". The only
    # value today is 'system_expense'; the CHECK and the partial unique index
    # (created in migration 0002) guarantee at most one account carries it.
    system_role: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # A CHECK rather than an Enum, so adding a provider's account type is a
        # new row in the CHECK and not an ALTER TYPE that rewrites the table.
        # Both lists are the migration's, verbatim.
        CheckConstraint(
            "account_type IN "
            "('checking','savings','credit_card','cash','investment','loan',"
            "'mortgage')",
            name="account_type",
        ),
        CheckConstraint(
            "account_nature IN ('asset','liability','equity')",
            name="account_nature",
        ),
        CheckConstraint(
            "system_role IS NULL OR system_role = 'system_expense'",
            name="system_role",
        ),
    )

    # NOT modelled: idx_account_nature, a PARTIAL index
    #     ON account (account_nature) WHERE is_active
    # It is created by migration 0001. A partial index is an `Index(...)` with a
    # `postgresql_where` clause rather than a table argument, so it lives with
    # the migration that owns the `WHERE`, not in the model.


class ProviderAccountLink(Base):
    """The mapping from a provider's account identifier to one of our accounts.

    The provider's identifier is an IBAN or a masked PAN exactly as the
    provider sends it. It is the join key for Tier-1 ingestion and it is not a
    primary key, because the same provider identifier can legitimately be
    re-issued to a different account by a different user later.
    """

    __tablename__ = "provider_account_link"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institution.id"), nullable=False
    )
    # CASCADE, not RESTRICT: the link has no meaning without the account, so
    # deleting the account must not be blocked by a row that only existed to
    # point at it.
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    # IBAN / masked PAN, as the provider sends it.
    provider_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # One provider identifier means one account, within one institution.
        # Scoped by institution rather than globally because two institutions
        # may issue the same masked PAN to different people.
        UniqueConstraint("institution_id", "provider_account_id"),
    )
