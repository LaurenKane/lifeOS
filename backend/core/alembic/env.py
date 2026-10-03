"""Alembic environment for the core schema.

Executed by Alembic via `exec`, not imported as a package — so the imports below
are deliberately untyped and this file is excluded from the mypy gate (see
`[tool.ruff.lint.per-file-ignores]` and the mypy exclude in pyproject.toml).

`target_metadata` stays None: `core` holds no tables of its own. Its only
object is the Alembic bookkeeping table, which Alembic creates itself, and
pointing it at another module's metadata would be `core` knowing about finance
- the coupling `.importlinter` contract 0 exists to prevent.

The `search_path` and the URL handling are identical to
`backend/finance/alembic/env.py`, with `core` in place of `finance`. That
symmetry is the point: a migration must never resolve a name differently from
the application, and the two files are where that is decided.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from config import get_settings, pg_connect_args
from sqlalchemy import engine_from_config, pool

# The Alembic Config object, providing the values in the .ini file in use.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The core schema is one Alembic bookkeeping table and nothing else, so there is
# no metadata for a migration to be compared against. See the module docstring.
target_metadata = None

SCHEMA = "core"


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    The only way to inspect a migration without a live database, so it has to
    work - including with no LIFEOS_DATABASE_URL set, which is why the URL here
    is the `.ini` literal and not `get_settings()`.
    """
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
    """Run migrations against a live connection.

    The URL is taken from settings and written over whatever the `.ini` says,
    so there is exactly one place a connection string is configured and no
    `sed`-into-a-temporary-file channel that can drift from it. The `.ini`
    value survives as the fallback for a shell with no LIFEOS_DATABASE_URL.
    """
    section = dict(config.get_section(config.config_ini_section, {}) or {})
    section["sqlalchemy.url"] = get_settings().DATABASE_URL
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # `core` first, then `public`. Identical in shape to the finance
        # environment so the two cannot drift apart in how they resolve a name.
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
