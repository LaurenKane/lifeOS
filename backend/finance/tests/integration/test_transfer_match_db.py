"""test_transfer_match_db.py — the transfer linker's ledger facts, on real Postgres.

The sweep in `finance.api.transfer_linker` links the unmatched OUTGOING leg
to its incoming half. These tests seed the ledger directly (like
`test_analytics_db.py`: the subject is the matcher's decision, so every
amount and date is stated rather than derived) and assert what landed:

* an exact same-day pair auto-links with `auto_amount_date` at 0.95;
* two pairs link as two pairs, never one merged row;
* a lone third-party debit stays an expense;
* two candidates queue one `multi_candidate` review, never a guess;
* a cross-currency candidate NEVER auto-links (`low_confidence`) — the
  mitigation of the PHASE2-PLAN §3d FX-residual defect;
* an out-of-window pair is left alone entirely.

Marked `db`: real migrated Postgres, assertions read back from a second
connection. Journal-line ids are never hardcoded: they are BIGSERIAL, so
every test finds its rows by account and amount.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Connection, Engine, text

from finance.api.transfer_linker import TransferLinkReport, link_transfers
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME

pytestmark = pytest.mark.db

#: One fixed day, so window arithmetic is a literal in every test.
DAY = dt.date(2026, 3, 10)

#: EUR minor units per major unit, for `amount_base`.
SCALE = Decimal(100)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`.

    Requested by every test that reads through HTTP or calls the linker: it
    clears the cached engine/sessionmaker so both aim at the throwaway
    database rather than whatever was read first.
    """
    del test_database_url
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
def seeded(engine: Engine) -> dict[str, int]:
    """Currencies, own accounts, a card, the system expense account, categories."""
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals) VALUES"
                    " ('EUR', 'Euro', 2), ('USD', 'US Dollar', 2)"
                )
            )
            ids: dict[str, int] = {}
            for key, name, kind, nature, currency in (
                ("checking", "Checking", "checking", "asset", "EUR"),
                ("savings", "Savings", "savings", "asset", "EUR"),
                ("savings2", "Savings 2", "savings", "asset", "EUR"),
                ("card", "Card", "credit_card", "liability", "EUR"),
                ("usd", "USD wallet", "checking", "asset", "USD"),
            ):
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, :kind, :nature, :currency)"
                            " RETURNING id"
                        ),
                        {
                            "name": name,
                            "kind": kind,
                            "nature": nature,
                            "currency": currency,
                        },
                    ).scalar_one()
                )
            ids["expense"] = int(
                connection.execute(
                    text(
                        "INSERT INTO finance.account"
                        " (name, account_type, account_nature, currency,"
                        "  system_role)"
                        " VALUES (:name, 'cash', 'equity', 'EUR',"
                        "  'system_expense') RETURNING id"
                    ),
                    {"name": SYSTEM_EXPENSE_ACCOUNT_NAME},
                ).scalar_one()
            )
            for key, name, kind in (
                ("groceries", "Groceries", "expense"),
                ("internal", "Internal transfer", "transfer"),
            ):
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.category (name, kind, is_system)"
                            " VALUES (:name, :kind, TRUE) RETURNING id"
                        ),
                        {"name": name, "kind": kind},
                    ).scalar_one()
                )
    return ids


# ---------------------------------------------------------------------------
# Database reads and writes, always from a second connection
# ---------------------------------------------------------------------------


def _scalar(engine: Engine, statement: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(statement), params).scalar_one()


def _rows(engine: Engine, statement: str, **params: object) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return list(connection.execute(text(statement), params).all())


def _as_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"expected an int column, got {value!r}"
    )
    return value


def _as_str(value: object) -> str:
    assert isinstance(value, str), f"expected a text column, got {value!r}"
    return value.strip()


