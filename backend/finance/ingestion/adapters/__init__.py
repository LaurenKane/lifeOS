"""adapters — one per source, all emitting `RawRecord`.

One per source, behind an ABC, and another ABC if a sixth source appears. No
plugin registry: a registry means import-time discovery, which means the
adapter set differs per environment and an import failure in an unused provider
breaks startup.

Every adapter emits the same `RawRecord`. The provider-agnostic part is the
schema; only the IdentityResolver is provider-aware.

The v1 provider set is `enable_banking`, `amex_pdf`, `rabobank_pdf`,
`revolut_pdf`, `manual` (`backend/finance/api/routes/imports.py::V1_PROVIDERS`,
decided in `docs/adr/0002-import-provider-enum.md`). The constraint that shaped
each adapter:

    enable_banking.py  API only. `entry_reference` is the Tier-1 key and is
                       unique per ACCOUNT, never globally. The only provider
                       whose rows carry a stable ID.
    amex_pdf.py        7-year history and no stable ID — Amex's own IDs are
                       documented to change — so Tier 3 fingerprints are the
                       only dedup available. Column alignment is noisy, so this
                       adapter is the most tolerant and the least confident.
    manual.py          Typed by a human, so it trusts the input.

Two of the five v1 providers have no adapter yet: `rabobank_pdf` and
`revolut_pdf`. Both are statement PDFs with no stable ID, so they dedupe by
fingerprint like `amex_pdf` when they land.

`amex_csv.py` and `revolut_csv.py` are retired as import sources — see ADR 0002
— but the modules and their exports remain until the removal is decided. They
are not reachable through the v1 provider list.
"""

from __future__ import annotations

from finance.ingestion.adapters.amex_csv import AmexCsvAdapter
from finance.ingestion.adapters.amex_pdf import AmexPdfAdapter
from finance.ingestion.adapters.base import ImportAdapter, ImportResult
from finance.ingestion.adapters.enable_banking import EnableBankingAdapter
from finance.ingestion.adapters.manual import ManualAdapter
from finance.ingestion.adapters.revolut_csv import RevolutCsvAdapter

__all__ = [
    "AmexCsvAdapter",
    "AmexPdfAdapter",
    "EnableBankingAdapter",
    "ImportAdapter",
    "ImportResult",
    "ManualAdapter",
    "RevolutCsvAdapter",
]
