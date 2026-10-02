"""amex_pdf.py — Amex PDF statement importer, 7 years of history.

No PDF dependency. Extraction is injected via `text_extractor`, which keeps this
file testable with a string and keeps `pdfplumber` (M7's concern) out of the
M0 dependency set. `extract_pdf_text` is the seam that M7 fills.

Format (verified Dutch Amex NL ``pdftotext -layout``):

* Every row carries TWO dates: ``Transactiedatum`` and ``Datum verwerkt``
  (processing). They differ on roughly a third of rows, so both are preserved.
* Dates are printed ``DD.MM.YY``; the year is expanded row-locally.
* Amounts are unsigned Dutch format. A credit is marked by a line containing
  only ``CR`` printed *beneath* the amount, not by a sign in the amount itself.
* Descriptions can wrap across continuation lines; the wrapped text must append
  to the merchant description rather than replace it.
* The printed ``Periode`` is advisory; rows dated just outside it are genuine
  and must not be filtered away.

PDF is the noisiest source we have. Column alignment drifts, a description can
wrap across two lines, and a page break can split one row in half. That costs
this adapter its confidence weight: **-0.15** relative to a clean CSV
(section G), which pushes borderline matches into the review queue instead of
merging them.

PDF is the only Amex source. There is no CSV to import alongside it, so nothing
here is reconciling two views of the same months. Amex exposes no stable provider
ID — its own IDs are documented to change — which leaves Tier 3 fingerprints as
the only dedup available: re-importing the same PDF has to add nothing, and it
has to hold for a different statement covering the same rows. See
`docs/adr/0002-import-provider-enum.md` for why Amex CSV is retired.
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
    normalize_record,
)
from finance.public import RawRecord

__all__ = [
    "AMEX_PDF_DATE_FORMATS",
    "AmexPdfAdapter",
    "extract_pdf_text",
    "iter_transactions",
]

# Retained for API compatibility; the verified Dutch layout uses DD.MM.YY only.
AMEX_PDF_DATE_FORMATS: Final = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y")

# Real Dutch Amex NL rows: two 2-digit dates, then description, then amount.
# Transactiedatum (first) and Datum verwerkt (second) differ on many rows.
_ROW_START = re.compile(
    r"^\s*(?P<txn_day>\d{2})\.(?P<txn_month>\d{2})\.(?P<txn_year>\d{2})\s+"
    r"(?P<post_day>\d{2})\.(?P<post_month>\d{2})\.(?P<post_year>\d{2})\s+"
    r"(?P<rest>.+)$"
)

# Dutch amount at the end of a row: thousands separated by dots, decimal comma.
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*$")

# A credit marker is a separate line containing only CR printed beneath the
# amount. The amount column itself carries no sign, so this marker is the only
# way to distinguish a refund/card payment from a purchase.
_CR_RE = re.compile(r"^\s*CR\s*$")

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


class _ParsedRow:
    """A row start after the leading two dates have been parsed."""

    def __init__(
        self,
        txn_date: dt.date,
        post_date: dt.date,
        description_source: str,
        amount_text: str,
    ) -> None:
        self.txn_date = txn_date
        self.post_date = post_date
        self.description_source = description_source
        self.amount_text = amount_text


class _PendingRow:
    """A row whose trailing fields have not been resolved yet."""

    def __init__(
        self,
        description_source: str,
        amount_text: str,
        txn_date: dt.date,
        post_date: dt.date,
        line_number: int,
    ) -> None:
        self.description_source = description_source
        self.amount_text = amount_text
        self.txn_date = txn_date
        self.post_date = post_date
        self.line_number = line_number
        self.is_credit = False


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

        parsed = _parse_row(stripped)
        if parsed is not None:
            # A new row starts. Flush the previous one.
            if pending is not None:
                results.append(_finish(pending, account_id))
            pending = _PendingRow(
                description_source=parsed.description_source,
                amount_text=parsed.amount_text,
                txn_date=parsed.txn_date,
                post_date=parsed.post_date,
                line_number=line_number,
            )
            continue

        # Not a new row: either a credit marker or a description continuation.
        if pending is not None:
            if _CR_RE.match(stripped):
                pending.is_credit = True
            else:
                pending.description_source = (
                    f"{pending.description_source} {stripped}"
                ).strip()

    if pending is not None:
        results.append(_finish(pending, account_id))
    return results


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


def _parse_row(text: str) -> _ParsedRow | None:
    """If the line starts with two Dutch-layout dates, parse them and the rest."""
    match = _ROW_START.match(text)
    if match is None:
        return None
    txn_date = _expand_date(
        match.group("txn_day"), match.group("txn_month"), match.group("txn_year")
    )
    post_date = _expand_date(
        match.group("post_day"), match.group("post_month"), match.group("post_year")
    )
    rest = match.group("rest")
    amount_text = _take_amount(rest)
    description_source = _AMOUNT.sub("", rest).strip()
    return _ParsedRow(txn_date, post_date, description_source, amount_text)


def _expand_date(day: str, month: str, year: str) -> dt.date:
    """Expand a DD.MM.YY date into a full date, using a sensible century pivot."""
    yy = int(year)
    yyyy = 1900 + yy if yy > 80 else 2000 + yy
    return dt.date(yyyy, int(month), int(day))


def _take_amount(text: str) -> str:
    """Strip a trailing amount off a row, returning it. Empty when absent."""
    match = _AMOUNT.search(text)
    return match.group(1).strip() if match else ""


def _to_minor(text: str) -> int:
    """Parse a Dutch-formatted amount string into unsigned minor units."""
    s = text.replace(".", "").replace(",", "")
    if not s.isdigit():
        raise ValueError(f"unparseable Dutch amount: {text!r}")
    return int(s)


def _finish(pending: _PendingRow, account_id: str) -> RawRecord | AdapterParseError:
    """Turn a pending row into a record, or explain why it cannot be one."""
    description = pending.description_source.strip()
    if not pending.amount_text:
        return AdapterParseError(
            "row has no amount", pending.line_number, AmexPdfAdapter.provider
        )
    try:
        amount_minor = _to_minor(pending.amount_text)
        signed_minor = amount_minor if pending.is_credit else -amount_minor
        normalized = normalize_record(
            account_id=account_id,
            description=description,
            amount=signed_minor,
            currency_code="EUR",
            booked_date=pending.txn_date,
            value_date=pending.post_date,
            # No stable provider id, same as the CSV: Amex's own IDs change.
            provider_txn_id=None,
            pending=False,
            line_number=pending.line_number,
            sign_convention=AmountSignConvention.SIGNED,
            raw_data={
                "raw_date": pending.txn_date.isoformat(),
                "raw_posting_date": pending.post_date.isoformat(),
                "raw_amount_minor": signed_minor,
                "is_credit": pending.is_credit,
            },
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
