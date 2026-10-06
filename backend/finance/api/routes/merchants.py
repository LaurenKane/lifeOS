"""merchants router — curation for layers 2-4 of the categorization engine.

Layers 2-4 match on rows this router owns: `merchant` (layer 3 substring,
layer 4 fuzzy, both via `load_known_merchants`) and `merchant_alias`
(layer 2 substring via `load_aliases`). Without these endpoints those tables
are write-only from the API's point of view — the mechanism works but nobody
can file anything into it.

Two routers, not one nested resource: `/merchants` for canonical names and
`/merchant-aliases` for raw-string mappings. Nesting aliases under
`/merchants/{id}/aliases` would collide with the merchant id path and would
also lie about the shape — an alias's canonical merchant is optional, not
its parent.

PATCH is PATCH and not PUT on both resources: an explicit null clears while
an absent field leaves the row alone (read via `model_fields_set`). That
distinction is the whole reason the update is not a PUT — a PUT would make
"unfile this merchant" indistinguishable from "leave it filed".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi import status as http_status
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from finance.api.deps import get_session
from finance.api.schemas import (
    MerchantAliasCreateRequest,
    MerchantAliasSummary,
    MerchantAliasUpdateRequest,
    MerchantCreateRequest,
    MerchantSummary,
    MerchantUpdateRequest,
)
from finance.domain.models.taxonomy import Category
from finance.domain.models.taxonomy import Merchant as MerchantRow
from finance.domain.models.taxonomy import MerchantAlias as MerchantAliasRow

router = APIRouter(tags=["finance"])

#: The dependency, annotated rather than declared as a default argument: this
#: project runs ruff with `B` selected, and `Depends(...)` in a default value is
#: exactly the pattern that rule exists for. Same idiom as
#: `routes/categories.py` and `routes/transactions.py`.
SessionDep = Annotated[Session, Depends(get_session)]

#: SQLSTATE 23505, `unique_violation`. On these routers that is exactly one
#: thing each: the `name` uniqueness on `merchant`, the `raw_string`
#: uniqueness on `merchant_alias`. Restated here rather than imported, because
#: a handler that reads the constant out of the trigger body agrees with every
#: value of it. Same idiom as `routes/categories.py`.
UNIQUE_VIOLATION = "23505"


def _duplicate_as_conflict(exc: DBAPIError, *, what: str) -> HTTPException | None:
    """Map a duplicate merchant/alias to 409, or None if it is not one.

    A pre-check `SELECT` would still race a concurrent insert, so the
    uniqueness is enforced by the constraint and read back here: the database
    is the authority on duplicates, and this only translates its answer.
    Anything that is not a unique violation comes back as None so the caller
    re-raises — a server fault dressed as a 409 would tell the client its
    request was wrong when it was not.
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
        detail=f"A merchant entry with that {what} already exists: {message}",
    )


def _summary_merchant(row: MerchantRow) -> MerchantSummary:
    """One `merchant` row, as the curation screen reads it."""
    return MerchantSummary(id=row.id, name=row.name, category_id=row.category_id)


def _summary_alias(row: MerchantAliasRow) -> MerchantAliasSummary:
    """One `merchant_alias` row, as the curation screen reads it."""
    return MerchantAliasSummary(
        id=row.id,
        raw_string=row.raw_string,
        merchant_id=row.merchant_id,
        category_id=row.category_id,
        confidence=row.confidence,
    )


def _require_category(session: Session, category_id: int) -> None:
    """404 when no category carries that id. Read inside the caller's block."""
    if session.get(Category, category_id) is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No category {category_id}",
        )


def _require_merchant(session: Session, merchant_id: int) -> MerchantRow:
    """The merchant row, or 404. Read inside the caller's block."""
    row = session.get(MerchantRow, merchant_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No merchant {merchant_id}",
        )
    return row


# ---------------------------------------------------------------------------
# Merchants
# ---------------------------------------------------------------------------


