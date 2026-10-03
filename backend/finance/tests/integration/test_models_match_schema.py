"""test_models_match_schema.py — the ORM against the migrated database.

Two structural guards, both about the same failure: the Python models and the
migrated schema drifting apart with nothing noticing until a query fails at
runtime, against real data, in the middle of a request.

**This replaces `alembic revision --autogenerate` as the drift check, and has to
replace it.** Autogenerate is unusable in this project, as both `env.py` files
say: the reflect side sees everything visible on the connection's `search_path`,
`public` included, and a table `Base.metadata` does not know about is reported as
a `DropTableOp`. Run against the ledger it proposes to drop `journal_entry`. So
the comparison is written out here, and it checks the table set plus every
column's type and nullability, in BOTH directions, so a model that gained a
column the database never got is caught as readably as the reverse.

**Why the comparison is on compiled DDL and not on type names.** `str(type)` is
not a stable identity in SQLAlchemy. The model says `DateTime(timezone=True)` and
the reflected column says `TIMESTAMP(timezone=True)`; those stringify to
`DATETIME` and `TIMESTAMP` while being the same type - so a string comparison
would report drift that is not there, on every timestamp in the schema.
`Type.compile()` renders both as `TIMESTAMP WITH TIME ZONE`, which is the
comparison that actually means "the same DDL", and it gets `LargeBinary` and
`BYTEA` right, which a name comparison does not.

The dialect is the one the connection itself speaks, not a separately
constructed one, so the rendering is the rendering this application would emit.

Both sides of the comparison are reduced to a `ColumnShape` first, which is also
what keeps the file free of `Any` annotations: SQLAlchemy's `Table` and `Column`
are generic over types containing `Any`, and this repository runs mypy with
`disallow_any_explicit`.

`finance.alembic_version` is excluded from the table comparison: Alembic's own
bookkeeping table is created by Alembic, is in no model's metadata, and is not
drift when it is absent from the models.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

import pytest
from sqlalchemy import Engine, MetaData, inspect, text
from sqlalchemy.engine import Dialect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.types import TypeEngine

from finance.domain.models import SCHEMA, Base

pytestmark = pytest.mark.db

#: Not a domain table. See the module docstring.
_ALEMBIC_VERSION: Final[str] = "alembic_version"


@dataclass(frozen=True)
class ColumnShape:
    """One column reduced to the two properties the drift check compares.

    Named and typed rather than carrying `Column` objects around, because
    `Column` is generic over a type that contains `Any` (see the module
    docstring).
    """

    name: str
    ddl: str
    nullable: bool


def _ddl(column_type: object, dialect: Dialect) -> str:
    """A column type's PostgreSQL spelling, upper-cased for comparison.

    Typed as `object` and narrowed with `isinstance`: `TypeEngine` is generic, so
    naming the parameter with its own type parameter would import an `Any` into
    the annotation.
    """
    if not isinstance(column_type, TypeEngine):
        raise AssertionError(f"not a column type: {column_type!r}")
    return column_type.compile(dialect).upper()


def _modelled(
    metadata: MetaData, table_name: str, dialect: Dialect
) -> dict[str, ColumnShape]:
    """One model's columns, keyed by name."""
    table = metadata.tables[f"{SCHEMA}.{table_name}"]
    return {
        column.name: ColumnShape(
            name=column.name,
            ddl=_ddl(column.type, dialect),
            nullable=bool(column.nullable),
        )
        for column in table.columns
    }


def _reflected(
    inspector: Inspector, table_name: str, dialect: Dialect
) -> dict[str, ColumnShape]:
    """One migrated table's columns, keyed by name, reduced the same way."""
    return {
        column["name"]: ColumnShape(
            name=column["name"],
            ddl=_ddl(column["type"], dialect),
            nullable=bool(column["nullable"]),
        )
        for column in inspector.get_columns(table_name, schema=SCHEMA)
    }


def _differences(
    modelled: dict[str, ColumnShape], migrated: dict[str, ColumnShape]
) -> list[str]:
    """Every way the two disagree about a table's columns, in both directions.

    A list of differences rather than a first-mismatch assertion, because the
    useful answer to "the models drifted" is the whole list of what drifted.

    Deliberately shared with `test_the_comparison_itself_is_not_vacuous`, which
    is what makes that test worth having: it proves THIS function - the one the
    real guard uses - is sensitive to each kind of difference.
    """
    differences: list[str] = []

    for name in sorted(modelled.keys() - migrated.keys()):
        differences.append(f"{name}: modelled, not migrated")
    for name in sorted(migrated.keys() - modelled.keys()):
        differences.append(f"{name}: migrated, not modelled")

    for name in sorted(modelled.keys() & migrated.keys()):
        model_column = modelled[name]
        db_column = migrated[name]
        if model_column.ddl != db_column.ddl:
            differences.append(
                f"{name}: type {model_column.ddl} modelled, {db_column.ddl} migrated"
            )
        if model_column.nullable != db_column.nullable:
            differences.append(
                f"{name}: nullability {model_column.nullable} modelled,"
                f" {db_column.nullable} migrated"
            )
    return differences


