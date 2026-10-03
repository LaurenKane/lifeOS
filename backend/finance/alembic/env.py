"""Alembic environment for the finance schema.

Executed by Alembic via `exec`, not imported as a package — so the imports below
are deliberately untyped and this file is excluded from the mypy gate (see
`[tool.ruff.lint.per-file-ignores]` and the mypy exclude in pyproject.toml).

This file owns two things that must agree with the running application, or the
failure surfaces in production rather than here:

1. `search_path`. Both this file and `finance/db.py` pin it through
   `config.pg_connect_args`, because migration 0001 writes FK targets and
   trigger bodies as UNQUALIFIED names. They resolve against the session's
   search_path, so a session that resolves them differently gets
   `relation does not exist` — and a trigger body is resolved at COMMIT time,
   inside whatever connection the application happens to be using.

2. The database URL. It comes from `get_settings().DATABASE_URL`, not from the
   `.ini`. The `.ini` literal stays as the offline fallback so `alembic upgrade
   head --sql` still works with no environment at all.

NEVER `alembic revision --autogenerate` against this project. `target_metadata`
is populated, so the temptation is real and the output is wrong: the reflect
side sees every table visible on the connection's search_path, `public`
included, and a table this metadata does not know is reported as
`DropTableOp`. Against the ledger that is a proposal to drop `journal_entry`.
Write the migration by hand, the way 0001 was written.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from config import get_settings, pg_connect_args
from finance.domain.models import SCHEMA, Base
from sqlalchemy import engine_from_config, pool

# The Alembic Config object, providing the values in the .ini file in use.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The 15 tables of `finance.*`. Populated by M1 (bead LifeOS-6). Populating it
# does NOT make autogenerate safe here - see the module docstring.
target_metadata = Base.metadata


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
        # The same search_path finance/db.py pins. See the module docstring.
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
