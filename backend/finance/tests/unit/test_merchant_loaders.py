"""Loader tests — aliases and known merchants as the engine reads them.

`load_aliases` and `load_known_merchants` are pure projections over two
tables, like `load_rules`: the query-then-shape logic, not a Postgres
trigger. They run on an in-memory SQLite database, deliberately — the same
reason `test_learn_categorization.py` does. The fast suite runs with no
Docker, and `merchant` / `merchant_alias` carry no Postgres-only column type,
so the same statements run on both. Anything about the migration itself
belongs in the `db` suite, not here.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from finance.domain.models.taxonomy import Merchant as MerchantRow
from finance.domain.models.taxonomy import MerchantAlias as MerchantAliasRow
from finance.ingestion.rules import load_aliases, load_known_merchants


def _session() -> Session:
    """A session on a fresh in-memory database carrying merchant tables.

    The models name their schema (`finance.merchant`), and SQLite knows no
    such database — so one is attached. Tables are created by hand rather
    than from the models, for the same reason `test_learn_categorization`
    does: the model's `id` renders as BIGINT, and SQLite generates ids only
    for INTEGER PRIMARY KEY. Every other column mirrors the model; a NOT NULL
    column added to the model without a default breaks this loudly, which is
    the signal that the mirror needs updating.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS finance"))
        connection.execute(
            text(
                "CREATE TABLE finance.merchant ("
                "id INTEGER PRIMARY KEY, "
                "name TEXT NOT NULL UNIQUE, "
                "category_id BIGINT, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE finance.merchant_alias ("
                "id INTEGER PRIMARY KEY, "
                "merchant_id BIGINT, "
                "raw_string TEXT NOT NULL, "
                "confidence NUMERIC(3, 2) DEFAULT 0.50 NOT NULL, "
                "usage_count INTEGER DEFAULT 0 NOT NULL, "
                "last_seen DATE DEFAULT CURRENT_DATE NOT NULL, "
                "category_id BIGINT)"
            )
        )
    return Session(engine)


class TestLoadAliases:
    def test_returns_only_categorized_rows_ordered_by_raw_string(self) -> None:
        """A category-less alias cannot feed layer 2, so it is skipped."""
        session = _session()
        session.add_all(
            [
                MerchantAliasRow(raw_string="zeta shop", category_id=7),
                MerchantAliasRow(raw_string="alpha shop", category_id=8),
                MerchantAliasRow(raw_string="no category row", category_id=None),
            ]
        )
        session.flush()
        aliases = load_aliases(session)
        assert [alias.raw_string for alias in aliases] == ["alpha shop", "zeta shop"]
        assert [alias.category_id for alias in aliases] == [8, 7]

    def test_confidence_is_carried_through_verbatim(self) -> None:
        """Layer 2 uses the STORED confidence for its `is_auto` decision."""
        session = _session()
        session.add(
            MerchantAliasRow(
                raw_string="paypal xyz", category_id=5, confidence=Decimal("0.95")
            )
        )
        session.flush()
        aliases = load_aliases(session)
        assert len(aliases) == 1
        assert aliases[0].raw_string == "paypal xyz"
        assert aliases[0].category_id == 5
        assert Decimal(str(aliases[0].confidence)) == Decimal("0.95")

    def test_empty_table_is_empty(self) -> None:
        assert load_aliases(_session()) == []


class TestLoadKnownMerchants:
    def test_returns_lowercased_names_and_skips_uncategorized(self) -> None:
        """The engine matches on the lowercased name substring."""
        session = _session()
        session.add_all(
            [
                MerchantRow(name="Albert Heijn", category_id=10),
                MerchantRow(name="JUMBO", category_id=11),
                MerchantRow(name="Unfiled Shop", category_id=None),
            ]
        )
        session.flush()
        assert load_known_merchants(session) == {"albert heijn": 10, "jumbo": 11}

    def test_empty_table_is_empty(self) -> None:
        assert load_known_merchants(_session()) == {}