@router.get(
    "/merchants", summary="List merchants", response_model=list[MerchantSummary]
)
def list_merchants(session: SessionDep) -> list[MerchantSummary]:
    """Every canonical merchant, in name order.

    Unpaginated on purpose, like `GET /categories/rules`: the curated merchant
    set is small (dozens, not millions), and the curation screen reads it whole.
    """
    rows = session.scalars(select(MerchantRow).order_by(MerchantRow.name)).all()
    return [_summary_merchant(row) for row in rows]


@router.post(
    "/merchants",
    summary="Create a merchant",
    response_model=MerchantSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_merchant(
    payload: MerchantCreateRequest,
    session: SessionDep,
) -> MerchantSummary:
    """Persist a canonical merchant name, optionally filed to a category.

    A `category_id` naming no row is a 404, and a name this table already
    holds is a 409 — the constraint decides, not a pre-check that could race
    it. A merchant with no category is known-but-unfiled: it feeds neither
    layer 3 nor layer 4, which is the honest answer for a name nobody filed.

    Raises:
        HTTPException: 404 for an unknown category; 409 for a duplicate name.
    """
    try:
        with session.begin():
            # Every read is inside the block. `Session.get()` autobegins, so
            # a lookup performed before `session.begin()` would make the
            # block raise "a transaction is already begun".
            if payload.category_id is not None:
                _require_category(session, payload.category_id)
            row = MerchantRow(name=payload.name, category_id=payload.category_id)
            session.add(row)
            session.flush()
    except DBAPIError as exc:
        mapped = _duplicate_as_conflict(exc, what="name")
        if mapped is None:
            raise
        raise mapped from exc
    return _summary_merchant(row)


@router.patch(
    "/merchants/{merchant_id}",
    summary="Update a merchant",
    response_model=MerchantSummary,
)
def update_merchant(
    merchant_id: int,
    payload: MerchantUpdateRequest,
    session: SessionDep,
) -> MerchantSummary:
    """Rename a merchant and/or (un)file its category.

    PATCH semantics via `model_fields_set`: an explicit `category_id: null`
    CLEARS the category (back to known-but-unfiled) while an absent field
    leaves it alone. Same for `name`: absent means "do not rename".

    Raises:
        HTTPException: 404 for an unknown merchant or category; 409 for a
            rename onto a name this table already holds.
    """
    try:
        with session.begin():
            row = session.get(MerchantRow, merchant_id)
            if row is None:
                raise HTTPException(
                    status_code=http_status.HTTP_404_NOT_FOUND,
                    detail=f"No merchant {merchant_id}",
                )
            fields = payload.model_fields_set
            if "name" in fields and payload.name is not None:
                row.name = payload.name
            if "category_id" in fields:
                if payload.category_id is not None:
                    _require_category(session, payload.category_id)
                row.category_id = payload.category_id
            session.flush()
    except DBAPIError as exc:
        mapped = _duplicate_as_conflict(exc, what="name")
        if mapped is None:
            raise
        raise mapped from exc
    return _summary_merchant(row)


@router.delete(
    "/merchants/{merchant_id}",
    summary="Delete a merchant",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_merchant(merchant_id: int, session: SessionDep) -> None:
    """Remove a merchant. Its aliases go with it (ON DELETE CASCADE).

    A merchant that names nothing is a 404: deleting nothing and reporting
    success would make a typo look like an edit.

    Raises:
        HTTPException: 404 when no merchant carries that id.
    """
    with session.begin():
        row = session.get(MerchantRow, merchant_id)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No merchant {merchant_id}",
            )
        session.delete(row)
    return None


# ---------------------------------------------------------------------------
# Merchant aliases
# ---------------------------------------------------------------------------


@router.get(
    "/merchant-aliases",
    summary="List merchant aliases",
    response_model=list[MerchantAliasSummary],
)
def list_aliases(session: SessionDep) -> list[MerchantAliasSummary]:
    """Every stored alias, in raw-string order.

    Unpaginated like the merchant list above: curated by hand, small by
    construction, read whole by the screen that edits it.
    """
    rows = session.scalars(
        select(MerchantAliasRow).order_by(MerchantAliasRow.raw_string)
    ).all()
    return [_summary_alias(row) for row in rows]


@router.post(
    "/merchant-aliases",
    summary="Create a merchant alias",
    response_model=MerchantAliasSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_alias(
    payload: MerchantAliasCreateRequest,
    session: SessionDep,
) -> MerchantAliasSummary:
    """Persist a raw-string mapping.

    At least one of `category_id` / `merchant_id` is required: an alias that
    points at neither is a string that matches nothing, and storing it would
    be filing a question instead of an answer. When the category is absent
    but the named merchant already has one, the alias inherits it — so
    curating a merchant and its alias in one step files both.

    `confidence` defaults to 1.00: a curated alias is a deliberate mapping,
    not a guess, and must clear layer 2's `is_auto` bar of 0.90.

    Raises:
        HTTPException: 422 when neither target is given; 404 for an unknown
            merchant or category; 409 for a duplicate raw string.
    """
    if payload.category_id is None and payload.merchant_id is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An alias needs a category_id, a merchant_id, or both",
        )
    try:
        with session.begin():
            # Every read is inside the block: `Session.get()` autobegins (see
            # `create_merchant`), so anything before `session.begin()` breaks it.
            category_id = payload.category_id
            merchant_id = payload.merchant_id
            if merchant_id is not None:
                merchant = _require_merchant(session, merchant_id)
                if category_id is None:
                    category_id = merchant.category_id
            if category_id is not None:
                _require_category(session, category_id)
            row = MerchantAliasRow(
                raw_string=payload.raw_string,
                merchant_id=merchant_id,
                category_id=category_id,
                confidence=payload.confidence,
            )
            session.add(row)
            session.flush()
    except DBAPIError as exc:
        mapped = _duplicate_as_conflict(exc, what="raw string")
        if mapped is None:
            raise
        raise mapped from exc
    return _summary_alias(row)


@router.patch(
    "/merchant-aliases/{alias_id}",
    summary="Update a merchant alias",
    response_model=MerchantAliasSummary,
)
def update_alias(
    alias_id: int,
    payload: MerchantAliasUpdateRequest,
    session: SessionDep,
) -> MerchantAliasSummary:
    """Repoint an alias and/or adjust its confidence.

    PATCH semantics via `model_fields_set`: an explicit null clears the
    field while an absent field leaves it alone. The raw string itself is
    immutable — it is the match key, and renaming it is a delete plus a
    create, not an edit — so it is absent from the body on purpose.

    Raises:
        HTTPException: 404 for an unknown alias, merchant or category.
    """
    with session.begin():
        row = session.get(MerchantAliasRow, alias_id)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No merchant alias {alias_id}",
            )
        fields = payload.model_fields_set
        if "merchant_id" in fields:
            if payload.merchant_id is not None:
                _require_merchant(session, payload.merchant_id)
            row.merchant_id = payload.merchant_id
        if "category_id" in fields:
            if payload.category_id is not None:
                _require_category(session, payload.category_id)
            row.category_id = payload.category_id
        if "confidence" in fields and payload.confidence is not None:
            row.confidence = payload.confidence
        session.flush()
    return _summary_alias(row)


@router.delete(
    "/merchant-aliases/{alias_id}",
    summary="Delete a merchant alias",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def delete_alias(alias_id: int, session: SessionDep) -> None:
    """Remove an alias. The merchant (if any) stays: the link points one way.

    Raises:
        HTTPException: 404 when no alias carries that id.
    """
    with session.begin():
        row = session.get(MerchantAliasRow, alias_id)
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No merchant alias {alias_id}",
            )
        session.delete(row)
    return None
