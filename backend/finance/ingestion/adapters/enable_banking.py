"""enable_banking.py — Enable Banking AIS adapter (PSD2).

No network calls here. This adapter converts a payload the caller already
fetched; the HTTP client, the RS256 signing and the consent flow are M5
(bead LifeOS-10), and keeping them out means this file is testable with a dict.

Two things about this provider drive the design:

- **`entryReference` is the Tier-1 key, and it is unique per ACCOUNT, never
  globally.** It also usually only materialises on `BOOK` — which is why
  Tier 2 exists, and why Tier 3 is computed for API rows too.
- **Pending is real.** This is the only provider that reports `PDNG`, and it is
  the only one where Tier 2 (pending -> booked) applies.

`account.uid` is deliberately never read. It rotates at every re-auth, so a
fingerprint or a link keyed on it breaks at the next consent. Accounts are
identified by `identification_hash` (stable), resolved by the caller.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Final

from core.datetime import parse_date

from finance.ingestion.adapters.base import (
    AdapterParseError,
    ImportAdapter,
    ImportResult,
)
from finance.ingestion.normalize import AmountSignConvention, normalize_record
from finance.public import RawRecord

__all__ = [
    "BOOK_STATUS",
    "PENDING_STATUS",
    "EnableBankingAdapter",
    "parse_ais_amount",
    "parse_ais_date",
]

BOOK_STATUS: Final = "BOOK"
PENDING_STATUS: Final = "PDNG"

# Only these two statuses are bookable. Others (information-only credit
# transfer notes, for instance) carry no amount to book and are skipped, not
# reported as failures: they are legal AIS output, not malformed input.
_BOOKABLE_STATUSES: Final = frozenset({BOOK_STATUS, PENDING_STATUS})

# Keys holding a JSON scalar. Nested objects are dropped from raw_data: the
# column is JSONB, but keeping it flat means replay never has to interpret an
# embedded blob to answer "what did the provider send".
_SCALAR = (str, int, float, bool)


def parse_ais_amount(transaction: dict[str, object]) -> tuple[int, str]:
    """Read an AIS `transactionAmount` into signed minor units and a code.

    AIS returns `{"amount": "12.34", "currency": "EUR"}`. Parsed as a string
    and scaled by integer arithmetic — never via float.

    Raises:
        ValueError: If the amount object is missing, shapeless, or unparseable.
    """
    field = transaction.get("transactionAmount")
    if not isinstance(field, dict):
        msg = "transactionAmount is missing or not an object"
        raise ValueError(msg)
    raw_amount = field.get("amount")
    if raw_amount is None:
        msg = "transactionAmount has no amount"
        raise ValueError(msg)
    currency = str(field.get("currency", "EUR")).upper()
    try:
        decimal_amount = Decimal(str(raw_amount))
    except InvalidOperation as exc:
        msg = f"unparseable amount {raw_amount!r}"
        raise ValueError(msg) from exc
    # AIS minor-unit exponent is 2 for every currency this project ingests.
    # normalize_amount re-applies the currency table's exponent downstream.
    return int(decimal_amount.scaleb(2)), currency


def parse_ais_date(value: object) -> dt.date | None:
    """Parse an AIS date string, or None when absent or unusable.

    A malformed date returns None rather than raising: one bad field should not
    drop the row, and the raw payload is preserved either way so a later pass can
    recover it.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_date(text)
    except ValueError:
        return None


def _description_of(transaction: dict[str, object]) -> str:
    """A stable description from the AIS fields.

    `remittanceInformation` is the closest thing to a human description. When it
    is absent the counterparty name is better than an empty string: a blank
    description fingerprints badly and cannot be shown in the review queue.
    """
    for key in ("remittanceInformation", "remittanceInformationUnstructured"):
        value = transaction.get(key)
        if isinstance(value, list) and value:
            first = str(value[0]).strip()
            if first:
                return first
        elif isinstance(value, str) and value.strip():
            return value.strip()
    for party_key in ("creditor", "debtor"):
        party = transaction.get(party_key)
        if isinstance(party, dict):
            name = party.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


