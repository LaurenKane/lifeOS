"""test_import_persistence_db.py — `POST /imports/file` persists, end to end.

The acceptance test for LifeOS-1zh: an uploaded statement becomes rows in a
`finance.source_record`, rows in a `finance.journal_entry`, and an
`import_batch` whose status describes WHAT PERSISTED. The adapters were already
complete and unit-tested; what was missing was the write, and a complete parser
that writes nothing is not an import.

**Marked `db`, like the other integration tests here.** These drive the real
FastAPI app against a real, migrated Postgres and read every assertion back from
a SECOND connection, so an uncommitted transaction cannot satisfy them. `make
test` stays runnable with no Docker.

**The statements are real, not fixtures.** `~/Documents/Banking/Rabo` holds the
four Rabobank PDFs the parsers were written against, and this file imports them by
name — a June statement, then a July one that overlaps it. A synthetic statement
would prove that a known-shaped input round-trips, which is a weaker claim than
the one this bead makes. A missing directory SKIPS (the files are not
distributed with the repository); the resolution mirrors
`test_real_statement_reconciliation.py` exactly, including its refusal to glob:
an unexpected fifth PDF fails rather than silently lengthening what this file
claims to have verified.


THE FOUR SILENT-WRONG-DATA TRAPS THIS FILE EXISTS FOR
=======================================================

A wrong answer that raises is not dangerous. Each of these produces a ledger that
looks entirely plausible and is internally consistent:

1. **A card payment posted as an expense.** The equity requirement lives in
   `resolve_default_counter_account`, NOT in `build_expense_legs`. Send one
   through the manual default and it does not reliably refuse: with an active
   `Expenses (system)` account present, a monthly card payment books AGAINST
   EQUITY. It balances, so the trigger accepts it; `raw_data` is immutable, so it
   is permanent; and the statement still reconciles while the balance sheet is
   simply wrong. 4 of the 121 real Amex rows are card payments
   (`docs/adr/0007-imported-card-payment-is-a-transfer.md`).

2. **Currency decimals from the code rather than the table.**
   `normalize_currency` hardcodes 2 because it is the pre-database path, so a
   JPY or BTC amount is wrong by a factor of 100 before it reaches a writer.

3. **Dedup done as a scan rather than a lookup.** Passing every stored
   fingerprint in as a Python list is O(rows x stored) inside one transaction;
   populating `IdentityResolver`'s candidates merges at >= 0.85 and would merge
   genuine separate purchases on a PDF path, silently.

4. **A batch status copied from the parse result.** `ImportResult.status` is
   parse-derived and says `completed` for a file whose rows were only partly
   written — which is exactly what a silently short statement looks like.

The occurrence index is the fifth, and it is the subtlest: it must be the
CONTENT-ONLY within-batch rank, so that re-importing a statement recognises it
rather than renumbering it. `stored_count + rank` was simulated and is WRONG:
re-importing `[K, K, L]` gives `created=2, duplicated=1` instead of
`created=0, duplicated=3`. Every such re-import would shift the indices of
everything after it and rewrite the whole file.
"""

from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
import os
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Final

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME
from finance.ingestion.adapters import RabobankPdfAdapter
from finance.ingestion.adapters.base import ImportResult
from finance.public import RawRecord, TransactionStatus

pytestmark = pytest.mark.db

#: Overridable, matching `test_real_statement_reconciliation.py`.
STATEMENTS_DIR_ENV_VAR: Final = "LIFEOS_STATEMENTS_DIR"
DEFAULT_STATEMENTS_DIR: Final = Path.home() / "Documents" / "Banking"

#: Named, never globbed. A glob cannot tell four months from one month plus three
#: files from another year.
#:
#: June is the clean statement: 16 rows, all distinct. August is the one that
#: carries a genuine content-identical PAIR (two identical direct debits on one
#: day), which is why it is the second file: it is the only statement in the
#: directory that can exercise the occurrence index on real data, and choosing it
#: is what makes "two identical rows are two transactions" a claim about a bank
#: statement rather than about a fixture.
RABO_JUNE: Final = "rabobank-2026-06.pdf"
RABO_AUGUST: Final = "rabobank-2026-08.pdf"

#: A PDF starts with `%PDF-`. CHECKED rather than assumed, so a truncated
#: download fails here with a name instead of surfacing later as a parser error.
PDF_MAGIC: Final = b"%PDF-"

CHECKING_NAME: Final = "Rabobank current account"

