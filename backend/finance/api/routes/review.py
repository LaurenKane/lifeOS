"""review router — uncategorized transaction review queue."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["finance"], prefix="/review")


@router.get("", summary="Get uncategorized transactions", response_model=list)
def get_uncategorized():
    """Get transactions pending categorization review."""
    return []


@router.post("/{txn_id}/confirm", summary="Confirm categorization", response_model=dict)
def confirm_categorization(txn_id: str):
    """Confirm a categorization decision."""
    return {"id": txn_id, "detail": "categorization confirmed placeholder"}
