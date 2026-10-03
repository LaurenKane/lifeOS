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
    rabobank_pdf.py    No stable ID either, and worse: the Debit/Credit column
                       offsets move from page to page, so each page is
                       re-anchored against its own printed header.
    revolut_pdf.py     No stable ID. One file can carry several products, so a
                       single account_id is not enough, and a row from an
                       unattributed section comes back with account_id=None.
    manual.py          Typed by a human, so it trusts the input.

All three PDF adapters share one text extractor, `pdf_text.py` — `pdftotext
-layout` behind an injectable callable — because the row parsers were written
and validated against that tool's output. `tools/bankparse/` is the reference
implementation and stays until these ports are considered settled.

`amex_csv.py` and `revolut_csv.py` were retired/deferred as import sources by
ADR 0002 and their modules have since been deleted. No CSV was ever supplied for
either, so both parsers were unverifiable; nothing here is reachable through the
v1 provider list.
"""

from __future__ import annotations

from finance.ingestion.adapters.amex_pdf import AmexPdfAdapter
from finance.ingestion.adapters.base import ImportAdapter, ImportResult
from finance.ingestion.adapters.enable_banking import EnableBankingAdapter
from finance.ingestion.adapters.manual import ManualAdapter
from finance.ingestion.adapters.rabobank_pdf import RabobankPdfAdapter
from finance.ingestion.adapters.revolut_pdf import RevolutPdfAdapter

__all__ = [
    "AmexPdfAdapter",
    "EnableBankingAdapter",
    "ImportAdapter",
    "ImportResult",
    "ManualAdapter",
    "RabobankPdfAdapter",
    "RevolutPdfAdapter",
]
