"""budgets router — the stored limits, one category per row.

Four verbs over `finance.budget`: list, create, patch, delete. This router
STORES limits; the comparison itself stays where it already is, pure and
tested — `finance.domain.services.budget.check_budget` does integer minor-unit
arithmetic over a limit the caller has already selected, and "how much did
this category spend this period" is a question about the ledger, not about a
budget row. Answering it here would put arithmetic in the one layer allowed to
touch only the database.

Every response's `name` is the budgeted CATEGORY's name, read through the
join: a budget is a limit on a category, so the category's own name is the
label, and storing a second copy here would go stale on rename.
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
    BudgetCreateRequest,
    BudgetSummary,
    BudgetUpdateRequest,
)
from finance.domain.models.budgets import Budget as BudgetRow
from finance.domain.models.reference import Currency as CurrencyRow
from finance.domain.models.taxonomy import Category

router = APIRouter(tags=["finance"], prefix="/budgets")

#: The dependency, annotated rather than declared as a default argument: this
#: project runs ruff with `B` selected, and `Depends(...)` in a default value is
#: exactly the pattern that rule exists for. Same idiom as
#: `routes/categories.py`.
SessionDep = Annotated[Session, Depends(get_session)]

#: SQLSTATE 23514, `check_violation`. On this router that is exactly one
#: thing: the `amount > 0` CHECK on `budget` in migration 0007. Restated here
#: rather than imported, because a handler that reads the constant out of the
#: constraint body agrees with every value of it. Same idiom as
#: `routes/transactions.py`.
CHECK_VIOLATION = "23514"


def _summary_budget(row: BudgetRow, category: Category) -> BudgetSummary:
    """One `budget` row with its category, as the budgets screen reads it.

    `currency` is stripped: `CHAR(3)` is blank-padded by PostgreSQL and the
    padding is not part of the ISO 4217 code.
    """
    return BudgetSummary(
        id=row.id,
        name=category.name,
        category_id=row.category_id,
        amount_minor=row.amount,
        currency=row.currency.strip(),
        period=row.period,
    )


def _require_category(session: Session, category_id: int) -> Category:
    """The category row, or 404. Read inside the caller's block.

    404 rather than the `ForeignKeyViolation` the INSERT would raise: a budget
    naming a category that does not exist is a client mistake, and the foreign
    key's message says less about it than this does. Same rule as the parent
    lookup in `routes/categories.py`.
    """
    category = session.get(Category, category_id)
    if category is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No category {category_id}",
        )
    return category


def _require_budget(session: Session, budget_id: int) -> BudgetRow:
    """The budget row, or 404. Read inside the caller's block."""
    row = session.get(BudgetRow, budget_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No budget {budget_id}",
        )
    return row


def _require_currency(session: Session, code: str) -> None:
    """Refuse a currency the `currency` table does not hold. 422.

    The table is the authority on `currency.decimals`, which is what gives a
    minor-unit integer its meaning — a currency that exists only in the
    request would store a number nobody can interpret. Same rule and message
    shape as `routes/accounts.py`.
    """
    if session.get(CurrencyRow, code) is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Unknown currency {code!r}; it must exist in the currency "
                "table, because currency.decimals is the authority for an "
                "amount's minor units"
            ),
        )


def _cleared(field: str) -> HTTPException:
    """422 for an explicit `null` on a field that cannot be cleared.

    PATCH distinguishes absent (leave alone) from null (clear); nothing on a
    budget is clearable — a row with no limit, no currency or no period is not
    a budget — so a null is a mistake rather than an instruction.
    """
    return HTTPException(
        status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"{field} cannot be cleared; send a value or omit the field",
    )


def _check_refusal(exc: DBAPIError) -> HTTPException | None:
    """Map the `amount > 0` CHECK refusal to 422, or None if it is not one.

    The CHECK, not a pre-check, is what validates the amount (see
    `BudgetCreateRequest`), so its refusal has to arrive at the client as a
    request error rather than a 500. A pre-checked `gt=0` in the schema would
    be a second opinion that could drift from the constraint and leave this
    translation dead.

    Anything that is not a check violation comes back as None so the caller
    re-raises — a server fault dressed as a 422 would tell the client its
    request was wrong when it was not.
    """
    origin = exc.orig if exc.orig is not None else exc
    code = getattr(origin, "sqlstate", None)
    if not isinstance(code, str):
        code = getattr(origin, "pgcode", None)
    if code != CHECK_VIOLATION:
        return None
    message = str(origin).splitlines()[0]
    return HTTPException(
        status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(f"A budget limit must be a positive amount in minor units: {message}"),
    )


