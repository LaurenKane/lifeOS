"""accounts router — read access to accounts.

M0 scope: the route surface exists so the OpenAPI spec is real and the frontend
has something to generate against. Handlers return typed empty responses rather
than `[]` with a bare `list` response_model, because an untyped response_model
produces an OpenAPI schema with no fields — and the generated TypeScript then
claims the endpoint returns nothing.
"""

from __future__ import annotations

from fastapi import APIRouter

from finance.api.schemas import AccountSummary
from finance.public import AccountNature, AccountType

router = APIRouter(tags=["finance"], prefix="/accounts")

# M0: no session yet. Populated in M1 when the ledger exists.
_ACCOUNTS: list[AccountSummary] = []


@router.get("", summary="List accounts", response_model=list[AccountSummary])
def list_accounts() -> list[AccountSummary]:
    """Every account, active and inactive."""
    return list(_ACCOUNTS)


@router.post("", summary="Create account", response_model=AccountSummary)
def create_account(account: AccountSummary) -> AccountSummary:
    """Register an account.

    M0 stub: validates the schema and echoes it. Persistence lands with the M1
    migrations, because writing to a table that does not exist yet would fail at
    request time rather than at review time.
    """
    return account


@router.get("/{account_id}", summary="Get account", response_model=AccountSummary)
def get_account(account_id: str) -> AccountSummary:
    """One account by id.

    Raises:
        HTTPException: 404 when no such account. M0 has no accounts, so this is
            the only reachable outcome until M1.
    """
    from fastapi import HTTPException

    for account in _ACCOUNTS:
        if account.id == account_id:
            return account
    raise HTTPException(status_code=404, detail=f"No account {account_id}")


@router.get(
    "/by-nature/{nature}",
    summary="Accounts by nature",
    response_model=list[AccountSummary],
)
def list_by_nature(nature: AccountNature) -> list[AccountSummary]:
    """Accounts filtered by asset/liability/equity.

    Asset and liability are first-class rather than a `type` guess: net worth is
    `assets - liabilities`, and a taxonomy that cannot answer "which side" cannot
    be used for it.
    """
    return [a for a in _ACCOUNTS if a.account_nature == nature]


@router.get(
    "/types", summary="Supported account types", response_model=list[AccountType]
)
def list_account_types() -> list[AccountType]:
    """The closed account taxonomy, for populating a form's dropdown."""
    return list(AccountType)
