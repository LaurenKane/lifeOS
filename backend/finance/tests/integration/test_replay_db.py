"""test_replay_db.py — `replay_batch` rebuilds the journal side from raw.

The acceptance tests for LifeOS-11 (M6: replay-from-raw). An import batch
stores its uploaded bytes in `import_batch.raw_payload`, and a replay rebuilds
everything journal-side from those bytes: detach, delete the orphaned
entries, re-book through the same writers, and re-apply the stored
categorization rules.

**Marked `db`, like the other integration tests here.** Real FastAPI app,
real migrated Postgres, assertions read back from a second connection.

**On ids.** `journal_entry` ids are BIGSERIAL handed out at INSERT, so a
rebuilt entry never carries the deleted entry's id. These tests therefore
assert the ledger FACTS — row counts, fingerprint sets, per-entry zero sums,
leg counts, category values — and never assert that an entry id survived a
rebuild. `source_record` ids ARE stable (replay updates the rows in place),
and the category assertions find their lines through those stable ids.
"""

from __future__ import annotations

from collections.abc import Iterator

import cli
import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text

from finance.api.replay import ReplayReport, replay_batch
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME
from finance.ingestion.fingerprint import normalize_description
from finance.tests.integration.test_import_persistence_db import (
    CHECKING_NAME,
    RABO_JUNE,
    _as_int,
    _counts,
    _rows,
    _scalar,
    rabo,
    upload,
)

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Fixtures, mirroring `test_import_persistence_db`
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`.

    The same fixture as in `test_import_persistence_db`, repeated rather than
    imported: fixtures read better next to the tests that request them, and a
    shared import would couple this file's setup to another file's teardown.
    """
    del test_database_url
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
def seeded(engine: Engine) -> dict[str, int]:
    """One EUR checking account and the system expense account, committed.

    The same two rows `test_import_persistence_db` needs: the checking account
    is what the June statement is attributed to, and the system expense
    account is the contra-leg every posted row books against.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            checking = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.account"
                        " (name, account_type, account_nature, currency)"
                        " VALUES (:name, 'checking', 'asset', 'EUR') RETURNING id"
                    ),
                    {"name": CHECKING_NAME},
                ).scalar_one()
            )
            connection.execute(
                text(
                    "INSERT INTO finance.account"
                    " (name, account_type, account_nature, currency, system_role)"
                    " VALUES (:name, 'cash', 'equity', 'EUR', 'system_expense')"
                ),
                {"name": SYSTEM_EXPENSE_ACCOUNT_NAME},
            )
    return {"checking": checking}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_replay(batch_id: int) -> ReplayReport:
    """Run `replay_batch` in its own transaction, the way the CLI does."""
    with get_sessionmaker()() as session:
        with session.begin():
            return replay_batch(session, batch_id=batch_id)


def _posted_descriptions(engine: Engine) -> list[str]:
    """Posted rows' descriptions, deterministically ordered."""
    return [
        str(row[0])
        for row in _rows(
            engine,
            "SELECT DISTINCT raw_description FROM finance.source_record"
            " WHERE journal_entry_id IS NOT NULL ORDER BY raw_description",
        )
    ]


def _sr_lines(engine: Engine, sr_id: int) -> list[tuple[int, int, int | None]]:
    """`(line id, account id, category id)` for one `source_record`'s entry.

    Scoped to a single record — unlike `_lines`, which spans every entry that
    shares a description — so a hand edit on one entry is asserted on that
    entry alone.
    """
    found: list[tuple[int, int, int | None]] = []
    for row in _rows(
        engine,
        "SELECT jl.id, jl.account_id, jl.category_id"
        " FROM finance.journal_line jl"
        " JOIN finance.source_record sr"
        " ON sr.journal_entry_id = jl.journal_entry_id"
        " WHERE sr.id = :id ORDER BY jl.id",
        id=sr_id,
    ):
        category = row[2]
        found.append(
            (
                _as_int(row[0]),
                _as_int(row[1]),
                None if category is None else _as_int(category),
            )
        )
    return found


def _lines(engine: Engine, description: str) -> list[tuple[int, int, int | None]]:
    """`(line id, account id, category id)` for one description's entry lines.

    Found through the `source_record` rows, whose ids are stable across a
    replay while `journal_entry` ids are not.
    """
    found: list[tuple[int, int, int | None]] = []
    for row in _rows(
        engine,
        "SELECT jl.id, jl.account_id, jl.category_id"
        " FROM finance.journal_line jl"
        " JOIN finance.source_record sr"
        " ON sr.journal_entry_id = jl.journal_entry_id"
        " WHERE sr.raw_description = :description ORDER BY jl.id",
        description=description,
    ):
        category = row[2]
        found.append(
            (
                _as_int(row[0]),
                _as_int(row[1]),
                None if category is None else _as_int(category),
            )
        )
    return found


# ---------------------------------------------------------------------------
# Acceptance #1: the journal side is rebuilt from the stored file
# ---------------------------------------------------------------------------


class TestReplayRebuildsTheJournalSide:
    """Delete every entry, replay, and read back identical ledger facts."""

    def test_replay_after_journal_delete_restores_identical_facts(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The entry rows are gone; the facts come back byte for byte.

        `source_record` rows are untouched throughout — replay updates them in
        place — so their count and fingerprint set are invariant. The journal
        side is rebuilt from `raw_payload`: same line count, every entry
        balancing to exactly zero, every entry carrying at least two legs.
        """
        payload = rabo(RABO_JUNE)
        summary = upload(client, payload, account_id=seeded["checking"])
        batch_id = _as_int(_scalar(engine, "SELECT id FROM finance.import_batch"))
        before = _counts(engine)
        before_fingerprints = {
            str(row[0])
            for row in _rows(
                engine,
                "SELECT encode(fingerprint, 'hex') FROM finance.source_record",
            )
        }

        # The disaster: every entry and line is gone, and the FK's SET NULL
        # leaves the raw side orphaned but intact.
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("DELETE FROM finance.journal_entry"))
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_line") == 0
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.source_record"
                " WHERE journal_entry_id IS NOT NULL",
            )
            == 0
        )

        report = _run_replay(batch_id)

        assert report.batch_id == batch_id
        assert report.ok
        assert report.parsed_rows == summary.record_count
        assert report.reposted == summary.created
        assert report.restored_pending == summary.failed

        # Replay writes no batch and no raw row: the counts are the import's.
        assert _counts(engine) == before
        assert {
            str(row[0])
            for row in _rows(
                engine,
                "SELECT encode(fingerprint, 'hex') FROM finance.source_record",
            )
        } == before_fingerprints

        unbalanced = _rows(
            engine,
            "SELECT l.journal_entry_id, sum(l.amount_base) AS total"
            " FROM finance.journal_line l GROUP BY l.journal_entry_id"
            " HAVING sum(l.amount_base) <> 0",
        )
        assert unbalanced == []
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM (SELECT journal_entry_id"
                " FROM finance.journal_line"
                " GROUP BY journal_entry_id HAVING count(*) < 2) AS under_legged",
            )
            == 0
        )


# ---------------------------------------------------------------------------
# Acceptance #2: rules from the table land on replay; manual edits stay
# ---------------------------------------------------------------------------


class TestReplayReappliesCategorization:
    """Rules are re-applied from the DB tables on every replay."""

    def test_rules_reapply_and_manual_edits_survive(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A rule categorizes on replay, a rule change re-categorizes, and a
        hand-set category on a line no rule claims survives the next replay.

        The rule pattern is the LONGEST posted description, normalized: no
        shorter description can contain it as a substring, so the rule matches
        exactly one entry. The hand-edited line is on an entry the rule does
        not match, which is the case the snapshot preserves.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])
        batch_id = _as_int(_scalar(engine, "SELECT id FROM finance.import_batch"))

        posted = _posted_descriptions(engine)
        assert len(posted) > 1, "June must hold several posted descriptions"
        target = max(posted, key=len)
        pattern = normalize_description(target)
        second = next(
            description
            for description in posted
            if pattern not in normalize_description(description)
        )

        with engine.connect() as connection:
            with connection.begin():
                cat_one = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.category (name, kind)"
                            " VALUES ('Replay One', 'expense') RETURNING id"
                        )
                    ).scalar_one()
                )
                cat_two = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.category (name, kind)"
                            " VALUES ('Replay Two', 'expense') RETURNING id"
                        )
                    ).scalar_one()
                )
                rule_id = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.category_rule"
                            " (description_pattern, category_id, priority)"
                            " VALUES (:pattern, :cat, 100) RETURNING id"
                        ),
                        {"pattern": pattern, "cat": cat_one},
                    ).scalar_one()
                )

        first = _run_replay(batch_id)
        assert first.category_rules_applied >= 2
        target_lines = [cat for _, _, cat in _lines(engine, target)]
        assert target_lines, "the rule must have matched the target entry"
        assert all(cat == cat_one for cat in target_lines), target_lines

        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE finance.category_rule"
                        " SET category_id = :cat WHERE id = :id"
                    ),
                    {"cat": cat_two, "id": rule_id},
                )

        _run_replay(batch_id)
        assert all(cat == cat_two for _, _, cat in _lines(engine, target)), _lines(
            engine, target
        )

        second_lines = _lines(engine, second)
        assert second_lines, "the second entry must exist before editing"
        second_sr = _as_int(
            _scalar(
                engine,
                "SELECT min(id) FROM finance.source_record"
                " WHERE raw_description = :description"
                " AND journal_entry_id IS NOT NULL",
                description=second,
            )
        )
        checking_line = min(
            line_id
            for line_id, account_id, _ in _sr_lines(engine, second_sr)
            if account_id == seeded["checking"]
        )
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE finance.journal_line"
                        " SET category_id = :cat WHERE id = :id"
                    ),
                    {"cat": cat_one, "id": checking_line},
                )

        _run_replay(batch_id)
        survived = [
            cat
            for _, account_id, cat in _sr_lines(engine, second_sr)
            if account_id == seeded["checking"]
        ]
        assert survived and all(cat == cat_one for cat in survived), survived
        assert all(cat == cat_two for _, _, cat in _lines(engine, target)), _lines(
            engine, target
        )


# ---------------------------------------------------------------------------
# Acceptance #3: the CLI reports and exits honestly
# ---------------------------------------------------------------------------


class TestReplayCli:
    """`lifeos-cli replay` exits 0 with the batch id, non-zero without it."""

    def test_replay_command_reports_success(
        self,
        client: TestClient,
        engine: Engine,
        seeded: dict[str, int],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A replayed batch exits 0 and names the batch on stdout."""
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])
        batch_id = _as_int(_scalar(engine, "SELECT id FROM finance.import_batch"))

        assert cli.main(["replay", "--batch-id", str(batch_id)]) == 0
        assert str(batch_id) in capsys.readouterr().out

    def test_replay_missing_batch_is_nonzero(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A batch that does not exist refuses with a non-zero exit."""
        assert cli.main(["replay", "--batch-id", "999999"]) != 0
