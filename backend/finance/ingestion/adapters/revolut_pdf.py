"""revolut_pdf.py — Revolut annual account-statement PDF importer.

Text extraction is `pdftotext -layout` behind the same injectable
`text_extractor` seam as `amex_pdf` (see `finance.ingestion.adapters.pdf_text`).

Format — Revolut annual statement (en-GB)
-----------------------------------------
The rules below were transcribed from the reference parser in
`tools/bankparse/revolut.py`, which was **verified against one real statement**
(`account-statement_2026-01-01_2026-10-01_en-gb_783080.pdf`, 18 pages). Its
self-test run is recorded under `data/extracted/revolut*_selftest.json`:
extracted money out and money in equal the printed `Balance summary` totals for
every product AND for the `Total` row, and the per-row running-balance
continuity check passes on every row that carries a balance. Treat that as the
authority on the layout.

* **One file can cover several products.** The verified statement carries an
  "Account transactions ..." section and a "Deposit transactions ..." section,
  and the balance summary has one row per product.
* **Amounts use a PERIOD decimal separator, the opposite of the Dutch
  statements**: `EUR 5,378.27` is 537827 minor units, comma thousands. The
  Dutch parsers read `5.471,14` as 547114. Never reuse one converter for both —
  this module has its own `to_minor_units` and does not share the Dutch one.
* Money out and money in are **separate columns**, not a single signed column.
  The last column is a running balance, which makes per-row arithmetic checkable
  and is what proved the parse complete rather than merely plausible.
* A description may wrap onto following, more-indented lines. Those carry either
  sub-fields ("To: <counterparty>", "Card: 416598******3958") or the tail of a
  wrapped description ("Net Interest Paid ... for 25 May" then "2026").
* Balances go negative and the statement prints the sign OUTSIDE the symbol
  ("-EUR 9.10"), so the sign is matched before the currency, not inside the
  digits.
* The statement repeats the same legal footer and running header on all 18
  pages, so the furniture is stripped per page (each footer runs to its
  "Page N of M" line). A single truncation at the first footer would silently
  discard the other 17 pages.

**Attribution, and the honest limit of it.** The verified statement does not
state which IBAN belongs to which product. The reference attributed the Deposit
section to the secondary IBAN *by inference* and recorded that it was an
inference. This adapter does the same and flags it in `raw_data`
(`account_ref_inferred`), and it does **not** guess an `account_id`: rows from a
section other than the caller's account come back with `account_id=None` unless
the caller supplies `section_account_ids`. An unattributed row is visible; a
wrongly attributed row is not.

**Privacy.** Counterparty and reference strings carry full IBANs (12 distinct
ones in the verified statement). The project's rule is last-four-only, and
`raw_data` is immutable forever, so masking happens at parse time.

Sign convention: money leaving the account negative, arriving positive.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping
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
    "RevolutPdfAdapter",
    "extract_pdf_text",
    "iter_transactions",
    "mask_iban",
    "redact_ibans",
    "strip_page_furniture",
    "to_minor_units",
]

_MONTHS: Final = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Sept": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

# The statement is English (en-GB), so amounts print in European format but with
# a PERIOD decimal mark: "EUR 5,378.27". The sign sits outside the symbol
# ("-EUR 9.10"), so it is captured before the currency, not inside the digits.
_AMOUNT_RE = re.compile(r"(-?)€\s*([\d.,]+)")

# A row opens with "1 Jan 2026" followed by two or more spaces.
_ROW_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Z][a-z]{2,4})\s+(\d{4})\s{2,}")

# "<Product> transactions from <from> to <to>" opens a section.
_SECTION_RE = re.compile(
    r"^\s*(\w+)\s+transactions\s+from\s+(.+?)\s+to\s+(.+?)\s*$", re.I
)

# The transaction table's column header. It repeats at the top of every page, so
# the column anchors are re-read each time it appears.
_COLUMN_HEADER_RE = re.compile(r"Description.*Money\s+out.*Money\s+in.*Balance", re.I)

# The balance-summary table's header. "Closing balance" wraps over two lines,
# which is why the anchors are taken from a block of lines.
_SUMMARY_HEADER_RE = re.compile(
    r"Product.*Opening\s+balance.*Money\s+out.*Money\s+in", re.I
)

_CLOSING_LABEL_RE = re.compile(r"Closing")
_BALANCE_LABEL_RE = re.compile(r"\bBalance\b")
_PAGE_NUMBER_RE = re.compile(r"Page\s+\d+\s+of\s+\d+")
# The running header repeats at the top of every page, just after the form feed:
# "EUR Statement", the generated stamp "Generated on the <d> <Mon> <year>", then
# the bank name. The stamp carries its date, so an earlier version that matched
# the bare prefix "Generated on the" as a whole line left the dated line in the
# body; `_absorb_continuation` then folded it onto the last row of the previous
# page as a wrapped-description tail (LifeOS-qvv). Anchored to the WHOLE line, so
# a genuine wrapped description can never be mistaken for the stamp, and the date
# is optional so a statement that prints a bare stamp is still covered.
_RUNNING_HEADER_RE = re.compile(
    r"^\s*(?:EUR Statement"
    r"|Generated on the(?:\s+\d{1,2}\s+[A-Z][a-z]{2,4}\s+\d{4})?"
    r"|Revolut Bank UAB \(Netherlands Branch\))\s*$",
    re.I,
)

_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")
_BIC_RE = re.compile(r"\bBIC\s+([A-Z0-9]{8,11})\b")
_GENERATED_RE = re.compile(r"Generated on the (.+?)\s*$", re.I | re.M)
_CARD_RE = re.compile(r"Card:\s*(\d{6})\*+(\d{4})")
_TO_RE = re.compile(r"^To:\s*(.+)$")
_FROM_RE = re.compile(r"^From:\s*(.+)$")
_REFERENCE_RE = re.compile(r"^Reference:\s*(.+)$")

# Foreign-currency context for a EUR row (investment top-ups), printed as a bare
# "$1,234.56" on a continuation line. Recorded raw: the statement carries no
# exchange rate to pair it with, so nothing is computed from it.
_FX_AMOUNT_RE = re.compile(r"^\$([\d.,]+)$")

# The per-page footer block, dropped from its first marker to its "Page N of M".
_FOOTER_MARKERS: Final = (
    "Report lost or stolen card",
    "Get help directly in app",
    "Scan the QR code",
    "© 2026 Revolut",
    "AFM number",
)

# "To Savings" / "From Savings" and "To Instant Access Savings": moving your own
# money between your own Revolut products. Not spending, and not income.
_INTERNAL_TRANSFER_RE = re.compile(
    r"^(?:to|from)\s+(?:instant\s+access\s+)?savings$", re.I
)

# Section name -> the product name reported in raw_data, and whether the mapping
# is stated by the statement or inferred by this adapter.
_SECTION_PRODUCTS: Final = {"account": "Current Account", "deposit": "Deposit"}


class _ParsedRow:
    """One statement row plus the sub-fields of its continuation lines."""

    __slots__ = (
        "date",
        "description",
        "extras",
        "line_number",
        "money_in",
        "money_out",
        "product",
    )

    def __init__(
        self,
        *,
        date: dt.date,
        description: str,
        product: str,
        money_out: int | None,
        money_in: int | None,
        line_number: int,
    ) -> None:
        self.date = date
        self.description = description
        self.product = product
        self.money_out = money_out
        self.money_in = money_in
        self.line_number = line_number
        self.extras: dict[str, str | int | None] = {}


def to_minor_units(text: str) -> int:
    """`'5,378.27'` -> `537827`. **Period is decimal, comma is thousands.**

    The inverse of the Dutch parsers in this package, which read `5.471,14` as
    547114. Sharing one converter between the two would silently corrupt half
    the imports, which is why this is a separate function with its own docstring
    rather than a flag on a shared one.

    Raises:
        ValueError: If `text` is not a Revolut-formatted amount.
    """
    cleaned = text.replace("€", "").strip().replace(" ", "")
    if not cleaned:
        msg = "empty amount"
        raise ValueError(msg)
    negative = cleaned.startswith("-")
    digits = cleaned.lstrip("-").replace(",", "")
    if "." in digits:
        whole, _, fraction = digits.partition(".")
        fraction = (fraction + "00")[:2]
    else:
        whole, fraction = digits, "00"
    if not whole.isdigit() or not fraction.isdigit():
        msg = f"unparseable amount: {text!r}"
        raise ValueError(msg)
    value = int(whole) * 100 + int(fraction)
    return -value if negative else value


def strip_page_furniture(lines: list[str]) -> tuple[list[str], int]:
    """Drop the per-page legal footer block and the repeated running header.

    The verified statement is 18 pages and repeats the same footer and header on
    every one, so each footer is dropped from its first marker through its
    "Page N of M" line. Truncating at the FIRST footer would silently discard the
    other 17 pages.

    Returns:
        `(kept_lines, dropped_count)`.
    """
    kept: list[str] = []
    in_footer = False
    dropped = 0
    for line in lines:
        if not in_footer and any(marker in line for marker in _FOOTER_MARKERS):
            in_footer = True
        if in_footer:
            dropped += 1
            if _PAGE_NUMBER_RE.search(line):
                in_footer = False
            continue
        if _RUNNING_HEADER_RE.match(line):
            dropped += 1
            continue
        kept.append(line)
    return kept, dropped


def column_anchors(*header_lines: str) -> dict[str, int]:
    """Midpoint boundaries between left-aligned column labels, as character offsets.

    Takes the header as a BLOCK rather than one line, because the summary table
    wraps "Closing balance" over two lines. Labels are left-aligned, so the first
    offset at which any block line contains a label is that column's start.

    Returns:
        Column name -> boundary offset, or an empty dict when the header is too
        incomplete to anchor. Empty means "do not classify", not "guess".
    """
    found: dict[str, int] = {}
    for name, phrase in (
        ("open", "Opening balance"),
        ("out", "Money out"),
        ("in", "Money in"),
        ("close", "Closing balance"),
    ):
        positions = [line.index(phrase) for line in header_lines if phrase in line]
        if positions:
            found[name] = min(positions)
    if "close" not in found:
        # The transaction table labels its last column plain "Balance"; the
        # summary table splits "Closing balance" over two lines. Without a close
        # anchor the running balance would be misclassified into "Money in".
        candidates: list[int] = []
        for line in header_lines:
            for pattern in (_CLOSING_LABEL_RE, _BALANCE_LABEL_RE):
                match = pattern.search(line)
                if match:
                    candidates.append(match.start())
        if candidates:
            found["close"] = min(candidates)
    if not {"out", "in", "close"} <= found.keys():
        return {}
    ordered = sorted(found.items(), key=lambda item: item[1])
    bounds: dict[str, int] = {}
    for index, (name, start) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else 10**6
        bounds[name] = (start + end) // 2
    return bounds


def _classify(
    tokens: list[tuple[int, int]], bounds: dict[str, int], order: list[str]
) -> dict[str, int]:
    """Assign each `(offset, minor)` amount to a column by nearest preceding bound."""
    classified: dict[str, int] = {}
    for position, value in tokens:
        chosen = order[-1]
        for name in order:
            if position < bounds.get(name, 10**6):
                chosen = name
                break
        classified[chosen] = value
    return classified


def _amounts_on(line: str) -> list[tuple[int, int]]:
    """Every `€`-amount on a line as `(character offset, signed minor units)`."""
    found: list[tuple[int, int]] = []
    for match in _AMOUNT_RE.finditer(line):
        try:
            value = to_minor_units(match.group(2))
        except ValueError:
            continue
        found.append((match.start(), -value if match.group(1) and value > 0 else value))
    return found


def statement_ibans(lines: list[str]) -> list[str]:
    """Every IBAN the statement prints, in order of first appearance.

    Unmasked, because the caller has to resolve an account from one. Never placed
    on a `RawRecord`: `raw_data` carries only the masked form.
    """
    text = "\n".join(lines)
    seen: list[str] = []
    for match in _IBAN_RE.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def statement_generated(lines: list[str]) -> str | None:
    """The printed `Generated on the ...` stamp, when present."""
    match = _GENERATED_RE.search("\n".join(lines))
    return match.group(1).strip() if match else None


def extract_pdf_text(payload: bytes) -> list[str]:
    """Extract a PDF's text layer with `pdftotext -layout`.

    The injectable seam; see `amex_pdf.extract_pdf_text` for the same contract.

    Raises:
        PdfTextUnavailableError: `pdftotext` (poppler-utils) is not installed.
        PdfTextExtractionError: The binary ran and produced no usable text.
    """
    return _pdftotext_extract(payload)


class RevolutPdfAdapter(ImportAdapter):
    """Parses a Revolut annual statement PDF into RawRecords.

    One file can carry several products, so a single `account_id` is not enough.
    `section_account_ids` maps a section name ("Account", "Deposit") to the local
    account for that product; sections absent from the mapping come back with
    `account_id=None` rather than inheriting an account that may be wrong.
    """

    provider: ClassVar[str] = "revolut_pdf"
    import_method: ClassVar[str] = "pdf"
    # Multi-section table, wrapped descriptions, per-page repeated headers.
    confidence_weight: ClassVar[float] = -0.15

    def __init__(
        self,
        text_extractor: Callable[[bytes], list[str]] = extract_pdf_text,
        *,
        section_account_ids: Mapping[str, int] | None = None,
    ) -> None:
        self._extract = text_extractor
        self._section_account_ids = dict(section_account_ids or {})

    def parse(
        self,
        payload: bytes,
        account_id: int | None = None,
        filename: str | None = None,
    ) -> ImportResult:
        """Extract the text, then parse each transaction row of each section."""
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

        for row in iter_transactions(
            extracted,
            account_id=account_id,
            section_account_ids=self._section_account_ids,
        ):
            if isinstance(row, AdapterParseError):
                result.failed.append(row)
            else:
                result.records.append(row)
        return result


def iter_transactions(
    lines: list[str],
    *,
    account_id: int | None = None,
    section_account_ids: Mapping[str, int] | None = None,
) -> list[RawRecord | AdapterParseError]:
    """Parse every row of every section into records and reported failures.

    Args:
        lines: Extracted text lines.
        account_id: The account for the statement's primary (Account) section.
        section_account_ids: Optional per-section account ids, for the statement
            covering more than one product.

    Returns:
        Records and errors in document order.
    """
    body, _dropped = strip_page_furniture(lines)
    per_section = dict(section_account_ids or {})

    ibans = statement_ibans(body)
    # The verified statement lists the NL account IBAN first and the LT deposit
    # IBAN second. That ordering is an observation, not a statement, so the
    # Deposit section's account is flagged as inferred below.
    account_refs = {
        name: (mask_iban(iban) if iban else None)
        for name, iban in (
            ("account", ibans[0] if ibans else None),
            ("deposit", ibans[1] if len(ibans) > 1 else None),
        )
    }

    staged: list[RawRecord | _ParsedRow | AdapterParseError] = []
    section_name: str | None = None
    product = ""
    bounds: dict[str, int] = {}
    last: _ParsedRow | None = None

    for index, line in enumerate(body, start=1):
        section_match = _SECTION_RE.match(line)
        if section_match is not None:
            section_name = section_match.group(1)
            product = _SECTION_PRODUCTS.get(section_name.lower(), section_name)
            # Column anchors are per page, and the header repeats, so a section
            # cannot inherit them.
            bounds = {}
            last = None
            continue

        if section_name is None:
            # The balance summary and the account-identification block live above
            # the first section. Not transactions.
            continue

        if _COLUMN_HEADER_RE.search(line):
            # Re-anchored on EVERY occurrence, not only the first: the header
            # repeats at the top of each page and its offsets differ per page.
            bounds = column_anchors(line)
            continue

        if not line.strip():
            continue

        if not bounds:
            staged.append(
                AdapterParseError(
                    "content before any column header in this section, so its "
                    "money-out/money-in columns cannot be resolved",
                    index,
                    RevolutPdfAdapter.provider,
                )
            )
            continue

        row_match = _ROW_RE.match(line)
        if row_match is not None:
            tokens = _amounts_on(line)
            # The description ends where the run of two-or-more spaces before the
            # amount columns starts.
            description = re.sub(r"\s{2,}.*$", "", line[row_match.end() :]).strip()
            if not description and not tokens:
                staged.append(
                    AdapterParseError(
                        "row anchor matched but there is no description and no amount",
                        index,
                        RevolutPdfAdapter.provider,
                    )
                )
            classified = _classify(tokens, bounds, ["out", "in", "close"])
            last = _ParsedRow(
                date=_parse_statement_date(*row_match.groups()),
                description=description,
                product=product,
                money_out=classified.get("out"),
                money_in=classified.get("in"),
                line_number=index,
            )
            # The running balance is the last column and makes each row's
            # arithmetic checkable, so it is kept rather than discarded.
            last.extras["running_balance_minor"] = classified.get("close")
            staged.append(last)
            continue

        if last is not None:
            last = _absorb_continuation(line, last)

    return [
        _finish(
            item,
            section_name_for=_section_of(product),
            account_id_for=lambda name: per_section.get(
                name, account_id if name == "account" else None
            ),
            account_refs=account_refs,
        )
        if isinstance(item, _ParsedRow)
        else item
        for item in staged
    ]


def _section_of(product: str) -> str:
    """The section key ("account"/"deposit") a product name came from."""
    for name, known in _SECTION_PRODUCTS.items():
        if known == product:
            return name
    return product.lower()


def _parse_statement_date(day: str, month: str, year: str) -> dt.date:
    """`'1'`, `'Jan'`, `'2026'` -> `date(2026, 1, 1)`.

    Raises:
        ValueError: If the month abbreviation is not one the statement uses.
    """
    return dt.date(int(year), _MONTHS[month], int(day))


def _absorb_continuation(line: str, row: _ParsedRow) -> _ParsedRow:
    """Fold one more-indented line into the row above it.

    A line can be a known sub-field, a bare foreign-currency amount, or the tail
    of a wrapped description. Anything else is left out of the description
    rather than guessed at.
    """
    stripped = line.strip()

    card = _CARD_RE.search(stripped)
    if card is not None:
        row.extras["card_last4"] = card.group(2)
        row.extras["card_bin6"] = card.group(1)
        return row
    for pattern, key in (
        (_TO_RE, "counterparty"),
        (_FROM_RE, "counterparty_from"),
        (_REFERENCE_RE, "reference"),
    ):
        found = pattern.match(stripped)
        if found:
            row.extras[key] = redact_ibans(found.group(1).strip())
            return row
    fx = _FX_AMOUNT_RE.match(stripped)
    if fx:
        # Recorded raw. There is no exchange rate in the statement to pair it
        # with, so no converted amount is computed or claimed.
        row.extras["foreign_amount_raw"] = fx.group(1)
        return row
    if line.startswith(" ") and stripped:
        row.description = f"{row.description} {stripped}".strip()
    return row


def _finish(
    row: _ParsedRow,
    *,
    section_name_for: str,
    account_id_for: Callable[[str], int | None],
    account_refs: dict[str, str | None],
) -> RawRecord | AdapterParseError:
    """Turn a parsed row into a record, or explain why it cannot be one."""
    if row.money_out is None and row.money_in is None:
        return AdapterParseError(
            "row has no amount in either money column",
            row.line_number,
            RevolutPdfAdapter.provider,
        )

    signed_minor = -(row.money_out or 0) + (row.money_in or 0)
    account_ref = account_refs.get(section_name_for)
    raw_data: dict[str, str | int | float | bool | None] = {
        key: redact_ibans(value) if isinstance(value, str) else value
        for key, value in row.extras.items()
    }
    raw_data["product"] = row.product
    raw_data["is_internal_transfer"] = bool(
        _INTERNAL_TRANSFER_RE.match(row.description)
    )
    raw_data["account_ref"] = account_ref
    # The Deposit -> secondary-IBAN mapping is inferred, not stated by the
    # statement. Recorded so nothing downstream mistakes it for a fact.
    if section_name_for != "account":
        raw_data["account_ref_inferred"] = True
    raw_data["raw_date"] = row.date.isoformat()
    raw_data["raw_amount_minor"] = signed_minor

    try:
        normalized = normalize_record(
            account_id=account_id_for(section_name_for),
            description=redact_ibans(row.description) or "",
            # Money out negative, money in positive. The columns were separated
            # above, so the sign is already decided here.
            amount=signed_minor,
            currency_code="EUR",
            booked_date=row.date,
            value_date=None,
            # No stable provider id. The statement prints no per-transaction id,
            # so this provider dedupes by Tier-3 fingerprint like Amex does.
            provider_txn_id=None,
            pending=False,
            line_number=row.line_number,
            sign_convention=AmountSignConvention.SIGNED,
            raw_data=raw_data,
        )
    except ValueError as exc:
        return AdapterParseError(str(exc), row.line_number, RevolutPdfAdapter.provider)
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
