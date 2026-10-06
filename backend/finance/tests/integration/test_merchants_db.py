"""test_merchants_db.py — merchant and alias curation as real database rows.

The curation lane of LifeOS-8: layers 2-4 match on rows somebody has to file,
and these endpoints are how. Every test drives HTTP and reads the ledger
facts back from a second connection. The tie-in tests at the end prove the
point of the whole lane: a merchant or alias filed here is read by the
matcher on the next manual transaction.

**Marked `db`.** Fixtures mirror `test_categories_db.py`: same throwaway
database, same manual-transaction shape for the tie-in tests.
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


def _create_category(client: TestClient, name: str) -> dict[str, object]:
    """POST one expense category. Asserts the create; returns the body."""
    response = client.post("/api/v1/categories", json={"name": name, "kind": "expense"})
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


def _create_merchant(
    client: TestClient, name: str, category_id: int | None = None
) -> dict[str, object]:
    """POST one merchant. Asserts the create; returns the decoded body."""
    body: dict[str, object] = {"name": name}
    if category_id is not None:
        body["category_id"] = category_id
    response = client.post("/api/v1/merchants", json=body)
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


def _create_alias(client: TestClient, body: dict[str, object]) -> dict[str, object]:
    """POST one merchant alias. Asserts the create; returns the body."""
    response = client.post("/api/v1/merchant-aliases", json=body)
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


# ---------------------------------------------------------------------------
# Merchants
# ---------------------------------------------------------------------------


class TestMerchants:
    def test_create_with_category_lists_it(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A filed merchant is a row: it lists with its category."""
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        created = _create_merchant(client, "Albert Heijn", _as_int(groceries["id"]))
        assert _as_int(created["id"]) >= 1
        assert created["name"] == "Albert Heijn"
        assert created["category_id"] == _as_int(groceries["id"])

        response = client.get("/api/v1/merchants")
        assert response.status_code == 200, response.text
        names = [row["name"] for row in response.json()]
        assert "Albert Heijn" in names

    def test_create_without_category_is_unfiled(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """No category is NULL, not a guess: the row feeds nothing."""
        del engine, seeded
        created = _create_merchant(client, "Some Shop")
        assert created["category_id"] is None

    def test_duplicate_name_conflicts(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The `name` constraint decides duplicates, and one is a 409."""
        del engine, seeded
        _create_merchant(client, "Albert Heijn")
        repeat = client.post("/api/v1/merchants", json={"name": "Albert Heijn"})
        assert repeat.status_code == 409, repeat.text

    def test_unknown_category_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A merchant pointing at no category is refused, not stored dangling."""
        del engine, seeded
        response = client.post(
            "/api/v1/merchants", json={"name": "Albert Heijn", "category_id": 999999}
        )
        assert response.status_code == 404, response.text

    def test_blank_name_is_unprocessable(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A blank name fails at the edge, before any write."""
        del engine, seeded
        response = client.post("/api/v1/merchants", json={"name": "   "})
        assert response.status_code == 422, response.text

    def test_patch_sets_and_clears_the_category(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """PATCH files the merchant; an explicit null unfiles it again.

        The null must be EXPLICIT: this is what `model_fields_set` buys over
        a PUT, and what makes "unfile" statable at all.
        """
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        created = _create_merchant(client, "Albert Heijn")
        merchant_id = _as_int(created["id"])
        assert created["category_id"] is None

        filed = client.patch(
            f"/api/v1/merchants/{merchant_id}",
            json={"category_id": _as_int(groceries["id"])},
        )
        assert filed.status_code == 200, filed.text
        assert filed.json()["category_id"] == _as_int(groceries["id"])

        cleared = client.patch(
            f"/api/v1/merchants/{merchant_id}", json={"category_id": None}
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["category_id"] is None

    def test_patch_absent_field_leaves_the_category(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A PATCH that names only `name` must not touch the category."""
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        created = _create_merchant(client, "Albert Heijn", _as_int(groceries["id"]))
        merchant_id = _as_int(created["id"])

        renamed = client.patch(
            f"/api/v1/merchants/{merchant_id}", json={"name": "Albert Heijn BV"}
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Albert Heijn BV"
        assert renamed.json()["category_id"] == _as_int(groceries["id"])

    def test_patch_unknown_merchant_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        response = client.patch("/api/v1/merchants/999999", json={"name": "Nope"})
        assert response.status_code == 404, response.text

    def test_patch_duplicate_name_conflicts(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        _create_merchant(client, "Albert Heijn")
        other = _create_merchant(client, "Jumbo")
        response = client.patch(
            f"/api/v1/merchants/{_as_int(other['id'])}",
            json={"name": "Albert Heijn"},
        )
        assert response.status_code == 409, response.text

    def test_delete_then_repeat_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        created = _create_merchant(client, "Albert Heijn")
        first = client.delete(f"/api/v1/merchants/{_as_int(created['id'])}")
        assert first.status_code == 204, first.text
        again = client.delete(f"/api/v1/merchants/{_as_int(created['id'])}")
        assert again.status_code == 404, again.text

    def test_deleting_a_merchant_removes_its_aliases(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The FK says ON DELETE CASCADE; this proves it through the API."""
        del seeded
        created = _create_merchant(client, "Albert Heijn")
        merchant_id = _as_int(created["id"])
        alias = _create_alias(
            client, {"raw_string": "ah 1234", "merchant_id": merchant_id}
        )
        assert client.delete(f"/api/v1/merchants/{merchant_id}").status_code == 204
        remaining = _rows(
            engine,
            "SELECT count(*) FROM finance.merchant_alias WHERE id = :id",
            id=_as_int(alias["id"]),
        )
        assert remaining == [(0,)]


# ---------------------------------------------------------------------------
# Merchant aliases
# ---------------------------------------------------------------------------


class TestMerchantAliases:
    def test_create_lists_with_default_full_confidence(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A curated alias is deliberate: confidence defaults to 1.00, which
        clears layer 2's `is_auto` bar — asserted on the stored row."""
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        created = _create_alias(
            client,
            {"raw_string": "paypal xyz", "category_id": _as_int(groceries["id"])},
        )
        assert created["raw_string"] == "paypal xyz"
        assert created["category_id"] == _as_int(groceries["id"])
        assert Decimal(str(created["confidence"])) == Decimal("1.00")

        response = client.get("/api/v1/merchant-aliases")
        assert response.status_code == 200, response.text
        raws = [row["raw_string"] for row in response.json()]
        assert "paypal xyz" in raws

    def test_duplicate_raw_string_conflicts(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        _create_alias(
            client,
            {"raw_string": "paypal xyz", "category_id": _as_int(groceries["id"])},
        )
        repeat = client.post(
            "/api/v1/merchant-aliases",
            json={
                "raw_string": "paypal xyz",
                "category_id": _as_int(groceries["id"]),
            },
        )
        assert repeat.status_code == 409, repeat.text

    def test_unknown_merchant_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        response = client.post(
            "/api/v1/merchant-aliases",
            json={"raw_string": "ah 1234", "merchant_id": 999999},
        )
        assert response.status_code == 404, response.text

    def test_unknown_category_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        response = client.post(
            "/api/v1/merchant-aliases",
            json={"raw_string": "ah 1234", "category_id": 999999},
        )
        assert response.status_code == 404, response.text

    def test_blank_raw_string_is_unprocessable(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        response = client.post(
            "/api/v1/merchant-aliases",
            json={"raw_string": "   ", "category_id": _as_int(groceries["id"])},
        )
        assert response.status_code == 422, response.text

    def test_neither_target_is_unprocessable(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """An alias pointing at neither is a string matching nothing."""
        del engine, seeded
        response = client.post(
            "/api/v1/merchant-aliases", json={"raw_string": "ah 1234"}
        )
        assert response.status_code == 422, response.text

    def test_merchant_category_copies_onto_the_alias(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Curating a merchant and its alias in one step files both: the
        alias inherits the merchant's category when its own is absent."""
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        merchant = _create_merchant(client, "Albert Heijn", _as_int(groceries["id"]))
        created = _create_alias(
            client,
            {"raw_string": "ah 1234", "merchant_id": _as_int(merchant["id"])},
        )
        assert created["category_id"] == _as_int(groceries["id"])
        assert created["merchant_id"] == _as_int(merchant["id"])

    def test_patch_and_delete_alias(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        groceries = _create_category(client, "Groceries")
        music = _create_category(client, "Music")
        created = _create_alias(
            client,
            {"raw_string": "paypal xyz", "category_id": _as_int(groceries["id"])},
        )
        alias_id = _as_int(created["id"])

        patched = client.patch(
            f"/api/v1/merchant-aliases/{alias_id}",
            json={"category_id": _as_int(music["id"]), "confidence": "0.50"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["category_id"] == _as_int(music["id"])
        assert Decimal(str(patched.json()["confidence"])) == Decimal("0.50")

        assert client.delete(f"/api/v1/merchant-aliases/{alias_id}").status_code == 204
        again = client.delete(f"/api/v1/merchant-aliases/{alias_id}")
        assert again.status_code == 404, again.text

    def test_patch_unknown_alias_is_not_found(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        response = client.patch(
            "/api/v1/merchant-aliases/999999", json={"confidence": "0.50"}
        )
        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# End-to-end tie-in: the API writes rows the matcher actually reads
# ---------------------------------------------------------------------------


class TestCurationReachesTheMatcher:
    def test_a_curated_merchant_auto_categorizes_layer3(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """File "Albert Heijn" -> Groceries through the API; a manual
        transaction containing the name arrives already Groceries."""
        del engine
        groceries = _create_category(client, "Groceries")
        _create_merchant(client, "Albert Heijn", _as_int(groceries["id"]))

        posted = client.post(
            "/api/v1/transactions",
            json={
                "account_id": seeded["checking"],
                "description": "ALBERT HEIJN 1234 AMSTERDAM",
                "amount": "-12.50",
                "currency": "EUR",
                "booked_date": BOOKED,
            },
        )
        assert posted.status_code == 201, posted.text
        assert posted.json()["category_id"] == _as_int(groceries["id"])

    def test_a_curated_alias_auto_categorizes_layer2(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """File an alias through the API; a transaction containing the alias
        text arrives already categorized — at default 1.00 confidence, so
        the write path's `is_auto` bar is cleared with nothing else set."""
        del engine
        groceries = _create_category(client, "Groceries")
        _create_alias(
            client,
            {"raw_string": "paypal xyz", "category_id": _as_int(groceries["id"])},
        )

        posted = client.post(
            "/api/v1/transactions",
            json={
                "account_id": seeded["checking"],
                "description": "PAYPAL XYZ 9999",
                "amount": "-12.50",
                "currency": "EUR",
                "booked_date": BOOKED,
            },
        )
        assert posted.status_code == 201, posted.text
        assert posted.json()["category_id"] == _as_int(groceries["id"])
