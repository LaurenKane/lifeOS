"""adapters — one per source, all emitting `RawRecord`.

Three classes, and an ABC if a fourth appears. No plugin registry: a registry
means import-time discovery, which means the adapter set differs per environment
and an import failure in an unused provider breaks startup.

Every adapter emits the same `RawRecord`. The provider-agnostic part is the
schema; only the IdentityResolver is provider-aware.

Providers, and the constraint that shaped each adapter:

    enable_banking.py  API only. `entry_reference` is the Tier-1 key and is
                       unique per ACCOUNT, never globally.
    amex_csv.py        No stable ID exists — Amex's own IDs are documented to
                       change. Tier 3 is the only dedup available.
    amex_pdf.py        7-year history. Column alignment is noisy, so this adapter
                       is the most tolerant and the least confident.
    revolut_csv.py     Stable `id` column, so Tier 1 works here too.
    manual.py          Typed by a human, so it trusts the input.
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
