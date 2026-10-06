"""test_manual_transactions_db.py — manual transaction CRUD, end to end.

The acceptance test for the last criterion of M1: "Manual expense CRUD works end
to end." These tests drive the real FastAPI app against a real, migrated
Postgres — `POST /transactions` writes an `import_batch`, a `source_record` and
a two-leg `journal_entry`, and every assertion is read back from a SECOND
connection so an uncommitted transaction cannot satisfy it.

**Marked `db`, deliberately.** The whole point of the marker is that `make test`
stays runnable with no Docker at all, and these tests cannot run without a
database. They are selected by `make test-db` and deselected by `make test`.

**The `TestClient` is not mocked and `raise_server_exceptions` is left ON.** A
handler that raised would fail the test with the exception rather than returning
a 500 — which is exactly what the "not a 500" assertions need: they would pass
against a handler that returned a typed 500, and they cannot pass against one
that crashed.

**The rules under test are not this file's.** The balance trigger's behaviour at
COMMIT is `test_balance_db.py`'s subject and is not duplicated here; what is new
in M1 is that the API produces entries the trigger accepts, refuses to produce
entries it would not, and surfaces a refusal as a status code rather than a
crash.

`SOURCE_RECORD` and friends are held in constants for the reason stated in
`test_balance_db.py`: the `raw_data_immutable` rule forbids UPDATE and DELETE on
the raw columns, so a test asserting the database REFUSES to mutate raw evidence
must not itself contain the statement it is testing for. Everything here
that touches the raw side goes through the ORM, which emits no such SQL text.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

import pytest
from core.money import Currency as CoreCurrency
from core.money import Money
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from finance.api.routes import transactions as transactions_routes
from finance.api.schemas import ManualTransactionRequest
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import (
    SYSTEM_EXPENSE_ACCOUNT_NAME,
    PostingAccount,
    PostingLeg,
    build_expense_legs,
)
from finance.ingestion.dedupe import fingerprint_account_scope
from finance.ingestion.fingerprint import compute_fingerprint

pytestmark = pytest.mark.db

#: One fixed booked date for every test. Varying it per test would make
#: "same account, amount, date and description" impossible to state, and the
#: duplicate-detection test depends on that phrasing being exact.
BOOKED: Final[str] = "2026-10-01"

#: EUR 1 buys 160 JPY. Also the rate the JPY tests need, and chosen so
#: 1500 / 160 = 9.375 lands on a value with three decimals — an `amount_base`
#: that is NOT a whole number of cents, which is what proves the EUR leg's
#: integer minor units and its 4-decimal base are independent columns.
JPY_RATE: Final[str] = "160"

#: SQLSTATE 23514, `check_violation`. Every invariant trigger in migration 0001
#: raises with it.
CHECK_VIOLATION: Final[str] = "23514"

#: The EUR checking account a manual expense is paid from.
CHECKING_NAME: Final[str] = "Test current account"


# ---------------------------------------------------------------------------
# Payload helper
# ---------------------------------------------------------------------------


def _payload(
    account_id: int,
    *,
    amount: str = "-40.50",
    description: str = "JUMBO 4321 AMSTERDAM",
    currency: str = "EUR",
    booked_date: str = BOOKED,
    **extra: object,
) -> dict[str, object]:
    """A `POST /transactions` body.

    `amount` is a STRING and it is SIGNED, which is the contract: `"40.50"` is
    money in and `"-40.50"` is money out. Written as a string in the helper so
    no test can accidentally prove the float path works.
    """
    body: dict[str, object] = {
        "account_id": account_id,
        "description": description,
        "amount": amount,
        "currency": currency,
        "booked_date": booked_date,
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`.

    Function-scoped, and the three `cache_clear()` calls are the point. `finance
    .db.get_engine` and `get_sessionmaker` are `lru_cache`d, and `config
    .get_settings` is too, so without clearing them the application would build
    its engine against whatever `LIFEOS_DATABASE_URL` was at FIRST USE — the
    development database, most likely — and this file would be testing a database
    nobody truncated. Requesting `test_database_url` guarantees the conftest has
    already pointed the environment at the throwaway database; the clears make
    the cached engine pick that up.

    Cleared again on teardown for the same reason in reverse: a leaked engine
    keeps a connection to `lifeos_test` alive for the rest of the session, and
    the next `TRUNCATE` would wait on it.

    `expire_on_commit=False` (set in `finance/db.py`) means objects read after a
    commit are still populated, so a read-back after the transaction block is a
    query rather than a lazy load on a closed session.
    """
    del test_database_url  # Requested for its side effect: setting the env var.
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
    """Currencies, accounts, a category and an FX rate, all committed.

    One fixture rather than several because the counter-leg resolution reads
    EVERY account in the table: seeding accounts in a different order would
    change which rows exist, and `resolve_default_counter_account` raises when
    two rows share the name. A per-test fixture graph is the wrong shape for a
    rule that looks at the whole table.

    Committed, not left open: these rows are read by a separate connection (the
    application's), and an open transaction on this one would not be visible to
    it.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals) VALUES"
                    " ('EUR', 'Euro', 2), ('JPY', 'Japanese Yen', 0),"
                    " ('USD', 'US Dollar', 2)"
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
            jpy_account = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.account"
                        " (name, account_type, account_nature, currency)"
                        " VALUES ('Yen wallet', 'checking', 'asset', 'JPY')"
                        " RETURNING id"
                    )
                ).scalar_one()
            )
            counter = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.account"
                        " (name, account_type, account_nature, currency, system_role)"
                        " VALUES (:name, 'cash', 'equity', 'EUR', 'system_expense')"
                        " RETURNING id"
                    ),
                    {"name": SYSTEM_EXPENSE_ACCOUNT_NAME},
                ).scalar_one()
            )
            category = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, is_system)"
                        " VALUES ('Groceries', 'expense', TRUE) RETURNING id"
                    )
                ).scalar_one()
            )
            connection.execute(
                text(
                    "INSERT INTO finance.exchange_rate"
                    " (date, base_currency, quote_currency, rate)"
                    " VALUES (:date, 'EUR', 'JPY', :rate)"
                ),
                {"date": BOOKED, "rate": JPY_RATE},
            )
    return {
        "checking": checking,
        "jpy_account": jpy_account,
        "counter": counter,
        "category": category,
    }


# ---------------------------------------------------------------------------
# Database reads, always from a second connection
# ---------------------------------------------------------------------------


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


def _scalar(engine: Engine, statement: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(statement), params).scalar_one()


def _as_int(value: object) -> int:
    """An int, or a loud failure.

    `assert isinstance` rather than `int(value)`: casting an `object` to `int`
    is exactly how a test ends up asserting on a value the driver returned in a
    shape nobody expected. The check turns that into a named failure here.
    """
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _as_str(value: object) -> str:
    """A string, with `CHAR` padding removed.

    `CHAR(3)` is blank-padded by PostgreSQL and the padding is not part of an
    ISO 4217 code, so every currency read goes through here.
    """
    assert isinstance(value, str), f"expected a text column, got {value!r}"
    return value.strip()


def _as_date(value: object) -> dt.date:
    assert isinstance(value, dt.date), f"expected a date column, got {value!r}"
    return value


def _as_json(value: object) -> dict[str, object]:
    assert isinstance(value, dict), f"expected a JSON object, got {value!r}"
    return value


@dataclass(frozen=True)
class Leg:
    """One stored `journal_line`, read back from the database.

    A named shape rather than a tuple so an assertion can say `funding.amount`
    instead of `funding[1]`: a positional index silently means the wrong column
    if the SELECT above it is ever reordered, and the test would keep passing.
    """

    account_id: int
    amount: int
    currency: str
    amount_base: Decimal


def _entry_legs(engine: Engine, entry_id: int) -> list[Leg]:
    """The legs of one entry, in `sort_order`."""
    return [
        Leg(
            account_id=_as_int(row[0]),
            amount=_as_int(row[1]),
            currency=_as_str(row[2]),
            amount_base=Decimal(str(row[3])),
        )
        for row in _rows(
            engine,
            "SELECT account_id, amount, currency, amount_base"
            " FROM finance.journal_line WHERE journal_entry_id = :id"
            " ORDER BY sort_order",
            id=entry_id,
        )
    ]


def _legs_by_account(engine: Engine, entry_id: int) -> dict[int, Leg]:
    return {leg.account_id: leg for leg in _entry_legs(engine, entry_id)}


def _base_sum(engine: Engine, entry_id: int) -> Decimal:
    return Decimal(
        str(
            _scalar(
                engine,
                "SELECT COALESCE(sum(amount_base), 0) FROM finance.journal_line"
                " WHERE journal_entry_id = :id",
                id=entry_id,
            )
        )
    )


@dataclass(frozen=True)
class StoredRecord:
    """One stored `source_record`, read back from the database.

    `raw_data` is a `dict[str, object]` because JSONB is arbitrary JSON; the
    tests that assert on it index it by key and compare values, never assume a
    type beyond "whatever the user sent".
    """

    raw_description: str
    raw_amount: int
    raw_currency: str
    raw_date: dt.date
    status: str
    journal_entry_id: int | None
    raw_data: dict[str, object]


def _stored_record(engine: Engine, record_id: int) -> StoredRecord:
    rows = _rows(
        engine,
        "SELECT raw_description, raw_amount, raw_currency, raw_date, status,"
        " journal_entry_id, raw_data FROM finance.source_record WHERE id = :id",
        id=record_id,
    )
    assert len(rows) == 1, f"expected exactly one source_record {record_id}"
    entry_id = rows[0][5]
    return StoredRecord(
        raw_description=_as_str(rows[0][0]),
        raw_amount=_as_int(rows[0][1]),
        raw_currency=_as_str(rows[0][2]),
        raw_date=_as_date(rows[0][3]),
        status=_as_str(rows[0][4]),
        journal_entry_id=None if entry_id is None else _as_int(entry_id),
        raw_data=_as_json(rows[0][6]),
    )


# ---------------------------------------------------------------------------
# The acceptance case
# ---------------------------------------------------------------------------


class TestAManualExpenseIsPostedToTheLedger:
    """Create → one source_record, one balanced two-leg entry, both readable back.

    The M1 acceptance criterion, and the assertion that distinguishes it from the
    M0 stub it replaces: the rows are in a real table, in a real transaction, and
    read back from a connection that did not write them.
    """

    def test_a_manual_expense_creates_a_source_record_and_a_two_leg_entry(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        body = response.json()

        # -- the raw side ------------------------------------------------
        record = _stored_record(engine, int(body["id"]))
        assert record.status == "posted"
        assert record.journal_entry_id is not None, "the record was not linked"
        assert body["journal_entry_id"] == record.journal_entry_id
        assert body["account_id"] == seeded["checking"]

        # -- the ledger --------------------------------------------------
        entry_id = int(record.journal_entry_id)
        legs = _entry_legs(engine, entry_id)
        assert len(legs) == 2, f"an entry needs two legs, got {len(legs)}"

        by_account = _legs_by_account(engine, entry_id)
        assert set(by_account) == {seeded["checking"], seeded["counter"]}

        funding = by_account[seeded["checking"]]
        assert funding.amount == -4050
        assert funding.currency == "EUR"
        assert funding.amount_base == Decimal("-40.5000")

        contra = by_account[seeded["counter"]]
        assert contra.amount == 4050
        assert contra.amount_base == Decimal("40.5000")

    def test_the_legs_sum_to_exactly_zero_not_merely_within_tolerance(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**`sum(amount_base) == Decimal("0.0000")`.**

        The trigger tolerates `abs(sum) <= 0.005`, so a test asserting
        `abs(sum) < 0.005` would pass against an entry that is out by half a
        cent — a ledger that balances only approximately, with the drift
        inherited by every downstream sum. The stored data must be exact, so this
        asserts equality against zero and nothing else.

        Stated in one assertion precisely so that relaxing it would have to be a
        deliberate edit to a single visible line.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        entry_id = int(response.json()["journal_entry_id"])

        assert _base_sum(engine, entry_id) == Decimal("0.0000")

    def test_the_entry_is_readable_back_through_the_api(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        created = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        ).json()
        fetched = client.get(f"/api/v1/transactions/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == created

    def test_the_batch_is_a_manual_batch(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A manual transaction travels the same pipeline as a file import.

        `manual`/`manual` are both in the provider CHECK list, so this is not a
        new value — it is the one the schema already reserved for this. Asserted
        because a `provider` of `'api'` would make a manual entry invisible to
        every query that filters by provider.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        rows = _rows(
            engine,
            "SELECT provider, import_method, status FROM finance.import_batch",
        )
        assert rows == [("manual", "manual", "completed")]

    def test_the_fingerprint_is_the_frozen_algorithm_scoped_to_the_account(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The stored BYTEA is the hash-pinned digest, byte for byte.

        Recomputed here through the same two functions the route uses, with the
        account scope rendered by `fingerprint_account_scope` — never `str(None)`,
        which would put every unattributed row in one bucket and let two unrelated
        purchases collide. The assertion is on the hex, which is the exact inverse
        of `bytes.fromhex`, so it cannot pass by a truncation or a padding.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text

        expected = compute_fingerprint(
            raw_description="JUMBO 4321 AMSTERDAM",
            raw_amount=-4050,
            raw_currency="EUR",
            raw_date=BOOKED,
            account_id=fingerprint_account_scope(seeded["checking"]),
            occurrence_index=1,
        )
        stored = response.json()["fingerprint"]
        assert stored == expected
        assert (
            _scalar(
                engine,
                "SELECT encode(fingerprint, 'hex') FROM finance.source_record"
                " WHERE id = :id",
                id=int(response.json()["id"]),
            )
            == expected
        )

    def test_two_identical_manual_entries_are_refused_as_duplicates(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """The user who types the same transaction twice gets a conflict.

        `uq_sr_fingerprint` is unique on the digest alone, which is what makes a
        manual entry travel the same dedupe path as an imported one. A 409 rather
        than a silent second row: the manual adapter's own docstring says the same
        thing — "a review item rather than a silent merge", and a review item
        starts with the caller being told.
        """
        first = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        assert first.status_code == 201, first.text
        second = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        assert second.status_code == 409
        assert "already recorded" in second.json()["detail"]

    def test_a_credit_posts_the_other_way_round(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`"40.50"` is money IN: the asset leg is positive.

        The mirror of the expense case, and the one that catches a sign flip
        applied unconditionally. A flip that always negates would book every
        refund as an expense, which is the kind of error a balance check cannot
        see: the entry still balances.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], amount="40.50", description="SALARY"),
        )
        assert response.status_code == 201, response.text
        entry_id = int(response.json()["journal_entry_id"])
        legs = _legs_by_account(engine, entry_id)
        assert legs[seeded["checking"]].amount == 4050
        assert legs[seeded["counter"]].amount == -4050
        assert _base_sum(engine, entry_id) == Decimal("0.0000")


# ---------------------------------------------------------------------------
# Money is minor units, and the currency decides how many
# ---------------------------------------------------------------------------


class TestTheCurrencyOwnsTheExponent:
    """`currency.decimals` drives the conversion. There is no 2 anywhere below it.

    The M0 stub did `int(payload.amount_decimal.scaleb(2))`, which is exactly the
    bug `currency.decimals` exists to prevent: it is right for EUR by accident
    and wrong by a factor of 100 for JPY.
    """

    def test_a_zero_decimal_currency_produces_the_right_minor_units(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """JPY 1500 is 1500 minor units, not 150,000.

        JPY is `decimals = 0`, so `Money.from_decimal(1500, JPY)` is 1500. The
        old `scaleb(2)` would have written 150,000 — a 100x error that still
        balances, because the counter-leg is derived from the same wrong number.
        That is why this assertion is on the stored integer and not on the sum.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(
                seeded["jpy_account"],
                amount="-1500",
                currency="JPY",
                description="RAMEN TOKYO",
            ),
        )
        assert response.status_code == 201, response.text

        record = _stored_record(engine, int(response.json()["id"]))
        assert record.raw_amount == -1500
        assert record.journal_entry_id is not None

        entry_id = int(record.journal_entry_id)
        by_account = _legs_by_account(engine, entry_id)
        funding = by_account[seeded["jpy_account"]]
        counter = by_account[seeded["counter"]]

        assert funding.amount == -1500
        assert funding.currency == "JPY"
        # 1500 JPY / 160 = 9.375 EUR exactly, which is three decimals and
        # therefore NOT a whole number of cents. The EUR leg's integer minor
        # units have to round, and its 4-decimal base must not.
        assert funding.amount_base == Decimal("-9.3750")
        assert counter.amount_base == Decimal("9.3750")
        # +9.375 EUR, which is not a whole number of cents: the EUR leg's integer
        # minor units round to 938 while its 4-decimal base keeps all four. Two
        # columns, two precisions, and they are allowed to disagree by the
        # rounding — which is why the balance trigger sums `amount_base` and not
        # `amount`.
        assert counter.amount == 938
        assert _base_sum(engine, entry_id) == Decimal("0.0000")

    def test_the_currency_row_is_what_decides_not_the_code(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`normalize_currency` defaults to 2; this path never uses it.

        `finance.ingestion.normalize.normalize_currency` hardcodes `decimals=2`
        because it is the pre-database path. A manual posting reads the row, so a
        currency whose `decimals` differ from the conventional exponent is
        honoured — here JPY at 0, which the previous hardcoded 2 could not be.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO finance.currency (code, name, decimals)"
                        " VALUES ('XTS', 'Test code', 4)"
                    )
                )
                account = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES ('Crypto wallet', 'investment', 'asset',"
                            " 'XTS') RETURNING id"
                        )
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        "INSERT INTO finance.exchange_rate"
                        " (date, base_currency, quote_currency, rate)"
                        " VALUES (:date, 'EUR', 'XTS', 1)"
                    ),
                    {"date": BOOKED},
                )

        response = client.post(
            "/api/v1/transactions",
            json=_payload(
                account, amount="1.2345", currency="XTS", description="SATOSHI"
            ),
        )
        assert response.status_code == 201, response.text
        record = _stored_record(engine, int(response.json()["id"]))
        assert record.raw_amount == 12345

    def test_no_float_reaches_the_ledger(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`raw_amount` is an `int` in Python and a `bigint` in Postgres.

        Asserted at both ends because a float can only enter by being *written* as
        one: the request type is a string, `Money` refuses a float amount, and
        the column is BIGINT. The type check here is what a future
        "just accept a JSON number here" shortcut would break, and the assertion
        that it is not a `bool` is because `True == 1` in Python.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text

        record = _stored_record(engine, int(response.json()["id"]))
        assert type(record.raw_amount) is int
        assert not isinstance(record.raw_amount, bool)

        column_type = _scalar(
            engine,
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_schema = 'finance' AND table_name = 'source_record'"
            "   AND column_name = 'raw_amount'",
        )
        assert column_type == "bigint"

    def test_a_non_numeric_amount_is_a_422_not_a_crash(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """The edge validates, so the handler never sees an unparseable amount.

        Without the validator, `Decimal("twelve")` raises `InvalidOperation` from
        inside the handler — an unhandled exception and a 500 for what is a
        malformed body. Asserting the 422 also pins the *absence* of a stack
        trace: `TestClient` re-raises server exceptions, so a crash fails here.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], amount="forty"),
        )
        assert response.status_code == 422

    def test_an_unknown_currency_is_refused(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """A code with no row has no `decimals`, so there is nothing to convert to.

        Refused rather than defaulted to 2. Defaulting would be the exact bug the
        currency table exists to prevent, arriving through the front door.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], currency="ZZZ"),
        )
        assert response.status_code == 422
        assert "Unknown currency" in response.json()["detail"]

    def test_a_missing_fx_rate_is_refused_rather_than_guessed(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """No rate row for that date means no EUR value; the ledger will not invent one.

        The rate is stored on the line so the conversion is reproducible later; a
        guessed rate would be an invented fact that no later audit could
        distinguish from a real one.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(
                seeded["jpy_account"],
                amount="1500",
                currency="JPY",
                description="NO RATE",
                booked_date="2026-10-09",
            ),
        )
        assert response.status_code == 422
        assert "exchange rate" in response.json()["detail"]
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0


# ---------------------------------------------------------------------------
# The counter-leg is resolved or the request is refused
# ---------------------------------------------------------------------------


class TestTheCounterLegIsResolvedOrRefused:
    """There is no expense account type, so the contra-account must be found.

    `account_nature` is `(asset | liability | equity)`. A EUR 40.50 expense paid
    from checking is `checking -40.50` against `equity +40.50` — and the equity
    account has to be identified by name, or the request is refused. A silently
    mis-posted expense is far worse than a refused one: the entry balances either
    way, so nothing downstream would ever notice.
    """

    def test_a_missing_counter_account_fails_loudly_and_writes_nothing(
        self, client: TestClient, engine: Engine
    ) -> None:
        """No designated system expense account → 422 naming the role, empty ledger.

        The message is asserted because "could not resolve the counter-leg" is
        not actionable and "no account is designated as the system expense
        account" is. A silent fallback here would be the worst possible
        behaviour, so the test also asserts the ledger is still empty.
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
                            " VALUES ('Lonely checking', 'checking', 'asset',"
                            " 'EUR') RETURNING id"
                        )
                    ).scalar_one()
                )

        response = client.post("/api/v1/transactions", json=_payload(checking))
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "system expense account" in detail.lower()
        assert "designate" in detail.lower()

        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_line") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.import_batch") == 0

    def test_an_inactive_counter_account_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Deactivated is not the same as absent, and the message says which.

        An account that exists but is inactive is the far more likely mistake —
        somebody closed last year's "Expenses (system)" and created a new one.
        Saying "not found" would send them looking in the wrong place.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text("UPDATE finance.account SET is_active = FALSE WHERE id = :id"),
                    {"id": seeded["counter"]},
                )

        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 422
        assert "not active" in response.json()["detail"]
        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == 0

    def test_the_role_not_the_name_selects_the_counter_account(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """An account that merely shares the old magic name is NOT selected.

        The old resolver matched on `name == SYSTEM_EXPENSE_ACCOUNT_NAME`. The
        new resolver matches on `system_role == 'system_expense'`. An ordinary
        account carrying the legacy name but no role is ignored.
        """
        with engine.connect() as connection:
            with connection.begin():
                decoy = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, 'cash', 'equity', 'EUR') RETURNING id"
                        ),
                        {"name": SYSTEM_EXPENSE_ACCOUNT_NAME},
                    ).scalar_one()
                )

        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        entry_id = int(response.json()["journal_entry_id"])
        by_account = _legs_by_account(engine, entry_id)
        assert seeded["counter"] in by_account
        assert decoy not in by_account

    def test_renaming_the_designated_account_still_selects_it(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The role is the identity; the name is a human-editable label.

        Renaming the designated account in the database must not break posting:
        the resolver still finds the same account id.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE finance.account SET name = 'Renamed expenses'"
                        " WHERE id = :id"
                    ),
                    {"id": seeded["counter"]},
                )

        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        entry_id = int(response.json()["journal_entry_id"])
        by_account = _legs_by_account(engine, entry_id)
        assert set(by_account) == {seeded["checking"], seeded["counter"]}

    def test_an_explicit_counter_account_is_honoured(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A caller may name the contra-account, and it is still validated.

        Naming it explicitly is not a way around the rule — the id has to exist,
        be active, be `equity`, and differ from the funding account. This posts
        against a second equity row and proves the named one is the one used.
        """
        with engine.connect() as connection:
            with connection.begin():
                named = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES ('Expenses (imports)', 'cash', 'equity',"
                            " 'EUR') RETURNING id"
                        )
                    ).scalar_one()
                )

        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], counter_account_id=named),
        )
        assert response.status_code == 201, response.text
        entry_id = int(response.json()["journal_entry_id"])
        assert set(_legs_by_account(engine, entry_id)) == {
            seeded["checking"],
            named,
        }

    def test_an_explicit_counter_account_that_is_the_funding_account_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Both legs on one row is not double entry.

        It would in fact satisfy the trigger's `sum(amount_base)` check — one row
        at −40.50 and one at +40.50 on the same account balances — so this is
        refused in the domain rules, where it is visible, rather than by the
        database, which cannot see the mistake.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE finance.account SET account_nature = 'equity'"
                        " WHERE id = :id"
                    ),
                    {"id": seeded["checking"]},
                )

        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], counter_account_id=seeded["checking"]),
        )
        assert response.status_code == 422
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0

    def test_a_counter_account_of_the_wrong_nature_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`equity` is checked, not assumed.

        A `savings` contra-leg would balance perfectly and mean the opposite
        thing — the sandwich would look like money moving between the user's own
        accounts, which is the single most misleading thing this ledger can
        report.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(
                seeded["jpy_account"],
                currency="JPY",
                counter_account_id=seeded["jpy_account"],
            ),
        )
        assert response.status_code == 422
        assert "equity" in response.json()["detail"]

    def test_an_unknown_explicit_counter_account_is_refused(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], counter_account_id=9999),
        )
        assert response.status_code == 422
        assert "9999" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Bad input is a client error, not a crash
# ---------------------------------------------------------------------------


class TestBadInputIsRefusedNotCrashed:
    """The distinction that matters: a 404/409/422, never a 500.

    `TestClient` is left with `raise_server_exceptions=True`, so a handler that
    raised would fail these tests with the exception instead of returning a
    response. That is deliberate: a typed 500 would satisfy a loose "it is not a
    success" assertion, and it would still be a bug.
    """

    def test_a_non_existent_account_id_is_a_404(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """404, not a foreign-key IntegrityError surfacing as a 500.

        And not a silent post either: the ledger stays empty. A route that caught
        the IntegrityError and returned 201 would pass "not a 500" and be exactly
        the silent-post failure this test exists to catch.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"]) | {"account_id": 999999},
        )
        assert response.status_code == 404
        assert "999999" in response.json()["detail"]
        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0

    def test_a_non_integer_account_id_is_a_422(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """The path/body parameter is typed, so validation catches it at the edge."""
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"]) | {"account_id": "abc"},
        )
        assert response.status_code == 422

    def test_an_unknown_transaction_id_is_a_404(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        assert client.get("/api/v1/transactions/999999").status_code == 404

    def test_a_non_integer_transaction_id_is_a_422(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """Typed `int`, so this is FastAPI's validation rather than a lookup miss.

        Asserted separately from the 404 because the two are easy to confuse: a
        handler that took a `str` and compared it would return 404 here, and the
        diagnosis would point at the query instead of at the annotation.
        """
        assert client.get("/api/v1/transactions/not-a-number").status_code == 422

    def test_a_zero_amount_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two legs of 0.00 satisfy the balance trigger and record nothing.

        Worth refusing in the domain rules, where the intent is visible: the
        database can only see that the sum is zero.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], amount="0.00", description="Nonsense"),
        )
        assert response.status_code == 422
        assert "zero" in response.json()["detail"].lower()
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0

    def test_a_currency_that_contradicts_the_account_is_refused(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """The EUR account does not take a JPY transaction.

        `journal_line.currency` is a snapshot of `account.currency`, so a mismatch
        is a mistake in the request, not a conversion to perform. Posting anyway
        would re-denominate the account's history.
        """
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], currency="USD", description="WRONG"),
        )
        assert response.status_code == 422
        assert "holds EUR" in response.json()["detail"]

    def test_an_unknown_field_is_refused(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """`extra="forbid"`: a client cannot smuggle a column in through the body."""
        response = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"]) | {"status": "posted"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Raw fields round-trip, and stay put
# ---------------------------------------------------------------------------


class TestTheRawFieldsRoundTripAndAreThenImmutable:
    """What the user typed is stored verbatim — and cannot then be rewritten.

    Both halves matter and they are different guarantees. Round-tripping is a
    correctness property of the write path. Immutability afterwards is enforced
    by the database, not by this application: a future importer, a psql session,
    or a careless service all reach the same trigger.
    """

    def test_every_raw_field_round_trips_unchanged(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The stored raw fields are BYTE-FOR-BYTE what the API validated.

        The comparison is against `ManualTransactionRequest(**body)`, not against
        the literal the test sent. That distinction is the whole test: `_Write`
        sets `str_strip_whitespace=True`, so `"  COFFEE  "` is validated to
        `"COFFEE"` and `"COFFEE"` is what is stored. Comparing against the raw
        literal would fail on a correct implementation and — worse — would pass on
        an incorrect one that happened to strip the same amount.

        What is asserted: the stored value equals the value the request model
        produced, for the description, the amount, the currency, the dates and the
        whole `raw_data` payload. A normalisation that happened anywhere BELOW
        validation would break this.

        The awkward characters are kept deliberately and are inside the validated
        value: a run of internal spaces, a trailing provider reference marker, and
        mixed case. `normalize_description` would strip all three, so their
        survival is evidence that the route stores the user's text and does not
        run it through the matching normaliser on the way in.
        """
        description = "COFFEE   Shop  #REF:000123"
        body = _payload(
            seeded["checking"],
            amount="-12.34",
            description=description,
            value_date="2026-09-30",
        )
        accepted = ManualTransactionRequest(**body)  # type: ignore[arg-type]

        response = client.post("/api/v1/transactions", json=body)
        assert response.status_code == 201, response.text

        record = _stored_record(engine, int(response.json()["id"]))

        assert record.raw_description == accepted.description
        assert record.raw_amount == -1234
        assert record.raw_currency == "EUR"
        assert record.raw_date == accepted.booked_date

        assert record.raw_data["amount"] == accepted.amount
        assert record.raw_data["description"] == accepted.description
        assert record.raw_data["currency"] == "EUR"
        assert record.raw_data["booked_date"] == BOOKED
        assert record.raw_data["value_date"] == "2026-09-30"
        assert record.raw_data["counter_account_id"] == seeded["counter"]
        assert record.raw_data["provider"] == "manual"

    def test_an_absent_value_date_is_stored_as_null_not_dropped(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The shape of the request stays recoverable.

        A missing key and a key holding `null` are different claims about what the
        user sent, and `raw_data` is what a replay reads.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        record = _stored_record(engine, int(response.json()["id"]))
        assert record.raw_data["value_date"] is None

    def test_raw_data_cannot_be_rewritten_afterwards(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**The database refuses, not the application.**

        This goes through the ORM deliberately rather than through a handler: the
        guarantee is that NO writer can rewrite the evidence, so the test must not
        depend on this application having thought to stop it. `TestClient` cannot
        help here, so the session is taken from `finance.db` directly — which also
        proves the application's own session is subject to the same trigger.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        record_id = int(response.json()["id"])

        from finance.domain.models import importer

        with pytest.raises(DBAPIError) as refusal:
            with get_sessionmaker()() as session:
                orm_record = session.get(importer.SourceRecord, record_id)
                assert orm_record is not None
                orm_record.raw_data = {"tampered": True}
                session.commit()

        assert _sqlstate(refusal.value) == CHECK_VIOLATION
        assert "raw_data" in str(refusal.value)
        assert "immutable" in str(refusal.value)

        # BEFORE trigger: the row is untouched, not compensated afterwards.
        record = _stored_record(engine, record_id)
        assert record.raw_data["description"] == "JUMBO 4321 AMSTERDAM"

    def test_raw_description_cannot_be_rewritten_afterwards(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The second of the two frozen columns.

        Both are asserted because the trigger loops over a field list, and a loop
        that named only one field would pass a test that checked only one.
        """
        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 201, response.text
        record_id = int(response.json()["id"])

        from finance.domain.models import importer

        with pytest.raises(DBAPIError) as refusal:
            with get_sessionmaker()() as session:
                orm_record = session.get(importer.SourceRecord, record_id)
                assert orm_record is not None
                orm_record.raw_description = "TAMPERED"
                session.commit()

        assert _sqlstate(refusal.value) == CHECK_VIOLATION
        assert "raw_description" in str(refusal.value)
        stored = _stored_record(engine, record_id)
        assert stored.raw_description == "JUMBO 4321 AMSTERDAM"


def _sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE the driver reported, by name rather than by driver import."""
    origin: BaseException = exc
    candidate = getattr(exc, "orig", None)
    if isinstance(candidate, BaseException):
        origin = candidate
    code = getattr(origin, "sqlstate", None)
    if isinstance(code, str):
        return code
    code = getattr(origin, "pgcode", None)
    return code if isinstance(code, str) else None


# ---------------------------------------------------------------------------
# The database is the backstop, and the route says so
# ---------------------------------------------------------------------------


class TestAnUnbalancedEntryIsRefusedByTheDatabase:
    """What happens when the application tries to post an entry that does not balance.

    The route builds balanced legs, so this cannot happen by accident — which is
    exactly why it is worth proving the backstop works. The balance trigger itself
    is `test_balance_db.py`'s subject; what is new here is that the API turns its
    refusal into a status code and leaves the ledger untouched, instead of a 500
    with half a transaction's worth of rows in it.

    **The sabotage has to disable BOTH balancing steps, and finding that out is
    the point of writing it.** Two attempts failed first, and both were green for
    the same wrong reason:

    * Replacing `absorb_fx_residual` with an identity function changed nothing,
      because `build_expense_legs` already emits exact negations for a two-leg
      entry — absorption has nothing left to do.
    * Replacing `build_expense_legs` with a one-cent-wrong version was ALSO
      absorbed: `absorb_fx_residual` recomputes the largest leg from the others,
      so it repairs the sabotage instead of passing it through.

    Each failure is the correctness of the step above it: absorption exists
    precisely to catch a leg builder that rounds independently, and the two are
    only redundant when the second is broken too. `test_the_sabotage_really_does
    _unbalance_the_legs` below asserts that the sabotage still bites, so a future
    fix to either function cannot quietly turn this file's backstop proof into a
    proof of a balanced entry.

    A sabotage that cannot fail is worse than no sabotage, because it reads as
    coverage. What still runs here is production behaviour — the transaction
    block, the sign convention, the fingerprint, the source record — so the
    assertion is "the ledger caught a bad entry and the API survived it", not "the
    arithmetic is right".
    """

    @staticmethod
    def _one_cent_wrong(**kwargs: object) -> tuple[PostingLeg, PostingLeg]:
        """The real leg builder, with the counter-leg one cent short."""
        funding, counter = build_expense_legs(**kwargs)  # type: ignore[arg-type]
        return funding, replace(
            counter, amount_base=counter.amount_base - Decimal("0.0100")
        )

    @classmethod
    def _sabotage(cls, monkeypatch: pytest.MonkeyPatch) -> None:
        """Break BOTH balancing steps, so an imbalance can reach the database."""
        monkeypatch.setattr(
            transactions_routes, "build_expense_legs", cls._one_cent_wrong
        )
        monkeypatch.setattr(
            transactions_routes,
            "absorb_fx_residual",
            lambda legs: tuple(legs),
        )

    def test_the_sabotage_really_does_unbalance_the_legs(
        self,
        client: TestClient,
        engine: Engine,
        seeded: dict[str, int],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**The guard on the guard.** Without this, the test below proves nothing.

        It runs the sabotage and checks that a leg builder under it really does
        produce legs that do not sum to zero. If the sabotage ever stops working —
        because `build_expense_legs` was corrected, or because absorption is
        reordered ahead of it — the refusal test would stay green while testing a
        perfectly balanced entry, which is the worst state a test can be in.
        """
        self._sabotage(monkeypatch)
        # Two assertions, because either alone is insufficient: the first says
        # the ROUTE is wired to the sabotaged builder, the second says the
        # sabotaged builder is wrong. Checking only the arithmetic would pass even
        # if `monkeypatch` had silently patched the wrong name.
        assert (
            vars(transactions_routes)["build_expense_legs"]
            is TestAnUnbalancedEntryIsRefusedByTheDatabase._one_cent_wrong
        )
        funding, counter = self._one_cent_wrong(
            funding_account=PostingAccount(1, "Checking", "EUR", "asset"),
            counter_account=PostingAccount(2, "Expenses", "EUR", "equity"),
            amount=Money(amount=-4050, currency=CoreCurrency(code="EUR", decimals=2)),
            rate=Decimal(1),
        )
        assert funding.amount_base + counter.amount_base == Decimal("-0.0100")

    def test_an_unbalanced_posting_is_rejected_at_commit_and_nothing_is_written(
        self,
        client: TestClient,
        engine: Engine,
        seeded: dict[str, int],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._sabotage(monkeypatch)

        response = client.post(
            "/api/v1/transactions", json=_payload(seeded["checking"])
        )
        assert response.status_code == 409, response.text
        assert "Refused by the ledger" in response.json()["detail"]
        assert "does not balance" in response.json()["detail"]

        # The trigger is DEFERRABLE INITIALLY DEFERRED: every INSERT succeeded and
        # only the COMMIT was refused. So these counts are what proves the whole
        # transaction rolled back rather than leaving a half-written manual entry —
        # the batch, the entry, the lines and the raw record all go together.
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_line") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.import_batch") == 0

    def test_the_refusal_happens_at_commit_and_not_at_the_first_leg(
        self,
        client: TestClient,
        engine: Engine,
        seeded: dict[str, int],
    ) -> None:
        """The first line of a two-line entry is NOT supposed to be rejected.

        If the route ever ran its writes outside one explicit transaction, the
        trigger would stop being deferred for this path and the very first leg
        would be refused — and every multi-line posting would become impossible.
        That is the same footgun as `AUTOCOMMIT`, and it is invisible in a test
        that only checks "an unbalanced entry is refused": both outcomes are an
        exception.

        So this asserts the positive half directly. The two legs are written by
        hand, in ONE explicit transaction, and the point of interest is that
        `session.flush()` after the FIRST line does not raise. Reaching the second
        `flush()` at all is the evidence; a per-row trigger would have failed on
        the line above.
        """
        del client
        # Write the same two legs the sabotaged route would write, in one
        # explicit transaction, and observe WHERE the failure lands.
        from finance.db import get_sessionmaker
        from finance.domain.models.ledger import JournalEntry, JournalLine

        with pytest.raises(DBAPIError) as refusal:
            # `get_sessionmaker` rather than the deleted `db.session_scope`: the
            # commit is explicit below, because the trigger only fires there and
            # a context manager would commit on the way out of the `raises`.
            with get_sessionmaker()() as session:
                session.begin()
                entry = JournalEntry(entry_date=dt.date.fromisoformat(BOOKED))
                session.add(entry)
                session.flush()
                session.add(
                    JournalLine(
                        journal_entry_id=entry.id,
                        account_id=seeded["checking"],
                        amount=-4050,
                        currency="EUR",
                        amount_base=Decimal("-40.5000"),
                    )
                )
                session.flush()
                # Accepted: still inside the transaction, and visible to it.
                assert session.get(JournalEntry, entry.id) is not None
                session.add(
                    JournalLine(
                        journal_entry_id=entry.id,
                        account_id=seeded["counter"],
                        amount=4049,
                        currency="EUR",
                        amount_base=Decimal("40.4900"),
                    )
                )
                session.flush()
                # Only the COMMIT makes the imbalance the database's problem.
                session.commit()

        assert _sqlstate(refusal.value) == CHECK_VIOLATION
        assert "does not balance" in str(refusal.value)
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 0


# ---------------------------------------------------------------------------
# Listing, the queue, update, and the delete that is refused
# ---------------------------------------------------------------------------


class TestListingAndTheUncategorizedQueue:
    """The two read endpoints, over real rows.

    The queue is defined exactly as `idx_jl_uncat` defines it in migration 0001 —
    uncategorised, unmatched, negative. Anything looser is a queue the user learns
    not to read.
    """

    def test_listing_returns_what_was_created(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        first = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], description="COFFEE", amount="-3.20"),
        )
        second = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], description="TRAIN", amount="-12.50"),
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text

        listed = client.get("/api/v1/transactions")
        assert listed.status_code == 200
        assert {row["id"] for row in listed.json()} == {
            int(first.json()["id"]),
            int(second.json()["id"]),
        }
        assert [row["raw_description"] for row in listed.json()] == [
            "COFFEE",
            "TRAIN",
        ]

    def test_listing_filters_by_account_and_status(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        mine = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        theirs = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["jpy_account"], currency="JPY", description="RAMEN"),
        )
        assert mine.status_code == 201, mine.text
        assert theirs.status_code == 201, theirs.text

        by_account = client.get(
            "/api/v1/transactions", params={"account_id": seeded["checking"]}
        )
        assert [row["id"] for row in by_account.json()] == [int(mine.json()["id"])]

        by_status = client.get("/api/v1/transactions", params={"status": "posted"})
        assert len(by_status.json()) == 2

        nothing = client.get("/api/v1/transactions", params={"status": "duplicate"})
        assert nothing.json() == []

    def test_the_uncategorized_queue_excludes_categorised_transactions(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """The queue is a filter over the same rows, not a separate table.

        Both transactions are created the same way and differ only in whether
        they have been categorised. Categorising one — the one thing a user opens
        this app to do — must remove it from the queue and change nothing else
        about the listing.
        """
        queued = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], description="COFFEE", amount="-3.20"),
        )
        categorised = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], description="TRAIN", amount="-12.50"),
        )
        assert queued.status_code == 201, queued.text
        assert categorised.status_code == 201, categorised.text

        before = client.get("/api/v1/transactions/uncategorized")
        assert {row["id"] for row in before.json()} == {
            int(queued.json()["id"]),
            int(categorised.json()["id"]),
        }

        updated = client.patch(
            f"/api/v1/transactions/{categorised.json()['id']}",
            json={"category_id": seeded["category"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["category_id"] == seeded["category"]

        after = client.get("/api/v1/transactions/uncategorized")
        assert [row["id"] for row in after.json()] == [int(queued.json()["id"])]

        # And the listing is unchanged: categorising moved it out of the QUEUE,
        # not out of the ledger.
        assert len(client.get("/api/v1/transactions").json()) == 2

    def test_the_queue_excludes_a_credit(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """Income is not "what did I spend".

        `idx_jl_uncat` filters on `amount_base < 0`, and this asserts the route
        agrees with the index rather than merely being looser than it.
        """
        expense = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], description="COFFEE", amount="-3.20"),
        )
        income = client.post(
            "/api/v1/transactions",
            json=_payload(seeded["checking"], description="SALARY", amount="2500.00"),
        )
        assert expense.status_code == 201, expense.text
        assert income.status_code == 201, income.text

        queued = client.get("/api/v1/transactions/uncategorized")
        assert [row["id"] for row in queued.json()] == [int(expense.json()["id"])]

    def test_an_empty_ledger_queues_nothing(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """Asserted because a queue that 500s on an empty database is a real bug."""
        del seeded
        response = client.get("/api/v1/transactions/uncategorized")
        assert response.status_code == 200
        assert response.json() == []


class TestUpdateAndDelete:
    """What may be edited, and the one edit the database forbids.

    `raw_data_immutable` is the project's highest-priority invariant, and a
    DELETE endpoint that quietly worked would make it decorative. So the refusal
    is asserted here as behaviour, not left untested because it "can't happen".
    """

    def test_updating_the_category_moves_the_transaction_out_of_the_queue(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        created = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        assert created.status_code == 201, created.text
        record_id = int(created.json()["id"])

        assert len(client.get("/api/v1/transactions/uncategorized").json()) == 1

        updated = client.patch(
            f"/api/v1/transactions/{record_id}",
            json={"category_id": seeded["category"]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["category_id"] == seeded["category"]

        fetched = client.get(f"/api/v1/transactions/{record_id}")
        assert fetched.json()["category_id"] == seeded["category"]
        assert client.get("/api/v1/transactions/uncategorized").json() == []

    def test_updating_the_entry_date_leaves_the_booking_date_alone(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`entry_date` is an accounting fact; `raw_date` is evidence.

        Changing only the former is the point of having both columns: the entry
        can be re-dated for a statement cut-off without rewriting what the user
        originally said, which is what a replay has to reproduce.
        """
        created = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        assert created.status_code == 201, created.text
        record_id = int(created.json()["id"])
        entry_id = int(created.json()["journal_entry_id"])

        updated = client.patch(
            f"/api/v1/transactions/{record_id}", json={"entry_date": "2026-09-30"}
        )
        assert updated.status_code == 200, updated.text

        assert _scalar(
            engine,
            "SELECT entry_date FROM finance.journal_entry WHERE id = :id",
            id=entry_id,
        ) == dt.date.fromisoformat("2026-09-30")
        stored = _stored_record(engine, record_id)
        assert stored.raw_date == dt.date.fromisoformat(BOOKED)

    def test_the_amount_and_description_are_not_editable(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """`extra="forbid"`, so an attempt to rewrite the evidence is a 422.

        There is no `amount` or `description` field on the update body at all.
        Correcting an amount means reversing the entry, which is a new fact and a
        later milestone; offering an edit here would make the fingerprint
        unreproducible from `raw_data`.
        """
        created = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        record_id = int(created.json()["id"])
        response = client.patch(
            f"/api/v1/transactions/{record_id}",
            json={"amount": "-99.00", "description": "REWRITTEN"},
        )
        assert response.status_code == 422

    def test_updating_an_unknown_category_is_a_404(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """Not a 500 from a foreign-key violation, and not a silent no-op."""
        created = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        record_id = int(created.json()["id"])
        response = client.patch(
            f"/api/v1/transactions/{record_id}", json={"category_id": 9999}
        )
        assert response.status_code == 404

    def test_updating_an_unknown_transaction_is_a_404(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        del seeded
        response = client.patch(
            "/api/v1/transactions/999999", json={"category_id": None}
        )
        assert response.status_code == 404

    def test_deleting_a_source_record_is_refused_by_the_database(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """**`raw_data_immutable` is the highest-priority invariant, and it wins.**

        The endpoint issues the DELETE and the database says no. Asserted as
        behaviour because an endpoint that "cannot fail" would leave the invariant
        untested from the only direction that matters.

        Three things are asserted, and each is a distinct way this could be wrong:

        * the status is a client error, so the caller learns the rule;
        * the message is the TRIGGER's, so the rule is stated rather than
          paraphrased;
        * the row is STILL THERE, because the trigger is BEFORE DELETE — a
          trigger raising afterwards would have deleted it, and a restore-from-
          `raw_data` path does not exist.
        """
        created = client.post("/api/v1/transactions", json=_payload(seeded["checking"]))
        assert created.status_code == 201, created.text
        record_id = int(created.json()["id"])

        response = client.delete(f"/api/v1/transactions/{record_id}")
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert "Refused by the ledger" in detail
        assert "raw_data_immutable" in detail
        assert "forbidden" in detail

        assert _scalar(engine, "SELECT count(*) FROM finance.source_record") == 1
        assert (
            _scalar(
                engine,
                "SELECT raw_description FROM finance.source_record WHERE id = :id",
                id=record_id,
            )
            == "JUMBO 4321 AMSTERDAM"
        )

    def test_deleting_an_unknown_transaction_is_a_404(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        del seeded
        assert client.delete("/api/v1/transactions/999999").status_code == 404


# ---------------------------------------------------------------------------
# Accounts are real rows too
# ---------------------------------------------------------------------------


class TestAccountsAreRealRows:
    """The accounts router is the other half of "manual CRUD works end to end".

    A manual posting names an account id, and that id is a foreign key into
    `finance.account`. While the accounts router served an in-memory list, there
    was no way to create the account a manual transaction needed — so these tests
    create one through the API and post against it, in the same test, with no
    direct SQL in between.
    """

    def test_an_account_created_through_the_api_can_be_posted_against(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        del seeded
        created = client.post(
            "/api/v1/accounts",
            json={
                "name": "Savings via API",
                "account_type": "savings",
                "account_nature": "asset",
                "currency": "EUR",
            },
        )
        assert created.status_code == 201, created.text
        account_id = int(created.json()["id"])

        # The system counter-leg is seeded directly, because "create the expense
        # account" is a setup step, not part of creating a transaction.
        posted = client.post(
            "/api/v1/transactions",
            json=_payload(account_id, description="BOOKS", amount="-19.99"),
        )
        assert posted.status_code == 201, posted.text
        assert posted.json()["account_id"] == account_id

    def test_listing_accounts_returns_what_was_created(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del seeded
        created = client.post(
            "/api/v1/accounts",
            json={
                "name": "New current",
                "account_type": "checking",
                "account_nature": "liability",
                "currency": "JPY",
            },
        )
        assert created.status_code == 201, created.text
        listed = client.get("/api/v1/accounts")
        assert int(created.json()["id"]) in {row["id"] for row in listed.json()}

        by_nature = client.get("/api/v1/accounts/by-nature/liability")
        assert [row["name"] for row in by_nature.json()] == ["New current"]

    def test_creating_an_account_with_an_unknown_currency_is_a_422(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The currency must exist, because `decimals` is what gives amounts meaning.

        Refused rather than defaulted: an account whose currency is wrong
        re-denominates every posting made through it, and the error surfaces
        nowhere near the cause.
        """
        del seeded
        response = client.post(
            "/api/v1/accounts",
            json={
                "name": "Nowhere account",
                "account_type": "checking",
                "account_nature": "asset",
                "currency": "ZZZ",
            },
        )
        assert response.status_code == 422
        assert _scalar(engine, "SELECT count(*) FROM finance.account") == 3

    def test_an_unknown_account_is_a_404(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        del seeded
        assert client.get("/api/v1/accounts/999999").status_code == 404


# ---------------------------------------------------------------------------
# The module, not just the happy path
# ---------------------------------------------------------------------------


class TestTheRulesOnTheirOwn:
    """`manual_posting` is pure, so it is tested without a database.

    These are marked `db` only because they live in this module; they need no
    connection. The 2-leg path cannot produce a residual by construction, so the
    residual absorption is exercised on THREE legs, where it actually has work to
    do — and it is also shown to be a no-op on two, which is the property that
    makes it safe to apply unconditionally.
    """

    def test_two_legs_are_already_balanced_so_absorbing_is_a_no_op(self) -> None:
        from finance.domain.services.manual_posting import (
            PostingAccount,
            absorb_fx_residual,
            build_expense_legs,
        )

        legs = build_expense_legs(
            funding_account=PostingAccount(1, "Checking", "EUR", "asset"),
            counter_account=PostingAccount(2, "Expenses", "EUR", "equity"),
            amount=Money(amount=-4050, currency=CoreCurrency(code="EUR", decimals=2)),
            rate=Decimal(1),
        )
        assert sum(leg.amount_base for leg in legs) == Decimal("0.0000")
        assert absorb_fx_residual(legs) == legs

    def test_three_legs_are_absorbed_into_the_largest(
        self,
    ) -> None:
        """A third leg (an FX fee, a split) leaves a residual. It goes on the biggest.

        Without absorption this entry is inside the ±0.005 tolerance and would
        commit — and every downstream sum would carry the drift. Asserted on the
        exact sum, and on WHICH leg absorbed it, because putting the residual on
        the smallest leg would distort the smallest amount by the largest
        proportion.
        """
        from finance.domain.services.manual_posting import (
            PostingLeg,
            absorb_fx_residual,
        )

        legs = (
            PostingLeg(1, "EUR", -10000, Decimal("-100.0000"), Decimal(1), 0),
            PostingLeg(2, "EUR", 9999, Decimal("99.9900"), Decimal(1), 1),
            PostingLeg(3, "EUR", 1, Decimal("0.0150"), Decimal(1), 2),
        )
        assert sum(leg.amount_base for leg in legs) == Decimal("0.0050")

        absorbed = absorb_fx_residual(legs)
        assert sum(leg.amount_base for leg in absorbed) == Decimal("0.0000")
        # The biggest leg (|-100.0000|) absorbed it, so the 0.01 fee is untouched.
        assert absorbed[0].amount_base == Decimal("-100.0050")
        assert absorbed[1].amount_base == Decimal("99.9900")
        assert absorbed[2].amount_base == Decimal("0.0150")

    def test_a_real_residual_is_refused_not_absorbed(self) -> None:
        """A shift past the balance tolerance is a real FX loss, not rounding.

        Absorption may fold a ROUNDING residual into the largest leg, but a
        genuine difference must become its own leg. Silently absorbing this would
        balance the entry while erasing the loss, which is the defect LifeOS-fwc
        exists to close. The tolerance boundary itself is proven by the test
        above, which absorbs exactly 0.0050.
        """
        from finance.domain.services.manual_posting import (
            ManualPostingError,
            PostingLeg,
            absorb_fx_residual,
        )

        legs = (
            PostingLeg(1, "EUR", -10000, Decimal("-100.0000"), Decimal(1), 0),
            PostingLeg(2, "EUR", 9900, Decimal("99.0000"), Decimal(1), 1),
            PostingLeg(3, "EUR", 50, Decimal("0.5000"), Decimal(1), 2),
        )
        assert sum(leg.amount_base for leg in legs) == Decimal("-0.5000")

        with pytest.raises(ManualPostingError, match="real FX gain/loss"):
            absorb_fx_residual(legs)

    def test_absorbing_needs_at_least_two_legs(self) -> None:
        """A single leg has nothing to absorb into, and returning it would be a lie."""
        from finance.domain.services.manual_posting import (
            ManualPostingError,
            PostingLeg,
            absorb_fx_residual,
        )

        with pytest.raises(ManualPostingError):
            absorb_fx_residual(
                (PostingLeg(1, "EUR", -100, Decimal("-1.0000"), Decimal(1), 0),)
            )

    def test_the_convention_documented_is_the_convention_used(self) -> None:
        """The counter-leg's role, nature and type are read from the module.

        Asserted as a round trip through the resolver so that changing a constant
        without changing the resolver's expectations — or vice versa — fails here
        rather than in production.
        """
        from finance.domain.services.manual_posting import (
            SYSTEM_EXPENSE_ACCOUNT_NATURE,
            SYSTEM_EXPENSE_ACCOUNT_ROLE,
            SYSTEM_EXPENSE_ACCOUNT_TYPE,
            PostingAccount,
            resolve_default_counter_account,
        )

        resolved = resolve_default_counter_account(
            [
                PostingAccount(
                    id=7,
                    name=SYSTEM_EXPENSE_ACCOUNT_NAME,
                    currency="EUR",
                    account_nature=SYSTEM_EXPENSE_ACCOUNT_NATURE,
                    system_role=SYSTEM_EXPENSE_ACCOUNT_ROLE,
                )
            ]
        )
        assert resolved.id == 7
        # And the type the seeded row must carry is one the schema's CHECK allows.
        assert SYSTEM_EXPENSE_ACCOUNT_TYPE in {
            "checking",
            "savings",
            "credit_card",
            "cash",
            "investment",
            "loan",
            "mortgage",
        }