def _post(
    connection: Connection,
    when: dt.date,
    legs: list[tuple[int, int, int | None]],
    *,
    description: str = "seeded",
    is_transfer: bool = False,
) -> int:
    """Write one balanced journal entry, in minor units. Returns its id."""
    entry_id = int(
        connection.execute(
            text(
                "INSERT INTO finance.journal_entry"
                " (entry_date, description, is_transfer, is_split)"
                " VALUES (:when, :description, :transfer, FALSE) RETURNING id"
            ),
            {"when": when, "description": description, "transfer": is_transfer},
        ).scalar_one()
    )
    for order, (account_id, amount, category_id) in enumerate(legs):
        connection.execute(
            text(
                "INSERT INTO finance.journal_line"
                " (journal_entry_id, account_id, amount, currency, amount_base,"
                "  exchange_rate, category_id, sort_order)"
                " VALUES (:entry, :account, :amount,"
                "  (SELECT currency FROM finance.account WHERE id = :account),"
                "  :base, 1.0, :category, :order)"
            ),
            {
                "entry": entry_id,
                "account": account_id,
                "amount": amount,
                "base": Decimal(amount) / SCALE,
                "category": category_id,
                "order": order,
            },
        )
    return entry_id


def _link() -> TransferLinkReport:
    """Run the linker in its own transaction, the way the import route does."""
    with get_sessionmaker()() as session:
        with session.begin():
            return link_transfers(session)


def _spend(client: TestClient, start: dt.date, end: dt.date) -> dict[str, int]:
    """Spend by category as `{name: amount}`, read through the real endpoint."""
    response = client.get(
        "/api/v1/analytics/spend-by-category",
        params={"from": start.isoformat(), "to": end.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list), body
    reported: dict[str, int] = {}
    for row in body:
        assert isinstance(row, dict), row
        name = row["category_name"]
        amount = row["amount"]
        assert isinstance(name, str) and isinstance(amount, int), row
        reported[name] = amount
    return reported


# ---------------------------------------------------------------------------
# The linker's decisions
# ---------------------------------------------------------------------------


class TestAutoLink:
    def test_same_day_same_amount_pair_links_automatically(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The textbook transfer: -€50 out of checking, +€50 into savings.

        Exactly one `transfer_match`: `auto_amount_date` at 0.95, unconfirmed
        (`confirmed_at` NULL — the matcher decided, no human agreed). Both
        asset lines carry its id, both entries are flagged transfers, and the
        spend report excludes both — a transfer is not spending.
        """
        with engine.connect() as connection:
            with connection.begin():
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["checking"], -5_000, seeded["groceries"]),
                        (seeded["expense"], 5_000, seeded["groceries"]),
                    ],
                    description="transfer out",
                )
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["savings"], 5_000, seeded["groceries"]),
                        (seeded["expense"], -5_000, seeded["groceries"]),
                    ],
                    description="transfer in",
                )

        report = _link()

        assert report.auto_matched == 1
        assert report.review_queued_multi == 0
        assert report.review_queued_low_confidence == 0

        matches = _rows(
            engine,
            "SELECT match_method, confidence, confirmed_at,"
            " journal_line_id_out, journal_line_id_in"
            " FROM finance.transfer_match",
        )
        assert len(matches) == 1, matches
        method, confidence, confirmed_at, out_line, in_line = matches[0]
        assert method == "auto_amount_date"
        assert Decimal(str(confidence)) == Decimal("0.95")
        assert confirmed_at is None

        # The pair is (checking leg, savings leg): directional, out then in.
        assert (
            _scalar(
                engine,
                "SELECT account_id FROM finance.journal_line WHERE id = :id",
                id=_as_int(out_line),
            )
            == seeded["checking"]
        )
        assert (
            _scalar(
                engine,
                "SELECT account_id FROM finance.journal_line WHERE id = :id",
                id=_as_int(in_line),
            )
            == seeded["savings"]
        )

        # Both asset lines stamped, both entries flagged, equity legs untouched.
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line"
                " WHERE transfer_match_id IS NOT NULL",
            )
            == 2
        )
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_entry WHERE is_transfer",
            )
            == 2
        )

        # And neither side reads as spending: the category sits on transfer
        # entries, which the report excludes.
        assert _spend(client, DAY, DAY) == {}

    def test_two_pairs_link_as_two_pairs_never_one_merged(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two outbounds and two inbounds of DIFFERENT amounts link 1:1.

        Distinct amounts (€50 vs €60) so each outbound sees exactly one
        candidate. The assertion is on distinct ordered pairs: one merged row
        would silently claim four legs are one movement.
        """
        del client
        with engine.connect() as connection:
            with connection.begin():
                for amount in (5_000, 6_000):
                    _post(
                        connection,
                        DAY,
                        [
                            (seeded["checking"], -amount, None),
                            (seeded["expense"], amount, None),
                        ],
                        description=f"out {amount}",
                    )
                    _post(
                        connection,
                        DAY,
                        [
                            (seeded["savings"], amount, None),
                            (seeded["expense"], -amount, None),
                        ],
                        description=f"in {amount}",
                    )

        report = _link()

        assert report.auto_matched == 2
        pairs = _rows(
            engine,
            "SELECT journal_line_id_out, journal_line_id_in"
            " FROM finance.transfer_match",
        )
        assert len(pairs) == 2, pairs
        assert pairs[0] != pairs[1]
        assert len({pair[0] for pair in pairs}) == 2
        assert len({pair[1] for pair in pairs}) == 2


class TestLeftAlone:
    def test_a_third_party_direct_debit_stays_an_expense(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A lone SEPA debit names no counterparty, so nothing is queued.

        It stays what it is: an unmatched expense on a non-transfer entry —
        and the spend report still counts it.
        """
        with engine.connect() as connection:
            with connection.begin():
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["checking"], -10_000, seeded["groceries"]),
                        (seeded["expense"], 10_000, seeded["groceries"]),
                    ],
                    description="INCASSO ENERGIEBEDRIJF",
                )

        report = _link()

        assert report.auto_matched == 0
        assert report.review_queued_multi == 0
        assert report.review_queued_low_confidence == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_review") == 0
        assert (
            _scalar(
                engine,
                "SELECT is_transfer FROM finance.journal_entry",
            )
            is False
        )
        assert _spend(client, DAY, DAY) == {"Groceries": -10_000}

    def test_out_of_window_pair_is_left_alone(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Outbound on D, inbound on D+10: outside the -1/+3 day window.

        No match AND no review — a question about a pair ten days apart is
        noise, not diligence.
        """
        del client
        with engine.connect() as connection:
            with connection.begin():
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["checking"], -5_000, None),
                        (seeded["expense"], 5_000, None),
                    ],
                    description="out",
                )
                _post(
                    connection,
                    DAY + dt.timedelta(days=10),
                    [
                        (seeded["savings"], 5_000, None),
                        (seeded["expense"], -5_000, None),
                    ],
                    description="too late",
                )

        report = _link()

        assert report.auto_matched == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_review") == 0


