"""imports router — CSV/PDF/API import pipeline."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(tags=["finance"], prefix="/imports")


@router.post("", summary="Upload import file", response_model=dict)
def upload_import_file(file: UploadFile = File(...)):
    """Upload an import file (CSV, PDF)."""
    # Validate file type
    filename = file.filename or ""
    lower = filename.lower()
    if not (lower.endswith(".csv") and lower.endswith(".pdf")):
        raise HTTPException(
            status_code=400, detail="Unsupported file type. Use CSV or PDF."
        )
    return {"filename": filename, "detail": "import file upload placeholder"}
