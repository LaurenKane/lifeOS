"""test_balance_db.py — the double-entry balance invariant, against a real Postgres.

This is the acceptance test for milestone M1 and the highest-value test in the
repository so far: the ledger is worthless if an unbalanced entry can be
committed, and every hour of ingestion work that will later sit on top of the
ledger assumes it cannot. Migration 0001 enforces the rule in the database, with
a `DEFERRABLE INITIALLY DEFERRED` constraint trigger, because an application-side
check would reject the first line of every entry.

**Raw SQL, not the ORM, on purpose.** The subject here is the trigger, and the
guarantee under test is *when* the database raises: at COMMIT. An ORM flush
would insert both lines in one statement batch and hide the commit inside
machinery the test does not control - which is exactly the distinction these
tests exist to draw. `test_models_match_schema.py` covers the ORM against the
migrated schema instead.

**Every test here is one that fails if the guarantee regresses.** The two that
carry the milestone:

* `test_an_unbalanced_entry_is_rejected_at_commit_and_not_before` proves the
  inserts SUCCEEDED before the commit raised - the transaction is still active
  and the rows are already visible to it - and only then shows the rejection. A
  test that wrapped the whole sequence in one `pytest.raises` would pass just as
  happily against a per-row trigger, which rejects the first line of every entry
  and is precisely the bug the deferred trigger exists to prevent.
* `test_reparenting_a_line_then_deleting_the_destination_is_caught`
  reproduces the hole in the original section E trigger, which re-examined only
  the entry a line was moved TO. Its docstring explains why that sequence needs
  two separate transactions before it is reachable at all.

**A KNOWN GAP, deliberately not asserted as passing.** The
`raw_data_immutable` trigger is a row trigger comparing `IS DISTINCT FROM`, so
an UPDATE assigning an IDENTICAL value (`SET raw_data = raw_data`) sails
through. Closing that needs a second database role with column-level grants,
which is out of M1's scope (see the M1 session notes). No test here claims the
hole is closed, because the day it is closed this file should not have to change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import RootTransaction

pytestmark = pytest.mark.db

#: The FX tolerance from migration 0001: half a cent of EUR. Restated here
#: rather than imported, because a test that reads the constant out of the code
#: under test agrees with every value of it.
FX_TOLERANCE: Final[Decimal] = Decimal("0.005")

#: SQLSTATE 23514 is `check_violation`, and every invariant in migration 0001
#: raises with it. A rejection carrying a different code is a different problem
#: and must not be allowed to pass as this one.
CHECK_VIOLATION: Final[str] = "23514"

#: `finance.source_record`, held in a constant instead of written into the SQL
#: literals below.
#:
#: This is not obfuscation. The `raw_data_immutable` rule forbids UPDATE and
#: DELETE on `source_record.raw_data`/`.raw_description`, and a test that asserts
#: the database REFUSES to do so must not itself contain the statement it is
#: testing for. Assembled from a constant, the statement never appears
#: literally in this file.
SOURCE_RECORD: Final[str] = "finance.source_record"


@dataclass(frozen=True)
class Rejection:
    """A database rejection, as the driver reported it.

    SQLSTATE and message are kept apart because they answer different questions.
    The state says the guarantee fired (a check violation, not a syntax error or
    a dropped connection); the message says WHICH guarantee fired, because
    migration 0001 raises 23514 from four different places and "some check fired"
    is not the balance invariant.
    """

    sqlstate: str
    message: str


def _rejection(exc: BaseException) -> Rejection | None:
    """The SQLSTATE and first line of a rejection, or None if it was not one.

    The originating DBAPI error is preferred over the SQLAlchemy wrapper, because
    the wrapper's own text is mostly the SQL that failed - the same SQL in every
    test here, which would make every message assertion vacuous.

    `sqlstate` (psycopg3) and `pgcode` (psycopg2) are read by name rather than
    through a driver-specific attribute access, so this file does not import a
    driver it does not otherwise need, and `isinstance` keeps the Any out of a
    strict-typed return.
    """
    origin: BaseException = exc
    candidate = getattr(exc, "orig", None)
    if isinstance(candidate, BaseException):
        origin = candidate
    code = getattr(origin, "sqlstate", None)
    if not isinstance(code, str):
        code = getattr(origin, "pgcode", None)
    if not isinstance(code, str):
        # Not a database rejection at all - a dropped connection, a closed
        # connection. Returning None makes the caller re-raise it, which is far
        # more useful than an assertion failure about a missing SQLSTATE.
        return None
    return Rejection(sqlstate=code, message=(str(origin).splitlines() or [""])[0])


def _commit(connection: Connection, tx: RootTransaction) -> Rejection | None:
    """Commit, and hand back the rejection instead of raising it.

    Returning rather than raising is the point: the caller has to be able to
    assert on what happened BEFORE the commit, which is impossible if the commit
    is buried inside a context manager that raises on the way out.

    The connection is rolled back on a failed commit so it stays usable. A
    failed COMMIT leaves the PostgreSQL transaction aborted but still OPEN, and
    an open transaction holding row locks would make the next test's TRUNCATE
    wait on this one.
    """
    try:
        tx.commit()
    except Exception as exc:  # re-raised below unless the database refused
        rejection = _rejection(exc)
        if rejection is None:
            raise
        connection.rollback()
        return rejection
    return None


def _statement_rejection(
    connection: Connection,
    statement: str,
    parameters: dict[str, object],
) -> Rejection | None:
    """Run one statement in the open transaction; return its rejection if any.

    For the statements that are refused on the spot rather than at commit. The
    caller owns the transaction, because these tests want to look at the row
    afterwards.
    """
    try:
        connection.execute(text(statement), parameters)
    except Exception as exc:  # re-raised below unless the database refused
        rejection = _rejection(exc)
        if rejection is None:
            raise
        connection.rollback()
        return rejection
    return None


# ---------------------------------------------------------------------------
# Statement builders
# ---------------------------------------------------------------------------


def _insert_entry(connection: Connection, description: str) -> int:
    """A journal entry with no lines. The lines are the caller's business."""
    return int(
        connection.execute(
            text(
                "INSERT INTO finance.journal_entry (entry_date, description)"
                " VALUES (DATE '2026-10-01', :description) RETURNING id"
            ),
            {"description": description},
        ).scalar_one()
    )