class TestReviewQueueing:
    def test_two_candidates_queue_one_multi_candidate_review(
        self, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two in-window candidates on different own accounts: a question.

        Zero matches, one `pending` review naming BOTH candidate ids. Linking
        either one automatically would be a coin flip about where the money
        went.
        """
        with engine.connect() as connection:
            with connection.begin():
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["checking"], -5_000, None),
                        (seeded["expense"], 5_000, None),
                    ],
                    description="out",
                )
                for account in ("savings", "savings2"):
                    _post(
                        connection,
                        DAY,
                        [
                            (seeded[account], 5_000, None),
                            (seeded["expense"], -5_000, None),
                        ],
                        description=f"in on {account}",
                    )

        report = _link()

        assert report.auto_matched == 0
        assert report.review_queued_multi == 1
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        rows = _rows(
            engine,
            "SELECT outbound_journal_line_id, candidate_journal_line_ids,"
            " reason, status FROM finance.transfer_review",
        )
        assert len(rows) == 1, rows
        outbound_id, candidate_ids, reason, status = rows[0]
        assert _as_str(reason) == "multi_candidate"
        assert _as_str(status) == "pending"

        expected = {
            _as_int(
                _scalar(
                    engine,
                    "SELECT id FROM finance.journal_line"
                    " WHERE account_id = :account AND amount = 5000",
                    account=seeded[account],
                )
            )
            for account in ("savings", "savings2")
        }
        assert isinstance(candidate_ids, list), candidate_ids
        assert {int(value) for value in candidate_ids} == expected
        assert _as_int(outbound_id) == _as_int(
            _scalar(
                engine,
                "SELECT id FROM finance.journal_line"
                " WHERE account_id = :account AND amount = -5000",
                account=seeded["checking"],
            )
        )

    def test_cross_currency_candidate_never_auto_matches(
        self, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The PHASE2-PLAN §3d FX-residual defect, asserted as its absence.

        A USD inbound within the 50-minor cross-currency tolerance satisfies
        the pure rule — but the linker must NEVER auto-link across currencies.
        One `low_confidence` review, zero matches: a human decides whether the
        €50 debit and the $50.25 credit are the same money.
        """
        with engine.connect() as connection:
            with connection.begin():
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["checking"], -5_000, None),
                        (seeded["expense"], 5_000, None),
                    ],
                    description="euro out",
                )
                _post(
                    connection,
                    DAY,
                    [
                        (seeded["usd"], 5_025, None),
                        (seeded["expense"], -5_025, None),
                    ],
                    description="dollar in",
                )

        report = _link()

        assert report.auto_matched == 0
        assert report.review_queued_low_confidence == 1
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        rows = _rows(
            engine,
            "SELECT reason, status FROM finance.transfer_review",
        )
        assert rows == [("low_confidence", "pending")]


