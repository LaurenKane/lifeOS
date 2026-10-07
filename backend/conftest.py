"""conftest.py — the DB-backed fixtures. The FIRST ones in this repository.

Everything here is opt-in behind the `db` marker, and nothing here runs during a
plain `pytest` (`make test`). That is deliberate: this repository's whole offline
suite - 459 tests - runs with no Docker and no Postgres, and a default that
quietly needs a database would make `make test` fail on a machine that has not
run `make up-db` yet. The database tests are selected explicitly, by
`make test-db` or by `pytest -m db`.

**WHY THERE IS NO skip-if-no-database FIXTURE.** The obvious implementation is
`pytest.skip("no database")` when `LIFEOS_TEST_DATABASE_URL` is unset, and it is
the wrong one. A skipped database test is a green gate that checked nothing,
which is the specific failure mode this repository's own CI comments rail
against (the strace guard in `tests/egress/`). So the fixture calls `pytest.fail`
instead: selecting the database tests
without a URL is a mistake in the command line, and it is reported as one.

**WHAT THE FIXTURE ACTUALLY DOES.** It does not call
`Base.metadata.create_all()`. It drops and recreates a database, runs
`backend/db_bootstrap.sql`, and then runs the REAL Alembic migrations through
the committed `backend/{core,finance}/alembic.ini`. `create_all()` would emit
tables and nothing else: the deferrable balance trigger, the
`raw_data_immutable` trigger and the `updated_at` trigger are all created by
`op.execute()` inside migration 0001, so a metadata-built schema would have
NONE of them and every test in here would pass against a database that enforces
nothing. The triggers are the subject under test; the harness has to build the
thing being tested.

Order matters, and it is not the other way round. `backend/db_bootstrap.sql` runs
FIRST because it is the part a migration cannot do for itself: both `env.py`
files set `version_table_schema`, so Alembic's own bookkeeping table is created
as `CREATE TABLE finance.alembic_version` and fails with `InvalidSchemaName`
before a single revision executes. That is exactly why docker-compose.yml mounts
the same file, and this fixture runs the same file rather than a copy of it.

**WHY THE CLEANUP IS TRUNCATE AND NOT ROLLBACK-PER-TEST.** The balance trigger
is `DEFERRABLE INITIALLY DEFERRED`, so it fires at COMMIT. A test that has to
prove "rejected at commit" needs a real commit, and a test that commits cannot
be rolled back afterwards. The alternative - one transaction per test - would
make those tests impossible to write, which means the guarantee they check would
be untested. So: a real commit, and `TRUNCATE ... RESTART IDENTITY CASCADE`
between tests. The table list comes from `information_schema`, so a table added
to schema `finance` by a later migration is truncated automatically and this file
needs no edit.

`TRUNCATE` bypasses row triggers entirely, which is a documented limitation of
the ledger's guarantees (and not something this fixture tries to paper over):
these tests prove what the triggers enforce, not that they are a complete
integrity boundary.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest
from alembic import command
from alembic.config import Config
from config import get_settings, pg_connect_args
from finance.domain.models import SCHEMA as FINANCE_SCHEMA
from life.domain.models import SCHEMA as LIFE_SCHEMA
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

__all__ = ["admin_database_url", "engine", "test_database_url"]

#: Both module schemas whose tables the db tests truncate. `finance` keeps its
#: penultimate place so an unqualified name resolves to finance first — the
#: finance tests' spelling is unchanged; `life` resolves its own names too,
#: which is all the life tests need. `public` is appended by pg_connect_args.
MODULE_SCHEMAS: Final[tuple[str, ...]] = (FINANCE_SCHEMA, LIFE_SCHEMA)

#: `backend/` is this file's directory. Alembic's `script_location` in the
#: committed .ini files is RELATIVE ("finance/alembic"), so it resolves against
#: the current working directory. Nothing here relies on where pytest was
#: started from: the paths below are absolute, and `_run_migrations` sets
#: `script_location` explicitly for the same reason.
BACKEND_DIR: Final[Path] = Path(__file__).resolve().parent

#: The single source of truth for "what must run before any migration". Mounted
#: by docker-compose.yml as /docker-entrypoint-initdb.d/10-pg_trgm.sql and read
#: here. Not duplicated, not inlined: two copies of these statements would
#: drift, and the test database is exactly the place that drift would hide.
BOOTSTRAP_SQL: Final[Path] = BACKEND_DIR / "db_bootstrap.sql"

#: The database the fixture creates and owns. It is dropped and recreated on
#: every session, so it must never be a database anyone else is using.
TEST_DATABASE: Final[str] = "lifeos_test"

#: Where the URL comes from. `make test-db` sets it from the compose `db`
#: service; CI sets it to its postgres service container.
TEST_URL_ENV_VAR: Final[str] = "LIFEOS_TEST_DATABASE_URL"

#: The application's own setting. Alembic's `env.py` reads the URL from
#: `get_settings()` rather than from the .ini (see the module docstring there),
#: so this has to be set for the migrations to hit the test database and not
#: whatever the ambient environment points at.
APP_URL_ENV_VAR: Final[str] = "LIFEOS_DATABASE_URL"

#: All schemas, all revision histories. `core` has no revisions of its own yet
#: - only its bookkeeping table - and it is upgraded anyway so that adding the
#: first core migration needs no change here. `life` is the second module
#: (docs/adr/0011): its Alembic env joins on the same terms.
ALEMBIC_SCHEMAS: Final[tuple[str, ...]] = ("core", "finance", "life")

#: Alembic's own bookkeeping table. It is not a domain table, it is not in
#: `Base.metadata`, and truncating it would leave the migrated database
#: claiming to be unmigrated.
ALEMBIC_VERSION_TABLE: Final[str] = "alembic_version"

_NO_URL_MESSAGE: Final[str] = (
    "A database URL is required by the tests marked `db`, and "
    f"{TEST_URL_ENV_VAR} is not set.\n"
    "\n"
    "    make up-db      # start the compose db service\n"
    "    make test-db    # sets the URL for you and runs `pytest -m db`\n"
    "\n"
    "This fixture FAILS rather than skips, on purpose. A database test that "
    "skips is a green gate that checked nothing, which is the failure mode "
    "this repository's CI comments call out explicitly (see the strace guard in "
    "tests/egress/). Selecting these tests "
    "without a database is a mistake in the command line and is reported as one."
)


# ---------------------------------------------------------------------------
# Building the test database
# ---------------------------------------------------------------------------


def _autocommit(url: URL) -> Engine:
    """An engine for DDL, which in PostgreSQL cannot run inside a transaction.

    `CREATE DATABASE` in particular is rejected inside a transaction block, and
    so is most of `backend/db_bootstrap.sql` in the versions of PostgreSQL that
    allow transactional DDL. `AUTOCOMMIT` is how this fixture says each
    statement stands alone.
    """
    return create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)


def _recreate_database(admin_url: URL) -> None:
    """Drop and recreate `lifeos_test`.

    **This deletes a database.** Exactly one, named `lifeos_test`, which this
    fixture owns and is about to rebuild from the migrations. It never drops the
    database named in the URL, which is used only as the admin connection to
    reach the server - so pointing `LIFEOS_TEST_DATABASE_URL` at the development
    database cannot destroy the development database.

    `WITH (FORCE)` terminates leftover connections instead of failing on them.
    That matters because the previous session's pool may still hold one, and a
    fixture that dies on a stale connection is a fixture that has to be retried
    by hand.
    """
    engine = _autocommit(admin_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
            )
            connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    except OperationalError as exc:
        pytest.fail(
            f"Could not create the {TEST_DATABASE!r} test database on "
            f"{admin_url.render_as_string(hide_password=True)}.\n"
            "Is the database running? `make up-db` starts the compose service, "
            "and `make test-db` expects it on 127.0.0.1:55432.\n"
            f"The server said: {exc}"
        )
    finally:
        engine.dispose()


def _run_bootstrap(url: URL) -> None:
    """Run `backend/db_bootstrap.sql` against the fresh test database.

    The schema has to exist before Alembic's own bookkeeping table can be
    created inside it, so this cannot come after `alembic upgrade head`.
    """
    engine = _autocommit(url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql(BOOTSTRAP_SQL.read_text(encoding="utf-8"))
    finally:
        engine.dispose()


def _run_migrations(url: URL) -> None:
    """`alembic upgrade head` for both schemas, from the committed .ini files.

    Three deliberate choices:

    * **Absolute `script_location`.** The committed .ini files say
      `script_location = finance/alembic`, relative to the working directory.
      Reading them from anywhere but `backend/` would resolve that to nothing.
    * **The URL is set in BOTH places.** `sqlalchemy.url` for offline mode, and
      `LIFEOS_DATABASE_URL` (via the caller) for online mode, because `env.py`
      overwrites `sqlalchemy.url` from `get_settings()`. Setting only the .ini
      option would migrate the wrong database - and it fails confusingly, with
      `schema "core" does not exist`, because the maintenance database has no
      module schemas in it.
    * **`config_file_name = None`.** Each `env.py` calls
      `logging.config.fileConfig()` on the .ini, which reconfigures the root
      logger AND disables every logger that already existed. Inside pytest that
      silences the capture handlers for the rest of the session, for no benefit.
      `env.py` skips that call when this is None. If an `env.py` ever starts
      reading `config_file_name` for something else, this line is where to look.
    """
    rendered = url.render_as_string(hide_password=False)
    for schema in ALEMBIC_SCHEMAS:
        config = Config(str(BACKEND_DIR / schema / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / schema / "alembic"))
        config.set_main_option("sqlalchemy.url", rendered)
        config.config_file_name = None
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def admin_database_url() -> str:
    """The server to build the test database on. Fails loudly if unset.

    This is `LIFEOS_TEST_DATABASE_URL`, and it is REQUIRED rather than optional.
    See the module docstring: a skip here would be a green gate over nothing.
    """
    url = os.environ.get(TEST_URL_ENV_VAR, "").strip()
    if not url:
        pytest.fail(_NO_URL_MESSAGE)
    return url


@pytest.fixture(scope="session")
def test_database_url(admin_database_url: str) -> Iterator[str]:
    """A freshly migrated `lifeos_test`, built once per session.

    Yields the URL of the test database (never the one named in the URL above -
    that is only the admin connection).
    """
    admin_url = make_url(admin_database_url)
    url: URL = admin_url.set(database=TEST_DATABASE)

    _recreate_database(admin_url)
    _run_bootstrap(url)

    # Both Alembic environments read the URL from `get_settings()`, which is
    # `lru_cache`d - so the cache has to be cleared after the environment
    # changes or the migration would use whatever was read first.
    previous_app_url = os.environ.get(APP_URL_ENV_VAR)
    os.environ[APP_URL_ENV_VAR] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        _run_migrations(url)
        yield url.render_as_string(hide_password=False)
    finally:
        # Restored rather than left pointing at the throwaway database, so a
        # process that mixes `pytest -m db` with anything else does not silently
        # address lifeos_test afterwards.
        if previous_app_url is None:
            os.environ.pop(APP_URL_ENV_VAR, None)
        else:
            os.environ[APP_URL_ENV_VAR] = previous_app_url
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine(test_database_url: str) -> Iterator[Engine]:
    """A session-wide engine on the test database.

    Built with `pg_connect_args(SCHEMA)` - the SAME helper the application
    engine and both Alembic environments use. That is the whole point of the
    helper: the test connection has to resolve unqualified names exactly like the
    running application, or a trigger body that works here is not evidence about
    production.

    `NullPool`: one session's tests, sequential, and a pooled connection carries
    transaction state that a test asserting on a failed commit has no use for.
    """
    test_engine = create_engine(
        test_database_url,
        connect_args=pg_connect_args(*MODULE_SCHEMAS),
        poolclass=NullPool,
    )
    try:
        yield test_engine
    finally:
        test_engine.dispose()


def _engine_of(request: pytest.FixtureRequest) -> Engine:
    """The session `engine`, requested lazily.

    Lazily because the autouse fixture below runs for EVERY test in the
    repository, including the 459 that need no database. Declaring `engine` as a
    parameter would build it (and connect) unconditionally, and `make test`
    would stop being Docker-free.
    """
    engine = request.getfixturevalue("engine")
    assert isinstance(engine, Engine), "the `engine` fixture must return an Engine"
    return engine


def _truncate_modules(engine: Engine) -> None:
    """Empty every table in every MODULE schema, and restart every identity.

    Driven by `information_schema` rather than a hard-coded list, so a table
    added by a later migration is covered without editing this file - which is
    the difference between a cleanup that stays correct and one that silently
    stops clearing the newest table.

    Fails rather than passes if it finds nothing to truncate in EITHER module
    schema: `TRUNCATE` of an empty list is a no-op, and a no-op cleanup leaves
    every test reading every other test's rows. One `lifeos_test` run exercises
    both schemas' suites (finance and life), so both are cleared together.
    """
    with engine.connect() as connection:
        tables: list[str] = []
        for schema in MODULE_SCHEMAS:
            schema_tables: list[str] = list(
                connection.execute(
                    text(
                        "SELECT quote_ident(table_name) FROM information_schema.tables"
                        " WHERE table_schema = :schema"
                        "   AND table_type = 'BASE TABLE'"
                        "   AND table_name <> :version_table"
                        " ORDER BY table_name"
                    ),
                    {"schema": schema, "version_table": ALEMBIC_VERSION_TABLE},
                )
                .scalars()
                .all()
            )
            if not schema_tables:
                pytest.fail(
                    f"Schema {schema!r} has no tables to truncate, so the database "
                    "is not the migrated one this suite expects. The migration "
                    "step either did not run or ran against another database."
                )
            tables.extend(f"{schema}.{name}" for name in schema_tables)
        connection.execute(
            text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
        )
        connection.commit()


@pytest.fixture(autouse=True)
def _isolated_database(request: pytest.FixtureRequest) -> Iterator[None]:
    """Truncate both module schemas before each test marked `db`, and only
    those.

    Autouse because forgetting to ask for cleanup is how a test silently reads
    another test's rows and passes for the wrong reason. Marker-guarded because
    the other 459 tests in this repository have no business touching a
    database, and `make test` must keep working with no Docker at all.
    """
    if request.node.get_closest_marker("db") is None:
        yield
        return
    _truncate_modules(_engine_of(request))
    yield
