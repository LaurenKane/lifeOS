"""accounts router — CRUD for accounts."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["finance"], prefix="/accounts")


@router.get("", summary="List accounts", response_model=list)
def list_accounts():
    """List all accounts."""
    return []


@router.post("", summary="Create account", response_model=dict)
def create_account():
    """Create a new account."""
    return {"id": "new", "detail": "account creation placeholder"}


@router.get("/{account_id}", summary="Get account by ID", response_model=dict)
def get_account(account_id: str):
    """Get account by ID."""
    return {"id": account_id, "detail": "account lookup placeholder"}
