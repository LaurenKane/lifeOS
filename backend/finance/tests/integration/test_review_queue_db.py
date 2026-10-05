"""test_review_queue_db.py — the transfer review queue's HTTP contract, on Postgres.

`GET /review/transfers` lists the linker's pending questions with both legs
described; the three POSTs answer them. Every test seeds the ledger directly
(the matcher's inputs, stated not derived), runs the linker, and then drives
the real FastAPI app — asserting both the status codes and the ledger facts.

Marked `db`. Line and entry ids are never hardcoded: they are BIGSERIAL, so
every test finds its rows by account, amount and review id from the API.
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

#: One fixed day, so every pair in here is same-day and exact.
DAY = dt.date(2026, 3, 10)

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
    """Two own asset accounts plus a spare, and the system expense account."""
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            ids: dict[str, int] = {}
            for key, name, kind in (
                ("checking", "Checking", "checking"),
                ("savings", "Savings", "savings"),
                ("savings2", "Savings 2", "savings"),
            ):
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, :kind, 'asset', 'EUR') RETURNING id"
                        ),
                        {"name": name, "kind": kind},
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


def _as_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _post(
    connection: Connection,
    when: dt.date,
    legs: list[tuple[int, int]],
    *,
    description: str = "seeded",
) -> None:
    """Write one balanced two-leg entry against the expense account."""
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
                " VALUES (:entry, :account, :amount, 'EUR', :base, 1.0, :order)"
            ),
            {
                "entry": entry_id,
                "account": account_id,
                "amount": amount,
                "base": Decimal(amount) / SCALE,
                "order": order,
            },
        )


def _seed_multi(engine: Engine, seeded: dict[str, int], *, amount: int = 5_000) -> None:
    """One outbound leg with TWO same-day candidates on different accounts."""
    with engine.connect() as connection:
        with connection.begin():
            _post(
                connection,
                DAY,
                [(seeded["checking"], -amount), (seeded["expense"], amount)],
                description="outbound",
            )
            for account in ("savings", "savings2"):
                _post(
                    connection,
                    DAY,
                    [(seeded[account], amount), (seeded["expense"], -amount)],
                    description=f"candidate on {account}",
                )


def _link() -> TransferLinkReport:
    """Run the linker in its own transaction, the way the import route does."""
    with get_sessionmaker()() as session:
        with session.begin():
            return link_transfers(session)


def _queue(client: TestClient) -> list[dict[str, object]]:
    """The pending queue, each item checked to be a JSON object."""
    response = client.get("/api/v1/review/transfers")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list), body
    items: list[dict[str, object]] = []
    for item in body:
        assert isinstance(item, dict), item
        items.append(item)
    return items


def _leg(value: object) -> dict[str, object]:
    assert isinstance(value, dict), value
    return value


def _stats(client: TestClient) -> dict[str, int]:
    response = client.get("/api/v1/review/transfers/stats")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict), body
    return {
        key: _as_int(body[key])
        for key in ("multi_candidate", "low_confidence", "total")
    }


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------


class TestConfirm:
    def test_confirm_with_a_candidate_links_as_user_confirmed(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Confirming names the winner: a `user_confirmed` match at 1.00.

        The review resolves (`confirmed`, `resolved_at` set), both lines carry
        the new match id, and both entries are flagged transfers.
        """
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)
        review_id = _as_int(item["id"])
        candidates = item["candidates"]
        assert isinstance(candidates, list) and len(candidates) == 2
        chosen = _as_int(_leg(candidates[0])["journal_line_id"])

        response = client.post(
            f"/api/v1/review/transfers/{review_id}/confirm",
            json={"candidate_journal_line_id": chosen},
        )
        assert response.status_code == 200, response.text

        assert (
            _scalar(
                engine,
                "SELECT status FROM finance.transfer_review WHERE id = :id",
                id=review_id,
            )
            == "confirmed"
        )
        assert (
            _scalar(
                engine,
                "SELECT resolved_at FROM finance.transfer_review WHERE id = :id",
                id=review_id,
            )
            is not None
        )

        matches = _rows(
            engine,
            "SELECT match_method, confidence, confirmed_at,"
            " journal_line_id_out, journal_line_id_in"
            " FROM finance.transfer_match",
        )
        assert len(matches) == 1, matches
        method, confidence, confirmed_at, out_line, in_line = matches[0]
        assert _as_str(method) == "user_confirmed"
        assert _as_decimal(confidence) == Decimal("1.00")
        assert confirmed_at is not None
        assert _as_int(in_line) == chosen

        stamped = _rows(
            engine,
            "SELECT id FROM finance.journal_line"
            " WHERE transfer_match_id IS NOT NULL ORDER BY id",
        )
        assert {row[0] for row in stamped} == {_as_int(out_line), chosen}
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_entry WHERE is_transfer",
            )
            == 2
        )
        # Resolved items leave the queue.
        assert _queue(client) == []

    def test_confirm_without_a_candidate_is_a_400(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two candidates and no choice is a refusal, not a guess."""
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)

        response = client.post(
            f"/api/v1/review/transfers/{_as_int(item['id'])}/confirm", json={}
        )
        assert response.status_code == 400, response.text
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        assert (
            _scalar(
                engine,
                "SELECT status FROM finance.transfer_review WHERE id = :id",
                id=_as_int(item["id"]),
            )
            == "pending"
        )

    def test_confirm_with_an_unknown_candidate_is_a_400(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """A candidate the review never listed is a refusal, not a link."""
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)

        response = client.post(
            f"/api/v1/review/transfers/{_as_int(item['id'])}/confirm",
            json={"candidate_journal_line_id": 999_999},
        )
        assert response.status_code == 400, response.text
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0

    def test_confirming_a_missing_or_resolved_review_is_a_404(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """No such review — or one already decided — is a 404 either way."""
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)
        review_id = _as_int(item["id"])
        candidates = item["candidates"]
        assert isinstance(candidates, list) and len(candidates) == 2
        chosen = _as_int(_leg(candidates[0])["journal_line_id"])

        assert (
            client.post(
                "/api/v1/review/transfers/999999/confirm",
                json={"candidate_journal_line_id": chosen},
            ).status_code
            == 404
        )
        first = client.post(
            f"/api/v1/review/transfers/{review_id}/confirm",
            json={"candidate_journal_line_id": chosen},
        )
        assert first.status_code == 200, first.text
        second = client.post(
            f"/api/v1/review/transfers/{review_id}/confirm",
            json={"candidate_journal_line_id": chosen},
        )
        assert second.status_code == 404, second.text


# ---------------------------------------------------------------------------
# Reject and ignore
# ---------------------------------------------------------------------------


class TestReject:
    def test_reject_leaves_the_ledger_untouched(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Rejected means "not a transfer": no match, lines free, flags kept."""
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)
        review_id = _as_int(item["id"])

        response = client.post(f"/api/v1/review/transfers/{review_id}/reject")
        assert response.status_code == 200, response.text

        assert (
            _scalar(
                engine,
                "SELECT status FROM finance.transfer_review WHERE id = :id",
                id=review_id,
            )
            == "rejected"
        )
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_line"
                " WHERE transfer_match_id IS NOT NULL",
            )
            == 0
        )
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.journal_entry WHERE is_transfer",
            )
            == 0
        )
        assert _queue(client) == []

    def test_rejecting_a_missing_review_is_a_404(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        assert client.post("/api/v1/review/transfers/999999/reject").status_code == 404


class TestIgnore:
    def test_ignore_writes_one_skip_per_candidate(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Ignoring silences every pair the review named — one row each."""
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)
        review_id = _as_int(item["id"])
        candidates = item["candidates"]
        assert isinstance(candidates, list) and len(candidates) == 2

        response = client.post(f"/api/v1/review/transfers/{review_id}/ignore")
        assert response.status_code == 200, response.text

        assert (
            _scalar(
                engine,
                "SELECT status FROM finance.transfer_review WHERE id = :id",
                id=review_id,
            )
            == "ignored"
        )
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_skip") == 2
        assert _scalar(engine, "SELECT count(*) FROM finance.transfer_match") == 0

    def test_after_ignore_relinking_suggests_nothing(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """The sweep must not re-ask a pair the user dismissed."""
        _seed_multi(engine, seeded)
        _link()
        (item,) = _queue(client)
        assert (
            client.post(
                f"/api/v1/review/transfers/{_as_int(item['id'])}/ignore"
            ).status_code
            == 200
        )

        report = _link()

        assert report.review_queued_multi == 0
        assert report.review_queued_low_confidence == 0
        assert report.auto_matched == 0
        assert (
            _scalar(
                engine,
                "SELECT count(*) FROM finance.transfer_review WHERE status = 'pending'",
            )
            == 0
        )

    def test_ignoring_a_missing_review_is_a_404(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        del engine, seeded
        assert client.post("/api/v1/review/transfers/999999/ignore").status_code == 404


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestListing:
    def test_the_queue_lists_only_pending_items_with_both_legs(
        self, client: TestClient, engine: Engine, seeded: dict[str, int]
    ) -> None:
        """Two questions, one answered: the queue holds the other, fully
        described — outbound leg, both candidates with confidences, reason."""
        with engine.connect() as connection:
            with connection.begin():
                for amount in (5_000, 6_000):
                    _post(
                        connection,
                        DAY,
                        [
                            (seeded["checking"], -amount),
                            (seeded["expense"], amount),
                        ],
                        description=f"outbound {amount}",
                    )
                    for account in ("savings", "savings2"):
                        _post(
                            connection,
                            DAY,
                            [
                                (seeded[account], amount),
                                (seeded["expense"], -amount),
                            ],
                            description=f"candidate {amount} on {account}",
                        )
        _link()
        assert len(_queue(client)) == 2

        items = _queue(client)
        by_id = {_as_int(item["id"]): item for item in items}
        assert len(by_id) == 2
        first_id = min(by_id)
        candidates = by_id[first_id]["candidates"]
        assert isinstance(candidates, list) and len(candidates) == 2
        chosen = _as_int(_leg(candidates[0])["journal_line_id"])
        assert (
            client.post(
                f"/api/v1/review/transfers/{first_id}/confirm",
                json={"candidate_journal_line_id": chosen},
            ).status_code
            == 200
        )

        remaining = _queue(client)
        assert len(remaining) == 1, remaining
        kept = remaining[0]
        assert _as_int(kept["id"]) != first_id
        assert _as_str(kept["reason"]) == "multi_candidate"

        outbound = _leg(kept["outbound"])
        assert _as_int(outbound["journal_line_id"]) > 0
        assert _as_int(outbound["amount_minor"]) == -6_000
        assert _as_str(outbound["currency"]) == "EUR"
        assert _as_str(outbound["booked_date"]) == DAY.isoformat()
        assert _as_str(outbound["account_name"]) != ""

        kept_candidates = kept["candidates"]
        assert isinstance(kept_candidates, list) and len(kept_candidates) == 2
        for raw in kept_candidates:
            candidate = _leg(raw)
            assert _as_int(candidate["amount_minor"]) == 6_000
            # Exact same-day pairs: the matcher is at its most confident.
            assert _as_decimal(candidate["confidence"]) == Decimal("0.95")

        assert _stats(client) == {
            "multi_candidate": 1,
            "low_confidence": 0,
            "total": 1,
        }
