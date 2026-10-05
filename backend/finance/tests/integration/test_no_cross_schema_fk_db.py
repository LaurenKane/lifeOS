"""test_no_cross_schema_fk_db.py — the schema-ownership invariant, against Postgres.

`no_cross_schema_fk` says no foreign key may reference a table in another
Postgres schema (ARCHITECTURE.md §4, ADR 0005): the module-per-schema mapping
stops describing anything the day a `finance` table points at a table that is
not in `finance`.

WHY AN EVENT TRIGGER, and why these tests run DDL rather than reading code.
The static regex that used to enforce this was deleted in commit `8bdfd57`,
so for a while the invariant was enforced by nothing. Role separation cannot
enforce it either: the app/migration/test role `lifeos` is a PostgreSQL
SUPERUSER, and a superuser bypasses USAGE/GRANT checks. What does bind a
superuser is a `ddl_command_end` event trigger (`trg_no_cross_schema_fk`,
installed by `backend/db_bootstrap.sql`): it runs inside the DDL's own
transaction and RAISEs before commit, so the offending CREATE/ALTER is
aborted with the transaction that carried it.

These tests therefore execute real DDL as `lifeos` and demand the refusal —
a source-code scan cannot prove what a superuser session does. Each is
self-gating: without the trigger the cross-schema FK succeeds and
`pytest.raises` fails, so a silently skipped install reads as red, not green.

Probe tables MUST NOT leak: the session database is truncated of ROWS
between tests, not of tables, so every committed probe table is dropped in
a `finally`. Refused DDL needs no cleanup — the trigger aborts its
transaction — and the absence check after each refusal proves it.
"""

from __future__ import annotations

from typing import Final

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db

#: The trigger's refusal always carries this marker. Asserted on the message
#: rather than the SQLSTATE so the test names WHICH guarantee fired: other
#: DDL failures (a missing referenced column, a duplicate table) carry
#: different text, and "some error happened" would pass for the wrong reason.
TRIGGER_MARKER: Final[str] = "no_cross_schema_fk"

#: A table the database is guaranteed to hold outside `finance`: the core
#: schema owns nothing but Alembic's own bookkeeping table, and its
#: `version_num` is a primary key, so the ONLY reason the FK below can fail
#: is the schema boundary — never a missing column or a missing unique
#: constraint, which would raise different errors and prove nothing.
CORE_VERSION_TABLE: Final[str] = "core.alembic_version(version_num)"

#: Probe tables. Fixed names rather than generated ones, so a leaked probe
#: from an interrupted run is recognizable — and so the `DROP TABLE IF EXISTS`
#: cleanups name exactly what the tests create.
INLINE_PROBE: Final[str] = "finance.no_cross_schema_fk_probe"
ALTER_PROBE: Final[str] = "finance.no_cross_schema_fk_alter_probe"
OK_PARENT: Final[str] = "finance.no_cross_schema_fk_ok_parent"
OK_CHILD: Final[str] = "finance.no_cross_schema_fk_ok_child"


def _unqualified(qualified: str) -> str:
    """The table half of a `schema.table` name, for `information_schema`."""
    return qualified.partition(".")[2]


def _table_exists(engine: Engine, qualified: str) -> bool:
    """Whether a `schema.table` survived, read on a fresh connection.

    Fresh because the refusal tests must prove the aborted transaction left
    nothing behind GLOBALLY, not merely that the failed connection cannot see
    it. Split from the qualified name rather than passed as two arguments,
    because every call site already names the probe as one string.
    """
    schema, _, table = qualified.partition(".")
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": schema, "table": table},
            ).scalar()
        )


def test_a_cross_schema_fk_is_refused_at_the_db(engine: Engine) -> None:
    """Inline `REFERENCES core.x` is refused, and the table never lands.

    `pytest.raises` sits OUTSIDE the transaction blocks on purpose: the
    trigger's RAISE aborts the transaction, and a block that exited cleanly
    would then try to COMMIT a doomed transaction — a second, confusing
    error on top of the refusal under test. Letting the error propagate
    through the blocks rolls everything back instead.
    """
    with pytest.raises(DBAPIError) as caught:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        f"CREATE TABLE {INLINE_PROBE} ("
                        "id TEXT, "
                        "CONSTRAINT fk_cross FOREIGN KEY (id) "
                        f"REFERENCES {CORE_VERSION_TABLE})"
                    )
                )
    assert TRIGGER_MARKER in str(caught.value)
    assert not _table_exists(engine, INLINE_PROBE)


def test_a_cross_schema_fk_added_by_alter_table_is_refused(
    engine: Engine,
) -> None:
    """The boundary holds for constraints added after creation, too.

    A trigger that only inspected `CREATE TABLE` would bless a two-step
    dodge: create the table clean, then bolt the cross-schema FK on with
    ALTER. The probe table itself is same-schema and committed first, so the
    refusal below can only be about the ADD CONSTRAINT — and it is dropped
    in a `finally`, because unlike refused DDL it really exists.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(f"CREATE TABLE {ALTER_PROBE} (id TEXT PRIMARY KEY)")
            )
    try:
        with pytest.raises(DBAPIError) as caught:
            with engine.connect() as connection:
                with connection.begin():
                    connection.execute(
                        text(
                            f"ALTER TABLE {ALTER_PROBE} ADD CONSTRAINT fk_cross "
                            f"FOREIGN KEY (id) REFERENCES {CORE_VERSION_TABLE}"
                        )
                    )
        assert TRIGGER_MARKER in str(caught.value)
    finally:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text(f"DROP TABLE IF EXISTS {ALTER_PROBE}"))


def test_a_same_schema_fk_still_succeeds(engine: Engine) -> None:
    """Two `finance` tables with an intra-schema FK commit without complaint.

    The negative tests above would also pass if the trigger refused ALL
    DDL, which would brick every future migration. This is the guard against
    over-blocking — and it rolls its own tables back, so the proof leaves
    nothing behind.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f"CREATE TABLE {OK_PARENT} (id TEXT PRIMARY KEY)"))
            connection.execute(
                text(
                    f"CREATE TABLE {OK_CHILD} (id TEXT PRIMARY KEY, "
                    f"parent_id TEXT REFERENCES {OK_PARENT}(id))"
                )
            )
            # Read on THIS connection: the tables are still uncommitted, so a
            # fresh connection cannot see them yet — and that invisibility is
            # exactly what the rollback below relies on.
            visible = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = 'finance'"
                    "   AND table_name IN (:parent, :child)"
                ),
                {
                    "parent": _unqualified(OK_PARENT),
                    "child": _unqualified(OK_CHILD),
                },
            ).scalar()
            assert visible == 2
        finally:
            transaction.rollback()
    assert not _table_exists(engine, OK_PARENT)
    assert not _table_exists(engine, OK_CHILD)


def test_the_trigger_is_installed(engine: Engine) -> None:
    """`trg_no_cross_schema_fk` exists: the bootstrap did not skip the install.

    Without this, a bootstrap that silently dropped the trigger section
    would still pass every other test in this file — vacuously, because the
    first two tests are self-gating only when they RUN against the trigger.
    Read straight from `pg_event_trigger`, which is what the server obeys,
    not what any file claims.
    """
    with engine.connect() as connection:
        installed = connection.execute(
            text(
                "SELECT count(*) FROM pg_event_trigger "
                "WHERE evtname = 'trg_no_cross_schema_fk'"
            )
        ).scalar()
    assert installed == 1