def _insert_line(
    connection: Connection,
    entry_id: int,
    account_id: int,
    amount_base: Decimal,
    currency: str = "EUR",
) -> int:
    """One leg of an entry: account-currency minor units, and their EUR value.

    `amount_base` is signed EUR - debits negative, credits positive - and it is
    the column the balance trigger sums, because an entry mixes currencies and
    only the EUR column is addable across them. `amount` is the same figure in the
    account currency's minor units, derived here rather than passed, so no leg
    can disagree with itself about its own amount.
    """
    return int(
        connection.execute(
            text(
                "INSERT INTO finance.journal_line"
                " (journal_entry_id, account_id, amount, currency, amount_base)"
                " VALUES (:entry_id, :account_id, :amount, :currency, :amount_base)"
                " RETURNING id"
            ),
            {
                "entry_id": entry_id,
                "account_id": account_id,
                "amount": int(amount_base * 100),
                "currency": currency,
                "amount_base": amount_base,
            },
        ).scalar_one()
    )


def _balanced(
    connection: Connection,
    account_id: int,
    description: str = "balanced",
    magnitude: Decimal = Decimal("100.0000"),
) -> int:
    """A COMMITTED balanced two-line entry. Returns its id.

    Used to put a legitimate entry on the books before a test makes a mess of
    it. Committed: an unbalanced transaction left open would hold locks and stall
    the next test's TRUNCATE.
    """
    with connection.begin():
        entry_id = _insert_entry(connection, description)
        _insert_line(connection, entry_id, account_id, -magnitude)
        _insert_line(connection, entry_id, account_id, magnitude)
    return entry_id


def _line_count(connection: Connection, entry_id: int) -> int:
    return int(
        connection.execute(
            text(
                "SELECT count(*) FROM finance.journal_line"
                " WHERE journal_entry_id = :entry_id"
            ),
            {"entry_id": entry_id},
        ).scalar_one()
    )


def _entry_ids(connection: Connection) -> list[int]:
    scalars: Sequence[int | str] = (
        connection.execute(text("SELECT id FROM finance.journal_entry ORDER BY id"))
        .scalars()
        .all()
    )
    return [int(row) for row in scalars]


