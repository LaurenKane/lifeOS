"""test_replay_multisection_db.py — a Revolut batch replays across sections.

LifeOS-muq: a Revolut annual statement covers several products, and the
statement never says which local account a product belongs to. The upload
therefore takes a per-section mapping, the batch remembers it
(`section_account_ids`, migration 0005), and the replay re-attributes each
row exactly as the import did.

Two tests, two levels of determinism. The first drives the REAL statement
through the real extractor, so the claim is about the file on disk rather
than a fixture. The second is fully synthetic — three crafted statement
lines through the adapter's own `text_extractor` seam — so CI proves the
stored-mapping round trip with no statements on disk.

**Marked `db`.** Facts are found through stable ids and fingerprint sets,
never through BIGSERIAL entry ids, which a replay does not preserve.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text

import finance.api.replay as replay_module
import finance.api.routes.imports as imports_module
from finance.api.replay import ReplayReport, replay_batch
from finance.api.routes.imports import FileAdapter
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME
from finance.ingestion.adapters.revolut_pdf import RevolutPdfAdapter

pytestmark = pytest.mark.db

#: Overridable, matching `test_import_persistence_db`.
STATEMENTS_DIR_ENV_VAR: Final = "LIFEOS_STATEMENTS_DIR"
DEFAULT_STATEMENTS_DIR: Final = Path.home() / "Documents" / "Banking"

#: Named, never globbed: the verified annual statement the adapter was
#: transcribed against (see `revolut_pdf.py`).
REVOLUT_STATEMENT: Final = "account-statement_2026-01-01_2026-10-01_en-gb_783080.pdf"

#: A PDF starts with `%PDF-`. Checked rather than assumed, so a truncated
#: download fails here with a name instead of surfacing as a parser error.
PDF_MAGIC: Final = b"%PDF-"

PRIMARY_NAME: Final = "Revolut current account"
DEPOSIT_NAME: Final = "Revolut deposit account"


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
    """EUR, a primary and a deposit account, and the system expense account."""
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            ids: dict[str, int] = {}
            for key, name in (
                ("primary", PRIMARY_NAME),
                ("deposit", DEPOSIT_NAME),
            ):
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, 'checking', 'asset', 'EUR')"
                            " RETURNING id"
                        ),
                        {"name": name},
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
    return ids


# ---------------------------------------------------------------------------
# Database reads, always from a second connection
# ---------------------------------------------------------------------------


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


def _fingerprints(engine: Engine) -> set[str]:
    """Every stored fingerprint as hex — the replay's equality claim."""
    return {
        str(row[0])
        for row in _rows(
            engine, "SELECT encode(fingerprint, 'hex') FROM finance.source_record"
        )
    }


def _counts_by_account(engine: Engine) -> dict[int, int]:
    """`account id -> source_record rows`, the attribution claim."""
    return {
        _as_int(row[0]): _as_int(row[1])
        for row in _rows(
            engine,
            "SELECT account_id, count(*) FROM finance.source_record"
            " GROUP BY account_id",
        )
    }


def _assert_rebuilt_identically(engine: Engine, *, entries: int) -> None:
    """The ledger facts a replay must preserve: entry count and zero sums."""
    assert (
        _as_int(_scalar(engine, "SELECT count(*) FROM finance.journal_entry"))
        == entries
    )
    unbalanced = _rows(
        engine,
        "SELECT journal_entry_id, sum(amount_base)"
        " FROM finance.journal_line GROUP BY journal_entry_id"
        " HAVING sum(amount_base) <> 0",
    )
    assert unbalanced == [], f"entries that do not sum to zero: {unbalanced}"


# ---------------------------------------------------------------------------
# The real statement
# ---------------------------------------------------------------------------


def revolut(name: str) -> bytes:
    """The named real statement's bytes, or a skip naming what is missing.

    The same convention as `test_import_persistence_db.rabo`: a missing file
    SKIPS, because statements are not distributed with the repository; a file
    that is not a PDF FAILS, because that is a truncated download.
    """
    configured = os.environ.get(STATEMENTS_DIR_ENV_VAR, "").strip()
    root = Path(configured or DEFAULT_STATEMENTS_DIR).expanduser() / "Rev"
    if not root.is_dir():
        pytest.skip(
            f"no bank statements directory at {root}. Point "
            f"{STATEMENTS_DIR_ENV_VAR} at the directory holding the Rev "
            "subdirectory; this test reconciles the real statement."
        )
    path = root / name
    if not path.is_file():
        pytest.skip(f"{path} is not present; this test imports it by name.")
    data = path.read_bytes()
    if PDF_MAGIC not in data[:1024]:
        pytest.fail(f"{path} is named as a statement but its contents are not a PDF")
    return data