def _flat_raw(
    transaction: dict[str, object],
) -> dict[str, str | int | float | bool | None]:
    """The JSON scalars of an AIS dict, for the immutable raw_data column."""
    flat: dict[str, str | int | float | bool | None] = {}
    for key, value in transaction.items():
        if isinstance(value, _SCALAR) or value is None:
            flat[key] = value
    return flat


def _optional_reference(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class EnableBankingAdapter(ImportAdapter):
    """Converts Enable Banking transaction dicts into RawRecords."""

    provider: ClassVar[str] = "enable_banking"
    import_method: ClassVar[str] = "api"
    # Clean structured data: +0.10 over the neutral baseline (section G).
    confidence_weight: ClassVar[float] = 0.10

    def parse(self, payload: bytes) -> ImportResult:
        """Not supported: an AIS payload is fetched over HTTP, not uploaded.

        Raises:
            NotImplementedError: Always. Call `parse_payload` with the decoded
                transaction list instead.
        """
        raise NotImplementedError(
            "Enable Banking is an API source; call parse_payload() with the "
            "decoded transaction list rather than parse() with a file"
        )

    def parse_payload(
        self,
        transactions: list[dict[str, object]],
        *,
        account_id: str,
        entry_reference: str | None = None,
        line_offset: int = 0,
    ) -> ImportResult:
        """Convert a list of AIS transaction dicts into RawRecords.

        Args:
            transactions: The `transactions` array from an AIS response.
            account_id: The local account the provider account is linked to.
            entry_reference: The account's `entryReference` for this fetch. Used
                as a fallback id and to detect the "both rows carry an equal
                provider id" Tier-1 exact-match case.
            line_offset: Line number of the first row, so occurrence indices stay
                stable across paginated fetches.

        Returns:
            An `ImportResult`. Unparseable rows land in `failed`; rows with a
            non-bookable status are skipped silently.
        """
        result = ImportResult(provider=self.provider, import_method=self.import_method)
        for offset, transaction in enumerate(transactions):
            line_number = line_offset + offset + 1
            try:
                record = self._parse_transaction(
                    transaction,
                    account_id=account_id,
                    entry_reference=entry_reference,
                    line_number=line_number,
                )
            except AdapterParseError as exc:
                result.failed.append(exc)
                continue
            if record is not None:
                result.records.append(record)
        return result

    def _parse_transaction(
        self,
        transaction: dict[str, object],
        *,
        account_id: str,
        entry_reference: str | None,
        line_number: int,
    ) -> RawRecord | None:
        """One AIS dict to a RawRecord, or None when it is not bookable."""
        status = str(transaction.get("status", BOOK_STATUS))
        if status not in _BOOKABLE_STATUSES:
            return None

        booking_date = parse_ais_date(transaction.get("bookingDate"))
        if booking_date is None:
            raise AdapterParseError(
                "transaction has no usable bookingDate", line_number, self.provider
            )

        try:
            amount_minor, currency = parse_ais_amount(transaction)
            normalized = normalize_record(
                account_id=account_id,
                description=_description_of(transaction),
                # AIS amounts are already signed: a debit is negative.
                amount=amount_minor,
                currency_code=currency,
                booked_date=booking_date,
                value_date=parse_ais_date(transaction.get("valueDate")),
                # Per-transaction entryReference is the Tier-1 key, commonly
                # absent on PDNG — the gap Tier 2 exists to cover.
                provider_txn_id=_optional_reference(transaction.get("entryReference"))
                or entry_reference,
                pending=status == PENDING_STATUS,
                line_number=line_number,
                sign_convention=AmountSignConvention.SIGNED,
                raw_data=_flat_raw(transaction),
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
