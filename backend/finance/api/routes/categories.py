"""categories router — category tree and the rules that feed it.

Categories are a closed set of four kinds (section I). `kind` drives sign
convention and budget eligibility, so it is not free text: a category whose kind
is unclear cannot be budgeted or reported on.

Both tables are read and written here, against the database — no in-memory
lists. The rule set is plain text by design: `GET /rules` returns every rule,
hand and learned, in engine order, so the screen that edits them reads the
same rows the matcher matches on.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from finance.api.deps import get_session
from finance.api.schemas import (
    CategoryCreateRequest,
    CategoryRuleCreateRequest,
    CategoryRuleSummary,
    CategorySummary,
)
from finance.domain.models.taxonomy import Category
from finance.domain.models.taxonomy import CategoryRule as CategoryRuleRow
from finance.public import CategoryKind

router = APIRouter(tags=["finance"], prefix="/categories")

#: The dependency, annotated rather than declared as a default argument: this
#: project runs ruff with `B` selected, and `Depends(...)` in a default value is
#: exactly the pattern that rule exists for. Same idiom as
#: `routes/transactions.py`.
SessionDep = Annotated[Session, Depends(get_session)]

#: SQLSTATE 23505, `unique_violation`. On this router that is exactly one
#: thing: the `(parent_id, name)` uniqueness on `category`. Restated here
#: rather than imported, because a handler that reads the constant out of
#: the trigger body agrees with every value of it.
UNIQUE_VIOLATION = "23505"


def _summary_category(row: Category) -> CategorySummary:
    """One `category` row as the frontend already reads it.

    `parent_id` is included so the UI can draw the tree: a root carries
    None, a child carries its parent's id.
    """
    return CategorySummary(
        id=row.id,
        name=row.name,
        kind=CategoryKind(row.kind),
        is_system=row.is_system,
        parent_id=row.parent_id,
    )


def _summary_rule(row: CategoryRuleRow) -> CategoryRuleSummary:
    """One `category_rule` row, hand or learned, in engine order downstream."""
    return CategoryRuleSummary(
        id=row.id,
        description_pattern=row.description_pattern,
        priority=row.priority,
        category_id=row.category_id,
        is_learned=row.is_learned,
        confidence=row.confidence,
    )


def _duplicate_as_conflict(exc: DBAPIError) -> HTTPException | None:
    """Map a duplicate category to 409, or None if it is not one.

    A pre-check `SELECT` would still race a concurrent insert, so the
    uniqueness is enforced by the constraint and read back here: the
    database is the authority on duplicates, and this only translates its
    answer. Anything that is not a unique violation comes back as None so
    the caller re-raises — a server fault dressed as a 409 would tell the
    client its request was wrong when it was not.
    """
    origin = exc.orig if exc.orig is not None else exc
    code = getattr(origin, "sqlstate", None)
    if not isinstance(code, str):
        code = getattr(origin, "pgcode", None)
    if code != UNIQUE_VIOLATION:
        return None
    message = str(origin).splitlines()[0]
    return HTTPException(
        status_code=http_status.HTTP_409_CONFLICT,
        detail=f"A category with that parent and name already exists: {message}",
    )


@router.get("", summary="List categories", response_model=list[CategorySummary])
def list_categories(
    session: SessionDep,
    kind: CategoryKind | None = None,
) -> list[CategorySummary]:
    """Every category, optionally filtered by kind."""
    query = select(Category).order_by(Category.sort_order, Category.id)
    if kind is not None:
        query = query.where(Category.kind == kind.value)
    return [_summary_category(row) for row in session.scalars(query).all()]


@router.post(
    "",
    summary="Create a category",
    response_model=CategorySummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_category(
    payload: CategoryCreateRequest,
    session: SessionDep,
) -> CategorySummary:
    """Persist a user category. `is_system` rows are seeded and read-only.

    `kind` arrives as the closed enum, so an unknown kind is a 422 from
    validation before this body runs. A `parent_id` naming no row is a 404,
    and a `(parent_id, name)` this tree already holds is a 409 — the
    constraint decides, not a pre-check that could race it.

    Raises:
        HTTPException: 404 for an unknown parent; 409 for a duplicate name
            under the same parent.
    """
    try:
        with session.begin():
            # Every read is inside the block. `Session.get()` autobegins, so
            # a lookup performed before `session.begin()` would make the
            # block raise "a transaction is already begun".
            if payload.parent_id is not None:
                parent = session.get(Category, payload.parent_id)
                if parent is None:
                    raise HTTPException(
                        status_code=http_status.HTTP_404_NOT_FOUND,
                        detail=f"No category {payload.parent_id}",
                    )
            row = Category(
                name=payload.name,
                kind=payload.kind.value,
                parent_id=payload.parent_id,
                is_system=False,
            )
            session.add(row)
            session.flush()
    except DBAPIError as exc:
        mapped = _duplicate_as_conflict(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return _summary_category(row)


@router.get(
    "/kinds", summary="Supported category kinds", response_model=list[CategoryKind]
)
def list_category_kinds() -> list[CategoryKind]:
    """The closed kind list, for populating a dropdown."""
    return list(CategoryKind)


@router.get(
    "/rules",
    summary="List categorization rules",
    response_model=list[CategoryRuleSummary],
)
def list_rules(session: SessionDep) -> list[CategoryRuleSummary]:
    """Every rule, hand and learned, in engine order.

    Returned whole and in order on purpose: section I requires the entire rule
    set to be readable on one screen and editable by hand. A paginated endpoint
    would make that impossible. Unpaginated for the same reason — this table
    stays small because rules are substrings, not per-transaction rows.
    """
    rows = session.scalars(
        select(CategoryRuleRow).order_by(CategoryRuleRow.priority, CategoryRuleRow.id)
    ).all()
    return [_summary_rule(row) for row in rows]


@router.post(
    "/rules",
    summary="Create a categorization rule",
    response_model=CategoryRuleSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_rule(
    payload: CategoryRuleCreateRequest,
    session: SessionDep,
) -> CategoryRuleSummary:
    """Persist a hand-authored rule.

    The pattern is a plain substring plus a priority integer, deliberately not a
    regex and not a DSL: a rule the user cannot read is a rule they will not fix.
    `is_learned` is forced False — a rule authored here is never "learned", and
    the router states that rather than trusting the body. The engine reads this
    same table on the next transaction, so what is stored here is what matches.

    Raises:
        HTTPException: 404 for a `category_id` naming no row.
    """
    with session.begin():
        category = session.get(Category, payload.category_id)
        if category is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No category {payload.category_id}",
            )
        row = CategoryRuleRow(
            description_pattern=payload.description_pattern,
            category_id=payload.category_id,
            priority=payload.priority,
            is_learned=False,
        )
        session.add(row)
        session.flush()
    return _summary_rule(row)


@router.delete("/rules/{rule_id}", summary="Delete a rule")
def delete_rule(rule_id: int, session: SessionDep) -> dict[str, object]:
    """Remove one rule by id.

    Addressed by id rather than by pattern text on purpose: a pattern may
    contain a slash (e.g. 'bakker/straat'), and a single path segment can
    never address such a pattern — the slash splits the route and the
    delete 404s for a rule the list shows. An id has no such ambiguity.
    A rule id that names no row is a 404, because deleting nothing and
    reporting success would make a typo look like an edit.

    Raises:
        HTTPException: 404 when no rule has that id.
    """
    with session.begin():
        row = session.get(CategoryRuleRow, rule_id)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No rule {rule_id}",
            )
        session.delete(row)
    return {"status": "deleted", "id": rule_id}
