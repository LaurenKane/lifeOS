"""test_replay_with_transfers_db.py — a replay keeps its transfer match, on Postgres.

`replay_batch` rebuilds a batch's journal side from its stored file and then
re-links that batch's legs. This test builds a batch holding an own-account
transfer, replays it, and asserts the replayed ledger carries the same match —
and that a second replay does not double it.

On the batch shape: a `manual`-provider batch stores no `raw_payload` and has
no file adapter, so `replay_batch` refuses it by design. The batch here is
therefore built through the file path with a stub adapter returning two rows
(an outbound on checking, its inbound on savings) — the replayable shape that
carries a transfer. Entry and line ids are NOT stable across a replay
(BIGSERIAL), so the assertions are on ledger facts found through the stable
`source_record` ids, never on entry ids.

Marked `db`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text

from finance.api.replay import ReplayReport, replay_batch
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME
from finance.ingestion.adapters.base import ImportResult
from finance.public import RawRecord

pytestmark = pytest.mark.db

#: The transfer under test: €50, same day, both directions.
DAY = dt.date(2026, 3, 10)
AMOUNT = 5_000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`, caches cleared."""
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
    """Two own asset accounts and the system expense account."""
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            ids: dict[str, int] = {}
            for key, name, kind in (
                ("checking", "Checking", "checking"),
                ("savings", "Savings", "savings"),
            ):
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, :kind, 'asset', 'EUR') RETURNING id"
                        ),
                        {"name": name, "kind": kind},
                    ).scalar_one()
                )
            ids["expense"] = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.account"
                        " (name, account_type, account_nature, currency,"
                        "  system_role)"
                        " VALUES (:name, 'cash', 'equity', 'EUR',"
                        "  'system_expense') RETURNING id"
                    ),
                    {"name": SYSTEM_EXPENSE_ACCOUNT_NAME},
                ).scalar_one()
            )
    return ids


# ---------------------------------------------------------------------------
# The stub statement: one transfer, two rows
# ---------------------------------------------------------------------------


class _TransferAdapter:
    """An adapter returning an own-account transfer: out and in, same day."""

    provider: ClassVar[str] = "rabobank_pdf"
    import_method: ClassVar[str] = "pdf"

    checking_id: ClassVar[int] = 0
    savings_id: ClassVar[int] = 0

    def parse(
        self,
        payload: bytes,
        account_id: int | None = None,
        filename: str | None = None,
    ) -> ImportResult:
        del payload, account_id, filename
        return ImportResult(
            provider="rabobank_pdf",
            import_method="pdf",
            records=[
                RawRecord(
                    account_id=type(self).checking_id,
                    description="TRANSFER TO SAVINGS",
                    amount_minor=-AMOUNT,
                    currency="EUR",
                    booked_date=DAY,
                    line_number=1,
                    raw_data={"row": "out"},
                ),
                RawRecord(
                    account_id=type(self).savings_id,
                    description="TRANSFER FROM CHECKING",
                    amount_minor=AMOUNT,
                    currency="EUR",
                    booked_date=DAY,
                    line_number=2,
                    raw_data={"row": "in"},
                ),
            ],
            source_checksum="1" * 64,
            source_filename="transfer.pdf",
        )


def _scalar(engine: Engine, statement: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(statement), params).scalar_one()


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


def _as_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _run_replay(batch_id: int) -> ReplayReport:
    """Run `replay_batch` in its own transaction, the way the CLI does."""
    with get_sessionmaker()() as session:
        with session.begin():
            return replay_batch(session, batch_id=batch_id)


def _match_facts(engine: Engine) -> list[tuple[object, ...]]:
    """`(method, out account, out amount, in account, in amount)` per match."""
    return _rows(
        engine,
        "SELECT tm.match_method,"
        " out_line.account_id, out_line.amount,"
        " in_line.account_id, in_line.amount"
        " FROM finance.transfer_match tm"
        " JOIN finance.journal_line out_line"
        "   ON out_line.id = tm.journal_line_id_out"
        " JOIN finance.journal_line in_line"
        "   ON in_line.id = tm.journal_line_id_in"
        " ORDER BY tm.id",
    )


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


class TestReplayKeepsTheTransfer:
    def test_replay_restores_the_same_match_and_never_doubles_it(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Import links the pair; each replay rebuilds exactly that pair.

        The match is found through the stable `source_record` rows: both
        records stay posted, their entries are flagged transfers, and the two
        matched legs are the same €50 out of checking into savings — while
        `transfer_match` holds exactly one row throughout.
        """
        from unittest import mock

        import finance.api.routes.imports as imports_routes

        _TransferAdapter.checking_id = seeded["checking"]
        _TransferAdapter.savings_id = seeded["savings"]

        def _stub_adapter() -> object:
            return _TransferAdapter()

        # `patch.dict` mutates the table IN PLACE rather than replacing the
        # attribute: `finance.api.replay` holds its own `from`-imported
        # reference to the same dict, so replacing the attribute on the route
        # module would leave the replay reading the real adapters.
        with mock.patch.dict(
            imports_routes._FILE_ADAPTERS,
            {"rabobank_pdf": (_stub_adapter, ".pdf")},
            clear=True,
        ):
            response = client.post(
                "/api/v1/imports/file",
                params={
                    "provider": "rabobank_pdf",
                    "account_id": seeded["checking"],
                },
                files={
                    "file": (
                        "transfer.pdf",
                        b"%PDF-1.4 synthetic",
                        "application/pdf",
                    )
                },
            )
            assert response.status_code == 200, response.text
            batch_id = _as_int(_scalar(engine, "SELECT id FROM finance.import_batch"))
            assert _match_facts(engine) == [
                (
                    "auto_amount_date",
                    seeded["checking"],
                    -AMOUNT,
                    seeded["savings"],
                    AMOUNT,
                )
            ]

            first = _run_replay(batch_id)
            assert first.ok
            assert _match_facts(engine) == [
                (
                    "auto_amount_date",
                    seeded["checking"],
                    -AMOUNT,
                    seeded["savings"],
                    AMOUNT,
                )
            ]

            second = _run_replay(batch_id)
            assert second.ok
            assert _match_facts(engine) == [
                (
                    "auto_amount_date",
                    seeded["checking"],
                    -AMOUNT,
                    seeded["savings"],
                    AMOUNT,
                )
            ]

        # Both source records stayed posted on transfer entries, across both
        # replays — found by their stable ids, not by entry ids.
        records = _rows(
            engine,
            "SELECT sr.journal_entry_id, je.is_transfer"
            " FROM finance.source_record sr"
            " JOIN finance.journal_entry je ON je.id = sr.journal_entry_id"
            " WHERE sr.import_batch_id = :batch ORDER BY sr.id",
            batch=batch_id,
        )
        assert len(records) == 2, records
        assert all(row[0] is not None and row[1] is True for row in records)

        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 1
        unbalanced = _rows(
            engine,
            "SELECT journal_entry_id, sum(amount_base)"
            " FROM finance.journal_line GROUP BY journal_entry_id"
            " HAVING sum(amount_base) <> 0",
        )
        assert unbalanced == []

        # The replays wrote no batch and no raw row: these are the import's.
        assert (
            _as_int(_scalar(engine, "SELECT count(*) FROM finance.import_batch")) == 1
        )
        assert (
            _as_int(
                _scalar(
                    engine,
                    "SELECT count(*) FROM finance.source_record"
                    " WHERE import_batch_id = :batch",
                    batch=batch_id,
                )
            )
            == 2
        )
