"""test_budgets_db.py — budgets as real database rows.

LifeOS-8bo gives the budgets page a backend: `finance.budget` plus the four
verbs its provider reads through. Every test drives HTTP and reads the stored
row back from a second connection — including the CHECK's own refusal, which
is the authority on what a limit may be (see `BudgetCreateRequest`).

**Marked `db`.** Fixtures mirror `test_categories_db.py`: same throwaway
database, same client construction, same "create over HTTP, assert over SQL"
split.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from main import create_app
from sqlalchemy import Engine, text

from finance.db import get_engine, get_sessionmaker

pytestmark = pytest.mark.db


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
def seeded(engine: Engine) -> None:
    """EUR, committed. A budget's currency must exist in `finance.currency`."""
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


def _as_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _detail(response: Response) -> str:
    """The response's `detail` string, asserted to be one."""
    body = response.json()
    assert isinstance(body, dict), body
    detail = body["detail"]
    assert isinstance(detail, str), detail
    return detail


def _create_category(client: TestClient, name: str) -> dict[str, object]:
    """POST one expense category. Asserts the create; returns the body."""
    response = client.post("/api/v1/categories", json={"name": name, "kind": "expense"})
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


def _create_budget(
    client: TestClient,
    category_id: int,
    *,
    amount_minor: int = 25_000,
    currency: str = "EUR",
    period: str = "monthly",
) -> dict[str, object]:
    """POST one budget. Asserts the create; returns the decoded body."""
    response = client.post(
        "/api/v1/budgets",
        json={
            "category_id": category_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "period": period,
        },
    )
    assert response.status_code == 201, response.text
    decoded = response.json()
    assert isinstance(decoded, dict), decoded
    return decoded


def _budget_list(client: TestClient) -> list[dict[str, object]]:
    """Every stored budget, as the budgets page reads them."""
    response = client.get("/api/v1/budgets")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list), body
    budgets: list[dict[str, object]] = []
    for row in body:
        assert isinstance(row, dict), row
        budgets.append(row)
    return budgets


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


class TestBudgetRoundTrip:
    def test_create_list_patch_delete(
        self, client: TestClient, engine: Engine, seeded: None
    ) -> None:
        """Create 201 -> list -> patch 200 -> delete 204, row verified in SQL."""
        del seeded
        category = _create_category(client, "Groceries")
        category_id = _as_int(category["id"])

        created = _create_budget(client, category_id, amount_minor=25_000)
        budget_id = _as_int(created["id"])
        assert budget_id >= 1
        # The label is the CATEGORY's name: budgets store no second copy.
        assert created["name"] == "Groceries"
        assert created["category_id"] == category_id
        # The camelCase wire name the frontend's BudgetSchema parses, with no
        # snake_case twin (the alias replaces it, same as `sourceFilename`).
        assert created["amountMinor"] == 25_000
        assert "amount_minor" not in created
        assert created["currency"] == "EUR"
        assert created["period"] == "monthly"

        listed = _budget_list(client)
        assert [_as_int(row["id"]) for row in listed] == [budget_id]

        stored = _rows(
            engine,
            "SELECT category_id, amount, currency, period"
            " FROM finance.budget WHERE id = :id",
            id=budget_id,
        )
        assert stored == [(category_id, 25_000, "EUR", "monthly")]

        patched = client.patch(
            f"/api/v1/budgets/{budget_id}",
            json={"amount_minor": 30_000, "period": "yearly"},
        )
        assert patched.status_code == 200, patched.text
        body = patched.json()
        assert body["amountMinor"] == 30_000
        assert body["period"] == "yearly"
        assert body["name"] == "Groceries"
        assert body["id"] == budget_id

        # A PATCH naming two fields changes exactly those two; the category
        # and the currency are untouched.
        reloaded = _rows(
            engine,
            "SELECT amount, period, category_id, currency"
            " FROM finance.budget WHERE id = :id",
            id=budget_id,
        )
        assert reloaded == [(30_000, "yearly", category_id, "EUR")]

        deleted = client.delete(f"/api/v1/budgets/{budget_id}")
        assert deleted.status_code == 204, deleted.text
        assert _budget_list(client) == []

        repeat = client.delete(f"/api/v1/budgets/{budget_id}")
        assert repeat.status_code == 404, repeat.text

    def test_two_budgets_list_side_by_side(
        self, client: TestClient, seeded: None
    ) -> None:
        """Each row is labelled by its own category, in category-name order."""
        del seeded
        groceries = _create_category(client, "Groceries")
        fun = _create_category(client, "Fun")
        first = _create_budget(client, _as_int(groceries["id"]))
        second = _create_budget(client, _as_int(fun["id"]), period="yearly")

        listed = _budget_list(client)
        assert [(row["name"], row["period"]) for row in listed] == [
            ("Fun", "yearly"),
            ("Groceries", "monthly"),
        ]
        assert {_as_int(row["id"]) for row in listed} == {
            _as_int(first["id"]),
            _as_int(second["id"]),
        }


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class TestCreateRefusals:
    def test_unknown_category_is_not_found(
        self, client: TestClient, seeded: None
    ) -> None:
        """A budget pointing at no category is a client mistake, not a 500
        from the foreign key."""
        del seeded
        response = client.post(
            "/api/v1/budgets",
            json={
                "category_id": 999999,
                "amount_minor": 25_000,
                "currency": "EUR",
                "period": "monthly",
            },
        )
        assert response.status_code == 404, response.text

    def test_zero_limit_is_refused_by_the_check(
        self, client: TestClient, seeded: None
    ) -> None:
        """Zero is a broken budget, and the CONSTRAINT says so: the 422 comes
        from `ck_budget_amount_positive`, not from an edge pre-check."""
        del seeded
        category = _create_category(client, "Groceries")
        response = client.post(
            "/api/v1/budgets",
            json={
                "category_id": _as_int(category["id"]),
                "amount_minor": 0,
                "currency": "EUR",
                "period": "monthly",
            },
        )
        assert response.status_code == 422, response.text
        assert "ck_budget_amount_positive" in _detail(response)

    def test_negative_limit_is_refused_by_the_check(
        self, client: TestClient, seeded: None
    ) -> None:
        """A negative limit is not a budget with a smaller allowance."""
        del seeded
        category = _create_category(client, "Groceries")
        response = client.post(
            "/api/v1/budgets",
            json={
                "category_id": _as_int(category["id"]),
                "amount_minor": -1,
                "currency": "EUR",
                "period": "monthly",
            },
        )
        assert response.status_code == 422, response.text
        assert "ck_budget_amount_positive" in _detail(response)
        assert _budget_list(client) == []

    def test_unknown_period_is_unprocessable(
        self, client: TestClient, seeded: None
    ) -> None:
        """The closed period set fails at the edge, before any write."""
        del seeded
        category = _create_category(client, "Groceries")
        response = client.post(
            "/api/v1/budgets",
            json={
                "category_id": _as_int(category["id"]),
                "amount_minor": 25_000,
                "currency": "EUR",
                "period": "fortnightly",
            },
        )
        assert response.status_code == 422, response.text
        assert _budget_list(client) == []

    def test_unknown_currency_is_unprocessable(
        self, client: TestClient, seeded: None
    ) -> None:
        """`currency.decimals` is the authority on minor units, so the row
        must exist in the `currency` table — 422, not an FK violation."""
        del seeded
        category = _create_category(client, "Groceries")
        response = client.post(
            "/api/v1/budgets",
            json={
                "category_id": _as_int(category["id"]),
                "amount_minor": 25_000,
                "currency": "USD",
                "period": "monthly",
            },
        )
        assert response.status_code == 422, response.text
        assert "USD" in _detail(response)
        assert _budget_list(client) == []


