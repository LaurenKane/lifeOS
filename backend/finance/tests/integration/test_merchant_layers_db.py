"""test_merchant_layers_db.py — layers 2-4 file through merchants and aliases.

The acceptance tests for LifeOS-8 layers 2-4: a stored alias (layer 2) and a
stored merchant (layer 3, now at auto confidence) categorize a manual
transaction on POST, while a merely fuzzy variant (layer 4, 0.60) stays
uncategorized — advisory, never imposed. The precedence test proves the
layer order through the write path: a hand rule beats an alias beats a
known merchant for the same description.

**Marked `db`.** The app is driven through HTTP and every assertion that
matters is read back from a second connection, so an uncommitted
transaction cannot satisfy them. Fixtures mirror
`test_learned_categorization_db.py` (same accounts, same payload shape)
because a categorized manual transaction is a manual transaction first.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text

from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME

pytestmark = pytest.mark.db

#: One fixed booked date, so no test depends on today.
BOOKED: Final[str] = "2026-10-01"

CHECKING_NAME: Final[str] = "Test current account"


def _payload(
    account_id: int,
    *,
    amount: str = "-12.50",
    description: str = "PAYPAL XYZ 9999",
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
    """EUR, a checking account, the system expense account, and Groceries."""
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
            groceries = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, is_system)"
                        " VALUES ('Groceries', 'expense', TRUE) RETURNING id"
                    )
                ).scalar_one()
            )
            music = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, is_system)"
                        " VALUES ('Music', 'expense', TRUE) RETURNING id"
                    )
                ).scalar_one()
            )
    return {"checking": checking, "groceries": groceries, "music": music}


def _as_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


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


class TestLayer2Alias:
    def test_alias_categorizes_and_leaves_the_queue(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A stored alias at 0.95 files the transaction without asking."""
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO finance.merchant_alias"
                        " (raw_string, category_id, confidence, merchant_id)"
                        " VALUES ('paypal xyz', :category, 0.95, NULL)"
                    ),
                    {"category": seeded["groceries"]},
                )
        created = _post(client, seeded["checking"], "PAYPAL XYZ 9999")
        assert created["category_id"] == seeded["groceries"]
        assert _as_int(created["id"]) not in _uncategorized_ids(client)


class TestLayer3KnownMerchant:
    def test_known_merchant_categorizes(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A hand-filed merchant clears the auto bar — this fails at 0.75.

        The write path persists only `is_auto` results, so a layer-3
        confidence below 0.90 would leave the line uncategorized and this
        assertion would go red.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO finance.merchant (name, category_id)"
                        " VALUES ('Albert Heijn', :category)"
                    ),
                    {"category": seeded["groceries"]},
                )
        created = _post(client, seeded["checking"], "ALBERT HEIJN 1234 AMSTERDAM")
        assert created["category_id"] == seeded["groceries"]
        assert _as_int(created["id"]) not in _uncategorized_ids(client)


class TestLayer4Advisory:
    def test_fuzzy_variant_stays_uncategorized(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Layer 4 proposes but does not impose: 0.60 never persists."""
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO finance.merchant (name, category_id)"
                        " VALUES ('Albert Heijn', :category)"
                    ),
                    {"category": seeded["groceries"]},
                )
        created = _post(client, seeded["checking"], "ALBERT HEJIN 1234")
        assert created["category_id"] is None
        assert _as_int(created["id"]) in _uncategorized_ids(client)


class TestLayerPrecedence:
    def test_hand_rule_beats_alias_beats_known_merchant(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """All three claim "PAYPAL XYZ 9999"; the hand rule (priority 100)
        wins over the alias, which wins over the known merchant.

        Asserted in two POSTs: with the rule present the rule's category
        lands; with the rule deleted the alias's category lands — proving
        the alias outranks layer 3 on the same description.
        """
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "INSERT INTO finance.category (name, kind, is_system)"
                        " VALUES ('Third', 'expense', FALSE)"
                    )
                )
                third = int(
                    connection.execute(
                        text("SELECT id FROM finance.category WHERE name = 'Third'")
                    ).scalar_one()
                )
                # Three inputs, three distinct categories: the hand rule points
                # at Music, the alias at Groceries, the merchant at Third.
                connection.execute(
                    text(
                        "INSERT INTO finance.merchant (name, category_id)"
                        " VALUES ('Paypal Xyz', :category)"
                    ),
                    {"category": third},
                )
                connection.execute(
                    text(
                        "INSERT INTO finance.merchant_alias"
                        " (raw_string, category_id, confidence, merchant_id)"
                        " VALUES ('paypal xyz', :category, 0.95, NULL)"
                    ),
                    {"category": seeded["groceries"]},
                )
                connection.execute(
                    text(
                        "INSERT INTO finance.category_rule"
                        " (description_pattern, category_id, priority)"
                        " VALUES ('paypal', :category, 100)"
                    ),
                    {"category": seeded["music"]},
                )
        first = _post(client, seeded["checking"], "PAYPAL XYZ 9999")
        assert first["category_id"] == seeded["music"]

        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("DELETE FROM finance.category_rule"))
        second = _post(client, seeded["checking"], "PAYPAL XYZ 8888")
        assert second["category_id"] == seeded["groceries"]
