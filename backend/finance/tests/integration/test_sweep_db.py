"""test_sweep_db.py — the nightly sweep over a backlog, on real Postgres.

`sweep_unmatched` is the same sweep as `link_transfers` with a default
90-day window. These tests seed a backlog of unmatched legs and assert the
sweep clears it, does nothing twice, and respects an ignore.

On dates: auto-linking needs same-day pairs (the 0.95 shape), so the backlog
is five same-day pairs dated yesterday — old enough to be a backlog, exact
enough to auto-link. A yesterday/today pair would queue `low_confidence`
reviews by design, which is the review queue's test, not this file's.

Marked `db`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Connection, Engine, text

from finance.api.sweep import sweep_unmatched
from finance.api.transfer_linker import TransferLinkReport, link_transfers
from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME

pytestmark = pytest.mark.db

#: The backlog day: yesterday relative to nothing in particular, simply old.
BACKLOG_DAY = dt.date(2026, 3, 10)

#: EUR minor units per major unit, for `amount_base`.
SCALE = Decimal(100)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated `lifeos_test`, caches cleared."""
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
    """Two own asset accounts and the system expense account."""
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals) VALUES"
                    " ('EUR', 'Euro', 2), ('USD', 'US Dollar', 2)"
                )
            )
            ids: dict[str, int] = {}
            for key, name, kind, currency in (
                ("checking", "Checking", "checking", "EUR"),
                ("savings", "Savings", "savings", "EUR"),
                ("usd", "USD wallet", "checking", "USD"),
            ):
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, :kind, 'asset', :currency)"
                            " RETURNING id"
                        ),
                        {"name": name, "kind": kind, "currency": currency},
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
    return ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scalar(engine: Engine, statement: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(statement), params).scalar_one()


def _post(
    connection: Connection,
    when: dt.date,
    legs: list[tuple[int, int]],
    *,
    description: str = "seeded",
) -> None:
    """Write one balanced two-leg entry, in minor units."""
    entry_id = int(
        connection.execute(
            text(
                "INSERT INTO finance.journal_entry"
                " (entry_date, description, is_transfer, is_split)"
                " VALUES (:when, :description, FALSE, FALSE) RETURNING id"
            ),
            {"when": when, "description": description},
        ).scalar_one()
    )
    for order, (account_id, amount) in enumerate(legs):
        connection.execute(
            text(
                "INSERT INTO finance.journal_line"
                " (journal_entry_id, account_id, amount, currency, amount_base,"
                "  exchange_rate, sort_order)"
                " VALUES (:entry, :account, :amount,"
                "  (SELECT currency FROM finance.account WHERE id = :account),"
                "  :base, 1.0, :order)"
            ),
            {
                "entry": entry_id,
                "account": account_id,
                "amount": amount,
                "base": Decimal(amount) / SCALE,
                "order": order,
            },
        )


def _seed_backlog(engine: Engine, seeded: dict[str, int]) -> None:
    """Five same-day outbound/inbound pairs, all unmatched.

    Distinct amounts (€10–€50) so each outbound sees exactly one candidate.
    """
    with engine.connect() as connection:
        with connection.begin():
            for step in range(1, 6):
                amount = step * 1_000
                _post(
                    connection,
                    BACKLOG_DAY,
                    [
                        (seeded["checking"], -amount),
                        (seeded["expense"], amount),
                    ],
                    description=f"backlog out {amount}",
                )
                _post(
                    connection,
                    BACKLOG_DAY,
                    [(seeded["savings"], amount), (seeded["expense"], -amount)],
                    description=f"backlog in {amount}",
                )


def _sweep(
    *, from_: dt.date | None = None, to_: dt.date | None = None
) -> TransferLinkReport:
    """Run the sweep in its own transaction, the way the CLI does."""
    with get_sessionmaker()() as session:
        with session.begin():
            return sweep_unmatched(session, from_=from_, to_=to_)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


class TestSweep:
    def test_a_backlog_of_five_pairs_is_matched(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Five unmatched pairs in, five `auto_amount_date` matches out."""
        del client
        _seed_backlog(engine, seeded)

        report = _sweep(from_=BACKLOG_DAY, to_=BACKLOG_DAY)

        assert report.auto_matched == 5
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 5
        assert (
            _scalar(
                engine,
                "SELECT count(DISTINCT match_method) FROM finance.transfer_match",
            )
            == 1
        )
        assert (
            _scalar(
                engine,
                "SELECT DISTINCT match_method FROM finance.transfer_match",
            )
            == "auto_amount_date"
        )
        # Every outbound leg stamped, no review left behind.
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line"
                " WHERE amount < 0 AND transfer_match_id IS NULL"
                "   AND account_id = :account",
                account=seeded["checking"],
            )
            == 0
        )
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.transfer_review WHERE status = 'pending'",
            )
            == 0
        )

    def test_running_the_sweep_twice_writes_nothing_new(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The sweep is idempotent: the second run links nothing."""
        del client
        _seed_backlog(engine, seeded)

        first = _sweep(from_=BACKLOG_DAY, to_=BACKLOG_DAY)
        assert first.auto_matched == 5
        second = _sweep(from_=BACKLOG_DAY, to_=BACKLOG_DAY)

        assert second.auto_matched == 0
        assert second.review_queued_multi == 0
        assert second.review_queued_low_confidence == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 5

    def test_a_pair_the_user_ignored_is_not_matched_by_the_sweep(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """An ignored pair stays ignored, even though the sweep re-examines it.

        The pair is cross-currency (EUR out, USD in): it can never auto-link,
        so the review is the only thing the ignore has to suppress — and after
        the ignore, the sweep writes neither a match nor a fresh review.
        """
        del client
        with engine.connect() as connection:
            with connection.begin():
                _post(
                    connection,
                    BACKLOG_DAY,
                    [(seeded["checking"], -5_000), (seeded["expense"], 5_000)],
                    description="euro out",
                )
                _post(
                    connection,
                    BACKLOG_DAY,
                    [(seeded["usd"], 5_000), (seeded["expense"], -5_000)],
                    description="dollar in",
                )

        with get_sessionmaker()() as session:
            with session.begin():
                first = link_transfers(session)
                assert first.review_queued_low_confidence == 1
                review_id = int(
                    session.execute(
                        text(
                            "SELECT id FROM finance.transfer_review"
                            " WHERE status = 'pending'"
                        )
                    ).scalar_one()
                )
                from finance.api.transfer_linker import ignore_review

                ignore_review(session, review_id=review_id)

        report = _sweep(from_=BACKLOG_DAY, to_=BACKLOG_DAY)

        assert report.auto_matched == 0
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.transfer_review WHERE status = 'pending'",
            )
            == 0
        )
