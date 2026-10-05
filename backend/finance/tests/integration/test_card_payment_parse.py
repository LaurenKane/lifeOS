"""test_card_payment_parse.py — the card payment is flagged at PARSE time.

`docs/adr/0007-imported-card-payment-is-a-transfer.md`. The flag has to exist
before the row reaches the writer, because the writer's whole job is to keep a
flagged row away from the expense resolver. If an adapter stops emitting it, the
payment silently books as spending — balanced, committed and permanent.

Both statement adapters are covered, because both produce a card payment from
opposite sides:

* Amex prints the payment as a CREDIT on the card statement.
* Rabobank prints the debit that pays the card on the checking statement.

These tests read the real statements and touch no database. The files are not
distributed with the repository, so a missing directory SKIPS.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import pytest

from finance.ingestion.adapters.amex_pdf import AmexPdfAdapter
from finance.ingestion.adapters.rabobank_pdf import RabobankPdfAdapter

STATEMENTS_DIR_ENV_VAR: Final = "LIFEOS_STATEMENTS_DIR"
DEFAULT_STATEMENTS_DIR: Final = Path.home() / "Documents" / "Banking"

PDF_MAGIC: Final = b"%PDF-"


def _statement(subdir: str, name: str) -> bytes:
    configured = os.environ.get(STATEMENTS_DIR_ENV_VAR, "").strip()
    root = Path(configured or DEFAULT_STATEMENTS_DIR).expanduser() / subdir
    if not root.is_dir():
        pytest.skip(f"no bank statements directory at {root}")
    path = root / name
    if not path.is_file():
        pytest.skip(f"{path} is not present; this test imports it by name.")
    data = path.read_bytes()
    if PDF_MAGIC not in data[:1024]:
        pytest.fail(f"{path} is named as a statement but its contents are not a PDF")
    return data


#: Measured against the four real statements: (file, card date, signed debit).
RABO_CARD_PAYMENTS: Final = (
    ("rabobank-2026-06.pdf", (2026, 6, 30), -76567),
    ("rabobank-2026-07.pdf", (2026, 7, 30), -33243),
    ("rabobank-2026-08.pdf", (2026, 8, 28), -27248),
    ("rabobank-2026-09.pdf", (2026, 9, 30), -92219),
)

AMEX_CARD_PAYMENTS: Final = (
    ("2026-06-23.pdf", (2026, 5, 28), 72135),
    ("2026-07-23.pdf", (2026, 6, 29), 76567),
    ("2026-08-23.pdf", (2026, 7, 28), 33243),
    ("2026-09-23.pdf", (2026, 8, 28), 27248),
)


class TestRabobankCardPaymentsAreFlagged:
    @pytest.mark.parametrize(
        ("name", "expected_date", "expected_amount"), RABO_CARD_PAYMENTS
    )
    def test_exactly_one_flagged_debit_per_statement(
        self, name: str, expected_date: tuple[int, int, int], expected_amount: int
    ) -> None:
        result = RabobankPdfAdapter().parse(_statement("Rabo", name), account_id=1)
        assert not result.failed, result.failed
        flagged = [
            record
            for record in result.records
            if record.raw_data.get("is_card_payment") is True
        ]
        assert len(flagged) == 1, (
            f"{name}: expected exactly one flagged card payment, got {len(flagged)}"
        )
        payment = flagged[0]
        assert (
            payment.booked_date.year,
            payment.booked_date.month,
            payment.booked_date.day,
        ) == expected_date
        assert payment.amount_minor == expected_amount, (
            f"{name}: the card payment is {payment.amount_minor}, expected "
            f"{expected_amount}"
        )
        # It is a DEBIT on the checking statement: money left to pay the card.
        assert payment.amount_minor < 0


class TestAmexCardPaymentsAreFlagged:
    @pytest.mark.parametrize(
        ("name", "expected_date", "expected_amount"), AMEX_CARD_PAYMENTS
    )
    def test_exactly_one_flagged_credit_per_statement(
        self, name: str, expected_date: tuple[int, int, int], expected_amount: int
    ) -> None:
        result = AmexPdfAdapter().parse(_statement("Amex", name), account_id=2)
        assert not result.failed, result.failed
        flagged = [
            record
            for record in result.records
            if record.raw_data.get("is_card_payment") is True
        ]
        assert len(flagged) == 1, (
            f"{name}: expected exactly one flagged card payment, got {len(flagged)}"
        )
        payment = flagged[0]
        assert (
            payment.booked_date.year,
            payment.booked_date.month,
            payment.booked_date.day,
        ) == expected_date
        assert payment.amount_minor == expected_amount
        # It is a CREDIT on the card statement: the liability falls.
        assert payment.amount_minor > 0
