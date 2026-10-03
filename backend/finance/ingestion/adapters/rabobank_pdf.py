"""rabobank_pdf.py — Rabobank NL monthly account-statement PDF importer.

Text extraction is `pdftotext -layout` behind the same injectable
`text_extractor` seam as `amex_pdf` (see `finance.ingestion.adapters.pdf_text`).

Format — Dutch Rabobank account statement
-----------------------------------------
The rules below were transcribed from the reference parser in
`tools/bankparse/rabo.py`, which was **verified against four real statements
(2026-06 .. 2026-09)**. Its self-test runs are recorded under
`data/extracted/rabo*_selftest.json`: on every statement, extracted debits
equal the printed `Total amount debited`, extracted credits equal the printed
`Total amount credited`, and `previous + credits - debits == closing balance`.
Treat that as the authority on the layout.

* **The column header repeats at the top of every page, and its character
  offsets DIFFER from page to page.** Verified: Debit@91/Credit@113 on page 1,
  Debit@86/Credit@102 on page 2, Debit@107/Credit@131 on page 5. So an amount is
  classified against the header of the page it appears on. A single global column
  offset silently mislabels *every* amount and can still happen to balance,
  which is exactly why the reference validated the rule against the statement's
  own stated totals rather than assuming it.
* Only one of Debit/Credit is populated per row, so a row carries one amount and
  its sign comes from which column it landed in.
* `Value date` is `DD-MM` with **no year**; the year comes from the statement
  period. A period that straddles New Year needs the December rule in
  `_year_for`, which is why the month is checked rather than just taking the
  period's end year.
* A record is a header line plus zero or more more-indented continuation lines
  carrying SEPA sub-fields: `Mandate Identifier / Creditor ID:`, `End-to-End
  ID:`, `Processing date:`, `Invoice ...`, `. Pas: ... Terminal: ...`,
  `. Appr Cd: ...`.
* `End-to-End ID` is captured raw and is **never used as a key**: verified values
  include a bare `261912`, a value with an embedded datetime
  (`01-09-2026 17:52 7670884404417442`), and an `AI0001398/112` code.
* The last page carries a type-code legend, which is parsed rather than
  hardcoded, so a type code the bank adds later still resolves.

**Privacy.** Counterparty IBANs appear in the row text. The reference masked
every printed IBAN to its last four characters before writing anything to disk,
including inside free-text descriptions, and this adapter does the same — the
project's rule is last-four-only, and `raw_data` is permanent.

Sign convention: credit positive, debit negative — the AIS/ISO convention, so
`AmountSignConvention.SIGNED` passes through unchanged.

**Confidence.** Same -0.15 as the other PDF sources. Per-page column offsets,
continuation lines and a multi-page table are all drift sources.
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
from finance.ingestion.adapters.pdf_text import extract_lines
from finance.ingestion.adapters.pdf_text import extract_pdf_text as _pdftotext_extract
from finance.ingestion.adapters.redaction import (
    mask_iban,
    redact_ibans,
)
from finance.ingestion.normalize import (
    AmountSignConvention,
    normalize_record,
)
from finance.public import RawRecord

__all__ = [
    "RabobankPdfAdapter",
    "extract_pdf_text",
    "iter_transactions",
    "mask_iban",
    "redact_ibans",
    "to_minor_units",
]

# Dutch amount: dot thousands, comma decimal. Neither a leading digit group nor
# the decimal part may be preceded/followed by more digits, so an IBAN fragment
# or a date fragment inside the row cannot be read as an amount.
_AMOUNT_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2})(?![\d])")

# A row opens with a `DD-MM` value date and a two-letter type code.
_ROW_RE = re.compile(r"^\s*(\d{2})-(\d{2})\s+([a-z]{2})(\s|$)")

# The per-page column header. Its character offsets are the column anchors.
_HEADER_RE = re.compile(r"Debit amount\s+Credit amount")

# `xx = meaning` pairs in the last page's type-code legend.
_LEGEND_RE = re.compile(r"(?:^|\s)([a-z]{2})\s*=\s")

# Dutch IBANs print as "NL79 RABO 0000 0000 00": country+check digits, then a
# 4-letter bank code, then three numeric groups. Used here for reading the
# statement's own IBAN and the counterparty IBAN that some rows carry as a
# prefix; redaction itself lives in `redaction.py` so the three adapters share
# one implementation.
_SPACED_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[ ][A-Z]{4}[ ]\d{4}[ ]\d{4}[ ]\d{2})\b")

_PROCESSING_RE = re.compile(r"Processing date:\s*(\d{2}-\d{2}-\d{4})", re.I)
_TERMINAL_RE = re.compile(r"Terminal:\s*(\d+)")
_PAS_RE = re.compile(r"Pas:\s*\d*[xX*]*(\d{4})")
_APPROVAL_RE = re.compile(r"Appr\s*C?d:\s*([0-9A-Fa-f]+)", re.I)
_KENMERK_RE = re.compile(r"Kenmerk\s*-?\s*(\S+)", re.I)
_INVOICE_RE = re.compile(r"Invoice\s+(\S+)", re.I)

# A bare label line with nothing after it: "Mandate Identifier / Creditor ID:".
_BARE_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z /-]*?):\s*$")

# Which bare labels name a field worth keeping, and under which key.
_EXTRACT_KEYS: Final = frozenset(
    {
        "mandate_id",
        "creditor_id",
        "end_to_end_id",
        "processing_date",
        "terminal",
        "card_last4",
        "approval_code",
        "invoice_ref",
        "kenmerk",
    }
)

# Value on the line below a two-column header label, e.g. the opening balance.
_BELOW_LABEL_VALUE_RE = re.compile(r"([\d.,]+)\s*(CR|DR)?\s*$")
_NEXT_LINE_DATE_RE = re.compile(r"\d{2}-\d{2}-\d{4}")


class _ParsedRow:
    """One statement row plus the SEPA sub-fields of its continuation lines."""

    __slots__ = (
        "amount_raw",
        "counterparty_iban_masked",
        "description",
        "extras",
        "is_credit",
        "line_number",
        "type_code",
        "value_date",
    )

    def __init__(
        self,
        *,
        value_date: dt.date,
        type_code: str,
        description: str,
        counterparty_iban_masked: str | None,
        amount_raw: str | None,
        is_credit: bool,
        line_number: int,
    ) -> None:
        self.value_date = value_date
        self.type_code = type_code
        self.description = description
        self.counterparty_iban_masked = counterparty_iban_masked
        self.amount_raw = amount_raw
        self.is_credit = is_credit
        self.line_number = line_number
        self.extras: dict[str, str] = {}


def to_minor_units(text: str) -> int:
    """`'1.260,00'` -> `126000`. Dot is thousands, comma is decimal.

    Integer arithmetic on the concatenated digits: never via float, because
    `"0.1" + 0.2` style error compounds across a ledger.

    Raises:
        ValueError: If `text` is not a Dutch-formatted amount.
    """
    stripped = text.replace(".", "").replace(",", "")
    if not stripped.isdigit():
        msg = f"unparseable Dutch amount: {text!r}"
        raise ValueError(msg)
    return int(stripped)


def _year_for(month: int, period_from: str | None, period_to: str | None) -> int:
    """Infer the year of a `DD-MM` value date from the statement period.

    A value date carries no year. A period that runs December -> January
    straddles the new year, and every December row belongs to the *earlier*
    year, so the December case is checked rather than blindly taking the period's
    end year.
    """
    end_year = int(period_to.split("-")[-1]) if period_to else dt.date.today().year
    start_year = int(period_from.split("-")[-1]) if period_from else end_year
    if start_year != end_year and month == 12:
        return start_year
    return end_year


def parse_legend(lines: list[str]) -> dict[str, str]:
    """The printed `xx = meaning` type-code legend, found rather than assumed.

    Parsed rather than hardcoded so a type code Rabobank introduces later still
    resolves, and so an unrecognised code is visible in `raw_data` as an absent
    meaning rather than silently mislabelled.
    """
    legend: dict[str, str] = {}
    for line in lines:
        hits = list(_LEGEND_RE.finditer(line))
        if len(hits) < 2:
            continue
        for index, match in enumerate(hits):
            end = hits[index + 1].start() if index + 1 < len(hits) else len(line)
            meaning = line[match.end() : end].strip()
            if meaning:
                legend.setdefault(match.group(1), meaning)
    return legend


def _field_below(lines: list[str], label: str) -> str | None:
    """The value sitting on the line below a two-column header label."""
    for index, line in enumerate(lines):
        if label in line and index + 1 < len(lines):
            following = lines[index + 1]
            match = _BELOW_LABEL_VALUE_RE.search(following)
            if match and any(char.isdigit() for char in following):
                suffix = f" {match.group(2)}" if match.group(2) else ""
                return match.group(1) + suffix
    return None


def _date_below(lines: list[str], label: str) -> str | None:
    """A bare `DD-MM-YYYY` on the line below `label`.

    The label is matched anywhere in the line, not only at its start: in a real
    statement "From (date)" shares its line with the postal address, while
    "To (date)" starts its own line.
    """
    for index, line in enumerate(lines):
        if label in line and index + 1 < len(lines):
            match = _NEXT_LINE_DATE_RE.search(lines[index + 1])
            if match:
                return match.group(0)
    return None


def _parse_optional_amount(text: str | None) -> int | None:
    """A balance printed as `1.234,56` or `1.234,56 CR`, as minor units."""
    if not text:
        return None
    cleaned = text.replace(" CR", "").replace(" DR", "")
    try:
        return to_minor_units(cleaned)
    except ValueError:
        return None


def statement_period(lines: list[str]) -> tuple[str | None, str | None]:
    """The printed `From (date)` / `To (date)` bounds, as printed.

    Advisory: the value date, not these bounds, is what dates a row. Real
    statements carry value dates on either side of the printed window, so
    filtering rows on these bounds would drop genuine activity.
    """
    return _date_below(lines, "From (date)"), _date_below(lines, "To (date)")


def statement_iban(lines: list[str]) -> str | None:
    """The statement's own IBAN, unmasked, so the caller can resolve the account.

    Never placed on a `RawRecord`: `raw_data` carries only the masked form.
    """
    for index, line in enumerate(lines):
        if "IBAN / account number" in line and index + 1 < len(lines):
            match = _SPACED_IBAN_RE.search(lines[index + 1])
            if match:
                return re.sub(r"\s+", "", match.group(1))
    return None


def statement_totals(lines: list[str]) -> tuple[int | None, int | None]:
    """The printed `(Total amount debited, Total amount credited)` minor units.

    Exposed because these are the numbers a parse has to reconcile against. The
    reference implementation's self-test compared exactly this pair with the
    extracted rows and found them equal on all four real statements; keeping the
    comparison available is what lets a future statement be checked the same way
    instead of trusted.
    """
    for index, line in enumerate(lines):
        if "Total amount debited" in line and index + 1 < len(lines):
            numbers = _AMOUNT_RE.findall(lines[index + 1])
            if len(numbers) >= 2:
                return to_minor_units(numbers[0]), to_minor_units(numbers[1])
    return None, None


def extract_pdf_text(payload: bytes) -> list[str]:
    """Extract a PDF's text layer with `pdftotext -layout`.

    The injectable seam; see `amex_pdf.extract_pdf_text` for the same contract.

    Raises:
        PdfTextUnavailableError: `pdftotext` (poppler-utils) is not installed.
        PdfTextExtractionError: The binary ran and produced no usable text.
    """
    return _pdftotext_extract(payload)


class RabobankPdfAdapter(ImportAdapter):
    """Parses a Rabobank statement PDF into RawRecords."""

    provider: ClassVar[str] = "rabobank_pdf"
    import_method: ClassVar[str] = "pdf"
    # Per-page column offsets, continuation lines, a multi-page table.
    confidence_weight: ClassVar[float] = -0.15

    def __init__(
        self,
        text_extractor: Callable[[bytes], list[str]] = extract_pdf_text,
    ) -> None:
        self._extract = text_extractor

    def parse(
        self,
        payload: bytes,
        account_id: int | None = None,
        filename: str | None = None,
    ) -> ImportResult:
        """Extract the text, then parse each statement row.

        A file names no account, so `account_id` is whatever the caller resolved;
        None leaves the rows unattributed rather than claiming an account they do
        not belong to.
        """
        result = ImportResult(
            provider=self.provider,
            import_method=self.import_method,
            source_checksum=source_checksum(payload),
            source_filename=filename,
        )
        extracted = extract_lines(self._extract, payload, provider=self.provider)
        if isinstance(extracted, AdapterParseError):
            result.failed.append(extracted)
            return result
        if not extracted:
            result.failed.append(
                AdapterParseError("no extractable text in the PDF", 1, self.provider)
            )
            return result

        for row in iter_transactions(extracted, account_id=account_id):
            if isinstance(row, AdapterParseError):
                result.failed.append(row)
            else:
                result.records.append(row)
        return result


def iter_transactions(
    lines: list[str], *, account_id: int | None
) -> list[RawRecord | AdapterParseError]:
    """Parse every row of a statement into records and reported failures.

    A row that matched the row anchor but carries no amount is a *failure*, not a
    silent skip: it is money the statement printed and this parse could not read,
    and the only honest thing is to say so. The same goes for a non-row line
    between rows that no rule recognised.

    Args:
        lines: Extracted text lines.
        account_id: The resolved account, or None when unattributed.

    Returns:
        Records and errors in document order.
    """
    results: list[RawRecord | _ParsedRow | AdapterParseError] = []
    period_from, period_to = statement_period(lines)
    legend = parse_legend(lines)
    own_iban = statement_iban(lines)
    account_ref = mask_iban(own_iban) if own_iban else None

    # Character offsets of the Debit/Credit header of the page a line is on. The
    # offsets move from page to page in real statements, which is why this is
    # re-anchored on every header line rather than computed once.
    page_header: tuple[int, int] | None = None
    last: _ParsedRow | None = None
    pending_key: str | None = None

    for line_number, line in enumerate(lines, start=1):
        header_match = _HEADER_RE.search(line)
        if header_match is not None:
            page_header = (header_match.start(), header_match.end())
            last = None
            pending_key = None
            continue
        if not line.strip():
            continue

        row_match = _ROW_RE.match(line)
        if row_match is not None:
            if page_header is None:
                results.append(
                    AdapterParseError(
                        "row appears before any column header, so its debit/credit "
                        "column cannot be resolved",
                        line_number,
                        RabobankPdfAdapter.provider,
                    )
                )
                continue
            last, pending_key = _parse_row(
                line,
                row_match,
                page_header=page_header,
                line_number=line_number,
                period_from=period_from,
                period_to=period_to,
            )
            results.append(last)
            continue

        if last is None:
            continue
        pending_key = _absorb_continuation(line, last, pending_key)

    # Continuation lines belong to the row above them, so a row can only be
    # finished once the whole document has been read.
    return [
        _finish(item, account_id=account_id, account_ref=account_ref, legend=legend)
        if isinstance(item, _ParsedRow)
        else item
        for item in results
    ]


def _parse_row(
    line: str,
    match: re.Match[str],
    *,
    page_header: tuple[int, int],
    line_number: int,
    period_from: str | None,
    period_to: str | None,
) -> tuple[_ParsedRow, str | None]:
    """Split one row into its date, type code, description, amount and sign."""
    day, month, type_code = match.group(1), match.group(2), match.group(3)
    debit_at, credit_at = page_header
    boundary = (debit_at + credit_at) // 2

    amounts = [(m.start(), m.group(1)) for m in _AMOUNT_RE.finditer(line)]
    amount_raw = amounts[-1][1] if amounts else None
    # The amount sits to the RIGHT of the boundary in the Credit column. Only one
    # of Debit/Credit is populated per row, so there is never a choice to make.
    is_credit = bool(amounts) and amounts[-1][0] >= boundary

    end = line.rfind(amount_raw) if amount_raw else len(line)
    body = line[match.end() : end]
    description = re.sub(r"\s{2,}", " ", body).strip()
    iban_match = _SPACED_IBAN_RE.match(description)

    value_date = dt.date(
        _year_for(int(month), period_from, period_to), int(month), int(day)
    )
    row = _ParsedRow(
        value_date=value_date,
        type_code=type_code,
        description=description,
        counterparty_iban_masked=(
            mask_iban(iban_match.group(1)) if iban_match else None
        ),
        amount_raw=amount_raw,
        is_credit=is_credit,
        line_number=line_number,
    )
    return row, None


def _absorb_continuation(
    line: str, row: _ParsedRow, pending_key: str | None
) -> str | None:
    """Fold one more-indented line into the row above it.

    Returns the pending field key for the next line, or None.
    """
    stripped = line.strip()

    for pattern, key in (
        (_PROCESSING_RE, "processing_date"),
        (_TERMINAL_RE, "terminal"),
        (_PAS_RE, "card_last4"),
        (_APPROVAL_RE, "approval_code"),
        (_INVOICE_RE, "invoice_ref"),
        (_KENMERK_RE, "kenmerk"),
    ):
        found = pattern.search(stripped)
        if found:
            row.extras[key] = found.group(1)
            return None

    label_match = _BARE_LABEL_RE.match(stripped)
    if label_match:
        key = (
            label_match.group(1).strip().lower().replace(" ", "_").replace("/", "_and_")
        )
        if "mandate" in key or "creditor" in key:
            return "mandate_id"
        if "end" in key and "to" in key:
            return "end_to_end_id"
        return key if key in _EXTRACT_KEYS else None

    if pending_key and stripped:
        previous = row.extras.get(pending_key, "")
        row.extras[pending_key] = f"{previous} {stripped}".strip()
        return None

    if stripped and line.startswith(" "):
        row.extras["supplementary"] = (
            f"{row.extras.get('supplementary', '')} {stripped}"
        ).strip()
    return pending_key


def _finish(
    row: _ParsedRow,
    *,
    account_id: int | None,
    account_ref: str | None,
    legend: dict[str, str],
) -> RawRecord | AdapterParseError:
    """Turn a parsed row into a record, or explain why it cannot be one."""
    if row.amount_raw is None:
        return AdapterParseError(
            "row has no amount", row.line_number, RabobankPdfAdapter.provider
        )

    extras: dict[str, str | int | float | bool | None] = {
        key: redact_ibans(value) for key, value in row.extras.items() if value
    }
    extras["value_date"] = row.value_date.isoformat()
    extras["processing_date"] = extras.get("processing_date")
    extras["amount_raw"] = row.amount_raw
    extras["is_credit"] = row.is_credit
    extras["type_code"] = row.type_code
    # Absent when the bank prints a code its own legend does not define. Left
    # missing on purpose rather than defaulted to a guess.
    extras["type_meaning"] = legend.get(row.type_code)
    extras["counterparty_iban_masked"] = row.counterparty_iban_masked
    extras["account_ref"] = account_ref

    processing_date = _parse_dd_mm_yyyy(row.extras.get("processing_date"))
    try:
        amount_minor = to_minor_units(row.amount_raw)
        # Credit positive, debit negative. The sign came from the page's column
        # layout above, so it is already decided here.
        signed_minor = amount_minor if row.is_credit else -amount_minor
        normalized = normalize_record(
            account_id=account_id,
            description=redact_ibans(row.description) or "",
            amount=signed_minor,
            currency_code="EUR",
            booked_date=row.value_date,
            value_date=processing_date,
            # No stable provider id. Rabobank's End-to-End ID is captured into
            # raw_data but is never a key: verified values include a bare number
            # and a value with an embedded datetime, so it is not unique.
            provider_txn_id=None,
            pending=False,
            line_number=row.line_number,
            sign_convention=AmountSignConvention.SIGNED,
            raw_data={**extras, "raw_amount_minor": signed_minor},
        )
    except ValueError as exc:
        return AdapterParseError(str(exc), row.line_number, RabobankPdfAdapter.provider)
    return RawRecord(
        account_id=normalized.account_id,
        description=normalized.description,
        amount_minor=normalized.amount.amount,
        currency=normalized.amount.currency.code,
        booked_date=normalized.booked_date,
        value_date=normalized.value_date,
        provider_txn_id=normalized.provider_txn_id,
        pending=normalized.pending,
        line_number=normalized.line_number,
        raw_data=normalized.raw_data,
    )


def _parse_dd_mm_yyyy(value: str | None) -> dt.date | None:
    """Parse a printed `DD-MM-YYYY`, or None when absent or unusable.

    A malformed date returns None rather than raising: the row still books on its
    value date, and the raw string is preserved either way.
    """
    if not value:
        return None
    try:
        return dt.datetime.strptime(value.strip(), "%d-%m-%Y").date()
    except ValueError:
        return None