class TestRealMultisectionReplay:
    def test_import_remembers_the_mapping_and_replay_rebuilds_it(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The bead's acceptance: 340 Account rows and 11 Deposit rows,
        re-attributed on replay from the stored mapping.

        Per-section expectations come from the adapter's own parse of the
        same bytes rather than from hardcoded counts, so a parser
        improvement moves both sides together instead of failing here with a
        stale number.
        """
        payload = revolut(REVOLUT_STATEMENT)
        mapping = {"account": seeded["primary"], "deposit": seeded["deposit"]}
        response = client.post(
            "/api/v1/imports/file",
            params={"provider": "revolut_pdf", "account_id": seeded["primary"]},
            data={"section_account_ids": json.dumps(mapping)},
            files={"file": (REVOLUT_STATEMENT, payload, "application/pdf")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, dict), body
        assert _as_int(body["failed"]) == 0, body["failures"]
        assert _as_int(body["created"]) == _as_int(body["record_count"]) > 0

        batch_id = _as_int(_scalar(engine, "SELECT id FROM finance.import_batch"))
        stored = _scalar(
            engine,
            "SELECT section_account_ids FROM finance.import_batch WHERE id = :id",
            id=batch_id,
        )
        assert isinstance(stored, dict), stored
        assert {str(key): _as_int(value) for key, value in stored.items()} == mapping

        parsed = RevolutPdfAdapter(section_account_ids=mapping).parse(
            payload, account_id=seeded["primary"], filename=REVOLUT_STATEMENT
        )
        expected: dict[int, int] = {}
        for record in parsed.records:
            assert record.account_id is not None
            expected[record.account_id] = expected.get(record.account_id, 0) + 1
        assert set(expected) == {seeded["primary"], seeded["deposit"]}, (
            "both sections must attribute rows, or this test proves nothing "
            "about a multi-section file"
        )
        assert _counts_by_account(engine) == expected

        before_entries = _as_int(
            _scalar(engine, "SELECT count(*) FROM finance.journal_entry")
        )
        before_fingerprints = _fingerprints(engine)
        _assert_rebuilt_identically(engine, entries=before_entries)

        report = _run_replay(batch_id)

        assert report.ok
        assert report.parsed_rows == _as_int(body["record_count"])
        assert _fingerprints(engine) == before_fingerprints
        assert _counts_by_account(engine) == expected
        _assert_rebuilt_identically(engine, entries=before_entries)


# ---------------------------------------------------------------------------
# The deterministic round trip (no statements on disk)
# ---------------------------------------------------------------------------

#: Two Account rows and one Deposit row, laid out for the adapter's own
#: column anchors: the money-out amount sits left of the out/in midpoint and
#: the running balance right of the in/close one. Verified by hand against
#: `column_anchors`, and the probe below pins the attribution, not the
#: arithmetic — the layout is a means, not the claim.
_STATEMENT_LINES: Final = (
    "Account transactions from 01 Jan 2026 to 31 Jan 2026",
    "Description          Money out    Money in     Balance",
    "01 Jan 2026  COFFEE SHOP  € 3.20                € 96.80",
    "02 Jan 2026  BOOKSTORE  € 12.50               € 109.30",
    "Deposit transactions from 01 Jan 2026 to 31 Jan 2026",
    "Description          Money out    Money in     Balance",
    "05 Jan 2026  DEPOSIT BONUS   € 50.00              € 50.00",
)


def _extract_statement_lines(payload: bytes) -> list[str]:
    """The extractor seam, returning the crafted sections whatever the bytes."""
    del payload
    return list(_STATEMENT_LINES)


def _adapter_with_crafted_sections(
    provider: str, *, section_account_ids: Mapping[str, int] | None = None
) -> FileAdapter:
    """The factory's shape, steering only the extractor, never the mapping."""
    assert provider == "revolut_pdf", provider
    return RevolutPdfAdapter(
        text_extractor=_extract_statement_lines,
        section_account_ids=dict(section_account_ids or {}),
    )


class TestStoredMappingRoundTrip:
    def test_import_stores_and_replay_reads_the_mapping(
        self,
        client: TestClient,
        engine: Engine,
        seeded: dict[str, int],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Three crafted rows: two land on the primary, one on the deposit.

        The factory is steered in BOTH modules that call it — the route and
        the replay hold separate references — so import and replay parse
        through the same extractor. Steering the mapping itself would prove
        nothing; only the text source is faked.
        """
        monkeypatch.setattr(
            imports_module, "_adapter_for", _adapter_with_crafted_sections
        )
        monkeypatch.setattr(
            replay_module, "_adapter_for", _adapter_with_crafted_sections
        )

        mapping = {"account": seeded["primary"], "deposit": seeded["deposit"]}
        response = client.post(
            "/api/v1/imports/file",
            params={"provider": "revolut_pdf", "account_id": seeded["primary"]},
            data={"section_account_ids": json.dumps(mapping)},
            files={"file": ("multi.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert isinstance(body, dict), body
        assert _as_int(body["failed"]) == 0, body["failures"]
        assert _as_int(body["created"]) == 3

        batch_id = _as_int(_scalar(engine, "SELECT id FROM finance.import_batch"))
        assert _counts_by_account(engine) == {
            seeded["primary"]: 2,
            seeded["deposit"]: 1,
        }

        before_fingerprints = _fingerprints(engine)
        report = _run_replay(batch_id)

        assert report.ok
        assert report.parsed_rows == 3
        assert report.reposted == 3
        assert _fingerprints(engine) == before_fingerprints
        assert _counts_by_account(engine) == {
            seeded["primary"]: 2,
            seeded["deposit"]: 1,
        }
        _assert_rebuilt_identically(engine, entries=3)

    def test_an_unknown_section_account_is_a_404_and_writes_no_batch(
        self,
        client: TestClient,
        engine: Engine,
        seeded: dict[str, int],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A mapping that names a missing account refuses before the batch."""
        monkeypatch.setattr(
            imports_module, "_adapter_for", _adapter_with_crafted_sections
        )
        response = client.post(
            "/api/v1/imports/file",
            params={"provider": "revolut_pdf", "account_id": seeded["primary"]},
            data={"section_account_ids": json.dumps({"deposit": 999_999})},
            files={"file": ("multi.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        )
        assert response.status_code == 404, response.text
        assert "999999" in response.json()["detail"]
        assert (
            _as_int(_scalar(engine, "SELECT count(*) FROM finance.import_batch")) == 0
        )
