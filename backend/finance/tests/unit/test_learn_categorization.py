"""Learned-rule tests — what one correction teaches, as data and as a row.

`build_learned_rule` is pure: a description and a category in, the rule row's
shape out. `upsert_learned_rule` is the same shape against a database — one
row per pattern, updated in place, never duplicated.

The upsert half runs on an in-memory SQLite database, not Postgres, and that
is deliberate rather than convenient: the fast suite runs with no Docker, and
what is under test here is the query-then-update-or-insert logic, not a
Postgres trigger. `category_rule` carries no Postgres-only column type, so
the same statements run on both. Anything about triggers or migrations
belongs in the `db` suite, not here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from finance.api.categorize import (
    LEARNED_RULE_PRIORITY,
    build_learned_rule,
    upsert_learned_rule,
)
from finance.domain.models.taxonomy import CategoryRule as CategoryRuleRow


def _session() -> Session:
    """A session on a fresh in-memory database carrying only `category_rule`.

    The model names its schema (`finance.category_rule`), and SQLite knows
    no such database — so one is attached. An attached `:memory:` database
    lives as long as the engine's single pooled connection, which is the
    whole test.

    The table is created by hand rather than from the model, for one reason:
    the model's `id` renders as BIGINT, and SQLite generates ids only for
    INTEGER PRIMARY KEY. Every other column mirrors the model; a NOT NULL
    column added to the model without a default breaks this loudly, which is
    the signal that the mirror needs updating. The table's dangling foreign
    keys (to `account`, to `category`) are names only — SQLite neither
    resolves them at CREATE time nor enforces them by default, so inserting
    a rule needs no parent rows.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS finance"))
        connection.execute(
            text(
                "CREATE TABLE finance.category_rule ("
                "id INTEGER PRIMARY KEY, "
                "priority INTEGER DEFAULT 100 NOT NULL, "
                "account_id BIGINT, "
                "merchant_id BIGINT, "
                "description_pattern TEXT, "
                "category_id BIGINT NOT NULL, "
                "is_learned BOOLEAN DEFAULT false NOT NULL, "
                "confidence NUMERIC(3, 2) DEFAULT 1.00 NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
    return Session(engine)


class TestBuildLearnedRule:
    def test_pattern_is_the_lowercased_stable_prefix(self) -> None:
        """ "PAYPAL XYZ 1234" teaches "paypal xyz" — the order number is noise."""
        rule = build_learned_rule(description="PAYPAL XYZ 1234", category_id=7)
        assert rule.description_pattern == "paypal xyz"
        assert rule.category_id == 7

    def test_a_learned_rule_loses_to_any_hand_rule(self) -> None:
        """Priority 500 sorts after the hand-rule default of 100, and the
        engine matches ascending — so an explicit rule always outranks this."""
        rule = build_learned_rule(description="PAYPAL XYZ 1234", category_id=7)
        assert rule.is_learned is True
        assert rule.priority == LEARNED_RULE_PRIORITY == 500
        assert rule.confidence == Decimal("1.00")

    def test_an_empty_description_teaches_nothing(self) -> None:
        """A pattern of nothing would match everything, so refuse loudly."""
        with pytest.raises(ValueError, match="empty description"):
            build_learned_rule(description="   ", category_id=7)


class TestUpsertLearnedRule:
    def test_first_correction_inserts_the_row(self) -> None:
        session = _session()
        row = upsert_learned_rule(session, description="PAYPAL XYZ 1234", category_id=7)
        assert row.description_pattern == "paypal xyz"
        assert row.category_id == 7
        assert row.is_learned is True
        assert row.priority == LEARNED_RULE_PRIORITY
        assert Decimal(str(row.confidence)) == Decimal("1.00")

    def test_second_correction_updates_in_place(self) -> None:
        """Same payee, new category: one row, rewritten — never a duplicate."""
        session = _session()
        upsert_learned_rule(session, description="PAYPAL XYZ 1234", category_id=7)
        upsert_learned_rule(session, description="PAYPAL XYZ 9999", category_id=9)
        rows = session.scalars(select(CategoryRuleRow)).all()
        assert len(rows) == 1
        assert rows[0].description_pattern == "paypal xyz"
        assert rows[0].category_id == 9

    def test_a_different_payee_is_a_different_row(self) -> None:
        """Upsert deduplicates by pattern; it must not merge payees."""
        session = _session()
        upsert_learned_rule(session, description="PAYPAL XYZ 1234", category_id=7)
        upsert_learned_rule(session, description="ALBERT HEIJN 5", category_id=9)
        rows = session.scalars(
            select(CategoryRuleRow).order_by(CategoryRuleRow.id)
        ).all()
        assert [row.description_pattern for row in rows] == [
            "paypal xyz",
            "albert heijn",
        ]

    def test_a_hand_rule_with_the_same_text_is_left_alone(self) -> None:
        """The match is among `is_learned` rows only: a hand rule sharing the
        text is the user's explicit word and must not be rewritten."""
        session = _session()
        session.add(
            CategoryRuleRow(
                description_pattern="paypal xyz",
                category_id=3,
                priority=100,
                is_learned=False,
            )
        )
        session.flush()
        upsert_learned_rule(session, description="PAYPAL XYZ 1234", category_id=7)
        rows = session.scalars(
            select(CategoryRuleRow).order_by(CategoryRuleRow.id)
        ).all()
        assert len(rows) == 2
        assert rows[0].category_id == 3
        assert rows[0].is_learned is False
        assert rows[1].category_id == 7
        assert rows[1].is_learned is True