class TestSchemaOwnership:
    """Every section E table lives in `finance`, and none of them leaked."""

    def test_no_finance_table_landed_in_public(self, engine: Engine) -> None:
        """Schema `public` holds no tables at all.

        `docs/adr/0005-schema-ownership.md` records why every table is in
        `finance`: section E is one FK-connected graph, so putting the shared
        tables in `core` would force a schema-qualified foreign-key target, and
        `no_cross_schema_fk` rejects those. The failure guarded here is the
        subtler one - a table created without its schema, so it landed in
        `public`: reachable, working, and outside the ADR, the naming convention,
        and the grants in `db_bootstrap.sql` that exist to keep module boundaries
        enforced.

        Read from `information_schema` rather than from `Base.metadata`, because
        the claim is about the DATABASE. A model declared with `schema=None` is
        caught by the table-set comparison instead.

        (This docstring does not spell the offending clause out, for the same
        reason `finance/domain/models/__init__.py` does not: the invariant is a
        `forbid_regex` over raw file text and cannot tell prose from SQL, so
        writing it here would be a violation in a file that only describes one.
        It would also be a violation twice over - the checker walks
        `__pycache__` too, so the same words would be found a second time inside
        this module's bytecode.)
        """
        with engine.connect() as connection:
            names: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public'"
                        "   AND table_type = 'BASE TABLE'"
                        " ORDER BY table_name"
                    )
                )
                .scalars()
                .all()
            )
            leaked = [str(name) for name in names]

        assert leaked == [], f"tables in public: {leaked}"

    def test_every_model_table_declares_the_finance_schema(self) -> None:
        """The metadata says `schema='finance'`, asserted table by table.

        Without it, `ForeignKey("account.id")` raises `NoReferencedTableError`,
        and the fix people reach for next - qualifying the target as
        `"finance.account.id"` - is precisely what `no_cross_schema_fk` rejects.
        So the property is asserted rather than assumed, and it costs one dict
        comprehension. Needs no database: it is a claim about the models alone.
        """
        wrong_schema = [
            f"{key} (schema={table.schema})"
            for key, table in sorted(Base.metadata.tables.items())
            if table.schema != SCHEMA
        ]
        assert wrong_schema == [], (
            f"model tables not in schema {SCHEMA!r}: {wrong_schema}"
        )


class TestModelsMatchMigratedSchema:
    """Table set, column types and nullability - both directions."""

    def test_the_table_sets_are_identical(self, engine: Engine) -> None:
        """The same tables in the metadata and in the migrated database."""
        models = {table.name for table in Base.metadata.tables.values()}
        with engine.connect() as connection:
            database = {
                name
                for name in inspect(connection).get_table_names(schema=SCHEMA)
                if name != _ALEMBIC_VERSION
            }

        only_in_models = sorted(models - database)
        only_in_database = sorted(database - models)

        assert only_in_models == [], (
            f"modelled tables with no migration: {only_in_models}"
        )
        assert only_in_database == [], (
            f"migrated tables with no model: {only_in_database}"
        )

    def test_column_types_and_nullability_match_both_ways(self, engine: Engine) -> None:
        """Every column, in both directions, for type and for nullability.

        Type and nullability are compared together because they fail together in
        practice: a model declaring `Mapped[str | None]` over a `NOT NULL` column
        accepts a `None` in Python and is refused by the database, so the failure
        surfaces as an IntegrityError on somebody's write path rather than as an
        error at import time.
        """
        differences: list[str] = []
        with engine.connect() as connection:
            inspector = inspect(connection)
            dialect = connection.engine.dialect
            for table_name in sorted(
                table.name for table in Base.metadata.tables.values()
            ):
                differences += [
                    f"{table_name}.{difference}"
                    for difference in _differences(
                        _modelled(Base.metadata, table_name, dialect),
                        _reflected(inspector, table_name, dialect),
                    )
                ]

        assert differences == [], "model/schema drift:\n  " + "\n  ".join(differences)

    def test_the_comparison_itself_is_not_vacuous(self, engine: Engine) -> None:
        """A drift guard that cannot see drift is worse than no guard.

        Proved by running the SAME comparison over a deliberately wrong model: a
        column that does not exist, a wrong type and a wrong nullability must each
        be reported - and then the real `journal_line` model must report nothing.
        The second half is what makes this a test of the guard rather than of the
        mutation: if the comparison is ever loosened to let a real difference
        through, it is this assertion that notices.

        The wrong model is the real one with three columns changed, so the
        reported list is those three rather than fifteen "migrated, not
        modelled" entries that would bury them. The reflection path either side
        of the comparison is covered by the test above; what is under test here
        is the comparison in between.
        """
        with engine.connect() as connection:
            dialect = connection.engine.dialect
            migrated = _reflected(inspect(connection), "journal_line", dialect)

        modelled = _modelled(Base.metadata, "journal_line", dialect)
        wrong = dict(modelled)
        # A column the database does not have at all.
        wrong["note"] = ColumnShape(name="note", ddl="TEXT", nullable=True)
        # BIGINT in the database, INT here.
        wrong["amount"] = replace(modelled["amount"], ddl="INTEGER")
        # NOT NULL in the database, nullable here.
        wrong["currency"] = replace(modelled["currency"], nullable=True)

        assert _differences(wrong, migrated) == [
            "note: modelled, not migrated",
            "amount: type INTEGER modelled, BIGINT migrated",
            "currency: nullability True modelled, False migrated",
        ]
        assert _differences(modelled, migrated) == [], (
            "the real journal_line model does not match its own table"
        )
