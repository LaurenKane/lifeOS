"""Reference data: currency, exchange_rate, institution.

The three tables everything else hangs off. `currency` is the authority for
interpreting a BIGINT amount - `currency.decimals` is the exponent, which is
why there is no whitelist of currencies anywhere in the codebase: JPY (0),
BTC (8) and ETH (18) are rows, not special cases.

One schema per module (section E), and the FKs here are the proof: a
`finance.currency` row cannot be deleted while anything references it, and a
currency cannot quietly come from another schema.
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
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from finance.domain.models import Base

__all__ = ["Currency", "ExchangeRate", "Institution"]


class Currency(Base):
    """An ISO-4217 currency plus the exponent that gives an amount meaning."""

    __tablename__ = "currency"

    # A CHAR(3), not a 3-character VARCHAR: Postgres CHAR is blank-padded to a
    # fixed width and compares equal for 'EUR' and 'EUR ', so 'EUR ' cannot
    # become a second, distinct currency that no join ever finds.
    code: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # SMALLINT, not INT and not BIGINT: 0..18 is the whole range, and the
    # annotation alone would render BIGINT because of type_annotation_map.
    decimals: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("2"), nullable=False
    )
    # 0..18 because that is the range a NUMERIC(18,x) column can carry. A
    # currency outside it cannot round-trip through journal_line.amount_base, so
    # it is refused at insert rather than at report time.
    __table_args__ = (CheckConstraint("decimals BETWEEN 0 AND 18", name="decimals"),)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class ExchangeRate(Base):
    """One day's rate for one (base, quote) pair.

    Stored, never fetched at read time: the ledger's `journal_line.exchange_rate`
    is a snapshot of what this table said on the day the entry was booked, so
    re-pricing history is impossible and a rate feed going down changes nothing
    that is already booked.
    """

    __tablename__ = "exchange_rate"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # Always 'EUR' in v1. That is a convention, not a constraint: the schema
    # stores the pair properly so a non-EUR base needs no migration later.
    base_currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    quote_currency: Mapped[str] = mapped_column(
        CHAR(3), ForeignKey("currency.code"), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="ecb")

    __table_args__ = (
        # One rate per day per pair. Without it, two imports on the same day
        # silently produce two prices for one conversion.
        UniqueConstraint("date", "base_currency", "quote_currency"),
        # A zero or negative rate turns a signed amount into a nonsense one, and
        # the arithmetic that consumes it happens far from the insert that
        # caused it. Refuse it here instead.
        CheckConstraint("rate > 0", name="rate_positive"),
    )


class Institution(Base):
    """A bank or card issuer, named once and referenced everywhere.

    `provider_key` is the mapping from this project's vocabulary to the
    provider's own identifiers, which is the join key an adapter needs before it
    has parsed anything.
    """

    __tablename__ = "institution"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 'rabobank' | 'revolut' | 'amex_nl'
    provider_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # CHAR(2) ISO-3166-1 alpha-2. Defaults to NL because every provider in v1
    # is Dutch; it is still stored, because a Revolut account has one and a
    # system that cannot record that will guess wrong later.
    country_code: Mapped[str] = mapped_column(
        CHAR(2), nullable=False, server_default="NL"
    )
