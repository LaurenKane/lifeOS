"""Budget schema tests — the edge refuses what the shape gets wrong.

An unknown period and a malformed currency are request-shape mistakes, so they
fail in validation (422 on the wire) rather than as a CHECK violation from
inside a transaction — the same split `test_category_schemas.py` draws.

The amount is deliberately NOT bounded here. The `amount > 0` CHECK in
migration 0007 is the authority, and `routes/budgets.py` translates its
refusal to 422; `test_non_positive_amount_reaches_the_database` below is the
tripwire that keeps a well-meaning `gt=0` from quietly turning that translation
into dead code. Synthetic data only; no database.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from finance.api.schemas import BudgetCreateRequest, BudgetUpdateRequest


class TestBudgetCreateRequest:
    def test_unknown_period_is_rejected(self) -> None:
        """The closed period set fails before the migration's CHECK sees it."""
        with pytest.raises(ValidationError):
            BudgetCreateRequest(
                category_id=1,
                amount_minor=1000,
                period="fortnightly",  # type: ignore[arg-type]
            )

    def test_currency_defaults_to_eur(self) -> None:
        """EUR is this user's base currency; a budget need not restate it."""
        request = BudgetCreateRequest(
            category_id=1, amount_minor=1000, period="monthly"
        )
        assert request.currency == "EUR"

    def test_malformed_currency_is_rejected(self) -> None:
        """Three UPPERCASE letters or nothing — the shape of an ISO 4217 code."""
        with pytest.raises(ValidationError):
            BudgetCreateRequest(
                category_id=1,
                amount_minor=1000,
                currency="eur",
                period="monthly",
            )

    def test_non_positive_category_is_rejected(self) -> None:
        """A ledger id counts from 1; zero names no row."""
        with pytest.raises(ValidationError):
            BudgetCreateRequest(category_id=0, amount_minor=1000, period="monthly")

    def test_non_positive_amount_reaches_the_database(self) -> None:
        """Zero and negative limits are the CONSTRAINT's refusal, not the edge's.

        A schema that pre-validated `gt=0` would answer this test with a
        `ValidationError` — and the router's CHECK-to-422 translation would be
        a branch no request could ever reach.
        """
        zero = BudgetCreateRequest(category_id=1, amount_minor=0, period="monthly")
        negative = BudgetCreateRequest(category_id=1, amount_minor=-1, period="monthly")
        assert zero.amount_minor == 0
        assert negative.amount_minor == -1


class TestBudgetUpdateRequest:
    def test_every_field_defaults_to_absent(self) -> None:
        """Absent means "leave alone", and `model_fields_set` is how PATCH knows."""
        request = BudgetUpdateRequest()
        assert request.model_fields_set == set()

    def test_explicit_null_is_accepted_by_the_schema(self) -> None:
        """The schema admits the mistake so the ROUTER can refuse it.

        Nothing on a budget is clearable, and the difference between an
        omitted field and a null has to survive validation for the router to
        tell them apart.
        """
        request = BudgetUpdateRequest(amount_minor=None)
        assert "amount_minor" in request.model_fields_set
        assert request.amount_minor is None

    def test_unknown_period_is_rejected(self) -> None:
        """The closed period set applies to PATCH as much as to create."""
        with pytest.raises(ValidationError):
            BudgetUpdateRequest(period="fortnightly")  # type: ignore[arg-type]

    def test_category_id_is_not_editable(self) -> None:
        """Retargeting is a different budget — and `extra="forbid"` says so."""
        with pytest.raises(ValidationError):
            BudgetUpdateRequest(category_id=2)  # type: ignore[call-arg]
