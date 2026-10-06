"""Category schema tests — the edge refuses what the database must not see.

A blank rule pattern and an unknown kind are both request-shape mistakes,
so they fail in validation (422 on the wire) rather than as constraint or
CHECK violations from inside a transaction. Synthetic data only; no
database.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from finance.api.schemas import CategoryCreateRequest, CategoryRuleCreateRequest
from finance.public import CategoryKind


class TestCategoryRuleCreateRequest:
    def test_blank_pattern_is_rejected(self) -> None:
        """Whitespace-only strips to empty, and empty matches nothing."""
        with pytest.raises(ValidationError):
            CategoryRuleCreateRequest(description_pattern="   ", category_id=1)

    def test_priority_defaults_to_the_hand_rule_tier(self) -> None:
        """100 sorts before learned rules' 500, so hand beats learned."""
        rule = CategoryRuleCreateRequest(description_pattern="jumbo", category_id=1)
        assert rule.priority == 100

    def test_non_positive_category_is_rejected(self) -> None:
        """A ledger id counts from 1; zero names no row."""
        with pytest.raises(ValidationError):
            CategoryRuleCreateRequest(description_pattern="jumbo", category_id=0)


class TestCategoryCreateRequest:
    def test_unknown_kind_is_rejected(self) -> None:
        """The closed enum fails before the migration's CHECK ever sees it."""
        with pytest.raises(ValidationError):
            CategoryCreateRequest(name="Hobbies", kind="bogus")  # type: ignore[arg-type]

    def test_blank_name_is_rejected(self) -> None:
        """A category with no name is a label for nothing."""
        with pytest.raises(ValidationError):
            CategoryCreateRequest(name="  ", kind=CategoryKind.EXPENSE)

    def test_parent_defaults_to_absent(self) -> None:
        """No parent means a root, not a zero id."""
        request = CategoryCreateRequest(name="Hobbies", kind=CategoryKind.EXPENSE)
        assert request.parent_id is None
