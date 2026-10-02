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
from finance.ingestion.adapters import AmexPdfAdapter
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


# Canonical v1 provider list. Source of truth for the backend schema, the
# `/imports/providers` response, and the frontend `provider` enum.
# See docs/adr/0002-import-provider-enum.md for the decision record.
V1_PROVIDERS: tuple[str, ...] = (
    "enable_banking",
    "amex_pdf",
    "rabobank_pdf",
    "revolut_pdf",
    "manual",
)

# provider -> the adapter and the file extension it accepts.
# `enable_banking`, `manual`, `rabobank_pdf`, and `revolut_pdf` are absent on
# purpose: the first two are not uploads, and the PDF adapters for Rabobank and
# Revolut are not implemented yet (docs/research/11-real-export-verification.md
# §5). They remain first-class providers so the schema can represent the files
# the user actually has.
_FILE_ADAPTERS: dict[str, tuple[type[FileAdapter], str]] = {
    "amex_pdf": (AmexPdfAdapter, ".pdf"),
}

_IMPORT_METHODS: dict[str, str] = {
    "enable_banking": "api",
    "amex_pdf": "pdf",
    "rabobank_pdf": "pdf",
    "revolut_pdf": "pdf",
    "manual": "manual",
}


def _provider_info(kind: str) -> Provider:
    """Build a Provider entry from the canonical v1 list."""
    return Provider(
        kind=kind,
        import_method=_IMPORT_METHODS[kind],
        accepts_upload=kind in _FILE_ADAPTERS,
    )


# The full provider list, including providers that are not yet uploadable.
_PROVIDERS: tuple[Provider, ...] = tuple(_provider_info(kind) for kind in V1_PROVIDERS)


@router.post("", summary="Upload a statement for import", response_model=ImportSummary)
async def upload_import_file(
    file: UploadFile = File(...),
    provider: str = "amex_pdf",
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
    table says nothing about import methods and omits providers that are not
    yet uploadable.
    """
    return ProviderInfo(providers=list(_PROVIDERS))
