"""test_card_payment_db.py — a card payment posts as a TRANSFER, end to end.

`docs/adr/0007-imported-card-payment-is-a-transfer.md`. The acceptance tests for
the resolved card-payment path: the paying account is resolved by account ID and
never guessed; the entry is a balanced transfer with one synthesized leg; and
whichever statement is imported second attaches to the synthesized leg instead
of double-booking the same payment.

Real statements throughout (`~/Documents/Banking`), because a synthetic row
proves a known shape round-trips and this bead's claim is about real ones. A
missing directory SKIPS. Marked `db`, like the other integration tests here: the
tests drive the real FastAPI app against a real, migrated Postgres and read
every assertion back from a SECOND connection.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from finance.api.transfer_linker import link_transfers
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME

pytestmark = pytest.mark.db

STATEMENTS_DIR_ENV_VAR: Final = "LIFEOS_STATEMENTS_DIR"
DEFAULT_STATEMENTS_DIR: Final = Path.home() / "Documents" / "Banking"
PDF_MAGIC: Final = b"%PDF-"

RABO_JUNE: Final = "rabobank-2026-06.pdf"
RABO_SEPTEMBER: Final = "rabobank-2026-09.pdf"
AMEX_JUNE: Final = "2026-06-23.pdf"
AMEX_JULY: Final = "2026-07-23.pdf"

#: The measured pair: the Amex July statement credits the card +765.67 on
#: 2026-06-29; the Rabobank June statement debits checking -765.67 on 06-30.
PAIR_CARD_DATE: Final = dt.date(2026, 6, 29)
PAIR_BANK_DATE: Final = dt.date(2026, 6, 30)
PAIR_MINOR: Final = 76567


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    del test_database_url
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()


def _insert_account(
    connection: object,
    *,
    name: str,
    account_type: str,
    account_nature: str,
    payment_from: int | None = None,
) -> int:
    return int(
        connection.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO finance.account"
                " (name, account_type, account_nature, currency,"
                "  payment_from_account_id)"
                " VALUES (:name, :account_type, :nature, 'EUR', :payment_from)"
                " RETURNING id"
            ),
            {
                "name": name,
                "account_type": account_type,
                "nature": account_nature,
                "payment_from": payment_from,
            },
        ).scalar_one()
    )


@pytest.fixture
def seeded(engine: Engine) -> dict[str, int]:
    """A checking account, an Amex card that it pays, and the system expense account.

    The card's `payment_from_account_id` points at checking, which is the ONLY
    thing that lets the importer resolve the paying account — there is no
    name-matching anywhere in the path.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            checking = _insert_account(
                connection,
                name="Rabobank current account",
                account_type="checking",
                account_nature="asset",
            )
            card = _insert_account(
                connection,
                name="Amex card",
                account_type="credit_card",
                account_nature="liability",
                payment_from=checking,
            )
            connection.execute(
                text(
                    "INSERT INTO finance.account"
                    " (name, account_type, account_nature, currency, system_role)"
                    " VALUES (:name, 'cash', 'equity', 'EUR', 'system_expense')"
                ),
                {"name": SYSTEM_EXPENSE_ACCOUNT_NAME},
            )
    return {"checking": checking, "card": card}


@dataclass(frozen=True)
class Summary:
    status: str
    record_count: int
    created: int
    duplicated: int
    failed: int
    failures: list[str]

    @property
    def landed(self) -> int:
        return self.created + self.duplicated


def _summary_of(body: object) -> Summary:
    assert isinstance(body, dict), body
    reasons = body["failures"]
    assert isinstance(reasons, list), reasons
    return Summary(
        status=str(body["status"]),
        record_count=int(body["record_count"]),
        created=int(body["created"]),
        duplicated=int(body["duplicated"]),
        failed=int(body["failed"]),
        failures=[str(reason) for reason in reasons],
    )


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


