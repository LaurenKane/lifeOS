"""transactions router — CRUD for transactions/ledger entries."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["finance"], prefix="/transactions")


@router.get("", summary="List transactions", response_model=list)
def list_transactions():
    """List all transactions."""
    return []


@router.post("", summary="Create transaction", response_model=dict)
def create_transaction():
    """Create a new transaction."""
    return {"id": "new", "detail": "transaction creation placeholder"}


@router.get("/{txn_id}", summary="Get transaction by ID", response_model=dict)
def get_transaction(txn_id: str):
    """Get transaction by ID."""
    return {"id": txn_id, "detail": "transaction lookup placeholder"}
