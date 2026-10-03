"""manual.py — user-entered transactions.

No file, no provider. The user typed the values, which changes what this adapter
is allowed to do:

- **It does not guess a date.** A user who leaves the date blank gets an error,
  not "today". Defaulting to today would silently misdate a statement.
- **It does not accept a float.** The API takes `amount` as a string ("12.34")
  or minor units as an integer. A JSON number here would be a float, and a float
  in this project is a bug regardless of who sent it.
- **It sets `provider_txn_id` to None.** A manual row has no provider id, so
  Tier 1 does not apply and Tier 3 is the dedup backstop. A user entering the
  same transaction twice gets a review item rather than a silent merge.

`manual` is still a first-class `import_batch.provider` value, so a manual entry
travels the same pipeline as a file import and produces the same immutable raw
record.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar, Final

from core.datetime import parse_date

from finance.ingestion.adapters.base import (
    AdapterParseError,
    ImportAdapter,
    ImportResult,
)
from finance.ingestion.normalize import AmountSignConvention, normalize_record
from finance.public import RawRecord

__all__ = ["ManualAdapter", "ManualEntry"]

# A user-entered description over this length is almost certainly a paste of a
# whole statement line, which then matches nothing.
MAX_DESCRIPTION_LENGTH: Final = 500


class ManualEntry:
    """One transaction as the user submitted it, still unvalidated."""

    def __init__(
        self,
        *,
        account_id: int,
        description: str,
        amount: str | int,
        currency: str,
        booked_date: str,
        value_date: str | None = None,
        line_number: int = 0,
    ) -> None:
        self.account_id = account_id
        self.description = description
        self.amount = amount
        self.currency = currency
        self.booked_date = booked_date
        self.value_date = value_date
        self.line_number = line_number


class ManualAdapter(ImportAdapter):
    """Builds RawRecords from user-entered values."""

    provider: ClassVar[str] = "manual"
    import_method: ClassVar[str] = "manual"
    # A human is the most reliable source available.
    confidence_weight: ClassVar[float] = 0.10

    def parse(self, payload: bytes) -> ImportResult:
        """Not supported: there is no manual file.

        Raises:
            NotImplementedError: Always. Call `parse_entry`.
        """
        raise NotImplementedError(
            "manual entries are constructed one at a time; call parse_entry() "
            "rather than parse() with a file"
        )

    def parse_entry(
        self,
        entry: ManualEntry,
        *,
        as_debit: bool = True,
        filename: str | None = None,
    ) -> ImportResult:
        """Validate and normalise one user-entered transaction.

        Args:
            entry: The submitted values.
            as_debit: Whether a positive amount means money out. The manual form
                asks for a direction explicitly rather than expecting a user to
                type a minus sign.
            filename: Unused; carried for signature symmetry with the file
                adapters.

        Returns:
            An `ImportResult` holding either one record or one error.
        """
        result = ImportResult(
            provider=self.provider,
            import_method=self.import_method,
            source_filename=filename,
        )
        try:
            description = entry.description.strip()
            if not description:
                msg = "description must not be empty"
                raise ValueError(msg)
            if len(description) > MAX_DESCRIPTION_LENGTH:
                msg = f"description exceeds {MAX_DESCRIPTION_LENGTH} characters"
                raise ValueError(msg)

            booked_date = self._require_date(entry.booked_date, "booked_date")
            value_date = (
                self._require_date(entry.value_date, "value_date")
                if entry.value_date is not None
                else None
            )

            normalized = normalize_record(
                account_id=entry.account_id,
                description=description,
                amount=entry.amount,
                currency_code=entry.currency,
                booked_date=booked_date,
                value_date=value_date,
                # A manual row has no provider id, so Tier 1 does not apply and
                # Tier 3 is the only dedup that can catch a double entry.
                provider_txn_id=None,
                pending=False,
                line_number=entry.line_number,
                sign_convention=(
                    AmountSignConvention.POSITIVE_IS_DEBIT
                    if as_debit
                    else AmountSignConvention.SIGNED
                ),
            )
        except ValueError as exc:
            result.failed.append(
                AdapterParseError(str(exc), entry.line_number or None, self.provider)
            )
            return result

        result.records.append(
            RawRecord(
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
        )
        return result

    @staticmethod
    def _require_date(value: str, field: str) -> dt.date:
        """Parse a required date.

        A missing or unparseable date is an error rather than a default. Filling
        it in with today would silently misdate the entry, which is worse than
        asking the user again.
        """
        if not value or not value.strip():
            msg = f"{field} is required; it is never defaulted"
            raise ValueError(msg)
        return parse_date(value)