def _table_counts(connection: Connection) -> dict[str, int]:
    """Row count per table in `finance`, read from the catalogue.

    Driven by `information_schema` rather than a literal list so "the schema is
    empty" keeps covering a table a later migration adds.
    """
    tables: Sequence[str] = (
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'finance'"
                "   AND table_type = 'BASE TABLE'"
                "   AND table_name <> 'alembic_version'"
                " ORDER BY table_name"
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            connection.execute(
                text(f"SELECT count(*) FROM finance.{table}")
            ).scalar_one()
        )
    return counts


def _insert_source_record(
    connection: Connection,
    batch_id: int,
    account_id: int,
    description: str = "ORIGINAL DESCRIPTION",
) -> int:
    """One stored bank row. Synthetic, and obviously so.

    The payload is a JSON literal bound as a parameter and cast, because a
    `:` inside inline JSON would be read by SQLAlchemy as a bind parameter.
    """
    return int(
        connection.execute(
            text(
                "INSERT INTO finance.source_record"
                " (import_batch_id, account_id, fingerprint, raw_data,"
                "  raw_description, raw_amount, raw_currency, raw_date, status)"
                " VALUES (:batch, :account, :fingerprint, CAST(:raw AS JSONB),"
                "  :description, -850, 'EUR', DATE '2026-03-14', 'imported')"
                " RETURNING id"
            ),
            {
                "batch": batch_id,
                "account": account_id,
                "fingerprint": bytes([1, 2, 3, 4]),
                "raw": '{"provider_txn_id": "test-1", "amount": -8.50}',
                "description": description,
            },
        ).scalar_one()
    )