def upload(
    client: TestClient,
    payload: bytes,
    *,
    provider: str,
    account_id: int,
    filename: str,
    card_payment_account_id: int | None = None,
) -> Summary:
    params: dict[str, object] = {"provider": provider, "account_id": account_id}
    if card_payment_account_id is not None:
        params["card_payment_account_id"] = card_payment_account_id
    response = client.post(
        "/api/v1/imports/file",
        params=params,
        files={"file": (filename, payload, "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return _summary_of(response.json())


def rabo(name: str) -> bytes:
    return _statement("Rabo", name)


def amex(name: str) -> bytes:
    return _statement("Amex", name)


def _scalar(engine: Engine, statement: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(statement), params).scalar_one()


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


#: The legs of the ONE entry a flagged card payment produced.
_CARD_PAYMENT_LEGS = """
    SELECT jl.account_id, jl.amount, jl.is_synthesized, jl.synthesized_reason,
           jl.amount_base
      FROM finance.journal_line jl
     WHERE jl.journal_entry_id = (
           SELECT sr.journal_entry_id
             FROM finance.source_record sr
            WHERE sr.raw_data->>'is_card_payment' = 'true'
              AND sr.account_id = :account_id
     )
     ORDER BY jl.sort_order, jl.id
"""


def _card_payment_entry_id(engine: Engine, *, account_id: int) -> int:
    value = _scalar(
        engine,
        "SELECT journal_entry_id FROM finance.source_record"
        " WHERE raw_data->>'is_card_payment' = 'true' AND account_id = :account_id",
        account_id=account_id,
    )
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"the card payment was stored unposted or produced no entry: {value!r}"
    )
    return value


# ---------------------------------------------------------------------------
# The transfer, from each side
# ---------------------------------------------------------------------------


class TestTheTransferIsBuilt:
    def test_rabobank_debit_builds_a_card_leg_and_a_real_paying_leg(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The checking statement's row synthesizes the CARD, the missing side."""
        body = upload(
            client,
            rabo(RABO_JUNE),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_JUNE,
        )
        assert body.created == body.record_count, body
        assert body.status == "completed", body

        legs = _rows(engine, _CARD_PAYMENT_LEGS, account_id=seeded["checking"])
        assert len(legs) == 2, legs
        by_account = {row[0]: row for row in legs}

        paying = by_account[seeded["checking"]]
        card = by_account[seeded["card"]]
        assert paying[1] == -PAIR_MINOR, paying
        assert paying[2] is False, "the paying leg was printed, so it is real"
        assert card[1] == PAIR_MINOR, card
        assert card[2] is True, "the card leg is the side no statement printed"
        assert card[3] == "card_payment"
        assert Decimal(str(paying[4])) + Decimal(str(card[4])) == Decimal(0)

        # No equity leg: this is not an expense.
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line jl"
                " JOIN finance.account a ON a.id = jl.account_id"
                " WHERE jl.journal_entry_id = :entry AND a.account_nature = 'equity'",
                entry=_card_payment_entry_id(engine, account_id=seeded["checking"]),
            )
            == 0
        )

    def test_amex_credit_builds_a_card_leg_and_a_synthesized_paying_leg(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        body = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        assert body.created == body.record_count, body

        legs = _rows(engine, _CARD_PAYMENT_LEGS, account_id=seeded["card"])
        assert len(legs) == 2, legs
        by_account = {row[0]: row for row in legs}
        assert by_account[seeded["card"]][1] == PAIR_MINOR
        assert by_account[seeded["card"]][2] is False
        paying = by_account[seeded["checking"]]
        assert paying[1] == -PAIR_MINOR
        assert paying[2] is True
        assert paying[3] == "card_payment"


# ---------------------------------------------------------------------------
# The double-booking guard, both import orders
# ---------------------------------------------------------------------------


class TestBothImportOrders:
    @pytest.mark.parametrize("order", ["card_first", "checking_first"])
    def test_the_pair_lands_once_and_nets_the_same(
        self, client: TestClient, engine: Engine, seeded: dict[str, int], order: str
    ) -> None:
        """Whichever statement is second, the payment is ONE entry, matched.

        The card credits first (06-29) and checking is debited the next day
        (06-30); `transfer_match`'s outbound-first window would reject the +2
        pair, so the card path states its own window. What is asserted is the
        ledger, not the code path: one entry, one synthesized leg, one
        `transfer_match`, and zero equity movement for the pair.
        """
        amex_payload = amex(AMEX_JULY)
        rabo_payload = rabo(RABO_JUNE)
        if order == "card_first":
            upload(
                client,
                amex_payload,
                provider="amex_pdf",
                account_id=seeded["card"],
                filename=AMEX_JULY,
            )
            upload(
                client,
                rabo_payload,
                provider="rabobank_pdf",
                account_id=seeded["checking"],
                filename=RABO_JUNE,
            )
        else:
            upload(
                client,
                rabo_payload,
                provider="rabobank_pdf",
                account_id=seeded["checking"],
                filename=RABO_JUNE,
            )
            upload(
                client,
                amex_payload,
                provider="amex_pdf",
                account_id=seeded["card"],
                filename=AMEX_JULY,
            )

        card_entry = _card_payment_entry_id(engine, account_id=seeded["card"])
        paying_entry = _card_payment_entry_id(engine, account_id=seeded["checking"])
        assert card_entry == paying_entry, "the two source rows must share one entry"

        legs = _rows(engine, _CARD_PAYMENT_LEGS, account_id=seeded["card"])
        assert len(legs) == 2, legs
        synthesized = [row for row in legs if row[2] is True]
        assert len(synthesized) == 1, f"expected one synthesized leg, got {legs}"

        matches = _rows(
            engine,
            "SELECT match_method, confidence, journal_line_id_out,"
            " journal_line_id_in FROM finance.transfer_match",
        )
        assert len(matches) == 1, matches
        method, confidence, out_line, in_line = matches[0]
        assert method == "auto_card_payment"
        assert Decimal(str(confidence)) == Decimal("0.95")
        # Money left the paying (checking) account and arrived on the card.
        assert (
            _scalar(
                engine,
                "SELECT account_id FROM finance.journal_line WHERE id = :id",
                id=out_line,
            )
            == seeded["checking"]
        )
        assert (
            _scalar(
                engine,
                "SELECT account_id FROM finance.journal_line WHERE id = :id",
                id=in_line,
            )
            == seeded["card"]
        )

        # Zero equity movement for the pair: paying a debt is not spending.
        assert _scalar(
            engine,
            "SELECT coalesce(sum(jl.amount_base), 0) FROM finance.journal_line jl"
            " JOIN finance.account a ON a.id = jl.account_id"
            " WHERE jl.journal_entry_id = :entry AND a.account_nature = 'equity'",
            entry=card_entry,
        ) == Decimal(0)

    def test_two_synthesized_legs_in_the_window_are_refused_not_guessed(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two synthesized paying legs of the same amount in the window.

        The checking row could be either, so the match is refused and both
        candidates are named. The two legs are built directly, so the ONE card
        mapping is unambiguous and the only ambiguity is the one under test.
        """
        with engine.connect() as connection:
            with connection.begin():
                for _ in range(2):
                    entry_id = int(
                        connection.execute(
                            text(
                                "INSERT INTO finance.journal_entry"
                                " (entry_date, description, is_transfer)"
                                " VALUES (:date, 'synth card payment', true)"
                                " RETURNING id"
                            ),
                            {"date": PAIR_CARD_DATE},
                        ).scalar_one()
                    )
                    connection.execute(
                        text(
                            "INSERT INTO finance.journal_line"
                            " (journal_entry_id, account_id, amount, currency,"
                            "  amount_base, exchange_rate, sort_order)"
                            " VALUES (:entry, :card, :amount, 'EUR', :base, 1, 0)"
                        ),
                        {
                            "entry": entry_id,
                            "card": seeded["card"],
                            "amount": PAIR_MINOR,
                            "base": Decimal("765.6700"),
                        },
                    )
                    connection.execute(
                        text(
                            "INSERT INTO finance.journal_line"
                            " (journal_entry_id, account_id, amount, currency,"
                            "  amount_base, exchange_rate, sort_order,"
                            "  is_synthesized, synthesized_reason)"
                            " VALUES (:entry, :checking, :amount, 'EUR', :base, 1, 1,"
                            "         true, 'card_payment')"
                        ),
                        {
                            "entry": entry_id,
                            "checking": seeded["checking"],
                            "amount": -PAIR_MINOR,
                            "base": Decimal("-765.6700"),
                        },
                    )
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line"
                " WHERE is_synthesized AND synthesized_reason = 'card_payment'"
                "   AND account_id = :account",
                account=seeded["checking"],
            )
            == 2
        )

        body = upload(
            client,
            rabo(RABO_JUNE),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_JUNE,
        )
        assert body.failed == 1, body
        assert body.status == "partial", body

        stored = _rows(
            engine,
            "SELECT journal_entry_id, error_message FROM finance.source_record"
            " WHERE account_id = :account"
            "   AND raw_data->>'is_card_payment' = 'true'",
            account=seeded["checking"],
        )
        assert len(stored) == 1, stored
        stored_entry, message = stored[0]
        assert stored_entry is None, "an ambiguous match must not be posted"
        assert isinstance(message, str)
        candidate_ids = _rows(
            engine,
            "SELECT id FROM finance.journal_line"
            " WHERE is_synthesized AND synthesized_reason = 'card_payment'"
            "   AND account_id = :account ORDER BY id",
            account=seeded["checking"],
        )
        for (candidate_id,) in candidate_ids:
            assert str(candidate_id) in message, (
                f"candidate {candidate_id} is not named in {message!r}"
            )


# ---------------------------------------------------------------------------
# Single-sided fixtures — the other statement is not on disk
# ---------------------------------------------------------------------------


class TestSingleSidedFixtures:
    def test_amex_721_35_posts_alone_with_one_unmatched_synthesized_leg(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The 2026-05-28 card payment has no Rabobank May statement."""
        upload(
            client,
            amex(AMEX_JUNE),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JUNE,
        )
        synthesized = _rows(
            engine,
            "SELECT account_id, amount, transfer_match_id FROM finance.journal_line"
            " WHERE is_synthesized AND synthesized_reason = 'card_payment'",
        )
        assert len(synthesized) == 1, synthesized
        account_id, amount, match_id = synthesized[0]
        assert account_id == seeded["checking"]
        assert amount == -72135
        assert match_id is None
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0

    def test_rabobank_922_19_posts_alone_with_one_unmatched_synthesized_leg(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The 2026-09-30 bank debit has no Amex statement covering it."""
        upload(
            client,
            rabo(RABO_SEPTEMBER),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_SEPTEMBER,
        )
        synthesized = _rows(
            engine,
            "SELECT account_id, amount, transfer_match_id FROM finance.journal_line"
            " WHERE is_synthesized AND synthesized_reason = 'card_payment'",
        )
        assert len(synthesized) == 1, synthesized
        account_id, amount, match_id = synthesized[0]
        assert account_id == seeded["card"], "the missing side is the CARD"
        assert amount == 92219
        assert match_id is None
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0


# ---------------------------------------------------------------------------
# Resolution is explicit, never by name, never guessed
# ---------------------------------------------------------------------------


class TestResolution:
    def test_a_mapping_that_points_at_a_liability_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        with engine.connect() as connection:
            with connection.begin():
                other_liability = _insert_account(
                    connection,
                    name="Another liability",
                    account_type="loan",
                    account_nature="liability",
                )
                connection.execute(
                    text(
                        "UPDATE finance.account SET payment_from_account_id = :other"
                        " WHERE id = :card"
                    ),
                    {"other": other_liability, "card": seeded["card"]},
                )

        body = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        assert body.failed >= 1, body
        stored = _rows(
            engine,
            "SELECT journal_entry_id, error_message FROM finance.source_record"
            " WHERE raw_data->>'is_card_payment' = 'true'",
        )
        assert len(stored) == 1, stored
        entry_id, message = stored[0]
        assert entry_id is None
        assert isinstance(message, str) and "transfer" in message.lower()

    def test_an_explicit_id_that_is_the_card_itself_is_refused(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        body = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
            card_payment_account_id=seeded["card"],
        )
        assert body.failed >= 1, body
        assert (
            _scalar(
                engine,
                "SELECT journal_entry_id FROM finance.source_record"
                " WHERE raw_data->>'is_card_payment' = 'true'",
            )
            is None
        )

    def test_an_explicit_paying_account_is_used_without_a_saved_mapping(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """An explicit request id resolves the pair with no `payment_from` row."""
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text(
                        "UPDATE finance.account SET payment_from_account_id = NULL"
                        " WHERE id = :card"
                    ),
                    {"card": seeded["card"]},
                )
        body = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
            card_payment_account_id=seeded["checking"],
        )
        assert body.status == "completed", body
        legs = _rows(engine, _CARD_PAYMENT_LEGS, account_id=seeded["card"])
        assert len(legs) == 2, legs


# ---------------------------------------------------------------------------
# Re-import, the historical rewrite, and the invariants
# ---------------------------------------------------------------------------


class TestReimportAndInvariants:
    def test_reimporting_each_statement_is_a_noop(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        first = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        second = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        assert second.created == 0, second
        assert second.duplicated == first.record_count, second

        upload(
            client,
            rabo(RABO_JUNE),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_JUNE,
        )
        after_match = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        assert after_match.created == 0, after_match
        assert after_match.duplicated == first.record_count, after_match
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 1

    def test_an_already_posted_expense_is_rewritten_into_the_transfer(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Checking-first, but the paying row was posted before the card existed.

        This is the historical shape: the checking statement was imported while
        the card was unknown, so its row was posted as an expense with an equity
        contra-leg. When the card statement arrives, the entry is rewritten in
        place — the equity leg is deleted and the card leg inserted — because
        `journal_line` is editable and the balance trigger is deferred to COMMIT.
        """
        with engine.connect() as connection:
            with connection.begin():
                entry_id = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.journal_entry"
                            " (entry_date, description) VALUES (:date, 'old expense')"
                            " RETURNING id"
                        ),
                        {"date": PAIR_BANK_DATE},
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        "INSERT INTO finance.journal_line"
                        " (journal_entry_id, account_id, amount, currency,"
                        "  amount_base, exchange_rate, sort_order)"
                        " VALUES (:entry, :account, :amount, 'EUR', :base, 1, 0)"
                    ),
                    {
                        "entry": entry_id,
                        "account": seeded["checking"],
                        "amount": -PAIR_MINOR,
                        "base": Decimal("-765.6700"),
                    },
                )
                equity_id = int(
                    connection.execute(
                        text(
                            "SELECT id FROM finance.account"
                            " WHERE system_role = 'system_expense'"
                        )
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        "INSERT INTO finance.journal_line"
                        " (journal_entry_id, account_id, amount, currency,"
                        "  amount_base, exchange_rate, sort_order)"
                        " VALUES (:entry, :account, :amount, 'EUR', :base, 1, 1)"
                    ),
                    {
                        "entry": entry_id,
                        "account": equity_id,
                        "amount": PAIR_MINOR,
                        "base": Decimal("765.6700"),
                    },
                )

        body = upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        assert body.status == "completed", body

        # The card source row points at the OLD entry, not a new one.
        assert _card_payment_entry_id(engine, account_id=seeded["card"]) == entry_id
        # The equity leg is gone and the card leg is in its place.
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line jl"
                " JOIN finance.account a ON a.id = jl.account_id"
                " WHERE jl.journal_entry_id = :entry AND a.account_nature = 'equity'",
                entry=entry_id,
            )
            == 0
        )
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line"
                " WHERE journal_entry_id = :entry AND account_id = :card",
                entry=entry_id,
                card=seeded["card"],
            )
            == 1
        )
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 1
        assert (
            _scalar(
                engine,
                "SELECT match_method FROM finance.transfer_match",
            )
            == "user_confirmed"
        )

    def test_balance_invariant_and_raw_data_immutability_hold(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        upload(
            client,
            rabo(RABO_JUNE),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_JUNE,
        )
        unbalanced = _rows(
            engine,
            "SELECT journal_entry_id, sum(amount_base)"
            " FROM finance.journal_line GROUP BY journal_entry_id"
            " HAVING sum(amount_base) <> 0",
        )
        assert unbalanced == [], f"entries that do not balance: {unbalanced}"

        record_id_value = _scalar(engine, "SELECT min(id) FROM finance.source_record")
        assert isinstance(record_id_value, int) and not isinstance(
            record_id_value, bool
        ), record_id_value
        record_id = record_id_value
        with pytest.raises(DBAPIError):
            with engine.connect() as connection:
                with connection.begin():
                    connection.execute(
                        text(
                            "UPDATE finance.source_record SET raw_data = '{}'"
                            " WHERE id = :id"
                        ),
                        {"id": record_id},
                    )


# ---------------------------------------------------------------------------
# The generic transfer sweep leaves a card payment alone
# ---------------------------------------------------------------------------


class TestLinkerLeavesCardPaymentsAlone:
    def test_link_transfers_adds_no_match_over_a_card_payment(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The card payment's match is the card writer's, not the sweep's.

        After both statements land there is exactly one `transfer_match`
        (`auto_card_payment`): the paying leg is real, the missing side is
        synthesized, and the generic sweep must not link a placeholder to a
        stranger. Running `link_transfers` over that ledger adds zero matches
        and the stored row is byte-for-byte the one the import wrote.
        """
        upload(
            client,
            amex(AMEX_JULY),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JULY,
        )
        upload(
            client,
            rabo(RABO_JUNE),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_JUNE,
        )
        before = _rows(
            engine,
            "SELECT match_method, confidence, journal_line_id_out,"
            " journal_line_id_in, confirmed_at FROM finance.transfer_match"
            " ORDER BY id",
        )
        assert len(before) == 1, before
        assert before[0][0] == "auto_card_payment"

        with get_sessionmaker()() as session:
            with session.begin():
                report = link_transfers(session)

        assert report.auto_matched == 0, report
        after = _rows(
            engine,
            "SELECT match_method, confidence, journal_line_id_out,"
            " journal_line_id_in, confirmed_at FROM finance.transfer_match"
            " ORDER BY id",
        )
        assert after == before


class TestCardPaymentIsNotUncategorisedSpending:
    """LifeOS-6aq: the uncategorised queue must not report debt repayment as spending.

    `GET /transactions/uncategorized` answers "what did I spend". A card payment
    debits checking, so its paying leg is a negative, uncategorised, unmatched
    line — every property the queue selects for — and it was being listed as
    consumption. `transfer_match_id IS NULL` cannot catch it: an unmatched card
    payment has no match row. The transfer flag is on the `JournalEntry`.

    Every test below asserts the queue by AMOUNT, so a fix that emptied the queue
    outright would fail rather than pass.
    """

    @staticmethod
    def _amounts(client: TestClient) -> list[int]:
        response = client.get("/api/v1/transactions/uncategorized")
        assert response.status_code == 200, response.text
        rows = response.json()
        return [int(row["raw_amount"]) for row in rows]

    def test_a_checking_debit_card_payment_is_absent_from_the_queue(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The live defect: 2026-09-30, -922.19, no Amex statement to match."""
        upload(
            client,
            rabo(RABO_SEPTEMBER),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_SEPTEMBER,
        )
        assert -92219 not in self._amounts(client), (
            "a card payment is debt repayment, not spending, and must not be queued"
        )

    def test_the_same_payment_absent_in_the_other_import_order_too(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Card-first must behave identically: the synthesized leg has no record."""
        upload(
            client,
            amex(AMEX_JUNE),
            provider="amex_pdf",
            account_id=seeded["card"],
            filename=AMEX_JUNE,
        )
        upload(
            client,
            rabo(RABO_SEPTEMBER),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_SEPTEMBER,
        )
        assert -92219 not in self._amounts(client)

    def test_a_genuine_uncategorised_expense_is_still_queued(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The guard on the fix: real spending must NOT disappear.

        The Rabobank statements hold ordinary debits alongside the card payment,
        so one of those is the control. If the whole September file were quietly
        filtered out, this fails.
        """
        summary = upload(
            client,
            rabo(RABO_SEPTEMBER),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_SEPTEMBER,
        )
        assert summary.landed > 1, "expected other rows in the September statement"
        queued = self._amounts(client)
        assert queued, "no uncategorised transactions left at all; over-filtered"
        assert all(amount != -92219 for amount in queued), queued

    def test_the_payment_is_still_visible_in_the_unfiltered_list(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Excluded from the spend queue, NOT hidden from the ledger.

        It is a real transaction. Suppressing it everywhere would make the
        account look like it never paid the card.
        """
        upload(
            client,
            rabo(RABO_SEPTEMBER),
            provider="rabobank_pdf",
            account_id=seeded["checking"],
            filename=RABO_SEPTEMBER,
        )
        response = client.get(
            "/api/v1/transactions", params={"account_id": seeded["checking"]}
        )
        assert response.status_code == 200, response.text
        amounts = [int(row["raw_amount"]) for row in response.json()]
        assert -92219 in amounts, "the card payment vanished from the transaction list"
