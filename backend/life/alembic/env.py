"""Alembic environment for the life schema.

Executed by Alembic via `exec`, not imported as a package — so the imports
below are deliberately untyped and this file is excluded from the mypy gate
(the same exclusion finance's env.py sits under; `pyproject.toml`'s
`backend/*/alembic/env.py` patterns cover this file automatically).

This file owns the two things that must agree with the running application:
the `search_path` (pinned through `config.pg_connect_args`, same helper as
`life/db.py`) and the database URL (from `get_settings().DATABASE_URL`).

NEVER `alembic revision --autogenerate` against this project — the argument
is finance's env.py's and transfers verbatim: the reflect side sees every
table visible on the `search_path`, and a table the metadata does not know is
reported as a `DropTableOp`. Write the migration by hand, the way 0001 was
written.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from config import get_settings, pg_connect_args
from life.domain.models import SCHEMA, Base
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The life tables. Populated here for completeness; never makes autogenerate
# safe — see the module docstring.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection, URL from settings."""
    section = dict(config.get_section(config.config_ini_section, {}) or {})
    section["sqlalchemy.url"] = get_settings().DATABASE_URL
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=pg_connect_args(SCHEMA),
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
