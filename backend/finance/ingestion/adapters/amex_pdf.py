"""amex_pdf.py — Amex PDF statement importer, 7 years of history.

No PDF dependency. Extraction is injected via `text_extractor`, which keeps this
file testable with a string and keeps `pdfplumber` (M7's concern) out of the
M0 dependency set. `extract_pdf_text` is the seam that M7 fills.

PDF is the noisiest source we have. Column alignment drifts, a description can
wrap across two lines, and a page break can split one row in half. That costs
this adapter its confidence weight: **-0.15** relative to a clean CSV
(section G), which pushes borderline matches into the review queue instead of
merging them.

The same file's CSV counterpart covers the last 6 months. Importing the PDF
first and the CSV after must not create duplicates — that non-destructiveness is
exactly what Tier 3 fingerprints buy.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import ClassVar, Final

from finance.ingestion.adapters.base import (
    AdapterParseError,
    ImportAdapter,
    ImportResult,
    source_checksum,
)
from finance.ingestion.normalize import (
    AmountSignConvention,
    normalize_european_number,
    normalize_record,
)
from finance.public import RawRecord

__all__ = [
    "AMEX_PDF_DATE_FORMATS",
    "AmexPdfAdapter",
    "extract_pdf_text",
    "iter_transactions",
]

AMEX_PDF_DATE_FORMATS: Final = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y")

# A row starts with a leading date. Descriptions never do, which is what makes
# this the anchor rather than "split on whitespace".
_ROW_START = re.compile(
    r"^\s*(?P<day>\d{1,2})[\s/.-]+(?P<month>[A-Za-z]{3,9}|\d{1,2})[\s/.-]+"
    r"(?P<year>\d{4})\s+(?P<rest>.+)$"
)
_AMOUNT = re.compile(r"(-?€?\s?-?\d[\d.,]*\d|\d+,\d{2})\s*$")
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ),
        start=1,
    )
}

# Words Amex prints that are not transactions.
_SECTION_HEADINGS = frozenset(
    {
        "payments",
        "transactions",
        "card summary",
        "activity",
        "new balance",
        "previous balance",
        "payment due",
        "credit limit",
        "available credit",
        "page",
        "continued",
        "date",
        "description",
        "amount",
    }
)


def extract_pdf_text(payload: bytes) -> list[str]:
    """Placeholder text extraction: return the payload as one line per byte run.

    A real implementation needs `pdfplumber`, which M7 introduces. Until then
    this is honest about what it is — the injectable seam — rather than a
    half-parser that silently produces wrong transactions.

    Args:
        payload: The PDF bytes.

    Returns:
        Text lines. This placeholder decodes and splits, which is enough to
        exercise the row parser with a realistic fixture.
    """
    return payload.decode("utf-8", errors="replace").splitlines()


class AmexPdfAdapter(ImportAdapter):
    """Parses Amex PDF statement text into RawRecords."""

    provider: ClassVar[str] = "amex_pdf"
    import_method: ClassVar[str] = "pdf"
    # Noisiest source: column alignment, wrapped descriptions, page breaks.
    confidence_weight: ClassVar[float] = -0.15

    def __init__(
        self,
        text_extractor: Callable[[bytes], list[str]] = extract_pdf_text,
    ) -> None:
        self._extract = text_extractor

    def parse(
        self,
        payload: bytes,
        account_id: str = "",
        filename: str | None = None,
    ) -> ImportResult:
        """Extract the text, then parse each transaction row.

        Raises:
            AdapterParseError: If extraction returns nothing at all.
        """
        result = ImportResult(
            provider=self.provider,
            import_method=self.import_method,
            source_checksum=source_checksum(payload),
            source_filename=filename,
        )
        lines = self._extract(payload)
        if not lines:
            result.failed.append(
                AdapterParseError("no extractable text in the PDF", 1, self.provider)
            )
            return result

        for record in iter_transactions(lines, account_id=account_id):
            if isinstance(record, AdapterParseError):
                result.failed.append(record)
            else:
                result.records.append(record)
        return result


def iter_transactions(
    lines: list[str], *, account_id: str, first_line: int = 1
) -> list[RawRecord | AdapterParseError]:
    """Parse transaction rows out of statement text.

    Each output is either a RawRecord or the AdapterParseError explaining why a
    row was skipped, so the caller never loses the reason for a dropped row.

    Description continuation lines — a wrapped payee, a long reference — are
    appended to the row above rather than dropped. Dropping them would change
    the description, and therefore the fingerprint, for the same purchase.

    Args:
        lines: Extracted text lines.
        account_id: The account to attribute rows to; a PDF names none.
        first_line: Line number of `lines[0]`, for stable indices across a page
            boundary in the caller.

    Returns:
        Records and errors in document order.
    """
    results: list[RawRecord | AdapterParseError] = []
    pending: _PendingRow | None = None
    line_number = first_line

    for text in lines:
        line_number += 1
        stripped = text.strip()
        if not stripped:
            continue
        if _is_heading(stripped):
            continue

        parsed_date = _parse_row_date(stripped)
        if parsed_date is None:
            # A continuation of the description above.
            if pending is not None:
                pending.description = f"{pending.description} {stripped}".strip()
            continue

        # A new row starts. Flush the previous one.
        if pending is not None:
            results.append(_finish(pending, account_id))
        booked_date, rest = parsed_date
        pending = _PendingRow(
            description="",
            amount_text=_take_amount(rest),
            booked_date=booked_date,
            line_number=line_number,
            description_source=rest,
        )

    if pending is not None:
        results.append(_finish(pending, account_id))
    return results


class _PendingRow:
    """A row whose trailing fields have not been resolved yet."""

    def __init__(
        self,
        description: str,
        amount_text: str,
        booked_date: dt.date,
        line_number: int,
        description_source: str,
    ) -> None:
        self.description = description
        self.amount_text = amount_text
        self.booked_date = booked_date
        self.line_number = line_number
        self.description_source = description_source


def _is_heading(text: str) -> bool:
    """Whether a line is statement furniture rather than a transaction.

    Matched as a PREFIX, not an equality: a summary row in a real statement is
    "New Balance                                33,18", so the heading word is followed
    by whitespace and a figure. An exact match would let those through as
    transactions, and a EUR 33.18 "New Balance" would land in the ledger as a
    purchase.

    Requires whitespace after the heading rather than any prefix, so a merchant
    whose name merely begins with a heading word ("Payment Solutions BV") is not
    silently dropped.
    """
    lowered = text.lower().rstrip(":")
    if lowered in _SECTION_HEADINGS:
        return True
    return any(lowered.startswith(f"{heading} ") for heading in _SECTION_HEADINGS)


def _parse_row_date(text: str) -> tuple[dt.date, str] | None:
    """If the line starts with a transaction date, return it and the remainder."""
    match = _ROW_START.match(text)
    if match is None:
        return None
    date_value = f"{match.group('day')} {match.group('month')} {match.group('year')}"
    return _parse_date(date_value), match.group("rest")


def _parse_date(value: str) -> dt.date:
    for fmt in AMEX_PDF_DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    # Numeric month, e.g. "03 04 2026".
    parts = value.split()
    if len(parts) == 3:
        day, month, year = parts
        try:
            return dt.date(int(year), int(month), int(day))
        except ValueError:
            pass
    month_index = _MONTHS.get(parts[1].lower()[:3]) if len(parts) == 3 else None
    if month_index is not None:
        try:
            return dt.date(int(parts[2]), month_index, int(parts[0]))
        except ValueError:
            pass
    raise ValueError(f"unparseable PDF date {value!r}")


def _take_amount(text: str) -> str:
    """Strip a trailing amount off a row, returning it. Empty when absent."""
    match = _AMOUNT.search(text)
    return match.group(1).strip() if match else ""


def _finish(pending: _PendingRow, account_id: str) -> RawRecord | AdapterParseError:
    """Turn a pending row into a record, or explain why it cannot be one."""
    description = pending.description or pending.description_source.strip()
    description = _AMOUNT.sub("", description).strip()
    if not pending.amount_text:
        return AdapterParseError(
            "row has no amount", pending.line_number, AmexPdfAdapter.provider
        )
    try:
        normalized = normalize_record(
            account_id=account_id,
            description=description,
            amount=normalize_european_number(pending.amount_text.replace("€", "")),
            currency_code="EUR",
            booked_date=pending.booked_date,
            # No stable provider id, same as the CSV: Amex's own IDs change.
            provider_txn_id=None,
            pending=False,
            line_number=pending.line_number,
            sign_convention=AmountSignConvention.POSITIVE_IS_DEBIT,
        )
    except ValueError as exc:
        return AdapterParseError(str(exc), pending.line_number, AmexPdfAdapter.provider)
    return RawRecord(
        account_id=normalized.account_id,
        description=normalized.description,
        amount_minor=normalized.amount.amount,
        currency=normalized.amount.currency.code,
        booked_date=normalized.booked_date,
        value_date=normalized.value_date,
        provider_txn_id=normalized.provider_txn_id,
        pending=normalized.pending,
        line_number=pending.line_number,
        raw_data=normalized.raw_data,
    )
