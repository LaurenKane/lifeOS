"""imports router — run an import.

M0 scope: accepts an upload, validates the provider/method pair, and reports
what the adapter produced. The adapters themselves are complete and unit-tested
(`finance.ingestion.adapters`); what is missing is persistence, which arrives
with the M1 migrations.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from finance.api.schemas import Provider, ProviderInfo
from finance.ingestion.adapters import (
    AmexCsvAdapter,
    AmexPdfAdapter,
    RevolutCsvAdapter,
)
from finance.ingestion.adapters.base import ImportResult

router = APIRouter(tags=["finance"], prefix="/imports")

# Largest statement we accept. A 7-year Amex PDF is a few MB; 64 MiB is generous
# and keeps one request from exhausting the API container's memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


class FileAdapter(Protocol):
    """The adapter shape an upload needs: parse a file for one account.

    Narrower than `ImportAdapter` on purpose. `ManualAdapter` and
    `EnableBankingAdapter` implement `ImportAdapter` but are not uploadable, and
    typing the table against the full interface would push that distinction into
    runtime checks instead of the type system.
    """

    def parse(
        self, payload: bytes, account_id: str = "", filename: str | None = None
    ) -> ImportResult: ...


class ImportSummary(BaseModel):  # type: ignore[explicit-any]
    """What one import produced. Read-only in practice, frozen to say so."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    import_method: str
    status: str
    record_count: int
    source_checksum: str | None = None
    failures: list[str] = []


# provider -> the adapter and the file extension it accepts.
# `enable_banking` and `manual` are absent on purpose: neither is an upload, so
# they are not even `FileAdapter`-shaped (their `parse` takes no account).
_FILE_ADAPTERS: dict[str, tuple[type[FileAdapter], str]] = {
    "amex_csv": (AmexCsvAdapter, ".csv"),
    "amex_pdf": (AmexPdfAdapter, ".pdf"),
    "revolut_csv": (RevolutCsvAdapter, ".csv"),
}

# The full provider list, including the two that are not uploadable.
_PROVIDERS: tuple[Provider, ...] = (
    Provider(kind="enable_banking", import_method="api", accepts_upload=False),
    Provider(kind="amex_csv", import_method="csv", accepts_upload=True),
    Provider(kind="amex_pdf", import_method="pdf", accepts_upload=True),
    Provider(kind="revolut_csv", import_method="csv", accepts_upload=True),
    Provider(kind="manual", import_method="manual", accepts_upload=False),
)


@router.post("", summary="Upload a statement for import", response_model=ImportSummary)
async def upload_import_file(
    file: UploadFile = File(...),
    provider: str = "amex_csv",
    account_id: str = "",
) -> ImportSummary:
    """Accept a statement file and parse it.

    Args:
        file: The uploaded statement.
        provider: Which adapter to run. Must match the file extension.
        account_id: The local account to attribute rows to. A file names no
            account, so the user picks one and the caller passes it in.

    Returns:
        A summary: provider, counts, checksum, and per-row failures.

    Raises:
        HTTPException: 400 for an unknown provider, a mismatched extension, or a
            provider that is not uploadable; 413 when the upload is too large.
    """
    # Manual entries are typed field by field, and Enable Banking arrives over
    # its API. Neither is an upload, and saying so is more useful than a
    # confusing parse failure.
    if provider == "enable_banking":
        raise HTTPException(
            status_code=400,
            detail="Enable Banking is an API source, not an upload",
        )
    if provider == "manual":
        raise HTTPException(
            status_code=400, detail="Manual entries are POSTed as JSON, not uploaded"
        )
    if provider not in _FILE_ADAPTERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider {provider!r}")

    adapter_class, extension = _FILE_ADAPTERS[provider]
    filename = file.filename or ""
    if extension and not filename.lower().endswith(extension):
        raise HTTPException(
            status_code=400, detail=f"Expected a {extension} file, got {filename!r}"
        )

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    adapter = adapter_class()
    result = adapter.parse(payload, account_id=account_id, filename=filename)
    return ImportSummary(
        provider=result.provider,
        import_method=result.import_method,
        status=result.status,
        record_count=result.record_count,
        source_checksum=result.source_checksum,
        failures=[str(failure) for failure in result.failed],
    )


@router.get(
    "/providers", summary="Supported import providers", response_model=ProviderInfo
)
def list_providers() -> ProviderInfo:
    """The closed provider list, so a client does not hardcode it.

    Declared as data rather than derived from `_FILE_ADAPTERS`, because that
    table says nothing about import methods and omits the one provider that
    cannot be uploaded at all.
    """
    return ProviderInfo(providers=list(_PROVIDERS))
