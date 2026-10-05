"""test_learned_categorization_db.py — a correction teaches, the next payee knows.

The acceptance test for increment 1 of LifeOS-8: a manual category
correction with `learn=true` stores a learned `category_rule`, and the next
transaction from the same payee arrives already categorized — with no
hand-authored rule anywhere. The negative proves the default: without
`learn`, a correction stays a correction and teaches nothing.

**Marked `db`.** The app is driven through HTTP and every assertion that
matters is read back from a second connection, so an uncommitted
transaction cannot satisfy them. Fixtures mirror
`test_manual_transactions_db.py` (same accounts, same payload shape) because
a manual correction is a manual transaction first.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Final

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text

from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME

pytestmark = pytest.mark.db

#: One fixed booked date, so "same account, amount, date and description"
#: stays statable and no test depends on today.
BOOKED: Final[str] = "2026-10-01"

CHECKING_NAME: Final[str] = "Test current account"


# ---------------------------------------------------------------------------
# Payload helper
# ---------------------------------------------------------------------------


def _payload(
    account_id: int,
    *,
    amount: str = "-12.50",
    description: str = "PAYPAL XYZ 1234",
    booked_date: str = BOOKED,
) -> dict[str, object]:
    """A `POST /transactions` body. Signed string amount, per the contract."""
    return {
        "account_id": account_id,
        "description": description,
        "amount": amount,
        "currency": "EUR",
        "booked_date": booked_date,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`, caches cleared."""
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
    """EUR, a checking account, the system expense account, and the tree.

    `Entertainment > Music` plus `Groceries`: the acceptance needs two
    distinct categories so a re-correction is observable, and a parent so
    Music is a real leaf rather than a second root.
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
            entertainment = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, is_system)"
                        " VALUES ('Entertainment', 'expense', TRUE) RETURNING id"
                    )
                ).scalar_one()
            )
            music = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, parent_id)"
                        " VALUES ('Music', 'expense', :parent) RETURNING id"
                    ),
                    {"parent": entertainment},
                ).scalar_one()
            )
            groceries = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, is_system)"
                        " VALUES ('Groceries', 'expense', TRUE) RETURNING id"
                    )
                ).scalar_one()
            )
    return {"checking": checking, "music": music, "groceries": groceries}


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
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _uncategorized_ids(client: TestClient) -> list[int]:
    """The ids currently waiting in the review queue."""
    response = client.get("/api/v1/transactions/uncategorized")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list), body
    ids: list[int] = []
    for row in body:
        assert isinstance(row, dict), row
        ids.append(_as_int(row["id"]))
    return ids


def _post(client: TestClient, checking: int, description: str) -> dict[str, object]:
    """One manual transaction. Asserts the create; returns the decoded body."""
    response = client.post(
        "/api/v1/transactions",
        json=_payload(checking, description=description),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body, dict), body
    return body


def _rule_rows(engine: Engine) -> list[tuple[object, ...]]:
    """Every `category_rule` row: pattern, category, learned, confidence."""
    return _rows(
        engine,
        "SELECT description_pattern, category_id, is_learned, confidence"
        " FROM finance.category_rule ORDER BY id",
    )


# ---------------------------------------------------------------------------
# The acceptance and its negative
# ---------------------------------------------------------------------------


class TestACorrectionTeaches:
    def test_next_identical_payee_arrives_categorized(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The bead's acceptance, stated literally.

        "PAYPAL XYZ 1234" lands uncategorized; correcting it to Music with
        `learn=true` stores exactly one learned rule for "paypal xyz"; the
        next "PAYPAL XYZ 9999" arrives already Music, with no PATCH.
        """
        first = _post(client, seeded["checking"], "PAYPAL XYZ 1234")
        assert first["category_id"] is None
        assert _as_int(first["id"]) in _uncategorized_ids(client)

        corrected = client.patch(
            f"/api/v1/transactions/{_as_int(first['id'])}",
            json={"category_id": seeded["music"], "learn": True},
        )
        assert corrected.status_code == 200, corrected.text
        taught = corrected.json()
        assert isinstance(taught, dict), taught
        assert taught["category_id"] == seeded["music"]
        assert taught["learned"] is True
        assert _as_int(first["id"]) not in _uncategorized_ids(client)

        rows = _rule_rows(engine)
        assert len(rows) == 1, rows
        pattern, category_id, is_learned, confidence = rows[0]
        assert pattern == "paypal xyz"
        assert _as_int(category_id) == seeded["music"]
        assert is_learned is True
        assert Decimal(str(confidence)) == Decimal("1.00")

        second = _post(client, seeded["checking"], "PAYPAL XYZ 9999")
        assert second["category_id"] == seeded["music"]
        assert _as_int(second["id"]) not in _uncategorized_ids(client)

    def test_correction_without_learn_teaches_nothing(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The default: `learn` off writes no rule, and the next identical
        payee still waits in the queue."""
        first = _post(client, seeded["checking"], "PAYPAL ABC 1111")
        assert first["category_id"] is None

        corrected = client.patch(
            f"/api/v1/transactions/{_as_int(first['id'])}",
            json={"category_id": seeded["music"]},
        )
        assert corrected.status_code == 200, corrected.text
        taught = corrected.json()
        assert isinstance(taught, dict), taught
        assert taught["category_id"] == seeded["music"]
        assert taught["learned"] is False

        assert _rule_rows(engine) == []

        second = _post(client, seeded["checking"], "PAYPAL ABC 2222")
        assert second["category_id"] is None
        assert _as_int(second["id"]) in _uncategorized_ids(client)

    def test_a_second_correction_updates_the_rule_in_place(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The payee was Music; now it is Groceries. Still one row — and the
        next occurrence follows the update, proving it took effect."""
        first = _post(client, seeded["checking"], "PAYPAL XYZ 1234")
        assert (
            client.patch(
                f"/api/v1/transactions/{_as_int(first['id'])}",
                json={"category_id": seeded["music"], "learn": True},
            ).status_code
            == 200
        )

        second = _post(client, seeded["checking"], "PAYPAL XYZ 5555")
        assert second["category_id"] == seeded["music"]
        assert (
            client.patch(
                f"/api/v1/transactions/{_as_int(second['id'])}",
                json={"category_id": seeded["groceries"], "learn": True},
            ).status_code
            == 200
        )

        rows = _rule_rows(engine)
        assert len(rows) == 1, rows
        assert rows[0][0] == "paypal xyz"
        assert _as_int(rows[0][1]) == seeded["groceries"]

        third = _post(client, seeded["checking"], "PAYPAL XYZ 7777")
        assert third["category_id"] == seeded["groceries"]


class TestLearnedRulesYieldToHandRules:
    def test_an_explicit_rule_outranks_the_learned_one(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Priority 500 sorts after the hand-rule default of 100, so a rule
        the user wrote deliberately always wins over what a correction
        taught — asserted through the engine's own ordering, not the number
        alone."""
        first = _post(client, seeded["checking"], "PAYPAL XYZ 1234")
        assert (
            client.patch(
                f"/api/v1/transactions/{_as_int(first['id'])}",
                json={"category_id": seeded["music"], "learn": True},
            ).status_code
            == 200
        )
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO finance.category_rule"
                        " (description_pattern, category_id, priority)"
                        " VALUES ('paypal', :category, 100)"
                    ),
                    {"category": seeded["groceries"]},
                )

        later = _post(client, seeded["checking"], "PAYPAL XYZ 9999")
        assert later["category_id"] == seeded["groceries"]
