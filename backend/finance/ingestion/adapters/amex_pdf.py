"""amex_pdf.py — Amex PDF statement importer, 7 years of history.

Text extraction is `pdftotext -layout` behind the injectable
`text_extractor` seam (`finance.ingestion.adapters.pdf_text`), which keeps this
file testable with a list of strings and keeps the subprocess out of every test
that is not about extraction.

Format — Dutch Amex NL "Maandafrekening", The Gold Card
------------------------------------------------------------
The rules below were transcribed from the reference parser in
`tools/bankparse/amex.py`, which was **verified against four real statements
dated 23.06-23.09.2026** (periods 24.05-23.06 through 24.08-23.09). Its
self-test runs are recorded under `data/extracted/amex*_selftest.json`: on
every statement, extracted charges equal the printed `Debiteringen`, extracted
credits equal the printed `Crediteringen`, and
`Vorig + Debiteringen - Crediteringen == Nieuw saldo`. Treat that as the
authority on the layout, not as folklore:

* **Two dates per row, and both are load-bearing.** `Transactiedatum` (first)
  and `Datum verwerkt` (processing, second) differ on roughly a third of the
  rows. They are preserved as a pair and never collapsed to one date.
* **A credit is a bare `CR` line printed BENEATH the amount, not a sign.** The
  amount column holds charges and credits as positive numbers alike, so the `CR`
  marker is the *only* thing that distinguishes a refund from a purchase. This
  is load-bearing: one statement's credit section mixes the monthly card payment
  ("HARTELIJK BEDANKT VOOR UW BETALING") with genuine refunds, and conflating
  them turns real refunds into repayments.
* **The `Bedrag in vreemde valuta` column is empty in all four statements.** No
  original amount and no exchange rate are available from this export, so none
  is invented. The amount is taken from the END of the row rather than as "the
  last amount on the line", which is what makes that safe: if a future vintage
  does populate the foreign-currency column, an end-anchored read keeps taking
  the EUR amount instead of silently swapping in the foreign one.
* **Dutch number format** — dot thousands, comma decimal (`1.260,00`).
* **The printed `Periode` is advisory, never a filter.** Real statements carry
  transactions dated the day before the period starts, so filtering on it would
  drop genuine spend.
* The statement date is the *filename* date; the period is printed separately.
  The reference parser therefore expanded every row's two-digit year using the
  period's year. `_expand_date` below expands it row-locally with the same
  20xx pivot, which agrees for these statements and additionally survives a
  statement whose `Periode` line did not extract.
* A large fixed legal/footer block repeats on every page ("American Express
  Europe S.A. gevestigd ...") together with a "Nieuwe transacties voor:"
  sub-header. Those are not transactions and are skipped.

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
from finance.ingestion.adapters.pdf_text import extract_lines
from finance.ingestion.adapters.pdf_text import (
    extract_pdf_text as _pdftotext_extract,
)
from finance.ingestion.adapters.redaction import (
    redact_card_numbers,
    redact_ibans,
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
    "printed_period",
    "redact_card_numbers",
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
# The second alternative covers a four-digit group printed without a thousands
# separator (`1234,56`), which the dot-grouped form alone would reject.
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})\s*$")

# A credit marker is a separate line containing only CR printed beneath the
# amount. The amount column itself carries no sign, so this marker is the only
# way to distinguish a refund/card payment from a purchase.
_CR_RE = re.compile(r"^\s*CR\s*$")

# The monthly card payment. It lands in the same credit section as genuine
# refunds and is positive in exactly the same way, so the two are separated by
# description rather than by sign — see the module docstring.
_CARD_PAYMENT_RE = re.compile(r"HARTELIJK\s+BEDANKT\s+VOOR\s+UW\s+BETALING", re.I)

# Printed statement furniture, verified present in all four statements: the
# repeating legal block and its "Nieuwe transacties voor:" sub-header, the
# address-correction notice, the card-payment reminder, and the non-prose
# summary lines that follow the transaction table ("Totaal voor:", etc.). A line
# containing any of these is not a transaction. Checked as a substring because
# poppler's -layout output breaks the legal block across several lines at column
# boundaries.
_STATEMENT_FURNITURE: Final = (
    "American Express Europe S.A. gevestigd",
    "Nieuwe transacties voor:",
    "Is het adres onjuist?",
    "Het te betalen bedrag is met",
    "Totaal voor:",
    "Overige Transacties:",
    "Totaal Overige Transacties :",
)

# Headings that mark the start of a non-transaction block (the trailing legal
# section on pages 1-3, the running header that repeats on every page, and the
# Membership Rewards summary on page 4). Once one of these is seen, continuation
# lines must not be appended to the pending row until a new real row starts. The
# two-column layout means left/right headings can share one line; matching is by
# prefix so a heading is not confused with prose that merely contains the words.
# A heading line carries no trailing amount.
_LEGAL_SECTION_HEADINGS: Final = frozenset(
    {
        # Page 1-3 legal block headings.
        "belangrijke informatie",
        "handmatig betalen",
        "automatische incasso",
        "online services",
        "correspondentieadres",
        "bankgegevens",
        "zakelijke uitgaven",
        "american express app",
        # Running header that repeats at the top of every page. When it appears
        # after the last real row on a page it marks the start of the trailing
        # footer/rewards block.
        "the gold card",
        "maandafrekening",
        "pagina",
        "datum volgende",
        # Page 4 Membership Rewards summary.
        "membership rewards classic",
        "membership rewards nummer",
        "totaal aantal gespaarde punten",
        # Page 1 trailing address/legal block. "identificatienummer" is the first
        # word of the footer prose; the cardholder name line is caught by "mevr "
        # so the postal address cannot reach the ledger even if the first marker
        # were missed.
        "identificatienummer",
        "u kunt het adres",
        "mevr ",
    }
)

# Printed as `Periode: 24.05.2026 tot 23.06.2026`. Read only to keep it out of
# the row stream and to document that it is deliberately NOT applied as a filter
# — see the module docstring.
_PERIOD_RE = re.compile(
    r"Periode:\s*(\d{2}\.\d{2}\.\d{4})\s+tot\s+(\d{2}\.\d{2}\.\d{4})"
)

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


def printed_period(lines: list[str]) -> tuple[dt.date, dt.date] | None:
    """The statement's own printed `Periode: <from> tot <to>`, if present.

    **Advisory. Never apply this as a filter.** Real statements carry
    transactions dated the day before the period starts, so rejecting rows
    outside the printed bounds would drop genuine spend. It is exposed so a
    caller can *label* a batch with the period it claims to cover, and so a
    reader can see the period without scraping it out of the text.

    Returns:
        `(from, to)` as dates, or None when the line did not extract. Note the
        two-digit dates inside a row are expanded per row by `_expand_date`,
        independently of what this returns.
    """
    text = "\n".join(lines)
    match = _PERIOD_RE.search(text)
    if match is None:
        return None
    start = dt.datetime.strptime(match.group(1), "%d.%m.%Y").date()
    end = dt.datetime.strptime(match.group(2), "%d.%m.%Y").date()
    return start, end


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
    """Extract a PDF's text layer with `pdftotext -layout`.

    The injectable seam. This is no longer a placeholder: it is the real
    extractor, and it is the *same* one `tools/bankparse/amex.py` used to produce
    the verified output recorded under `data/extracted/`. Keeping the seam means
    the row parser below stays testable with a list of strings, and that
    `tests/egress/` can inject a pure function and still prove the offline path.

    Args:
        payload: The PDF bytes.

    Returns:
        Text lines, page order preserved.

    Raises:
        PdfTextUnavailableError: `pdftotext` (poppler-utils) is not installed.
        PdfTextExtractionError: The binary ran and produced no usable text.
    """
    return _pdftotext_extract(payload)


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
        account_id: int | None = None,
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
        extracted = extract_lines(self._extract, payload, provider=self.provider)
        if isinstance(extracted, AdapterParseError):
            # pdftotext missing, or the file has no text layer. One failure for
            # the whole upload, not a stack trace out of a request handler.
            result.failed.append(extracted)
            return result
        lines = extracted
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
    lines: list[str], *, account_id: int | None, first_line: int = 1
) -> list[RawRecord | AdapterParseError]:
    """Parse transaction rows out of statement text.

    Each output is either a RawRecord or the AdapterParseError explaining why a
    row was skipped, so the caller never loses the reason for a dropped row.

    Description continuation lines — a wrapped payee, a long reference — are
    appended to the row above rather than dropped. Dropping them would change
    the description, and therefore the fingerprint, for the same purchase.

    Two line kinds must never reach the continuation branch, because appending
    them would corrupt the row above:

    * a bare `CR` — the credit marker, which sets the sign rather than joining
      the description;
    * statement furniture — the repeating legal/footer block and its
      "Nieuwe transacties voor:" sub-header, which poppler breaks across
      several lines. Without the skip, the tail of the legal paragraph would be
      appended to the last real transaction's description, changing its
      fingerprint on every statement that has one.

    Args:
        lines: Extracted text lines.
        account_id: The account to attribute rows to; a PDF names none, so this
            is None unless the caller already resolved one.
        first_line: Line number of `lines[0]`, for stable indices across a page
            boundary in the caller.

    Returns:
        Records and errors in document order.
    """
    results: list[RawRecord | AdapterParseError] = []
    pending: _PendingRow | None = None
    line_number = first_line
    in_legal_block = False

    for text in lines:
        line_number += 1
        stripped = text.strip()
        if not stripped:
            continue
        if _is_heading(stripped) or _is_furniture(stripped):
            continue
        if _is_legal_heading(stripped):
            in_legal_block = True
            continue

        parsed = _parse_row(stripped)
        if parsed is not None:
            # A new row starts. Flush the previous one and leave the legal block.
            in_legal_block = False
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
        if pending is not None and not in_legal_block:
            if _CR_RE.match(stripped):
                pending.is_credit = True
            else:
                pending.description_source = (
                    f"{pending.description_source} {stripped}"
                ).strip()

    if pending is not None:
        results.append(_finish(pending, account_id))
    return results


def _is_furniture(text: str) -> bool:
    """Whether a line belongs to the statement's repeating legal/footer block.

    Verified present on every one of the four real statements, so this is not a
    speculative skip. Matched as a substring because `pdftotext -layout` breaks
    the legal paragraph across lines at column boundaries.
    """
    return any(marker in text for marker in _STATEMENT_FURNITURE)


def _is_legal_heading(text: str) -> bool:
    """Whether a line is a heading marking the start of the trailing legal block.

    The legal block is printed as two interleaved columns, so a naive substring
    whitelist misses it. A heading is recognised by its vocabulary and by the
    fact that it carries no amount. Once one is seen, every following non-row
    line is treated as legal prose until a new real row starts.

    Matching is by prefix (after lower-casing and stripping trademark symbols)
    so a sentence that merely contains the words is not treated as furniture.
    """
    if _AMOUNT.search(text):
        return False
    normalized = text.strip().lower().rstrip(":").replace("®", "").replace("™", "")
    return any(normalized.startswith(heading) for heading in _LEGAL_SECTION_HEADINGS)


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


def _finish(
    pending: _PendingRow, account_id: int | None
) -> RawRecord | AdapterParseError:
    """Turn a pending row into a record, or explain why it cannot be one."""
    description = (
        redact_ibans(redact_card_numbers(pending.description_source.strip())) or ""
    )
    if not pending.amount_text:
        return AdapterParseError(
            "row has no amount", pending.line_number, AmexPdfAdapter.provider
        )
    try:
        amount_minor = _to_minor(pending.amount_text)
        # Charges and credits are both printed positive. `is_credit` — the bare
        # CR line beneath the amount — is the only thing that distinguishes
        # them, so this branch is the credit rule, not a heuristic.
        signed_minor = amount_minor if pending.is_credit else -amount_minor
        raw_data: dict[str, str | int | float | bool | None] = {
            "raw_date": pending.txn_date.isoformat(),
            "raw_posting_date": pending.post_date.isoformat(),
            "raw_amount_minor": signed_minor,
            "amount_raw": pending.amount_text,
            "is_credit": pending.is_credit,
            # The monthly card payment shares the credit section with real
            # refunds and is positive in the same way, so it is separated by
            # description. Downstream matching must not treat a repayment as
            # a refund, or vice versa.
            "is_card_payment": bool(_CARD_PAYMENT_RE.search(description)),
            # `Bedrag in vreemde valuta` is empty in all four verified
            # statements, so no foreign amount or rate is available and none
            # is invented here.
            "foreign_amount_minor": None,
        }
        raw_data = {
            key: redact_ibans(value) if isinstance(value, str) else value
            for key, value in raw_data.items()
        }
        normalized = normalize_record(
            account_id=account_id,
            description=description,
            amount=signed_minor,
            currency_code="EUR",
            booked_date=pending.txn_date,
            # Datum verwerkt, the processing date. Kept as a separate field
            # because it differs from Transactiedatum on roughly a third of the
            # rows in every real statement.
            value_date=pending.post_date,
            # No stable provider id, same as the CSV: Amex's own IDs change.
            provider_txn_id=None,
            pending=False,
            line_number=pending.line_number,
            sign_convention=AmountSignConvention.SIGNED,
            raw_data=raw_data,
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
