"""categories router — category tree and the rules that feed it.

Categories are a closed set of four kinds (section I). `kind` drives sign
convention and budget eligibility, so it is not free text: a category whose kind
is unclear cannot be budgeted or reported on.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from finance.api.schemas import CategorySummary
from finance.domain.services.categorize import CategoryRule
from finance.public import CategoryKind

router = APIRouter(tags=["finance"], prefix="/categories")

_CATEGORIES: list[CategorySummary] = []
_RULES: list[CategoryRule] = []


@router.get("", summary="List categories", response_model=list[CategorySummary])
def list_categories(kind: CategoryKind | None = None) -> list[CategorySummary]:
    """Every category, optionally filtered by kind."""
    if kind is None:
        return list(_CATEGORIES)
    return [c for c in _CATEGORIES if c.kind == kind]


@router.post("", summary="Create a category", response_model=CategorySummary)
def create_category(category: CategorySummary) -> CategorySummary:
    """Create a user category. `is_system` rows are seeded and read-only."""
    return category


@router.get(
    "/kinds", summary="Supported category kinds", response_model=list[CategoryKind]
)
def list_category_kinds() -> list[CategoryKind]:
    """The closed kind list, for populating a dropdown."""
    return list(CategoryKind)


@router.get(
    "/rules", summary="List categorization rules", response_model=list[CategoryRule]
)
def list_rules() -> list[CategoryRule]:
    """Every rule, priority-ordered.

    Returned whole and in order on purpose: section I requires the entire rule
    set to be readable on one screen and editable by hand. A paginated endpoint
    would make that impossible.
    """
    return sorted(_RULES, key=lambda rule: (rule.priority, rule.description_pattern))


@router.post(
    "/rules", summary="Create a categorization rule", response_model=CategoryRule
)
def create_rule(rule: CategoryRule) -> CategoryRule:
    """Add a rule.

    The pattern is a plain substring plus a priority integer, deliberately not a
    regex and not a DSL: a rule the user cannot read is a rule they will not fix.
    """
    return rule


@router.delete("/rules/{pattern}", summary="Delete a rule")
def delete_rule(pattern: str) -> dict[str, str]:
    """Remove a rule by its description pattern.

    Raises:
        HTTPException: 404 when no rule has that pattern.
    """
    for index, rule in enumerate(_RULES):
        if rule.description_pattern == pattern:
            del _RULES[index]
            return {"status": "deleted", "description_pattern": pattern}
    raise HTTPException(status_code=404, detail=f"No rule matching {pattern!r}")
