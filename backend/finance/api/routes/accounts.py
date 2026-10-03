"""accounts router — real, database-backed account reads and creation.

M1. There is no in-memory list any more, because an account id that only exists
in a Python list cannot be referenced by a transaction: `source_record.account_id`
and `journal_line.account_id` are foreign keys into this table, and a manual
posting names one. A stubbed accounts list and a real ledger are two accounts
apart at best.

`account_nature` is the field that decides arithmetic — asset, liability or
equity — and `account_type` is the provider's own taxonomy, which is why both are
required on create and neither is defaulted. There is no expense account type;
the contra-account a manual expense posts against is a seeded `equity` row, and
`finance.domain.services.manual_posting` documents the convention at length.

`account.currency` is authoritative: `journal_line.currency` is a snapshot of it,
so an account whose currency is wrong re-denominates every future posting. The
currency is therefore validated against `finance.currency` on create rather than
being taken on trust.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import ScalarResult, select
from sqlalchemy.orm import Session

from finance.api.deps import get_session
from finance.api.schemas import AccountCreateRequest, AccountSummary
from finance.domain.models.accounts import Account
from finance.domain.models.reference import Currency as CurrencyRow
from finance.public import AccountNature, AccountType

router = APIRouter(tags=["finance"], prefix="/accounts")

SessionDep = Annotated[Session, Depends(get_session)]

#: `account_type` values the CHECK in migration 0001 allows. Restated, not read
#: out of the database, so an unexpected value fails at the edge with a useful
#: message rather than as an IntegrityError from a CHECK constraint.
ALLOWED_TYPES: frozenset[str] = frozenset(account.value for account in AccountType)
ALLOWED_NATURES: frozenset[str] = frozenset(nature.value for nature in AccountNature)


def _to_summary(account: Account) -> AccountSummary:
    """One `account` row as the public read model.

    `currency` is stripped: `CHAR(3)` is blank-padded by PostgreSQL and the
    padding is not part of the ISO 4217 code.
    """
    return AccountSummary(
        id=account.id,
        name=account.name,
        currency=account.currency.strip(),
        account_type=AccountType(account.account_type),
        account_nature=AccountNature(account.account_nature),
        is_active=account.is_active,
        is_hidden=account.is_hidden,
        sort_order=account.sort_order,
    )


@router.get("", summary="List accounts", response_model=list[AccountSummary])
def list_accounts(
    session: SessionDep,
    include_inactive: bool = False,
) -> list[AccountSummary]:
    """Accounts, newest taxonomy first; inactive ones only on request.

    `include_inactive` defaults to False because a deactivated card is closed:
    it still has history and still has to be selectable when reconciling an old
    statement, but it is not something to offer by default.
    """
    query = select(Account)
    if not include_inactive:
        query = query.where(Account.is_active.is_(True))
    accounts: ScalarResult[Account] = session.execute(
        query.order_by(Account.sort_order, Account.id)
    ).scalars()
    return [_to_summary(account) for account in accounts]


@router.post(
    "",
    summary="Create account",
    response_model=AccountSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def create_account(
    request: AccountCreateRequest,
    session: SessionDep,
) -> AccountSummary:
    """Register an account.

    The id is assigned by the database (BIGSERIAL) and never supplied by the
    caller: a client-chosen id is a client-chosen primary key, and two clients
    choosing the same one produce a conflict that looks like a duplicate
    account rather than a collision.

    Raises:
        HTTPException: 422 for an unknown currency, or for an account_type /
            account_nature the schema does not allow.
    """
    if request.account_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"account_type {request.account_type!r} is not one of "
                f"{sorted(ALLOWED_TYPES)}"
            ),
        )
    if request.account_nature not in ALLOWED_NATURES:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"account_nature {request.account_nature!r} is not one of "
                f"{sorted(ALLOWED_NATURES)}"
            ),
        )

    account = Account(
        name=request.name,
        account_type=request.account_type,
        account_nature=request.account_nature,
        currency=request.currency,
        sort_order=request.sort_order,
        is_active=request.is_active,
        is_hidden=request.is_hidden,
    )
    # The currency check is INSIDE the transaction block, and that is not a
    # style preference: `Session.get()` autobegins a transaction, so a lookup
    # performed before `session.begin()` would make the block raise
    # "a transaction is already begun". Every read a write handler does belongs
    # inside the same block as its writes.
    #
    # Nothing is caught here. The block rolls back on any exception, so nothing
    # partial survives, and an IntegrityError from a column the validation above
    # did not cover is a bug in that validation — dressing it as a 422 would hide
    # it rather than fix it.
    with session.begin():
        if session.get(CurrencyRow, request.currency) is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Unknown currency {request.currency!r}; it must exist in "
                    "the currency table, because currency.decimals is the "
                    "authority for an account's minor units"
                ),
            )
        session.add(account)
        session.flush()
    return _to_summary(account)


# DECLARATION ORDER IS LOAD-BEARING.
#
# Starlette matches routes in registration order, so a literal segment declared
# AFTER a one-segment parameterised route is unreachable: `GET /accounts/types`
# is captured by `GET /accounts/{account_id}` above it and never reaches the
# handler below. Before that route was typed `int` that surfaced as a 404 from a
# lookup miss; retype it and it surfaces as a 422, which points the diagnosis at
# validation rather than at shadowing. Both are wrong, so every literal-segment
# route in this file is declared BEFORE `/{account_id}`.
#
# `/by-nature/{nature}` needs no such care: it is two segments and cannot be
# captured by a one-segment pattern.
@router.get(
    "/types", summary="Supported account types", response_model=list[AccountType]
)
def list_account_types() -> list[AccountType]:
    """The closed account taxonomy, for populating a form's dropdown."""
    return list(AccountType)


@router.get("/natures", summary="Account natures", response_model=list[AccountNature])
def list_account_natures() -> list[AccountNature]:
    """The three natures. Which one an account is decides which way an amount runs."""
    return list(AccountNature)


@router.get("/{account_id}", summary="Get account", response_model=AccountSummary)
def get_account(account_id: int, session: SessionDep) -> AccountSummary:
    """One account by id.

    The path parameter is typed `int`, so a malformed id is a 422 from FastAPI's
    validation rather than a 404 from a lookup that could never have matched —
    the same idiom the enum-typed `/by-nature/{nature}` route below uses.

    Raises:
        HTTPException: 404 when no such account.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No account {account_id}",
        )
    return _to_summary(account)


@router.get(
    "/by-nature/{nature}",
    summary="Accounts by nature",
    response_model=list[AccountSummary],
)
def list_by_nature(nature: AccountNature, session: SessionDep) -> list[AccountSummary]:
    """Accounts filtered by asset/liability/equity.

    Asset and liability are first-class rather than a `type` guess: net worth is
    `assets - liabilities`, and a taxonomy that cannot answer "which side" cannot
    be used for it. `equity` is in this list because it is where a manual
    expense's counter-leg lives.
    """
    accounts: ScalarResult[Account] = session.execute(
        select(Account)
        .where(
            Account.account_nature == nature.value,
            Account.is_active.is_(True),
        )
        .order_by(Account.sort_order, Account.id)
    ).scalars()
    return [_to_summary(account) for account in accounts]
