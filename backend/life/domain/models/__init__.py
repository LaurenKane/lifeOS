"""life.domain.models — the SQLAlchemy ORM. PRIVATE.

Mirrors `finance.domain.models`, adjusted for what the life schema actually
holds. Every table here has a migration that creates it; the migration file is
the source of truth and this package mirrors it.

Conventions carried over from finance, and one deliberate difference:

- **No `user_id`.** Single user, by decision (`backend/finance/domain/models/
  __init__.py` states the same refusal).
- **No cross-schema foreign keys.** One schema per module; the
  `no_cross_schema_fk` event trigger aborts a qualified FK target at DDL time.
- **Every column type is explicit.** `Base.type_annotation_map` maps `int` to
  `BigInteger` for the BIGSERIAL keys, so every other integer column states
  its own width — `INTEGER` for aim-days, `SMALLINT` for nothing here.
  A `Mapped[datetime]` left implicit renders TIMESTAMP WITHOUT TIME ZONE; every
  datetime column spells `DateTime(timezone=True)`.

The difference: **no `updated_at`Markup column and no trigger maintaining one.**
Finance needs it for replay; `life` rows are append-and-patch with no replay
surface, and an untriggered `updated_at` column is a second clock lying by
omission. `created_at`, server-defaulted, is the only timestamp the database
owes a reader here.

Three things that are not optional, cost a schema if wrong, and are the reason
the structure copies finance's:

1. `metadata` carries `schema="life"` — the same reasoning finance documents:
   it makes every FK string UNQUALIFIED, which is exactly what
   `no_cross_schema_fk` demands.
2. Column types explicit (above).
3. No `Identity()`. `mapped_column(primary_key=True)` renders BIGSERIAL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

__all__ = [
    "NAMING_CONVENTION",
    "SCHEMA",
    "Base",
    "created_at_column",
    "metadata",
]

#: The one Postgres schema every table in this package lives in. Carried by
#: the metadata below; repeated here because every other layer needs it, and
#: this is the module that owns the schema's name (ADR 0010: name picked once).
SCHEMA: Final[str] = "life"

#: Deterministic constraint names — the same convention finance uses, so a
#: migration diff between the two schemas reads the same way.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def created_at_column() -> MappedColumn[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Base(DeclarativeBase):
    """Declarative base for every table in the life schema."""

    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)

    type_annotation_map = {int: BigInteger}


#: Module-level alias, mirroring finance — `from life.domain.models import
#: metadata` without reaching through the class.
metadata: Final[MetaData] = Base.metadata


# The table modules, imported LAST for their side effect: importing each one
# registers its classes on `Base.metadata`. Above this line `Base` exists but
# carries no tables. `thought`'s FKs point at `action`, `goal` and `upkeep` by
# string; SQLAlchemy resolves them lazily at DDL time, by which point every
# class is registered — the same cycle finance's `journal_line` /
# `transfer_match` pair relies on, and the same reason it is harmless.
from life.domain.models import (  # noqa: E402
    action,
    away,
    goal,
    thought,
    upkeep,
    vision,
)

__all__ += [
    "action",
    "away",
    "goal",
    "thought",
    "upkeep",
    "vision",
]