# ---------------------------------------------------------------------------
# Headline acceptance: a card purchase plus its payment counts once
# ---------------------------------------------------------------------------


class TestACardPurchaseAndItsPaymentCountOnce:
    """An Amex-style purchase then its Rabobank payment is €50 of spending.

    The purchase is the expense; the payment is a transfer (liability paid
    from an asset) and must not read as a second €50. Built with the
    card-payment writer — the real shape, including the synthesized leg —
    rather than hand-inserted rows that merely resemble it.
    """

    def test_spending_is_50_not_100_and_both_entries_balance(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        from core.money import Currency, Money

        from finance.api.writers import (
            open_import_batch,
            write_card_payment,
            write_posted_transaction,
        )

        eur = Currency(code="EUR", decimals=2)
        purchase_day = dt.date(2026, 4, 2)
        payment_day = dt.date(2026, 5, 1)

        with get_sessionmaker()() as session:
            with session.begin():
                purchase_batch = open_import_batch(
                    session,
                    provider="manual",
                    import_method="manual",
                    status="completed",
                    account_id=seeded["card"],
                )
                purchase_record = write_posted_transaction(
                    session,
                    batch_id=purchase_batch,
                    funding_account_id=seeded["card"],
                    description="AMEX PURCHASE",
                    amount=Money(amount=-5_000, currency=eur),
                    booked_date=purchase_day,
                    raw_data={"source": "test"},
                )
                payment_batch = open_import_batch(
                    session,
                    provider="manual",
                    import_method="manual",
                    status="completed",
                    account_id=seeded["checking"],
                )
                # The checking statement's row first: real paying leg plus the
                # synthesized card leg no statement printed.
                write_card_payment(
                    session,
                    batch_id=payment_batch,
                    card_account_id=seeded["card"],
                    paying_account_id=seeded["checking"],
                    statement_account_id=seeded["checking"],
                    amount=Money(amount=-5_000, currency=eur),
                    description="-50 American Express",
                    booked_date=payment_day,
                    raw_data={"source": "test"},
                )
                # Then the card statement's row, which attaches to that leg.
                write_card_payment(
                    session,
                    batch_id=payment_batch,
                    card_account_id=seeded["card"],
                    paying_account_id=seeded["checking"],
                    statement_account_id=seeded["card"],
                    amount=Money(amount=5_000, currency=eur),
                    description="-50 American Express",
                    booked_date=payment_day,
                    raw_data={"source": "test"},
                )
                assert isinstance(purchase_record, int)

        # Categorise the purchase's card leg, the way the review queue would.
        with engine.connect() as connection:
            with connection.begin():
                purchase_line = int(
                    connection.execute(
                        text(
                            "SELECT jl.id FROM finance.journal_line jl"
                            " JOIN finance.source_record sr"
                            "  ON sr.journal_entry_id = jl.journal_entry_id"
                            " WHERE sr.id = :id AND jl.account_id = :account"
                        ),
                        {"id": purchase_record, "account": seeded["card"]},
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        "UPDATE finance.journal_line"
                        " SET category_id = :cat WHERE id = :id"
                    ),
                    {"cat": seeded["groceries"], "id": purchase_line},
                )

        # Total spending is €50, not €100: the payment is a transfer.
        assert _spend(client, purchase_day, payment_day) == {"Groceries": -5_000}

        # Both journal entries balance to exactly zero.
        unbalanced = _rows(
            engine,
            "SELECT journal_entry_id, sum(amount_base)"
            " FROM finance.journal_line GROUP BY journal_entry_id"
            " HAVING sum(amount_base) <> 0",
        )
        assert unbalanced == []
        assert _scalar(engine, "SELECT count(*) FROM finance.journal_entry") == 2
