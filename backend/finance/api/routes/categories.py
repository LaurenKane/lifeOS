"""categories router — category management."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["finance"], prefix="/categories")


@router.get("", summary="List categories", response_model=list)
def list_categories():
    """List all categories."""
    return []


@router.post("", summary="Create category", response_model=dict)
def create_category():
    """Create a new category."""
    return {"id": "new", "detail": "category creation placeholder"}
