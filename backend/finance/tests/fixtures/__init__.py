"""Synthetic fixtures. No real financial data, ever.

SAFETY.md and the project's privacy posture: this repository holds no account
numbers, no real payees, no real balances and no real transaction data. What
follows are invented merchants and round-ish amounts.

The builders exist so a test states only what it cares about:

    make_record(description="Jumbo 4321", amount_minor=-850)
    make_csv(description="...", amount="8.50")

`am_statement_csv` and `ais_transaction` return the two real-world shapes —
Amex's CSV export and an Enable Banking AIS transaction — so the adapters are
tested against the format a provider actually sends, without shipping a real
export.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from typing import Final

from finance.ingestion.identity import Candidate
from finance.ingestion.normalize import (
    AmountSignConvention,
    NormalizedRecord,
    normalize_record,
)

__all__ = [
    "ACCOUNT_ID",
    "AIS_TRANSACTIONS",
    "SOURCE_RECORD_ID",
    "ais_transaction",
    "am_statement_csv",
    "make_candidate",
    "make_csv",
    "make_record",
    "make_revolut_csv",
    "synthetic_amex_csv",
    "synthetic_amex_pdf_text",
]

# Invented, and shaped like what M1 will actually hand out: a BIGSERIAL.
# No account number is a real one, and no real one is a real BIGSERIAL either.
ACCOUNT_ID: Final = 1001

# The same for a stored source_record row.
SOURCE_RECORD_ID: Final = 5001

# A bank-style description with a trailing reference, as providers send it.
_REF = "REF:000000123456"


def make_record(
    *,
    description: str = "Jumbo 4321",
    amount_minor: int = -850,
    currency: str = "EUR",
    booked_date: str = "2026-03-14",
    account_id: int | None = ACCOUNT_ID,
    line_number: int = 1,
    provider_txn_id: str | None = None,
    pending: bool = False,
) -> NormalizedRecord:
    """A normalized record with sensible synthetic defaults."""
    return normalize_record(
        account_id=account_id,
        description=description,
        amount=amount_minor,
        currency_code=currency,
        booked_date=booked_date,
        provider_txn_id=provider_txn_id,
        pending=pending,
        line_number=line_number,
        sign_convention=AmountSignConvention.SIGNED,
    )


def make_candidate(
    *,
    source_record_id: int = SOURCE_RECORD_ID,
    account_id: int | None = ACCOUNT_ID,
    amount_minor: int = -850,
    description: str = "Jumbo 4321",
    booked_date: str = "2026-03-14",
    status: str = "pending",
    fingerprint: str = "",
    journal_entry_id: int | None = None,
    transfer_match_id: int | None = None,
    merchant_alias_id: int | None = None,
) -> Candidate:
    """A stored row as the Tier-2 resolver sees it."""
    return Candidate(
        source_record_id=source_record_id,
        account_id=account_id,
        amount_minor=amount_minor,
        booked_date=dt.date.fromisoformat(booked_date),
        description=description,
        status=status,
        merchant_alias_id=merchant_alias_id,
        fingerprint=fingerprint,
        journal_entry_id=journal_entry_id,
        transfer_match_id=transfer_match_id,
    )


def am_statement_csv(
    rows: list[tuple[str, str, str]],
) -> str:
    """Amex-flavoured CSV: `Date,Description,Amount`, positive-is-debit.

    Args:
        rows: (date, description, amount) triples, written verbatim. Keep them
            synthetic; do not paste a real export into a test.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Description", "Amount"])
    for date_text, description, amount in rows:
        writer.writerow([date_text, description, amount])
    return buffer.getvalue()


def make_csv(
    rows: list[tuple[str, str, str]],
    *,
    currency_column: bool = False,
) -> str:
    """Revolut-flavoured CSV, with the stable `Id` column Tier 1 depends on."""
    header = ["Id", "Type", "Description", "Date", "Amount", "State"]
    if currency_column:
        header.append("Currency")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for index, (date_text, description, amount) in enumerate(rows, start=1):
        writer.writerow(
            [
                f"txn-{index:06d}",
                "card_payment",
                description,
                date_text,
                amount,
                "completed",
            ]
        )
    return buffer.getvalue()


def make_revolut_csv(rows: list[tuple[str, str, str]]) -> str:
    """Alias for `make_csv`, named for the provider it imitates."""
    return make_csv(rows)


def synthetic_amex_csv() -> bytes:
    """A small, entirely invented Amex export.

    Two identical EUR 3.20 coffees on one day, deliberately: that pair is what
    the occurrence-index rule exists for, and it is the hardest case in the whole
    design.
    """
    return am_statement_csv(
        [
            ("14/03/2026", f"JUMBO 4321 AMSTERDAM{_REF}", "8.50"),
            ("14/03/2026", f"ALBERT HEIJN 1234{_REF}", "12.34"),
            ("14/03/2026", f"ALBERT HEIJN 1234{_REF}", "12.34"),
            ("15/03/2026", f"NS INTERCITY{_REF}", "4.10"),
        ]
    ).encode("utf-8")


def synthetic_amex_pdf_text() -> list[str]:
    """A small, entirely invented Amex PDF pdftotext-layout extract.

    Mirrors the rows in `synthetic_amex_csv()` so the dedup and zero-egress
    proofs stay comparable while exercising the canonical Amex PDF adapter.
    Amounts use European formatting (",") because that is what the PDF prints.

    Uses the verified Dutch Amex NL layout: two DD.MM.YY dates per row
    (Transactiedatum and Datum verwerkt), unsigned amounts, and a bare CR line
    as the credit marker.

    The text is injected into `AmexPdfAdapter` via its `text_extractor` seam, so
    no pdfplumber dependency or real statement is needed.
    """
    return [
        "Card Summary",
        "Transactiedatum Datum verwerkt Omschrijving Bedrag",
        "14.03.26  14.03.26  JUMBO 4321 AMSTERDAM REF:000000123456            8,50",
        "14.03.26  15.03.26  ALBERT HEIJN 1234 REF:000000123456                12,34",
        "14.03.26  14.03.26  ALBERT HEIJN 1234 REF:000000123456                12,34",
        "15.03.26  15.03.26  NS INTERCITY REF:000000123456                       4,10",
        "New Balance                                                          33,18",
    ]


def ais_transaction(
    *,
    amount: str = "-12.34",
    currency: str = "EUR",
    booking_date: str = "2026-03-14",
    status: str = "BOOK",
    entry_reference: str | None = "eb-ref-0001",
    remittance: str | None = "JUMBO 4321 AMSTERDAM",
) -> dict[str, object]:
    """One Enable Banking AIS transaction, in the shape the API returns."""
    transaction: dict[str, object] = {
        "status": status,
        "bookingDate": booking_date,
        "valueDate": booking_date,
        "transactionAmount": {"amount": amount, "currency": currency},
        "remittanceInformation": [remittance] if remittance else [],
    }
    if entry_reference is not None:
        transaction["entryReference"] = entry_reference
    return transaction


#: Three AIS transactions: a booked card payment, its pending twin, and an
#: informational entry that must be skipped.
AIS_TRANSACTIONS: list[dict[str, object]] = [
    ais_transaction(),
    ais_transaction(status="PDNG", entry_reference=None, amount="-12.34"),
    ais_transaction(
        amount="0.00",
        status="INFO",
        entry_reference="eb-ref-info",
        remittance="Information only",
    ),
]
