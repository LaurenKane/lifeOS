"""test_categories_db.py — categories and rules as real database rows.

Increment 3 of LifeOS-8 replaces the in-memory stubs with reads and writes
against `finance.category` and `finance.category_rule`, so a user-created
category works end to end and the rule set the matcher reads is the rule
set this API stores. Every test drives HTTP and reads the ledger facts
back from a second connection.

**Marked `db`.** Fixtures mirror `test_learned_categorization_db.py`: same
throwaway database, same manual-transaction shape for the tie-in tests.
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

BOOKED: Final[str] = "2026-10-01"

CHECKING_NAME: Final[str] = "Test current account"


# ---------------------------------------------------------------------------
# Fixtures and helpers
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
    """EUR, a checking account and the system expense account, committed."""
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


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


def _as_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _create_category(
    client: TestClient,
    name: str,
    kind: str = "expense",
    parent_id: int | None = None,
) -> dict[str, object]:
    """POST one category. Asserts the create; returns the decoded body."""
    body: dict[str, object] = {"name": name, "kind": kind}
    if parent_id is not None:
        body["parent_id"] = parent_id
    response = client.post("/api/v1/categories", json=body)
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


def _create_rule(
    client: TestClient, pattern: str, category_id: int, priority: int = 100
) -> dict[str, object]:
    """POST one hand rule. Asserts the create; returns the decoded body."""
    response = client.post(
        "/api/v1/categories/rules",
        json={
            "description_pattern": pattern,
            "category_id": category_id,
            "priority": priority,
        },
    )
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


def _rule_list(client: TestClient) -> list[dict[str, object]]:
    """Every stored rule, in the order the engine reads them."""
    response = client.get("/api/v1/categories/rules")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list), body
    rules: list[dict[str, object]] = []
    for row in body:
        assert isinstance(row, dict), row
        rules.append(row)
    return rules


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class TestCreateCategory:
    def test_custom_category_persists_and_lists(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A user category is a row: it has an id, is not system, and GET
        returns it."""
        del seeded
        created = _create_category(client, "Hobbies")
        assert _as_int(created["id"]) >= 1
        assert created["name"] == "Hobbies"
        assert created["kind"] == "expense"
        assert created["is_system"] is False

        response = client.get("/api/v1/categories")
        assert response.status_code == 200, response.text
        names = [row["name"] for row in response.json()]
        assert "Hobbies" in names

    def test_kind_filter_selects(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """`?kind=` filters; it does not just echo."""
        del engine, seeded
        _create_category(client, "Fun", kind="expense")
        _create_category(client, "Pay", kind="income")
        response = client.get("/api/v1/categories", params={"kind": "expense"})
        assert response.status_code == 200, response.text
        kinds = {row["kind"] for row in response.json()}
        assert kinds == {"expense"}

    def test_child_category_names_its_parent(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A parent that exists is accepted, and the row points at it."""
        del seeded
        parent = _create_category(client, "Leisure")
        child = _create_category(client, "Games", parent_id=_as_int(parent["id"]))
        stored = _rows(
            engine,
            "SELECT parent_id FROM finance.category WHERE id = :id",
            id=_as_int(child["id"]),
        )
        assert stored == [(_as_int(parent["id"]),)]

    def test_child_category_lists_its_parent_id(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The tree the UI draws comes from the API: GET lists `parent_id`,
        None for a root and the parent's id for a child."""
        del engine, seeded
        parent = _create_category(client, "Leisure")
        child = _create_category(client, "Games", parent_id=_as_int(parent["id"]))
        assert child["parent_id"] == _as_int(parent["id"])
        assert parent["parent_id"] is None

        response = client.get("/api/v1/categories")
        assert response.status_code == 200, response.text
        by_id = {row["id"]: row for row in response.json()}
        assert by_id[_as_int(child["id"])]["parent_id"] == _as_int(parent["id"])
        assert by_id[_as_int(parent["id"])]["parent_id"] is None


class TestCreateCategoryRefusals:
    def test_duplicate_name_under_same_parent_conflicts(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The `(parent_id, name)` constraint decides duplicates, and a
        duplicate is a 409 — asserted under a real parent, because two
        top-level same-name rows do NOT collide under Postgres NULL
        semantics (a documented gap, not this endpoint's to close)."""
        del engine, seeded
        parent = _create_category(client, "Leisure")
        _create_category(client, "Games", parent_id=_as_int(parent["id"]))
        repeat = client.post(
            "/api/v1/categories",
            json={
                "name": "Games",
                "kind": "expense",
                "parent_id": _as_int(parent["id"]),
            },
        )
        assert repeat.status_code == 409, repeat.text

    def test_unknown_kind_is_unprocessable(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The closed kind enum fails at the edge, before any write."""
        del engine, seeded
        response = client.post(
            "/api/v1/categories", json={"name": "Hobbies", "kind": "bogus"}
        )
        assert response.status_code == 422, response.text

    def test_missing_parent_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A parent that names no row is a client mistake, not a 500 from
        the foreign key."""
        del engine, seeded
        response = client.post(
            "/api/v1/categories",
            json={"name": "Games", "kind": "expense", "parent_id": 999999},
        )
        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestRules:
    def test_hand_rule_persists_and_lists_in_engine_order(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Stored rows come back `(priority, id)`-ordered with `is_learned`
        False — the order the matcher itself uses."""
        del engine, seeded
        dining = _create_category(client, "Dining")
        travel = _create_category(client, "Travel")
        _create_rule(client, "zzz", _as_int(dining["id"]), priority=100)
        _create_rule(client, "aaa", _as_int(travel["id"]), priority=50)

        rules = _rule_list(client)
        assert [(row["description_pattern"], row["priority"]) for row in rules] == [
            ("aaa", 50),
            ("zzz", 100),
        ]
        assert all(row["is_learned"] is False for row in rules)

    def test_learned_rule_appears_in_the_list(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A correction taught with `learn=true` (increment 1) shows up
        here with `is_learned` True: one store, two doors."""
        del engine
        music = _create_category(client, "Music")
        posted = client.post(
            "/api/v1/transactions",
            json={
                "account_id": seeded["checking"],
                "description": "PAYPAL XYZ 1234",
                "amount": "-12.50",
                "currency": "EUR",
                "booked_date": BOOKED,
            },
        )
        assert posted.status_code == 201, posted.text
        patched = client.patch(
            f"/api/v1/transactions/{int(posted.json()['id'])}",
            json={"category_id": _as_int(music["id"]), "learn": True},
        )
        assert patched.status_code == 200, patched.text

        rules = _rule_list(client)
        assert len(rules) == 1, rules
        assert rules[0]["description_pattern"] == "paypal xyz"
        assert rules[0]["category_id"] == _as_int(music["id"])
        assert rules[0]["is_learned"] is True
        assert Decimal(str(rules[0]["confidence"])) == Decimal("1.00")

    def test_rule_for_unknown_category_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A rule pointing at no category is refused, not stored dangling."""
        del engine, seeded
        response = client.post(
            "/api/v1/categories/rules",
            json={"description_pattern": "jumbo", "category_id": 999999},
        )
        assert response.status_code == 404, response.text

    def test_blank_pattern_is_unprocessable(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Whitespace-only is empty after the strip, and an empty pattern
        has nothing to match with."""
        del engine, seeded
        dining = _create_category(client, "Dining")
        response = client.post(
            "/api/v1/categories/rules",
            json={
                "description_pattern": "   ",
                "category_id": _as_int(dining["id"]),
            },
        )
        assert response.status_code == 422, response.text


class TestDeleteRule:
    def test_delete_by_id_removes_and_repeat_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Delete by id removes the row; deleting it again is a 404,
        because deleting nothing and reporting success would make a typo
        look like an edit."""
        del engine, seeded
        dining = _create_category(client, "Dining")
        rule = _create_rule(client, "jumbo", _as_int(dining["id"]))
        rule_id = _as_int(rule["id"])

        first = client.delete(f"/api/v1/categories/rules/{rule_id}")
        assert first.status_code == 200, first.text
        assert [row["description_pattern"] for row in _rule_list(client)] == []

        repeat = client.delete(f"/api/v1/categories/rules/{rule_id}")
        assert repeat.status_code == 404, repeat.text

    def test_delete_rule_whose_pattern_contains_a_slash(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A pattern with '/' lists fine but could never survive a
        single-segment path delete; by id it deletes like any other."""
        del engine, seeded
        dining = _create_category(client, "Dining")
        rule = _create_rule(client, "bakker/straat", _as_int(dining["id"]))
        rule_id = _as_int(rule["id"])

        patterns = [row["description_pattern"] for row in _rule_list(client)]
        assert "bakker/straat" in patterns

        deleted = client.delete(f"/api/v1/categories/rules/{rule_id}")
        assert deleted.status_code == 200, deleted.text
        assert [row["description_pattern"] for row in _rule_list(client)] == []

    def test_delete_unknown_rule_id_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """An id naming no row is a 404, not a silent success."""
        del engine, seeded
        response = client.delete("/api/v1/categories/rules/999999")
        assert response.status_code == 404, response.text


class TestRuleTieIn:
    def test_hand_rule_categorizes_the_next_manual_transaction(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The API rule store is the store the engine reads: a hand rule
        for "jumbo" categorizes the next matching manual transaction with
        no PATCH."""
        del engine
        dining = _create_category(client, "Dining")
        _create_rule(client, "jumbo", _as_int(dining["id"]))

        response = client.post(
            "/api/v1/transactions",
            json={
                "account_id": seeded["checking"],
                "description": "JUMBO 4321 AMSTERDAM",
                "amount": "-40.50",
                "currency": "EUR",
                "booked_date": BOOKED,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["category_id"] == _as_int(dining["id"])
