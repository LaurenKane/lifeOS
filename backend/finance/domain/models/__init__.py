"""finance.domain.models - the SQLAlchemy ORM. PRIVATE.

The declarative base plus the shared column conventions. Table definitions
themselves land with the M1 migrations (bead LifeOS-6), which is deliberate:
defining tables here without the migrations that create them produces a model
layer that disagrees with the database, and that disagreement is silent until a
query fails in production.

Conventions every table follows:

- **Amounts are `BigInteger` minor units**, never `Numeric`. `Numeric(18,2)` is
  wrong for JPY (0 decimals) and crypto, and accumulates FX rounding errors.
- **No `user_id`.** Single user, by decision (section E).
- **No `direction` column.** Sign plus `account_nature` is the encoding; adding
  a `direction` column contradicts it.
- **No cross-schema foreign keys.** One schema per module, enforced by grants.

`finance.domain.models` is PRIVATE: nothing outside the finance module may
import it, and `finance.public` is the only export surface.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "AMOUNT_TYPE",
    "NAMING_CONVENTION",
    "Base",
    "created_at_column",
]

#: Every money column is a signed BIGINT of minor units. The exponent lives in
#: `currency.decimals`, which is the only authority for interpreting it.
AMOUNT_TYPE: Final = BigInteger

# Deterministic constraint names. Alembic autogenerate emits better diffs when
# constraint names are not random, and a name is required to drop or alter a
# constraint in a migration at all.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def created_at_column() -> Mapped[object]:
    """A `created_at` column: timezone-aware, server-defaulted, immutable.

    Only `created_at`, and no `updated_at`. A mutable timestamp column invites an
    UPDATE that sweeps in columns it should not touch, which is how the
    `raw_data_immutable` invariant gets violated by an innocent-looking helper.
    """
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Base(DeclarativeBase):
    """Declarative base for every table in the finance schema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Mapped[int] would make every primary key an INT4. Everything here is BIGINT
    # because the schema uses BIGSERIAL throughout.
    type_annotation_map = {int: BigInteger}
