"""amex_csv.py — Amex CSV export.

Six months of history, and the reason this adapter is the sharpest test of the
dedup design: **Amex exports contain no stable ID at all.** Amex's own
transaction identifiers are documented to change between exports, so re-importing
`September.csv` through Firefly or Actual produces a complete duplicate of every
row. Tier 3 is the only dedup available here.

Amounts are positive-is-debit: the column is an unsigned magnitude and the sign
carries no information. `AmountSignConvention.POSITIVE_IS_DEBIT` flips them, so
the ledger gets negative-for-money-out uniformly regardless of provider.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from typing import ClassVar, Final

from finance.ingestion.adapters.base import (
    AdapterParseError,
    ImportAdapter,
    ImportResult,
    source_checksum,
)
from finance.ingestion.normalize import AmountSignConvention, normalize_record
from finance.public import RawRecord

__all__ = ["AMEX_DATE_FORMATS", "AmexCsvAdapter"]

# Amex's NL export writes DD/MM/YYYY. `%y` is tried because some statement
# vintages abbreviate the year on the first row.
AMEX_DATE_FORMATS: Final = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d")

# Header aliases: Amex localises them per language and per export vintage.
_DESCRIPTION_KEYS: Final = ("Description", "Omschrijving", "Payee", "Narrative")
_DATE_KEYS: Final = ("Date", "Datum", "Transaction Date")
_VALUE_DATE_KEYS: Final = ("Date Paid", "Betaald", "Value Date")
_AMOUNT_KEYS: Final = ("Amount", "Bedrag")


class AmexCsvAdapter(ImportAdapter):
    """Parses an Amex CSV export into RawRecords.

    The account is a parameter, not something read from the file: an Amex export
    names no account, so the user picks it in the UI and the caller passes it in.
    """

    provider: ClassVar[str] = "amex_csv"
    import_method: ClassVar[str] = "csv"
    # csv extraction is clean: no adjustment relative to API data.
    confidence_weight: ClassVar[float] = 0.0

    def parse(
        self,
        payload: bytes,
        account_id: int | None = None,
        filename: str | None = None,
    ) -> ImportResult:
        """Parse the whole export.

        Rows that fail are collected rather than raised: one malformed line in a
        six-month file must not abandon the other ~200.
        """
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
        self, row: dict[str, str | None], line_number: int, account_id: int | None
    ) -> RawRecord | None:
        """One CSV row to a RawRecord, or None for a blank line."""
        if not any((value or "").strip() for value in row.values()):
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
                description=_first_value(row, _DESCRIPTION_KEYS),
                amount=amount_value,
                currency_code="EUR",
                booked_date=_parse_amex_date(date_value, line_number),
                value_date=_optional_date(row, _VALUE_DATE_KEYS),
                # Amex supplies no stable transaction id. Stated explicitly
                # rather than left as an accident, because "why is dedup slow"
                # starts here.
                provider_txn_id=None,
                # The export is settled activity only; Amex excludes pending.
                pending=False,
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
    """The first present, non-empty value among `keys`."""
    for key in keys:
        value = row.get(key)
        if value is not None and value.strip():
            return value.strip()
    return None


def _parse_amex_date(value: str, line_number: int) -> dt.date:
    """Parse an Amex date, trying the export's formats in order."""
    text = value.strip()
    for fmt in AMEX_DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise AdapterParseError(
        f"unrecognised date {value!r}", line_number, AmexCsvAdapter.provider
    )


def _optional_date(row: dict[str, str | None], keys: tuple[str, ...]) -> dt.date | None:
    """Parse a secondary date column. Absence is not an error."""
    value = _first_value(row, keys)
    if not value:
        return None
    try:
        return _parse_amex_date(value, 0)
    except AdapterParseError:
        return None
