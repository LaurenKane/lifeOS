"""test_models_match_schema.py — the life ORM against the migrated database.

The same structural guard finance's `test_models_match_schema.py` runs, on the
`life` schema: table set plus every column's type and nullability, in BOTH
directions. This replaces `alembic revision --autogenerate` as the drift check
for the same reason finance's docstring gives — autogenerate's reflect side
cannot be trusted with a `search_path` that sees other schemas.

`life.alembic_version` is excluded from the comparison: Alembic's bookkeeping
table belongs to no model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

import pytest
from sqlalchemy import Engine, MetaData, inspect
from sqlalchemy.engine import Dialect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.types import TypeEngine

from life.domain.models import SCHEMA, Base

pytestmark = pytest.mark.db

#: Not a domain table. See the module docstring.
_ALEMBIC_VERSION: Final[str] = "alembic_version"


@dataclass(frozen=True)
class ColumnShape:
    """One column reduced to the two properties the drift check compares."""

    name: str
    ddl: str
    nullable: bool


def _ddl(column_type: object, dialect: Dialect) -> str:
    """A column type's PostgreSQL spelling, upper-cased for comparison."""
    if not isinstance(column_type, TypeEngine):
        raise AssertionError(f"not a column type: {column_type!r}")
    return column_type.compile(dialect).upper()


def _modelled(
    metadata: MetaData, table_name: str, dialect: Dialect
) -> dict[str, ColumnShape]:
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
    """Every way the two disagree about a table's columns, in both directions."""
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
    def test_every_life_table_declares_the_life_schema_in_metadata(self) -> None:
        """Same paragraph, second schema: the metadata carries `schema='life'`."""
        wrong_schema = [
            f"{key} (schema={table.schema})"
            for key, table in sorted(Base.metadata.tables.items())
            if table.schema != SCHEMA
        ]
        assert wrong_schema == [], f"model tables not in {SCHEMA!r}: {wrong_schema}"


class TestModelsMatchMigratedSchema:
    def test_the_table_sets_are_identical(self, engine: Engine) -> None:
        models = {table.name for table in Base.metadata.tables.values()}
        with engine.connect() as connection:
            database = {
                name
                for name in inspect(connection).get_table_names(schema=SCHEMA)
                if name != _ALEMBIC_VERSION
            }
        assert sorted(models - database) == []
        assert sorted(database - models) == []

    def test_column_types_and_nullability_match_both_ways(self, engine: Engine) -> None:
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
        """The guard can see drift, proven on a deliberately wrong model.

        The table under mutation is `life.action` — the one data table whose
        name matches nothing in finance's schema, so a typo'd `search_path`
        cannot satisfy this test against the wrong table."
        """
        with engine.connect() as connection:
            dialect = connection.engine.dialect
            migrated = _reflected(inspect(connection), "action", dialect)

        modelled = _modelled(Base.metadata, "action", dialect)
        wrong = dict(modelled)
        wrong["note"] = ColumnShape(name="note", ddl="TEXT", nullable=True)
        wrong["urgent"] = replace(modelled["urgent"], nullable=True)

        assert _differences(wrong, migrated) == [
            "note: modelled, not migrated",
            "urgent: nullability True modelled, False migrated",
        ]
        assert _differences(modelled, migrated) == [], (
            "the real action model does not match its own table"
        )