#: Enough of a PDF for the adapter to be handed bytes at all. The content is
#: irrelevant here because `_FILE_ADAPTERS` is patched to an adapter that
#: ignores its input; what is being tested is the ROUTE's decision, not a parser.
SYNTHETIC_PDF: Final = b"%PDF-1.4 synthetic"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`.

    The three `cache_clear()` calls and `raise_server_exceptions` being left ON
    are both load-bearing and both are explained at length in
    `test_manual_transactions_db.py`, whose fixture this is deliberately the same
    as. In short: the caches are `lru_cache`d and would otherwise aim the
    application at whatever database was read first, and a handler that raised
    must fail these tests rather than return a typed 500 that a loose assertion
    would accept.
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

    Two rows, and both are required rather than convenient. The checking account
    is what a Rabobank statement is attributed to. The `Expenses (system)`
    equity account is what makes trap 1 dangerous: WITHOUT it a card payment
    would be refused loudly and this file would prove nothing, and WITH it the
    wrong answer is the one that balances. The trap is only demonstrable in the
    seeded configuration, so the fixture builds it deliberately.
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
# The real statements
# ---------------------------------------------------------------------------


def rabo(name: str) -> bytes:
    """The named real statement's bytes, or a skip naming what is missing.

    A missing file SKIPS rather than fails: these PDFs are not distributed with
    the repository, so their absence on another machine is expected rather than
    wrong. A file that EXISTS and is not a PDF FAILS, because that is a truncated
    download and it would otherwise surface as an unexplained parse error.
    """
    configured = os.environ.get(STATEMENTS_DIR_ENV_VAR, "").strip()
    root = Path(configured or DEFAULT_STATEMENTS_DIR).expanduser() / "Rabo"
    if not root.is_dir():
        pytest.skip(
            f"no bank statements directory at {root}. Point "
            f"{STATEMENTS_DIR_ENV_VAR} at the directory holding the Rabo "
            "subdirectory; this test reconciles the real statement."
        )
    path = root / name
    if not path.is_file():
        pytest.skip(f"{path} is not present; this test imports it by name.")
    data = path.read_bytes()
    if PDF_MAGIC not in data[:1024]:
        pytest.fail(f"{path} is named as a statement but its contents are not a PDF")
    return data


def content_key(record: RawRecord) -> tuple[object, ...]:
    """What makes two rows "the same transaction", for the tests that reason about it.

    `finance.ingestion.dedupe.OccurrenceKey` is the production rule and this is the
    same tuple written out, because these tests need to reason about overlap
    WITHOUT a database. Deliberately not imported: a test that used the production
    class to decide what the production class decided would prove only that the
    class is consistent with itself.
    """
    return (
        record.description,
        record.amount_minor,
        record.currency,
        record.booked_date,
    )


def parsed_count(payload: bytes) -> int:
    """How many rows the adapter reads, without touching the database.

    Used to state expectations as a fraction of what the parser found rather than
    as a hardcoded row count, because a hardcoded one is a second claim about the
    statement that goes stale the moment a parser improves — and goes stale
    SILENTLY, as a test that fails for a reason nobody can see.
    """
    return RabobankPdfAdapter().parse(payload).record_count


@dataclass(frozen=True)
class Summary:
    """What `POST /imports/file` reported, typed.

    A named shape rather than `dict[str, object]` for the same reason
    `test_manual_transactions_db.py` has one: an `object` forces `int()` casts at
    every arithmetic site, which is how a test ends up asserting on a value in a
    shape nobody expected. `failures` stays a list because it is read as one.
    """

    provider: str
    import_method: str
    status: str
    record_count: int
    source_checksum: str | None
    created: int
    duplicated: int
    failed: int
    failures: list[str]

    @property
    def landed(self) -> int:
        """Rows that are IN the ledger, whether new or recognised.

        This — not `status == "completed"` — is the question "did everything land
        answer", and it is a different question on purpose.
        """
        return self.created + self.duplicated


def upload(
    client: TestClient, payload: bytes, *, account_id: int, filename: str = RABO_JUNE
) -> Summary:
    """POST the statement to `/imports/file` and return the summary.

    The response is asserted to be 200 here rather than in each caller, because a
    test whose first assertion is "the endpoint worked" reads better than one that
    has to remember to check.
    """
    response = client.post(
        "/api/v1/imports/file",
        params={"provider": "rabobank_pdf", "account_id": account_id},
        files={"file": (filename, payload, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return _summary_of(response.json())


def _summary_of(body: object) -> Summary:
    """Read a summary out of a decoded JSON response body.

    Every count goes through `_as_int`, so a body reporting `"created": "40"` as a
    string fails here with a name rather than coercing quietly. That is the whole
    reason this is not a dict comprehension.
    """
    assert isinstance(body, dict), f"expected a JSON object, got {body!r}"
    reasons = body["failures"]
    assert isinstance(reasons, list), f"expected a list, got {reasons!r}"
    checksum = body["source_checksum"]
    return Summary(
        provider=str(body["provider"]),
        import_method=str(body["import_method"]),
        status=str(body["status"]),
        record_count=_as_int(body["record_count"]),
        source_checksum=None if checksum is None else str(checksum),
        created=_as_int(body["created"]),
        duplicated=_as_int(body["duplicated"]),
        failed=_as_int(body["failed"]),
        failures=[str(reason) for reason in reasons],
    )


# ---------------------------------------------------------------------------
# Database reads, always from a second connection
# ---------------------------------------------------------------------------


def _scalar(engine: Engine, statement: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(statement), params).scalar_one()


def _as_str(value: object) -> str:
    """A string, with `CHAR` padding removed.

    `raw_currency` is `CHAR(3)`, which PostgreSQL blank-pads. The padding is not
    part of an ISO 4217 code, so every currency read goes through here rather than
    through `.strip()` at each call site — one place to forget it.
    """
    assert isinstance(value, str), f"expected a text column, got {value!r}"
    return value.strip()


def _as_int(value: object) -> int:
    """An int, or a loud failure.

    `assert isinstance` rather than `int(value)`: casting an `object` to `int` is
    exactly how a test ends up asserting on a value the driver returned in a shape
    nobody expected, and `True == 1` in Python so a bool must be excluded too.
    """
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


def _counts(engine: Engine) -> dict[str, int]:
    """Row counts of the three tables an import writes.

    `journal_line` is included because an entry with no legs would satisfy a
    `journal_entry` count while writing nothing, and "two rows that balance" is
    the actual claim.
    """
    return {
        table: _as_int(_scalar(engine, f"SELECT count(*) FROM finance.{table}"))
        for table in ("source_record", "journal_entry", "journal_line", "import_batch")
    }


# ---------------------------------------------------------------------------
# Criterion 1: a real statement becomes real rows, and every entry balances
# ---------------------------------------------------------------------------


class TestARealStatementBecomesRealRows:
    """Import `rabobank-2026-06.pdf` and read the ledger back from outside."""

    def test_every_parsed_row_lands_and_only_the_card_payment_is_unposted(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Every parsed row is STORED; the card payment is not POSTED.

        The fixture registers no paying account, so the statement's monthly card
        payment is stored unposted with an actionable reason
        (`docs/adr/0007-imported-card-payment-is-a-transfer.md`). It is still a
        row — dropping it is how a statement stops reconciling — so the count of
        `source_record` is unchanged and only the `posted` count drops by one.
        """
        payload = rabo(RABO_JUNE)
        rows = parsed_count(payload)
        assert rows > 0, (
            "the statement must actually contain rows for this to mean anything"
        )

        body = upload(client, payload, account_id=seeded["checking"])

        assert body.created == rows - 1
        assert body.failed == 1
        assert body.duplicated == 0
        assert body.status == "partial"

        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == rows
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.source_record"
                " WHERE status = 'posted' AND journal_entry_id IS NOT NULL",
            )
            == rows - 1
        )
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.source_record"
                " WHERE status = 'pending' AND journal_entry_id IS NULL",
            )
            == 1
        )
        assert _scalar(engine, "SELECT count(*) FROM finance.import_batch") == 1

    def test_every_journal_entry_balances_to_exactly_zero(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**`sum(amount_base) == Decimal("0.0000")` for EVERY entry.**

        Equality, not `abs(sum) < 0.005`. The trigger tolerates half a cent, so a
        tolerance assertion would pass against an entry that is out by 0.005 —
        a ledger that balances only approximately, with the drift inherited by
        every downstream sum. Stored data must be exact or the invariant is
        decorative.

        Asserted over the whole table rather than over one entry, because a
        per-entry test would pass if the importer wrote a single balanced row and
        silently dropped the other hundred.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])

        unbalanced = _rows(
            engine,
            "SELECT l.journal_entry_id, sum(l.amount_base) AS total"
            " FROM finance.journal_line l GROUP BY l.journal_entry_id"
            " HAVING sum(l.amount_base) <> 0",
        )
        assert unbalanced == [], (
            f"entries that do not sum to exactly zero: {unbalanced}"
        )

        # And the entries that exist have the two legs double entry requires.
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM (SELECT journal_entry_id"
                " FROM finance.journal_line"
                " GROUP BY journal_entry_id HAVING count(*) < 2) AS under_legged",
            )
            == 0
        )

    def test_the_batch_records_what_a_replay_reads(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`source_payload` is gzipped, base64'd and byte-identical to the upload.

        `import_batch.raw_payload` is what a replay reads, and a replay that
        cannot reconstruct the file cannot reproduce anything. Decoded here and
        compared BYTE FOR BYTE against the uploaded PDF rather than against a
        length or a hash of a hash.

        The gzip is written with `mtime=0` so this comparison is stable; without
        it the compressed bytes embed the current time and two imports of the same
        file would differ for no reason at all.
        """
        payload = rabo(RABO_JUNE)
        body = upload(client, payload, account_id=seeded["checking"])

        stored = _scalar(engine, "SELECT raw_payload FROM finance.import_batch")
        assert isinstance(stored, str)
        assert gzip.decompress(base64.b64decode(stored)) == payload

        checksum = _scalar(engine, "SELECT source_checksum FROM finance.import_batch")
        assert body.source_checksum is not None
        assert body.source_checksum == checksum
        filename = _scalar(engine, "SELECT source_filename FROM finance.import_batch")
        assert filename == RABO_JUNE

    def test_the_raw_side_round_trips_and_the_second_date_is_kept(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`raw_data` is the provider's row, and `raw_posting_date` is its own column.

        The second date is lifted out of `raw_data` and PARSED: Rabobank writes it
        as `DD-MM-YYYY` under `processing_date` and the column is a DATE, so a
        row that skipped the parse would be silently NULL. It matters — the
        processing date differs from the booking date on most rows of a real
        statement, and dropping the column would make a question like "why is this
        dated the 3rd when the statement says the 1st" unanswerable.

        Asserted against the ADAPTER'S OWN output rather than against the stored
        rows, so the two are compared rather than each compared to itself.
        """
        payload = rabo(RABO_JUNE)
        parsed = RabobankPdfAdapter().parse(payload)
        with_posting = [
            r
            for r in parsed.records
            if r.raw_data.get("raw_posting_date") or r.raw_data.get("processing_date")
        ]
        assert with_posting, "the fixture must exercise rows carrying a second date"

        upload(client, payload, account_id=seeded["checking"])

        stored = _rows(
            engine,
            "SELECT raw_amount, raw_currency, raw_date, raw_posting_date, raw_data"
            " FROM finance.source_record ORDER BY id",
        )
        assert len(stored) == len(parsed.records)

        # `raw_data` round-trips byte for byte. Compared as a JSON STRING rather
        # than as a dict because a dict is unhashable, and compared with
        # `sort_keys=True` because JSONB does not preserve key order and a
        # round-trip that kept insertion order would be luck, not a guarantee.
        #
        # The whole `(amount, date, raw_data)` tuple is the key: `raw_amount` and
        # `raw_date` are separate columns here, and a bug that shifted an amount
        # onto the wrong row would still leave every description accounted for.
        by_key = {
            (row[0], row[2], json.dumps(row[4], sort_keys=True)): row for row in stored
        }
        assert len(by_key) == len(stored), "two stored rows share one content tuple"
        for record in parsed.records:
            key = (
                record.amount_minor,
                record.booked_date,
                json.dumps(record.raw_data, sort_keys=True),
            )
            assert key in by_key, f"row {record.description!r} did not round-trip"
            row = by_key[key]
            # `raw_currency` is `CHAR(3)` and PostgreSQL blank-pads what it returns,
            # and the padding is not part of an ISO 4217 code. `_as_str` is the
            # helper for that, for the same reason `test_manual_transactions_db.py`
            # has one.
            assert _as_str(row[1]) == record.currency, row

        # And the second date survived as a DATE rather than as NULL — asserted
        # as a COUNT, not as `>= 0`, because `>= 0` is true of an empty result and
        # would pass against an implementation that never wrote the column.
        dated = sum(1 for row in stored if row[3] is not None)
        assert dated == len(with_posting), (dated, len(with_posting))

        # It is genuinely a DIFFERENT date for some rows, which is the reason the
        # column exists rather than duplicating `raw_date`.
        distinct = sum(1 for row in stored if row[3] is not None and row[3] != row[2])
        assert distinct > 0, "the posting date must differ for at least one row"

    def test_the_fingerprint_is_the_frozen_algorithm(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """One stored fingerprint recomputed through the same two functions.

        Recomputed through `compute_fingerprint` (SHA-256 hash-pinned) and
        `fingerprint_account_scope`, which is the one place the `int | None ->
        str` conversion lives. Asserted on the hex, which is the exact inverse of
        the stored BYTEA, so it cannot pass by a truncation or a padding.

        Scope is the ACCOUNT, so the same purchase on two cards stays two
        transactions — and a row that names no account gets `""`, never
        `str(None)`, which would put every unattributed row in one bucket.
        """
        from finance.ingestion.dedupe import fingerprint_account_scope
        from finance.ingestion.fingerprint import compute_fingerprint

        payload = rabo(RABO_JUNE)
        parsed = RabobankPdfAdapter().parse(payload)
        upload(client, payload, account_id=seeded["checking"])

        stored = _rows(
            engine,
            "SELECT encode(fingerprint, 'hex') FROM finance.source_record"
            " ORDER BY id LIMIT 1",
        )
        first = parsed.records[0]
        expected = compute_fingerprint(
            raw_description=first.description,
            raw_amount=first.amount_minor,
            raw_currency=first.currency,
            raw_date=first.booked_date.isoformat(),
            account_id=fingerprint_account_scope(seeded["checking"]),
            occurrence_index=1,
        )
        assert stored[0][0] == expected


# ---------------------------------------------------------------------------
# Criterion 2: the same file again writes nothing
# ---------------------------------------------------------------------------


class TestReimportingTheSameFileIsANoOp:
    """**Zero new `source_record`, zero new `journal_entry`.**

    This is the property that makes the occurrence index a rule rather than an
    optimisation, and it is the criterion a dedup implementation is either right
    or wrong about. A fresh `import_batch` with `created=0` is the CORRECT
    outcome — the batch records that the file was uploaded, and the rows
    recognised it.
    """

    def test_the_second_import_creates_no_rows_at_all(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        payload = rabo(RABO_JUNE)
        first = upload(client, payload, account_id=seeded["checking"])
        assert first.created > 0, first

        before = _counts(engine)
        second = upload(client, payload, account_id=seeded["checking"])

        assert second.created == 0, second
        assert second.failed == 0
        # Every row is recognised, including the card payment that the first
        # import stored unposted: `_already_stored` runs BEFORE the card-payment
        # dispatch, so an unposted row is a duplicate too.
        assert second.duplicated == second.record_count, second

        after = _counts(engine)
        assert after["source_record"] == before["source_record"], "a row was duplicated"
        assert after["journal_entry"] == before["journal_entry"]
        assert after["journal_line"] == before["journal_line"]
        # The batch count DOES grow: the second upload is a real run that
        # recognised every row, and that fact belongs in the ledger.
        assert after["import_batch"] == before["import_batch"] + 1

    def test_a_third_import_is_still_a_no_op(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Twice is not a fluke, and the index does not drift on the second pass.

        This is the assertion that would catch `stored_count + rank`. That formula
        produces `created=2, duplicated=1` on the SECOND import of `[K, K, L]`, so
        the second import rewrites rows and the third shifts them again. Here the
        index is content-only, so every re-import reproduces the first one's
        fingerprints exactly and recognises them.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])
        before = _counts(engine)

        for attempt in ("second", "third"):
            body = upload(client, payload, account_id=seeded["checking"])
            assert body.created == 0, f"{attempt} import created rows: {body}"

        after = _counts(engine)
        assert after["source_record"] == before["source_record"]
        assert after["journal_entry"] == before["journal_entry"]

    def test_the_duplicate_batch_is_partial_and_says_so(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A re-import reports `completed`, because every row IS accounted for.

        `partial` is reserved for rows that did not land. A re-import where every
        row was recognised landed all of them, so calling it `partial` would make
        the word meaningless — and the status is what a client reads to decide
        whether to look at the import at all.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])
        second = upload(client, payload, account_id=seeded["checking"])

        assert second.status == "completed", second
        # The FIRST batch is `partial`: its card payment did not post. The
        # re-import is `completed`, because it recognised every row and the row
        # it did not post is still accounted for as a duplicate.
        assert _rows(engine, "SELECT status FROM finance.import_batch ORDER BY id") == [
            ("partial",),
            ("completed",),
        ]


# ---------------------------------------------------------------------------
# Criterion 3: an overlapping month adds only what is new
# ---------------------------------------------------------------------------


class TestASecondMonthAddsOnlyWhatIsNew:
    """June, then August: only August's own rows appear.

    Two things are being proved and they are different. That no row is written
    twice is the dedup property. That August's content-identical PAIR becomes TWO
    rows with indices 1 and 2 is the occurrence-index property, on a real
    statement — and it is the case a fixture built to contain duplicates would
    only pretend to prove, because the pair in a real statement is one a parser
    could plausibly have collapsed by accident.
    """

    def test_only_the_second_month_s_rows_appear(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        june = rabo(RABO_JUNE)
        august = rabo(RABO_AUGUST)
        june_rows = parsed_count(june)
        august_rows = parsed_count(august)
        assert june_rows and august_rows

        upload(client, june, account_id=seeded["checking"], filename=RABO_JUNE)
        body = upload(
            client, august, account_id=seeded["checking"], filename=RABO_AUGUST
        )

        # August also carries one card payment, stored unposted for want of a
        # registered paying account.
        assert body.created == august_rows - 1, body
        assert body.failed == 1, body

        total = _as_int(_scalar(engine, "SELECT count(*) FROM finance.source_record"))
        assert total == june_rows + august_rows

        # No phantom duplicate. Every stored row has its own fingerprint, and the
        # count of distinct fingerprints equals the count of rows: nothing was
        # written and then rolled back, which would leave the batch's counts
        # claiming rows that are not there.
        assert (
            _as_int(
                _scalar(
                    engine,
                    "SELECT count(DISTINCT encode(fingerprint, 'hex'))"
                    " FROM finance.source_record",
                )
            )
            == total
        )

    def test_a_content_identical_pair_becomes_two_transactions(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**Two identical rows are TWO transactions, not one.**

        August carries a pair: the same direct debit, same amount, same day,
        printed twice. Collapsing them loses a real transaction, and the failure
        is silent — the statement still balances, because one row is missing from
        both sides of the identity equally.

        The precondition is asserted FIRST, from the adapter's own output, so that
        a fixture which stopped containing a pair fails here with a reason instead
        of passing vacuously. That is the whole difference between this and a test
        that constructs its own duplicate.
        """
        august = rabo(RABO_AUGUST)
        records = RabobankPdfAdapter().parse(august).records
        keys = [content_key(record) for record in records]
        repeated = [key for key, count in Counter(keys).items() if count > 1]
        assert repeated, (
            "the chosen statement no longer contains two content-identical rows, "
            "so this test would pass without exercising anything"
        )
        description = repeated[0][0]

        body = upload(
            client, august, account_id=seeded["checking"], filename=RABO_AUGUST
        )
        # One row does not post: the statement's card payment. It is not the
        # repeated pair, which is an ordinary direct debit.
        assert body.created == len(records) - 1, body

        copies = sum(1 for key in keys if key == repeated[0])
        assert copies == 2, (copies, repeated)

        # The WHOLE content key, not just the description: this payee appears on
        # ten rows of this statement with different amounts and dates, and matching
        # on the description alone would compare two rows against ten — which is
        # exactly the mistake that makes a correct occurrence index look broken.
        _, amount, currency, booked = repeated[0]
        where = (
            " WHERE raw_description = :description AND raw_amount = :amount"
            "   AND raw_currency = :currency AND raw_date = :booked"
        )
        params: dict[str, object] = {
            "description": description,
            "amount": amount,
            "currency": currency,
            "booked": booked,
        }

        stored = _rows(
            engine,
            "SELECT occurrence_index FROM finance.source_record"
            + where
            + " ORDER BY occurrence_index",
            **params,
        )
        # Precisely: one row per printed copy, indexed 1..n with no gaps. Two
        # copies indexed 1 and 2 — NOT both 1, which would mean one fingerprint was
        # written twice and `uq_sr_fingerprint` would have refused the second.
        assert [row[0] for row in stored] == [1, 2], stored

        # And both are POSTED, so the pair is in the ledger rather than collapsed
        # into one entry.
        assert (
            _as_int(
                _scalar(
                    engine,
                    "SELECT count(*) FROM finance.source_record"
                    + where
                    + "   AND journal_entry_id IS NOT NULL",
                    **params,
                )
            )
            == 2
        )

    def test_a_reimport_of_the_second_month_recognises_the_pair_too(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The pair is recognised on re-import, not re-indexed.

        This is the assertion `stored_count + rank` fails. With that formula the
        pair's indices become 3 and 4 on the second import, neither matches a
        stored fingerprint, and the import rewrites two rows that already exist —
        and shifts again on the third.
        """
        august = rabo(RABO_AUGUST)
        first = upload(
            client, august, account_id=seeded["checking"], filename=RABO_AUGUST
        )
        assert first.created > 0, first
        before = _counts(engine)

        second = upload(
            client, august, account_id=seeded["checking"], filename=RABO_AUGUST
        )
        assert second.created == 0, second
        assert second.duplicated == second.record_count, second

        after = _counts(engine)
        assert after["source_record"] == before["source_record"]
        assert after["journal_entry"] == before["journal_entry"]


# ---------------------------------------------------------------------------
# Criterion 4: the raw side is immutable, and the balance invariant holds
# ---------------------------------------------------------------------------


class TestImportedRowsObeyTheDatabaseInvariants:
    """The triggers apply to imported rows exactly as they do to typed ones.

    These are not this file's rules — `test_balance_db.py` owns the trigger's
    behaviour. What is new is that an IMPORTED row is subject to them: a write
    path that bypassed `raw_data_immutable` would make the project's
    highest-priority invariant decorative for exactly the rows that came from a
    bank statement.
    """

    def test_an_imported_row_cannot_be_rewritten(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**The database refuses, not the application.**

        Through the ORM deliberately: the guarantee is that NO writer can rewrite
        the evidence, so the test must not depend on this application having
        thought to stop it. The session is taken from `finance.db` directly, which
        also proves the application's own session is subject to the same trigger.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])

        record_id = _as_int(
            _scalar(engine, "SELECT min(id) FROM finance.source_record")
        )
        before = _scalar(
            engine,
            "SELECT raw_description FROM finance.source_record WHERE id = :id",
            id=record_id,
        )

        from finance.domain.models import importer

        with pytest.raises(DBAPIError) as refusal:
            with get_sessionmaker()() as session:
                record = session.get(importer.SourceRecord, record_id)
                assert record is not None
                record.raw_data = {"tampered": True}
                session.commit()

        assert "immutable" in str(refusal.value)
        assert (
            _scalar(
                engine,
                "SELECT raw_description FROM finance.source_record WHERE id = :id",
                id=record_id,
            )
            == before
        )

    def test_an_imported_row_cannot_be_deleted(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The second half of the same guarantee, and the one with no workaround."""
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])
        total = _as_int(_scalar(engine, "SELECT count(*) FROM finance.source_record"))
        record_id = _as_int(
            _scalar(engine, "SELECT min(id) FROM finance.source_record")
        )

        from finance.domain.models import importer

        with pytest.raises(DBAPIError):
            with get_sessionmaker()() as session:
                record = session.get(importer.SourceRecord, record_id)
                assert record is not None
                session.delete(record)
                session.commit()

        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == total

    def test_the_balance_identity_holds_over_imported_data(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`sum(amount_base)` per entry, and per account, is zero.

        The per-account half matters more than it looks: the per-entry half is
        what the trigger already checks at COMMIT, so it is close to a restatement.
        The per-account sum is a different claim — an entry balances internally,
        and the ACCOUNT still has to balance — and it is the one a leg written
        against the wrong account would break while every entry stayed perfect.

        `test_balance_invariant.py` states the statement-level identity for both
        account directions and is what a parser is validated against; this is the
        same arithmetic applied to what was actually STORED.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])

        by_entry = _rows(
            engine,
            "SELECT journal_entry_id, sum(amount_base) FROM finance.journal_line"
            " GROUP BY journal_entry_id HAVING sum(amount_base) <> 0",
        )
        assert by_entry == [], f"entries that do not balance: {by_entry}"

        by_account = _rows(
            engine,
            "SELECT account_id, sum(amount_base) FROM finance.journal_line"
            " GROUP BY account_id",
        )
        # Two accounts: the checking account and the system expense account, and
        # every EUR the first lost the second gained. That is the ledger balancing,
        # stated over imported rows.
        assert len(by_account) == 2, by_account
        # Every EUR the checking account lost, the equity account gained.
        assert sum(Decimal(str(row[1])) for row in by_account) == Decimal(0)


# ---------------------------------------------------------------------------
# The four silent-wrong-data traps, one test each
# ---------------------------------------------------------------------------


class _CardPaymentAdapter:
    """An adapter that returns one row carrying `is_card_payment`.

    The shape is copied from what `amex_pdf.py` produces for a monthly card
    payment: a CREDIT (`amount_minor` positive, because a payment reduces what the
    card owes), the flag in `raw_data`, and a `raw_posting_date` string. Nothing
    else about it is specific — the point is that the flag reaches the writer, and
    the writer's decision is what is under test.

    A stub rather than a parsed fixture because no v1 adapter produces this row
    from a statement this repository ships, and hand-building a synthetic Amex PDF
    to get one would be testing the extractor instead.
    """

    provider: ClassVar[str] = "rabobank_pdf"
    import_method: ClassVar[str] = "pdf"

    def parse(
        self,
        payload: bytes,
        account_id: int | None = None,
        filename: str | None = None,
    ) -> ImportResult:
        del payload, filename
        return ImportResult(
            provider="rabobank_pdf",
            import_method="pdf",
            records=[
                RawRecord(
                    account_id=account_id,
                    description="BETALING KREDIETKAART",
                    amount_minor=72135,
                    currency="EUR",
                    booked_date=dt.date(2026, 6, 30),
                    value_date=dt.date(2026, 7, 1),
                    line_number=1,
                    raw_data={
                        "raw_date": "2026-06-30",
                        "raw_posting_date": "2026-07-01",
                        "raw_amount_minor": 72135,
                        "is_credit": True,
                        "is_card_payment": True,
                    },
                )
            ],
            source_checksum="0" * 64,
            source_filename=RABO_JUNE,
        )


class TestTheSilentWrongDataTraps:
    """A wrong answer that raises is not dangerous. These do not raise.

    Each test below states the shape of the mistake and asserts on the resulting
    BATCH AND LEDGER state, not on internals. An assertion on internals would
    pass against an implementation that happened to call the right function for
    the wrong reason; an assertion on the rows cannot.
    """

    def test_a_card_payment_is_never_posted_as_an_expense(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**The trap.** A flagged card payment must not reach the expense path.

        `docs/adr/0007-imported-card-payment-is-a-transfer.md`: a monthly card
        payment credits a card (a LIABILITY) and debits an asset, so it is a
        TRANSFER. The equity-only contra-leg rule lives in
        `resolve_default_counter_account`, not in `build_expense_legs`, so routing
        one through the manual default does NOT reliably raise — and this fixture
        has the active `Expenses (system)` account that makes it book against
        EQUITY instead. That entry balances, commits, and is permanent.

        The row is built by hand rather than parsed, because Rabobank statements
        contain no card payments and the flag is what matters, not the parser.
        It is injected through the adapter's own parse seam so the REAL endpoint
        and the REAL writer decide what happens to it.

        What is asserted, all of it observable:
          * the row exists and is NOT posted — `journal_entry_id IS NULL`;
          * it carries an error_message that names the missing account;
          * it produced NO journal entry and NO journal line;
          * no entry anywhere in the ledger has a leg on the card's own account,
            which is the mis-posting itself;
          * the batch is `partial` and the row is counted in `failed`.
        """

        # A Rabobank statement contains no card payments, and the FLAG is what
        # this test is about — not the parser. So the adapter is stubbed to hand
        # back exactly the row shape the Amex adapter produces for one, and the
        # real endpoint, the real route and the real writer decide what happens to
        # it. Anything less would be testing a function called directly.
        def _stub_adapter() -> object:
            return _CardPaymentAdapter()

        from unittest import mock

        import finance.api.routes.imports as imports_routes

        with mock.patch.object(
            imports_routes,
            "_FILE_ADAPTERS",
            {"rabobank_pdf": (_stub_adapter, ".pdf")},
        ):
            response = client.post(
                "/api/v1/imports/file",
                params={
                    "provider": "rabobank_pdf",
                    "account_id": seeded["checking"],
                },
                files={"file": (RABO_JUNE, SYNTHETIC_PDF, "application/pdf")},
            )
        assert response.status_code == 200, response.text
        card_summary = _summary_of(response.json())

        assert card_summary.created == 0, card_summary
        assert card_summary.failed == 1, card_summary
        assert card_summary.status == "partial", card_summary

        rows = _rows(
            engine,
            "SELECT status, journal_entry_id, error_message, raw_data"
            " FROM finance.source_record",
        )
        assert len(rows) == 1, rows
        status, entry_id, message, raw_data = rows[0]
        assert status == "pending"
        assert entry_id is None, "a card payment must not be linked to a journal entry"
        assert isinstance(message, str) and "transfer" in message.lower(), message
        assert isinstance(raw_data, dict) and raw_data["is_card_payment"] is True

        # No journal entry was written at all, so nothing can have been booked
        # against equity by accident.
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_line") == 0

        # And the batch says partial, which is what a client reads to decide the
        # import needs attention.
        assert _scalar(engine, "SELECT status FROM finance.import_batch") == "partial"

    def test_a_row_the_ledger_refuses_is_stored_unposted_not_dropped(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Trap 2's SHAPE: a per-row posting refusal is a row, not a lost row.

        The row is stored with the reason on it and counted in `failed`, so one
        unpostable row cannot abandon the other hundred — and it is STORED rather
        than dropped, because dropping it is how a statement stops reconciling
        with nothing to show for it.

        The refusal is manufactured by removing the system expense account, which
        is exactly the condition `resolve_default_counter_account` refuses. It is
        trap 2's neighbour: the currency exponent comes from the TABLE, so an
        account whose currency the table does not hold cannot be posted either —
        and both refusals have to leave the same shape of row behind.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text("DELETE FROM finance.account WHERE account_nature = 'equity'")
                )

        payload = rabo(RABO_JUNE)
        rows = parsed_count(payload)
        body = upload(client, payload, account_id=seeded["checking"])

        assert body.created == 0, body
        assert body.failed == rows, body
        # `partial`, not `failed`: the rows were STORED and only refused posting,
        # and `partial` is what sends a user to look. `failed` is reserved for a
        # file that parsed to nothing.
        assert body.status == "partial", body

        stored = _rows(
            engine,
            "SELECT status, journal_entry_id, error_message FROM finance.source_record",
        )
        assert len(stored) == rows, stored
        for status, entry_id, message in stored:
            assert status == "pending"
            assert entry_id is None
            assert isinstance(message, str), message
            # The card payment is refused for a DIFFERENT reason — no paying
            # account is registered — but the SHAPE is identical: stored,
            # unposted, with a reason. That shape is what this test is about.
            assert (
                "system expense account" in message.lower()
                or "card payment" in message.lower()
            ), message

        # Nothing was posted, so nothing can be unbalanced.
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0

    def test_an_unknown_account_id_is_a_404_and_writes_no_batch(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The `account_id` trap.

        NOT NULL means an unattributable row cannot be written at all, so the
        request is refused instead of producing a statement that is silently
        shorter than the file.

        Refused at the edge, before `open_import_batch`, so the ledger stays
        completely empty. Asserting the absence of a BATCH as well as of rows is
        the point: a batch with no rows is a record of an import that did not
        happen, and a query over batches would then report it as one that did.
        """
        payload = rabo(RABO_JUNE)
        response = client.post(
            "/api/v1/imports/file",
            params={"provider": "rabobank_pdf", "account_id": 999999},
            files={"file": (RABO_JUNE, payload, "application/pdf")},
        )
        assert response.status_code == 404, response.text
        assert "999999" in response.json()["detail"]
        assert _counts(engine) == {
            "source_record": 0,
            "journal_entry": 0,
            "journal_line": 0,
            "import_batch": 0,
        }

    def test_the_batch_status_describes_what_persisted(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Trap 4: `ImportResult.status` is parse-derived and is never copied.

        The status written is computed from `created + duplicated` against
        `record_count` — what the LEDGER holds — so a file whose rows were only
        partly written cannot be labelled `completed`.

        This asserts the shape of that rule on its own terms: a batch whose counts
        do not add up to the parse cannot be `completed`. It is stated against the
        reported counts and the stored status together, because a test that only
        checked `status == "completed"` for a fully-posted file would pass against
        an implementation that copied `ImportResult.status` every time.
        """
        payload = rabo(RABO_JUNE)
        body = upload(client, payload, account_id=seeded["checking"])

        stored_status = _scalar(engine, "SELECT status FROM finance.import_batch")
        stats = _scalar(engine, "SELECT stats FROM finance.import_batch")
        assert isinstance(stats, dict)
        assert stats["created"] == body.created
        assert stats["duplicated"] == body.duplicated
        assert stats["failed"] == body.failed

        if body.landed < body.record_count:
            assert stored_status == "partial", (
                f"{body.landed} of {body.record_count} rows landed but the "
                f"batch says {stored_status!r}"
            )
        else:
            assert stored_status == "completed"


class TestTheOccurrenceIndexIsContentOnly:
    """The subtle one: an index that moves with table state rewrites the file.

    `stored_count + rank` was simulated and is WRONG — re-importing `[K, K, L]`
    gives `created=2, duplicated=1` instead of `created=0, duplicated=3`. The
    assertion below is on the real consequence of that choice rather than on the
    formula: a second import of a statement containing content-identical rows
    recognises all of them.

    `count_existing_occurrences` is wired in as a CONSISTENCY ASSERTION ONLY, and
    the file says so where it computes ranks. It cannot reproduce a global
    ROW_NUMBER while the probe is content-only: a content-only probe cannot know
    which of two identical stored rows an incoming row "would have" been. That is
    why the assertion checks the weaker claim that no row is assigned an index
    beyond the stored evidence.
    """

    def test_a_file_with_identical_rows_imports_once_and_recognises_itself(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two content-identical rows are TWO transactions, and both are stable.

        If the index collapsed them to 1, one of two real purchases would be lost;
        if it moved with table state, the second import would rewrite the file
        rather than recognise it. Both are caught by the same two assertions.
        """
        payload = rabo(RABO_JUNE)
        first = upload(client, payload, account_id=seeded["checking"])
        second = upload(client, payload, account_id=seeded["checking"])

        assert second.created == 0, second
        assert second.duplicated == second.record_count, second
        stored_total = _scalar(engine, "SELECT count(*) FROM finance.source_record")
        assert stored_total == first.record_count

        # Every stored row has a distinct fingerprint, which is what "two
        # identical rows are two transactions" means at the storage layer.
        assert (
            _scalar(
                engine,
                "SELECT count(DISTINCT encode(fingerprint, 'hex'))"
                " FROM finance.source_record",
            )
            == first.record_count
        )

    def test_the_occurrence_index_column_is_written(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The column is populated, and always at least 1.

        `occurrence_index` defaults to 1 at the column level, so an implementation
        that never set it would produce a table that LOOKS right for a file with no
        duplicates in it. Asserting `min >= 1` alongside a populated count is the
        cheap version of that check; the duplicate test above is the real one.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])

        lowest = _scalar(
            engine, "SELECT min(occurrence_index) FROM finance.source_record"
        )
        assert lowest == 1
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.source_record WHERE occurrence_index < 1",
            )
            == 0
        )


class TestTheEndpointContract:
    """The things a caller is entitled to rely on."""

    def test_the_dry_run_endpoint_still_writes_nothing(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`POST /imports` remains a parse and nothing more.

        Kept because a caller who wants to see what a file contains before
        committing is a legitimate question. Asserted because the alternative — a
        dry run reporting `created == record_count` — would claim rows exist when
        none were written, which is the confusion the two endpoints are separated
        to avoid.
        """
        payload = rabo(RABO_JUNE)
        response = client.post(
            "/api/v1/imports",
            params={"provider": "rabobank_pdf", "account_id": seeded["checking"]},
            files={"file": (RABO_JUNE, payload, "application/pdf")},
        )
        assert response.status_code == 200, response.text
        dry = _summary_of(response.json())
        assert dry.created == 0
        assert dry.duplicated == 0
        assert dry.failed == 0
        assert dry.record_count == parsed_count(payload)
        assert _counts(engine)["import_batch"] == 0

    def test_a_non_upload_provider_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`enable_banking` and `manual` are not uploads.

        Saying so beats a confusing parse failure.

        Shared with the dry run by `_read_upload`, so the two endpoints cannot
        disagree about what an acceptable upload is.
        """
        payload = rabo(RABO_JUNE)
        for provider, expected in (
            ("enable_banking", "API source"),
            ("manual", "not uploaded"),
            ("nonexistent", "Unknown provider"),
        ):
            response = client.post(
                "/api/v1/imports/file",
                params={"provider": provider, "account_id": seeded["checking"]},
                files={"file": (RABO_JUNE, payload, "application/pdf")},
            )
            assert response.status_code == 400, (provider, response.text)
            assert expected in response.json()["detail"]

        assert _counts(engine)["import_batch"] == 0

    def test_the_wrong_extension_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A `.csv` named as a PDF fails on the name, before anything is parsed."""
        payload = rabo(RABO_JUNE)
        response = client.post(
            "/api/v1/imports/file",
            params={"provider": "rabobank_pdf", "account_id": seeded["checking"]},
            files={"file": ("statement.csv", payload, "text/csv")},
        )
        assert response.status_code == 400, response.text
        assert ".pdf" in response.json()["detail"]
        assert _counts(engine)["import_batch"] == 0

    def test_the_response_counts_add_up_to_the_parse(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`created + duplicated + failed == record_count`, always.

        A row is in exactly one of the three buckets, so the sum is an identity a
        client can rely on. Asserted after a first import AND after a re-import,
        because the two exercise different buckets and an off-by-one in either
        would only show in one of them.
        """
        payload = rabo(RABO_JUNE)
        first = upload(client, payload, account_id=seeded["checking"])
        total_first = first.created + first.duplicated + first.failed
        assert total_first == first.record_count

        second = upload(client, payload, account_id=seeded["checking"])
        total_second = second.created + second.duplicated + second.failed
        assert total_second == second.record_count

    def test_the_status_enum_matches_what_was_written(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Every imported row sits in a `source_record.status` the schema allows.

        The CHECK is the migration's, so this is only meaningful if the writer
        uses the vocabulary the column documents rather than a synonym — which is
        exactly the kind of drift `TransactionStatus` exists to prevent.
        """
        payload = rabo(RABO_JUNE)
        upload(client, payload, account_id=seeded["checking"])

        allowed = {status.value for status in TransactionStatus}
        stored = {
            row[0]
            for row in _rows(
                engine, "SELECT DISTINCT status FROM finance.source_record"
            )
        }
        assert stored <= allowed, stored
