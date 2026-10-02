"""Alembic environment for the finance schema.

Executed by Alembic via `exec`, not imported as a package — so the imports below
are deliberately untyped and this file is excluded from the mypy gate (see
`[tool.ruff.lint.per-file-ignores]` and the mypy exclude in pyproject.toml).

No migrations are written here. Bead LifeOS-6 (M1) owns the schema, including
the deferrable balance trigger and the `raw_data_immutable` DB trigger. M0
provides the scaffolding only.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# The Alembic Config object, providing the values in the .ini file in use.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Populated by M1 once `finance.domain.models.Base` carries the finance tables.
target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    The only way to inspect a migration without a live database, so it has to
    work.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="finance",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="finance",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