class TestPatchAndDeleteRefusals:
    def test_patch_unknown_budget_is_not_found(
        self, client: TestClient, seeded: None
    ) -> None:
        """An id naming no row is 404, not a silent success."""
        del seeded
        response = client.patch("/api/v1/budgets/999999", json={"amount_minor": 30_000})
        assert response.status_code == 404, response.text

    def test_patch_zero_limit_is_refused_by_the_check(
        self, client: TestClient, engine: Engine, seeded: None
    ) -> None:
        """The CHECK guards UPDATE as well as INSERT: an edit cannot lower a
        limit to zero, and the refusal leaves the stored limit untouched."""
        del seeded
        category = _create_category(client, "Groceries")
        created = _create_budget(client, _as_int(category["id"]))
        budget_id = _as_int(created["id"])

        response = client.patch(
            f"/api/v1/budgets/{budget_id}", json={"amount_minor": 0}
        )
        assert response.status_code == 422, response.text
        assert "ck_budget_amount_positive" in _detail(response)

        stored = _rows(
            engine,
            "SELECT amount FROM finance.budget WHERE id = :id",
            id=budget_id,
        )
        assert stored == [(25_000,)]

    def test_explicit_null_cannot_clear_a_field(
        self, client: TestClient, seeded: None
    ) -> None:
        """PATCH distinguishes absent from null — and nothing here is
        clearable, so null is a 422 rather than a clear."""
        del seeded
        category = _create_category(client, "Groceries")
        created = _create_budget(client, _as_int(category["id"]))

        response = client.patch(
            f"/api/v1/budgets/{_as_int(created['id'])}",
            json={"amount_minor": None},
        )
        assert response.status_code == 422, response.text
        assert "amount_minor" in _detail(response)

    def test_category_id_is_not_editable(
        self, client: TestClient, seeded: None
    ) -> None:
        """Retargeting is a different budget; `extra="forbid"` refuses it at
        the edge rather than half-applying it."""
        del seeded
        other = _create_category(client, "Fun")
        category = _create_category(client, "Groceries")
        created = _create_budget(client, _as_int(category["id"]))

        response = client.patch(
            f"/api/v1/budgets/{_as_int(created['id'])}",
            json={"category_id": _as_int(other["id"])},
        )
        assert response.status_code == 422, response.text

    def test_patch_unknown_period_is_unprocessable(
        self, client: TestClient, seeded: None
    ) -> None:
        """The closed period set applies to PATCH as much as to create."""
        del seeded
        category = _create_category(client, "Groceries")
        created = _create_budget(client, _as_int(category["id"]))

        response = client.patch(
            f"/api/v1/budgets/{_as_int(created['id'])}",
            json={"period": "fortnightly"},
        )
        assert response.status_code == 422, response.text

    def test_delete_unknown_budget_is_not_found(
        self, client: TestClient, seeded: None
    ) -> None:
        """Deleting nothing and reporting success would make a typo look
        like an edit."""
        del seeded
        response = client.delete("/api/v1/budgets/999999")
        assert response.status_code == 404, response.text