@router.get("", summary="List budgets", response_model=list[BudgetSummary])
def list_budgets(session: SessionDep) -> list[BudgetSummary]:
    """Every budget, joined to its category's name, in category-name order.

    Unpaginated, like every other list in this module: budgets are curated by
    hand and stay in the dozens, so the screen reads them whole.
    """
    rows = session.execute(
        select(BudgetRow, Category)
        .join(Category, BudgetRow.category_id == Category.id)
        .order_by(Category.name, BudgetRow.id)
    ).all()
    return [_summary_budget(budget, category) for budget, category in rows]


@router.post(
    "",
    summary="Create a budget",
    response_model=BudgetSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_budget(
    payload: BudgetCreateRequest,
    session: SessionDep,
) -> BudgetSummary:
    """Persist a limit on one category for one period.

    Raises:
        HTTPException: 404 for a `category_id` naming no row; 422 for an
            unknown currency, a period the schema does not allow, or a
            non-positive limit (the CHECK's refusal, translated here).
    """
    try:
        with session.begin():
            # Every read is inside the block. `Session.get()` autobegins, so a
            # lookup performed before `session.begin()` would make the block
            # raise "a transaction is already begun" — same rule as
            # `routes/categories.py`.
            category = _require_category(session, payload.category_id)
            _require_currency(session, payload.currency)
            row = BudgetRow(
                category_id=payload.category_id,
                amount=payload.amount_minor,
                currency=payload.currency,
                period=payload.period,
            )
            session.add(row)
            session.flush()
    except DBAPIError as exc:
        mapped = _check_refusal(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return _summary_budget(row, category)


@router.patch(
    "/{budget_id}",
    summary="Update a budget",
    response_model=BudgetSummary,
)
def update_budget(
    budget_id: int,
    payload: BudgetUpdateRequest,
    session: SessionDep,
) -> BudgetSummary:
    """Raise or lower a limit, re-denominate it, or change how often it repeats.

    PATCH semantics via `model_fields_set`: an absent field leaves the row
    alone, an explicit null is refused (nothing here is clearable), and
    `category_id` is not editable at all — retargeting a budget is a
    different budget.

    Raises:
        HTTPException: 404 for an unknown budget; 422 for a cleared field, an
            unknown currency, a period the schema does not allow, or a
            non-positive limit (the CHECK's refusal, translated here).
    """
    try:
        with session.begin():
            row = _require_budget(session, budget_id)
            fields = payload.model_fields_set
            if "amount_minor" in fields:
                if payload.amount_minor is None:
                    raise _cleared("amount_minor")
                row.amount = payload.amount_minor
            if "currency" in fields:
                if payload.currency is None:
                    raise _cleared("currency")
                _require_currency(session, payload.currency)
                row.currency = payload.currency
            if "period" in fields:
                if payload.period is None:
                    raise _cleared("period")
                row.period = payload.period
            session.flush()
            # Re-read for the response's `name`: the category is unchanged by
            # this router (it is not editable), and the join would otherwise
            # be the only place the label comes from.
            category = _require_category(session, row.category_id)
    except DBAPIError as exc:
        mapped = _check_refusal(exc)
        if mapped is None:
            raise
        raise mapped from exc
    return _summary_budget(row, category)


@router.delete(
    "/{budget_id}",
    summary="Delete a budget",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_budget(budget_id: int, session: SessionDep) -> None:
    """Remove one budget by id. The category stays: the limit points one way.

    A budget that names nothing is a 404: deleting nothing and reporting
    success would make a typo look like an edit.

    Raises:
        HTTPException: 404 when no budget carries that id.
    """
    with session.begin():
        row = _require_budget(session, budget_id)
        session.delete(row)
    return None
