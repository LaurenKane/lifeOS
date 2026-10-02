"""revolut_csv.py — Revolut CSV export.

Unlike Amex, Revolut's export carries a stable `id` column, so **Tier 1 dedup
works here**: a re-import with the same id on the same account is an exact hit.

Whether Revolut NL is reachable at all is still UNCERTAIN (ARCHITECTURE-PROPOSAL.md
section C) — the design must not assume an API arrives. This file is therefore
the *primary* Revolut lane, not a fallback: if `/aspsps?country=NL` does not list
Revolut, nothing upstream of this adapter needs to change.

Amounts are positive-is-debit, like Amex.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation
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

__all__ = ["REVOLUT_DATE_FORMATS", "RevolutCsvAdapter"]

REVOLUT_DATE_FORMATS: Final = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y")

# Revolut's export columns. The stable id is `Id`; `Kind` distinguishes a card
# payment from a top-up or an FX conversion.
_ID_KEYS: Final = ("Id", "id", "Transaction Id")
_TYPE_KEYS: Final = ("Type", "Kind", "Description type")
_DESCRIPTION_KEYS: Final = ("Description", "Payee", "Narrative", "Merchant")
_DATE_KEYS: Final = ("Date", "Started Date", "Completed Date")
_AMOUNT_KEYS: Final = ("Amount", "Value", "Bedrag")
_CURRENCY_KEYS: Final = ("Currency", "Account Currency")
_STATE_KEYS: Final = ("State", "Status")

# Revolut rows that are not transactions. A top-up moves the user's own money
# between their own accounts and must not become a spend.
# Revolut's `Type` column uses both spaced and underscored spellings depending on
# export vintage, so both are listed rather than normalised. Guessing which one a
# given file uses would silently change which rows are skipped.
_SKIPPED_TYPES: Final = frozenset(
    {
        "cash withdrawal",
        "cash_withdrawal",
        "cash withdrawal fee",
        "cash_withdrawal_fee",
        "transfer",
        "topup",
        "top up",
        "top_up",
    }
)


class RevolutCsvAdapter(ImportAdapter):
    """Parses a Revolut CSV export into RawRecords."""

    provider: ClassVar[str] = "revolut_csv"
    import_method: ClassVar[str] = "csv"
    confidence_weight: ClassVar[float] = 0.0

    def parse(
        self,
        payload: bytes,
        account_id: str = "",
        filename: str | None = None,
    ) -> ImportResult:
        """Parse the whole export, skipping non-transaction row types."""
        result = ImportResult(
            provider=self.provider,
            import_method=self.import_method,
            source_checksum=source_checksum(payload),
            source_filename=filename,
        )
        reader = csv.DictReader(
            io.StringIO(payload.decode("utf-8-sig", errors="replace"))
        )
        if reader.fieldnames is None:
            result.failed.append(
                AdapterParseError("empty file or missing header row", 1, self.provider)
            )
            return result

        for line_number, row in enumerate(reader, start=2):
            try:
                record = self._parse_row(row, line_number, account_id)
            except AdapterParseError as exc:
                result.failed.append(exc)
                continue
            if record is not None:
                result.records.append(record)
        return result

    def _parse_row(
        self, row: dict[str, str | None], line_number: int, account_id: str
    ) -> RawRecord | None:
        """One CSV row to a RawRecord, or None when the row is skipped."""
        if not any((value or "").strip() for value in row.values()):
            return None

        row_type = (_first_value(row, _TYPE_KEYS) or "").strip().lower()
        if row_type in _SKIPPED_TYPES:
            return None

        date_value = _first_value(row, _DATE_KEYS)
        amount_value = _first_value(row, _AMOUNT_KEYS)
        if not date_value or not amount_value:
            raise AdapterParseError(
                "row is missing a date or an amount", line_number, self.provider
            )

        try:
            normalized = normalize_record(
                account_id=account_id,
                description=_first_value(row, _DESCRIPTION_KEYS) or row_type,
                amount=_clean_amount(amount_value, line_number),
                currency_code=_first_value(row, _CURRENCY_KEYS) or "EUR",
                booked_date=_parse_date(date_value, line_number),
                # The stable id that makes Tier 1 possible for Revolut.
                provider_txn_id=_first_value(row, _ID_KEYS),
                pending=_is_pending(row),
                line_number=line_number,
                sign_convention=AmountSignConvention.POSITIVE_IS_DEBIT,
            )
        except ValueError as exc:
            raise AdapterParseError(str(exc), line_number, self.provider) from exc

        return RawRecord(
            account_id=normalized.account_id,
            description=normalized.description,
            amount_minor=normalized.amount.amount,
            currency=normalized.amount.currency.code,
            booked_date=normalized.booked_date,
            value_date=normalized.value_date,
            provider_txn_id=normalized.provider_txn_id,
            pending=normalized.pending,
            line_number=line_number,
            raw_data=normalized.raw_data,
        )


def _first_value(row: dict[str, str | None], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and value.strip():
            return value.strip()
    return None


def _clean_amount(value: str, line_number: int) -> str:
    """Strip currency symbols and rewrite European separators.

    "EUR 1,234.56" and "EUR 1.234,56" both become "1234.56". Handled as a string
    throughout, never via float, so the minor-unit conversion stays exact.
    """
    cleaned = value
    for symbol in ("EUR", "GBP", "USD", "€", "£", "$"):
        cleaned = cleaned.replace(symbol, "")
    cleaned = normalize_european_number(cleaned)
    try:
        Decimal(cleaned)
    except InvalidOperation as exc:
        raise AdapterParseError(
            f"unparseable amount {value!r}", line_number, RevolutCsvAdapter.provider
        ) from exc
    return cleaned


def _parse_date(value: str, line_number: int) -> dt.date:
    text = value.strip()
    for fmt in REVOLUT_DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise AdapterParseError(
        f"unrecognised date {value!r}", line_number, RevolutCsvAdapter.provider
    )


def _is_pending(row: dict[str, str | None]) -> bool:
    """Whether the row is awaiting settlement.

    Revolut's `State` column carries this. Unlike Amex, Revolut does export
    pending rows, so the flag is honoured rather than assumed False.
    """
    state = (_first_value(row, _STATE_KEYS) or "").strip().lower()
    return state in {"pending", "authorised", "authorized"}