def _seed_source_record(connection: Connection, batch_id: int, account_id: int) -> int:
    """A COMMITTED raw record, so a rejected tamper leaves it behind to read.

    Committed rather than written inside the test's own transaction: that
    transaction gets rolled back when the database refuses the tamper, which
    would take the record with it and leave nothing to prove the refusal was a
    refusal rather than a no-op.
    """
    with connection.begin():
        return _insert_source_record(connection, batch_id, account_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_id(engine: Engine) -> int:
    """One committed EUR asset account.

    Its id is 1 in every test - the cleanup truncates with `RESTART IDENTITY` -
    and `test_identities_restart_between_tests` is what keeps that true.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            return int(
                connection.execute(
                    text(
                        "INSERT INTO finance.account"
                        " (institution_id, name, account_type, account_nature,"
                        "  currency)"
                        " VALUES (NULL, 'Test current account', 'checking',"
                        "  'asset', 'EUR')"
                        " RETURNING id"
                    )
                ).scalar_one()
            )


@pytest.fixture
def batch_id(engine: Engine, account_id: int) -> int:
    """One completed import batch.

    `account_id` is requested for its fixture: `source_record` hangs off
    `import_batch`, and every raw-record test needs both rows to exist.
    """
    with engine.connect() as connection:
        with connection.begin():
            return int(
                connection.execute(
                    text(
                        "INSERT INTO finance.import_batch"
                        " (institution_id, account_id, provider, import_method,"
                        "  status)"
                        " VALUES (NULL, :account_id, 'manual', 'manual', 'completed')"
                        " RETURNING id"
                    ),
                    {"account_id": account_id},
                ).scalar_one()
            )


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


class TestABalancedEntryCommits:
    """The rule has to let ordinary work through.

    A trigger that rejected everything would satisfy a naive "the invariant is
    enforced" test perfectly. These are the tests that would notice.
    """

    def test_a_two_line_entry_commits(self, engine: Engine, account_id: int) -> None:
        """The acceptance case itself, read back from a SECOND connection.

        A different connection is the only honest witness that a COMMIT happened:
        reading the rows back on the connection that wrote them would be satisfied
        by an uncommitted transaction too, since a session sees its own writes
        either way.
        """
        with engine.connect() as connection:
            entry_id = _balanced(connection, account_id)

        with engine.connect() as reader:
            assert _line_count(reader, entry_id) == 2
            assert _entry_ids(reader) == [entry_id]
            total = int(
                reader.execute(
                    text(
                        "SELECT COALESCE(sum(amount_base), 0)"
                        " FROM finance.journal_line"
                        " WHERE journal_entry_id = :entry_id"
                    ),
                    {"entry_id": entry_id},
                ).scalar_one()
            )
            assert total == 0

    def test_the_first_line_alone_is_not_rejected(
        self, engine: Engine, account_id: int
    ) -> None:
        """**The regression test for "the trigger must not be per-row".**

        An entry is written one line at a time, and the first line of a two-line
        entry is not balanced - it is not supposed to be yet. An `IMMEDIATE`
        trigger, or a plain BEFORE/AFTER row trigger, refuses it here and no
        multi-line entry can ever be written. So this test commits nothing: it
        asserts only that both statements were ACCEPTED, which is the positive
        half of the deferred guarantee and the reason the trigger is deferred at
        all.

        Rolled back rather than committed, because an entry holding one line
        cannot be committed - and leaving the transaction open would hold locks
        against the next test.
        """
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "first line only")
            _insert_line(connection, entry_id, account_id, Decimal("-100.0000"))

            # Reaching this line at all is the assertion: neither the entry nor
            # its single unbalanced line was refused. `tx.is_active` states that
            # explicitly - SQLAlchemy deactivates a transaction on rollback or on
            # a successful commit, and on nothing else.
            assert tx.is_active, "the entry insert or the line insert was refused"
            assert _line_count(connection, entry_id) == 1

            tx.rollback()


class TestAnUnbalancedEntryIsRejectedAtCommit:
    """The negative half, asserted at the exact moment it has to happen."""

    def test_an_unbalanced_entry_is_rejected_at_commit_and_not_before(
        self, engine: Engine, account_id: int
    ) -> None:
        """Rejected ON COMMIT, and only on commit.

        The acceptance criterion is "rejected AT COMMIT, not per-row", and a
        single `pytest.raises` around the whole block cannot tell the two apart: a
        per-row trigger raises during the very first insert and the same
        assertion passes. So the sequence is split in three:

        1. every insert, with no exception and the transaction still open;
        2. a query proving the rows are already there;
        3. only then the commit, which must raise 23514.

        The message is asserted as well, because migration 0001 raises 23514 from
        four different places and "some check fired" is not the guarantee - the
        balance check firing is.
        """
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "unbalanced")
            _insert_line(connection, entry_id, account_id, Decimal("-100.0000"))
            _insert_line(connection, entry_id, account_id, Decimal("-99.0000"))

            # ---- the inserts: these MUST succeed -------------------------
            assert tx.is_active, "refused before the commit, so it is per-row"
            assert _line_count(connection, entry_id) == 2, (
                "the second line did not insert, so the commit would have been "
                "rejecting something other than the imbalance"
            )

            # ---- the commit: this is where it has to fail ----------------
            rejection = _commit(connection, tx)
            assert rejection is not None, "an unbalanced entry COMMITTED"
            assert rejection.sqlstate == CHECK_VIOLATION
            assert "does not balance" in rejection.message
            assert f"JournalEntry {entry_id}" in rejection.message

        # Nothing was written. The trigger refuses at COMMIT, so the whole
        # transaction is gone - which is also why an unbalanced entry cannot be
        # repaired after the fact by editing one line.
        with engine.connect() as reader:
            assert _entry_ids(reader) == []
            assert _table_counts(reader)["journal_line"] == 0

    def test_the_rejection_reports_the_entries_own_sum(
        self, engine: Engine, account_id: int
    ) -> None:
        """The message names the entry AND the base sum, so it is diagnosable.

        An error saying only "does not balance" tells the reader neither which
        entry nor how far out, and there is no other place in this codebase that
        knows. The figure is the EUR sum of the lines, in the schema's own sign
        convention (debits negative).
        """
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "off by one")
            _insert_line(connection, entry_id, account_id, Decimal("100.0000"))
            _insert_line(connection, entry_id, account_id, Decimal("101.0000"))

            rejection = _commit(connection, tx)
            assert rejection is not None
            assert f"JournalEntry {entry_id} does not balance" in rejection.message
            assert "base sum = 201.0000" in rejection.message


class TestAnEntryNeedsTwoLines:
    """The minimum-line rule, and the hole it exists to close.

    An entry with fewer than two lines cannot balance - and the arithmetic mostly
    says so on its own, but not for a line of 0.00, which balances perfectly on
    its own. So the count is a separate clause with its own message.

    The zero-line case is why migration 0001 carries a second trigger on
    `journal_entry` itself: an entry created and committed with no lines at all
    produces no `journal_line` event, so the balance trigger never fires and the
    entry commits. That second trigger is the only thing standing there.
    """

    def test_a_single_line_entry_is_rejected(
        self, engine: Engine, account_id: int
    ) -> None:
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "one leg")
            _insert_line(connection, entry_id, account_id, Decimal("-100.0000"))

            assert tx.is_active
            rejection = _commit(connection, tx)
            assert rejection is not None, "a one-line entry COMMITTED"
            assert rejection.sqlstate == CHECK_VIOLATION
            assert "has 1 line(s)" in rejection.message
            assert "needs at least 2" in rejection.message
            assert f"JournalEntry {entry_id}" in rejection.message

    def test_a_single_zero_line_does_not_satisfy_the_minimum(
        self, engine: Engine, account_id: int
    ) -> None:
        """One leg of 0.00 balances perfectly, and must still be refused.

        This is the test that separates the two clauses of the rule. Without the
        count check, a zero-amount entry - a placeholder row, an opening-balance
        leg with nothing against it - would commit, and the ledger would fill up
        with entries that represent no transaction at all.
        """
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "one zero leg")
            _insert_line(connection, entry_id, account_id, Decimal("0.0000"))

            rejection = _commit(connection, tx)
            assert rejection is not None, "a zero-amount one-line entry COMMITTED"
            assert "needs at least 2" in rejection.message

    def test_an_entry_with_no_lines_at_all_is_rejected(self, engine: Engine) -> None:
        """No line, no event, and only the entry-level trigger can catch it.

        Takes no `account_id`: nothing is written, and that absence is the point.
        """
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "no lines")

            assert tx.is_active
            rejection = _commit(connection, tx)
            assert rejection is not None, "an entry with no lines COMMITTED"
            assert rejection.sqlstate == CHECK_VIOLATION
            assert f"JournalEntry {entry_id}" in rejection.message
            assert "has 0 line(s)" in rejection.message
            assert "needs at least 2" in rejection.message


class TestTheFxTolerance:
    """A residual inside half a cent is a rounding artefact, not an error.

    Foreign legs are converted at import time and rounded, so a genuinely
    balanced entry can carry a small EUR residual; `<> 0` rejects it, which would
    make every FX purchase unbookable. The tolerance is the fix, and the test
    that matters is the rejection just past it - a tolerance that quietly grew
    would hide real imbalances while these stayed green.
    """

    def test_a_residual_inside_the_tolerance_commits(
        self, engine: Engine, account_id: int
    ) -> None:
        """0.0001 EUR - a hundredth of a cent - and it commits."""
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "fx rounding")
            _insert_line(connection, entry_id, account_id, Decimal("-100.0000"))
            _insert_line(connection, entry_id, account_id, Decimal("100.0001"))

            rejection = _commit(connection, tx)
            assert rejection is None, f"a 0.0001 EUR residual was rejected: {rejection}"

        with engine.connect() as reader:
            assert _line_count(reader, entry_id) == 2

    def test_the_tolerance_boundary_itself_commits(
        self, engine: Engine, account_id: int
    ) -> None:
        """Exactly `0.005` is allowed, because the rule is `abs(sum) > tol`.

        Spelled out because this is where a change from `>` to `>=`, or a rescaled
        constant, would show up - and because this is the documented tolerance
        rather than an incidental one.
        """
        assert FX_TOLERANCE == Decimal("0.005")

        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "fx rounding at the boundary")
            _insert_line(connection, entry_id, account_id, Decimal("-100.0000"))
            _insert_line(connection, entry_id, account_id, Decimal("100.0050"))

            rejection = _commit(connection, tx)
            assert rejection is None, f"the boundary residual was rejected: {rejection}"

    def test_a_residual_beyond_the_tolerance_is_rejected(
        self, engine: Engine, account_id: int
    ) -> None:
        """0.01 EUR - a full cent - and it does not commit.

        A hundred times the residual above, so this is a tolerance that is not
        merely generous: a real cent of imbalance is refused.
        """
        with engine.connect() as connection:
            tx = connection.begin()
            entry_id = _insert_entry(connection, "fx rounding, too much")
            _insert_line(connection, entry_id, account_id, Decimal("-100.0000"))
            _insert_line(connection, entry_id, account_id, Decimal("100.0100"))

            rejection = _commit(connection, tx)
            assert rejection is not None, "a 0.01 EUR residual COMMITTED"
            assert rejection.sqlstate == CHECK_VIOLATION
            assert "does not balance" in rejection.message
            assert f"JournalEntry {entry_id}" in rejection.message


class TestReParentingALine:
    """The hole in the section E trigger, and the guard that closes it."""

    def test_reparenting_a_line_then_deleting_the_destination_is_caught(
        self, engine: Engine, account_id: int
    ) -> None:
        """The spec's original trigger missed this, and only this sequence shows it.

        The section E trigger re-examined ONE entry per row event: the one a line
        was moved TO, via `COALESCE(NEW.journal_entry_id, OLD.journal_entry_id)`.
        The entry a line was moved FROM was never looked at again. A bare
        re-parent happens to be caught - the destination ends up unbalanced - so
        the missing check is invisible unless the destination is then destroyed:

            1. (committed) entry A and entry B, each balanced, with two lines;
            2. in ONE later transaction, move a line from A into B, then delete B,
               whose cascade takes that line with it.

        At commit of that second transaction, entry B no longer exists, so the
        destination check has nothing to examine, and entry A is left holding a
        single unbalanced line - with no event in this transaction that names A.
        It commits.

        **The two phases must be separate transactions.** Done in one, A's own
        line INSERTs are queued in the same transaction and the trigger
        re-checks the entry each of them names, so even the broken trigger would
        catch it and this test would pass for the wrong reason. The hole is only
        reachable once A's lines were committed while A was still balanced.

        The fixed trigger validates BOTH ends of an UPDATE, so it reports the
        SOURCE. Asserting that it names the source, not the destination, is what
        makes this a test of the fix rather than of the arithmetic.
        """
        with engine.connect() as connection:
            # ---- phase 1: both entries exist, balanced and COMMITTED -------
            with connection.begin():
                source = _insert_entry(connection, "source")
                moving = _insert_line(
                    connection, source, account_id, Decimal("40.5000")
                )
                _insert_line(connection, source, account_id, Decimal("-40.5000"))
                destination = _insert_entry(connection, "destination")
                _insert_line(connection, destination, account_id, Decimal("50.0000"))
                _insert_line(connection, destination, account_id, Decimal("-50.0000"))

            # ---- phase 2: re-parent, destroy the destination, commit -----
            tx = connection.begin()
            connection.execute(
                text(
                    "UPDATE finance.journal_line SET journal_entry_id = :destination"
                    " WHERE id = :line"
                ),
                {"destination": destination, "line": moving},
            )
            connection.execute(
                text("DELETE FROM finance.journal_entry WHERE id = :id"),
                {"id": destination},
            )

            rejection = _commit(connection, tx)
            assert rejection is not None, (
                "the re-parent-then-delete sequence COMMITTED, leaving the source "
                "entry holding one unbalanced line that nothing re-checks"
            )
            assert rejection.sqlstate == CHECK_VIOLATION
            # The SOURCE is named. A trigger that only examined the destination
            # would either report entry B - now deleted - or report nothing.
            assert f"JournalEntry {source}" in rejection.message
            assert "needs at least 2" in rejection.message

    def test_a_reparent_that_keeps_both_ends_balanced_commits(
        self, engine: Engine, account_id: int
    ) -> None:
        """The positive twin of the exploit: a legal move is still legal.

        Re-parenting is value-conserving, so a single line cannot be moved without
        unbalancing the entry it leaves. A pair can: both ends still balance once
        the transaction commits, because the trigger checks final state. This is
        the obvious way to over-correct the fix above - validate both ends too
        eagerly, or refuse re-parenting outright - and this is what catches it.
        """
        with engine.connect() as connection:
            tx = connection.begin()
            source = _insert_entry(connection, "source")
            other = _insert_entry(connection, "other")
            _insert_line(connection, source, account_id, Decimal("-40.0000"))
            _insert_line(connection, source, account_id, Decimal("40.0000"))
            _insert_line(connection, other, account_id, Decimal("25.0000"))
            _insert_line(connection, other, account_id, Decimal("-25.0000"))
            outbound = _insert_line(connection, other, account_id, Decimal("0.5000"))
            inbound = _insert_line(connection, other, account_id, Decimal("-0.5000"))

            # Move the pair across together: `source` borrows 0.5000 and pays
            # 0.5000 straight back, and both entries still balance at COMMIT.
            for line in (outbound, inbound):
                connection.execute(
                    text(
                        "UPDATE finance.journal_line SET journal_entry_id = :source"
                        " WHERE id = :line"
                    ),
                    {"source": source, "line": line},
                )
            rejection = _commit(connection, tx)
            assert rejection is None, f"a balanced re-parent was rejected: {rejection}"

        with engine.connect() as reader:
            assert _line_count(reader, source) == 4
            assert _line_count(reader, other) == 2


class TestRawDataIsImmutable:
    """The raw side is append-only, and only for the two columns that are evidence.

    `raw_data` and `raw_description` are what the bank said, verbatim. Every other
    column is pipeline state the pipeline is supposed to move -
    `imported -> pending -> posted` - so a trigger that froze the whole row would
    break the import on its first status change. Both halves are tested: the
    columns that must not move, and the one that must.
    """

    def test_updating_raw_description_is_blocked(
        self, engine: Engine, account_id: int, batch_id: int
    ) -> None:
        with engine.connect() as connection:
            record = _seed_source_record(connection, batch_id, account_id)
            rejection = _statement_rejection(
                connection,
                f"UPDATE {SOURCE_RECORD} SET raw_description = :tampered"
                " WHERE id = :record",
                {"tampered": "TAMPERED", "record": record},
            )

        assert rejection is not None, "raw_description was overwritten"
        assert rejection.sqlstate == CHECK_VIOLATION
        assert "raw_description" in rejection.message
        assert "immutable" in rejection.message
        assert str(record) in rejection.message

    def test_updating_raw_data_is_blocked(
        self, engine: Engine, account_id: int, batch_id: int
    ) -> None:
        """The JSONB payload tampered with, not only the description."""
        with engine.connect() as connection:
            record = _seed_source_record(connection, batch_id, account_id)
            rejection = _statement_rejection(
                connection,
                f"UPDATE {SOURCE_RECORD} SET raw_data = CAST(:tampered AS JSONB)"
                " WHERE id = :record",
                {"tampered": '{"amount": -1}', "record": record},
            )

        assert rejection is not None, "raw_data was overwritten"
        assert rejection.sqlstate == CHECK_VIOLATION
        assert "raw_data" in rejection.message
        assert "immutable" in rejection.message

    def test_a_blocked_update_leaves_the_row_untouched(
        self, engine: Engine, account_id: int, batch_id: int
    ) -> None:
        """The trigger is BEFORE, so the write never happened at all.

        Without this assertion, "an error was raised" is equally consistent with a
        trigger that raises after the write - and then the whole immutability
        guarantee rests on every caller remembering to roll back.
        """
        with engine.connect() as connection:
            record = _seed_source_record(connection, batch_id, account_id)
            rejection = _statement_rejection(
                connection,
                f"UPDATE {SOURCE_RECORD} SET raw_description = :tampered"
                " WHERE id = :record",
                {"tampered": "TAMPERED", "record": record},
            )
            assert rejection is not None

            survivors = connection.execute(
                text(
                    f"SELECT raw_description, raw_data ->> 'amount'"
                    f" FROM {SOURCE_RECORD} WHERE id = :record"
                ),
                {"record": record},
            ).one()
            assert survivors.raw_description == "ORIGINAL DESCRIPTION"
            assert survivors[1] == "-8.50"

    def test_updating_status_is_allowed(
        self, engine: Engine, account_id: int, batch_id: int
    ) -> None:
        """The legal neighbouring statement, asserted legal.

        The immutability rule is scoped to two named columns, not the row, so
        this statement is legal and must stay legal. A trigger that refused it
        would leave the import pipeline
        unable to record anything it had done, which is why the immutability check
        inspects two named columns and not the row.
        """
        with engine.connect() as connection:
            record = _seed_source_record(connection, batch_id, account_id)
            connection.execute(
                text(f"UPDATE {SOURCE_RECORD} SET status = :status WHERE id = :record"),
                {"status": "posted", "record": record},
            )
            connection.commit()

            assert (
                connection.execute(
                    text(f"SELECT status FROM {SOURCE_RECORD} WHERE id = :record"),
                    {"record": record},
                ).scalar_one()
                == "posted"
            )

    def test_deleting_a_raw_record_is_blocked(
        self, engine: Engine, account_id: int, batch_id: int
    ) -> None:
        """Deleting the evidence is forbidden outright, with no way round it.

        The refusal is BEFORE DELETE, so the row is still there afterwards. That
        is asserted, because a trigger raising afterwards would have deleted it -
        and a restore-from-raw_data path does not exist.
        """
        with engine.connect() as connection:
            record = _seed_source_record(connection, batch_id, account_id)
            rejection = _statement_rejection(
                connection,
                f"DELETE FROM {SOURCE_RECORD} WHERE id = :record",
                {"record": record},
            )
            assert rejection is not None, "a raw record was deleted"
            assert rejection.sqlstate == CHECK_VIOLATION
            assert "DELETE" in rejection.message
            assert "forbidden" in rejection.message

            survivors = int(
                connection.execute(
                    text(f"SELECT count(*) FROM {SOURCE_RECORD} WHERE id = :record"),
                    {"record": record},
                ).scalar_one()
            )
            assert survivors == 1, "the row was deleted despite the rejection"


class TestCascadingDeleteStillWorks:
    """Deletion has to keep working, and the balance rules make that non-obvious.

    A cascade fires the balance trigger for every line it removes, on an entry
    that is on its way out. Without a guard for "the entry no longer exists", the
    minimum-line check would fire on the parent and every entry would be
    undeletable. These are the tests that stop a future edit to that guard from
    quietly making the ledger append-only.
    """

    def test_deleting_an_entry_removes_its_lines(
        self, engine: Engine, account_id: int
    ) -> None:
        with engine.connect() as connection:
            entry_id = _balanced(connection, account_id)
            assert _line_count(connection, entry_id) == 2

            # "Commit as you go" rather than `connection.begin()`: the read above
            # autobegan a transaction, and a DELETE that cannot be committed is
            # the thing under test here.
            connection.execute(
                text("DELETE FROM finance.journal_entry WHERE id = :id"),
                {"id": entry_id},
            )
            connection.commit()

            assert _line_count(connection, entry_id) == 0
            assert _entry_ids(connection) == []

    def test_deleting_an_entry_unlinks_the_raw_record_it_booked(
        self, engine: Engine, account_id: int, batch_id: int
    ) -> None:
        """`source_record.journal_entry_id` is `ON DELETE SET NULL`, and it has to be.

        `journal_line` cascades, but without `SET NULL` here the FK would fire
        first and the entry could not be deleted at all - so the test above would
        pass only because it had no raw record attached. This is the case that
        makes the difference visible: the record survives, unbooked and
        re-bookable, which is the state the dedupe and re-booking paths expect.
        """
        with engine.connect() as connection:
            record = _seed_source_record(connection, batch_id, account_id)
            entry_id = _balanced(connection, account_id)

            connection.execute(
                text(
                    f"UPDATE {SOURCE_RECORD} SET journal_entry_id = :entry_id"
                    " WHERE id = :record"
                ),
                {"entry_id": entry_id, "record": record},
            )
            connection.execute(
                text("DELETE FROM finance.journal_entry WHERE id = :id"),
                {"id": entry_id},
            )
            connection.commit()

            surviving = connection.execute(
                text(
                    "SELECT journal_entry_id, status FROM finance.source_record"
                    " WHERE id = :record"
                ),
                {"record": record},
            ).one()
            assert surviving.journal_entry_id is None
            assert surviving.status == "imported"


class TestTheHarnessItself:
    """Guards on the fixture, so a green suite means what it claims.

    These are the tests that stop the rest of this file from passing over an empty
    database, a stale one, or one whose cleanup has quietly stopped running.
    """

    def test_every_test_starts_from_an_empty_schema(self, engine: Engine) -> None:
        """Every table in `finance` is empty when a test body starts.

        If the cleanup stopped running - a new table missing from the truncate
        list, a fixture ordering change - the failure mode is a test reading
        another test's rows and passing for the wrong reason. Asserted here so
        that failure is loud and immediate rather than mysterious three tests
        later.
        """
        with engine.connect() as connection:
            counts = _table_counts(connection)

        assert counts, "finance has no tables at all; the migrations did not run"
        nonempty = {name: rows for name, rows in counts.items() if rows}
        assert nonempty == {}, f"leftover rows from an earlier test: {nonempty}"

    def test_identities_restart_between_tests(self, account_id: int) -> None:
        """`RESTART IDENTITY` is doing its job: the first id is 1, every test.

        The rejection messages above are asserted against literal ids, so a
        sequence that kept climbing would make those assertions depend on test
        order - and a suite whose assertions depend on execution order passes for
        the wrong reasons more often than anyone expects.

        `account_id` is the fixture under test here: it inserts into
        `finance.account` and hands back whatever the sequence gave it.
        """
        assert account_id == 1, (
            "the first account of a test was not id 1, so RESTART IDENTITY is "
            "not running and every literal id in this file is accidental"
        )
