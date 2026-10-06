"""test_analytics_db.py — the net-worth sign rules, against a real Postgres.

Everything in `test_analytics.py` (the pure service) is unit-testable because the
decisions live in Python. The two that matter most do NOT, and they are the ones
this file exists for:

  1. **Equity is excluded from net worth.** Spending is parked in a seeded equity
     contra-account. Counting it makes every purchase RAISE the figure it should
     lower.
  2. **Liabilities are not subtracted.** They are already stored negative — a card
     charge writes a negative line so it balances against the positive equity
     line on the other side. Subtracting the column again reports money you owe
     as money you have.

Both produce a plausible number. Neither raises. A chart built on either would
quietly disagree with the user's bank statement, which is the failure this file
is here to make loud.

**The ledger is inserted directly rather than through the API.** The subject is
the read query, and writing the rows by hand means the sign on every line is
stated rather than derived — so a failure here means the QUERY is wrong, not that
a posting path was wired up differently than expected.

**Marked `db`.** Requires a real, migrated Postgres; selected by `make test-db`.

Note on isolation: `conftest._recreate_database` FORCE-drops a hardcoded
`lifeos_test`. Two agents running `make test-db` concurrently will destroy each
other's database mid-run, so this file is safe to run ONLY against a dedicated
server. See the module note in `conftest.py`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from main import create_app
from sqlalchemy import Connection, Engine, text

from finance.db import get_engine, get_sessionmaker
from finance.domain.services.manual_posting import SYSTEM_EXPENSE_ACCOUNT_NAME

pytestmark = pytest.mark.db

#: Fixed dates so every assertion is a literal and a reader can check the
#: arithmetic by hand. Nothing here depends on today's date.
DAY_ONE = dt.date(2026, 1, 5)
DAY_TWO = dt.date(2026, 1, 10)

#: EUR minor units per major unit, used to build `amount_base` from `amount`.
SCALE = Decimal(100)


@pytest.fixture
def client(test_database_url: str) -> Iterator[TestClient]:
    """A `TestClient` on the migrated test database.

    The `cache_clear()` calls are load-bearing and are not tidiness:
    `finance.db.get_engine`/`get_sessionmaker` and `config.get_settings` are all
    `lru_cache`d, so without clearing them the app would build its engine against
    whatever `LIFEOS_DATABASE_URL` was at FIRST USE — the development database,
    most likely — and every assertion below would read a database nobody
    truncated.
    """
    del test_database_url  # Requested for its side effect: setting the env var.
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
def ledger(engine: Engine) -> dict[str, int]:
    """Four accounts, two categories, and a committed ledger to assert against.

    `checking` and `savings` are assets, `card` is a liability, and the expense
    account is equity — the four natures that decide the answer. All committed,
    because the application reads them from a second connection and an
    uncommitted transaction would be invisible to it.
    """
    ids: dict[str, int] = {}
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(
                    "INSERT INTO finance.currency (code, name, decimals)"
                    " VALUES ('EUR', 'Euro', 2)"
                )
            )
            for key, name, kind in (
                ("checking", "Checking", "checking"),
                ("savings", "Savings", "savings"),
                ("card", "Card", "credit_card"),
                ("expense", SYSTEM_EXPENSE_ACCOUNT_NAME, "cash"),
            ):
                nature = (
                    "equity"
                    if key == "expense"
                    else ("liability" if key == "card" else "asset")
                )
                ids[key] = int(
                    connection.execute(
                        text(
                            "INSERT INTO finance.account"
                            " (name, account_type, account_nature, currency)"
                            " VALUES (:name, :kind, :nature, 'EUR')"
                            " RETURNING id"
                        ),
                        {"name": name, "kind": kind, "nature": nature},
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

            # Day one: €1,000 arrives in checking. The equity leg rises so the
            # entry balances — income is a credit on the real account and a debit
            # on the contra-account.
            _post(
                connection,
                DAY_ONE,
                [
                    (ids["checking"], 100_000, None),
                    (ids["expense"], -100_000, None),
                ],
            )
            # Day one: a €500 charge on the card. The card line is NEGATIVE and
            # the grocery category is POSITIVE. This is the pair of lines whose
            # handling decides whether the sign rules are right.
            _post(
                connection,
                DAY_ONE,
                [
                    (ids["card"], -50_000, ids["groceries"]),
                    (ids["expense"], 50_000, ids["groceries"]),
                ],
            )
            # Day two: €300 moved from checking to savings. Not spending, and not
            # income either — every leg is an asset, so the entry is dropped.
            _post(
                connection,
                DAY_TWO,
                [
                    (ids["checking"], -30_000, ids["internal"]),
                    (ids["savings"], 30_000, ids["internal"]),
                ],
                is_transfer=True,
            )
            # Day two: €200 paid to the card. Also a transfer, but it reaches a
            # LIABILITY, so the paying leg is real cash leaving. This is the pair
            # that a blanket "exclude all transfers" rule would hide, and it is
            # why the cashflow query judges transfers per entry.
            _post(
                connection,
                DAY_TWO,
                [
                    (ids["checking"], -20_000, ids["internal"]),
                    (ids["card"], 20_000, ids["internal"]),
                ],
                is_transfer=True,
            )
    return ids


def _post(
    connection: Connection,
    when: dt.date,
    legs: list[tuple[int, int, int | None]],
    *,
    is_transfer: bool = False,
) -> None:
    """Write one balanced journal entry, in minor units.

    `amount_base` is `amount / 100` for EUR at rate 1.0, which is exactly what
    `build_expense_legs` produces, so these rows are indistinguishable from ones
    the application would have written.
    """
    entry_id = int(
        connection.execute(
            text(
                "INSERT INTO finance.journal_entry"
                " (entry_date, description, is_transfer, is_split)"
                " VALUES (:when, 'seeded', :transfer, FALSE) RETURNING id"
            ),
            {"when": when, "transfer": is_transfer},
        ).scalar_one()
    )
    for order, (account_id, amount, category_id) in enumerate(legs):
        connection.execute(
            text(
                "INSERT INTO finance.journal_line"
                " (journal_entry_id, account_id, amount, currency, amount_base,"
                "  exchange_rate, category_id, sort_order)"
                " VALUES (:entry, :account, :amount, 'EUR', :base, 1.0,"
                "  :category, :order)"
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


def _net_worth_at(client: TestClient, day: dt.date) -> int:
    response = client.get(
        "/api/v1/analytics/net-worth",
        params={"from": day.isoformat(), "to": day.isoformat()},
    )
    assert response.status_code == 200, response.text
    points = response.json()
    assert len(points) == 1
    value: int = points[0]["net_worth"]
    return value


class TestNetWorthSign:
    def test_a_card_charge_lowers_net_worth(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """€1,000 in, then a €500 card charge leaves €500. Not €1,500.

        €1,500 is what subtracting the liability column produces, because the
        charge is already stored as -50,000. A net worth that goes UP when you
        spend is not a rounding error; it is the app disagreeing with the bank.
        """
        assert _net_worth_at(client, DAY_ONE) == 50_000

    def test_the_expense_account_is_not_counted_as_money(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """The equity leg on that same charge must contribute nothing.

        The groceries entry wrote +50,000 to equity. Counting equity would make
        net worth €1,000 again — the purchase would have erased itself. This is
        asserted separately from the liability case because the two fixes are
        independent and one can be applied without the other.
        """
        # €1,000 asset, -€500 liability, +€500 equity = €1,000. Wrong.
        # Correct answer is €500, which is what excluding equity alone gives.
        assert _net_worth_at(client, DAY_ONE) != 100_000

    def test_an_asset_to_asset_transfer_leaves_net_worth_unchanged(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """Moving €300 from checking to savings is not worth €300.

        Before: €500. After: still €500. If transfers were mishandled the figure
        would drop to €200, which is the classic 'I moved money and my net worth
        fell' bug.
        """
        assert _net_worth_at(client, DAY_TWO) == 50_000

    def test_amounts_come_back_as_integer_cents_and_never_a_float(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """The wire format is part of the contract, not a detail.

        A float here would be money in binary, and the frontend renders integers.
        """
        response = client.get(
            "/api/v1/analytics/net-worth",
            params={"from": DAY_ONE.isoformat(), "to": DAY_TWO.isoformat()},
        )
        assert response.status_code == 200, response.text
        for point in response.json():
            assert isinstance(point["net_worth"], int)
            assert not isinstance(point["net_worth"], bool)


class TestSpendByCategory:
    def test_a_transfer_is_not_reported_as_spending(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """`ledger.py` calls getting this wrong 'the single most misleading thing
        this application can do'. A savings top-up is not a purchase."""
        response = client.get(
            "/api/v1/analytics/spend-by-category",
            params={"from": DAY_ONE.isoformat(), "to": DAY_TWO.isoformat()},
        )
        assert response.status_code == 200, response.text
        reported = {row["category_name"]: row["amount"] for row in response.json()}
        assert "Internal transfer" not in reported
        assert reported == {"Groceries": -50_000}

    def test_expenses_arrive_negative_so_the_existing_amount_component_is_correct(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """The frontend `Amount` already reads a negative as money out.

        Re-signing here would make the sign mean one thing in this response and
        another everywhere else.
        """
        response = client.get(
            "/api/v1/analytics/spend-by-category",
            params={"from": DAY_ONE.isoformat(), "to": DAY_TWO.isoformat()},
        )
        assert response.status_code == 200, response.text
        assert all(row["amount"] <= 0 for row in response.json())


class TestCashflow:
    def test_moving_money_between_your_own_accounts_is_not_income(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """A €300 top-up into savings must not read as €300 of income.

        Aggregated line by line it is a -€300 debit and a +€300 credit, so a
        naive sign split books the credit as income and the user sees their income
        rise because they tidied their savings. Income here is €1,000 and nothing
        else.
        """
        response = client.get(
            "/api/v1/analytics/cashflow",
            params={
                "from": DAY_ONE.isoformat(),
                "to": DAY_TWO.isoformat(),
                "period": "month",
            },
        )
        assert response.status_code == 200, response.text
        (bucket,) = response.json()
        assert bucket["income"] == 100_000

    def test_paying_a_credit_card_shows_as_cash_leaving(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """A transfer that reaches a liability IS a cash movement.

        The mirror image of the savings top-up, and the reason transfers are
        judged per entry rather than all-or-nothing. €200 out of checking to the
        card: the card is a liability, so the paying leg is real cash leaving.
        """
        response = client.get(
            "/api/v1/analytics/cashflow",
            params={
                "from": DAY_ONE.isoformat(),
                "to": DAY_TWO.isoformat(),
                "period": "month",
            },
        )
        assert response.status_code == 200, response.text
        (bucket,) = response.json()
        assert bucket["expense"] == 20_000
        assert bucket["net"] == 80_000

    def test_a_card_purchase_is_not_cashflow(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """Cashflow is cash, and a card purchase moves none.

        The card is a liability and groceries sit on the equity contra-leg, so
        neither is counted as an asset. Spending on a card shows up when the bill
        is paid, not when the purchase is made. This is a deliberate definition,
        asserted so a future change to it is a deliberate one.
        """
        response = client.get(
            "/api/v1/analytics/cashflow",
            params={
                "from": DAY_ONE.isoformat(),
                "to": DAY_ONE.isoformat(),
                "period": "day",
            },
        )
        assert response.status_code == 200, response.text
        (bucket,) = response.json()
        assert bucket["income"] == 100_000
        assert bucket["expense"] == 0

    def test_income_and_expense_are_both_positive_and_net_can_be_negative(
        self, client: TestClient, ledger: dict[str, int]
    ) -> None:
        """The one asymmetry in the feature, checked against real rows."""
        response = client.get(
            "/api/v1/analytics/cashflow",
            params={
                "from": DAY_ONE.isoformat(),
                "to": DAY_TWO.isoformat(),
                "period": "month",
            },
        )
        assert response.status_code == 200, response.text
        (bucket,) = response.json()
        assert bucket["income"] >= 0
        assert bucket["expense"] >= 0
        assert bucket["net"] == bucket["income"] - bucket["expense"]


def _set_account_flag(
    engine: Engine, account_id: int, column: str, value: bool
) -> None:
    """Flip `is_hidden`/`is_active` on one account, committed.

    The flag changes AFTER the ledger rows are posted, which is exactly the
    real-world order: the account was open when the money moved, then hidden
    or closed. `column` is caller-hardcoded, never user input.
    """
    assert column in ("is_hidden", "is_active")
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(
                text(f"UPDATE finance.account SET {column} = :value WHERE id = :id"),
                {"value": value, "id": account_id},
            )


def _spend_map(client: TestClient) -> dict[str, int]:
    response = client.get(
        "/api/v1/analytics/spend-by-category",
        params={"from": DAY_ONE.isoformat(), "to": DAY_TWO.isoformat()},
    )
    assert response.status_code == 200, response.text
    return {row["category_name"]: row["amount"] for row in response.json()}


def _cashflow_bucket(client: TestClient) -> dict[str, int]:
    response = client.get(
        "/api/v1/analytics/cashflow",
        params={
            "from": DAY_ONE.isoformat(),
            "to": DAY_TWO.isoformat(),
            "period": "month",
        },
    )
    assert response.status_code == 200, response.text
    (bucket,) = response.json()
    typed: dict[str, int] = bucket
    return typed


class TestHiddenAndClosedAccountsCount:
    """LifeOS-uac: analytics totals INCLUDE hidden and inactive/closed accounts.

    `is_hidden` is display-only and `is_active = FALSE` marks a closed account,
    but neither removes money from any total: the balance was real on the dates
    the account was open. Each endpoint pins this separately because each query
    filters differently and one could be "tidied" without the others.
    """

    def test_hidden_savings_still_counts_in_net_worth(
        self, client: TestClient, engine: Engine, ledger: dict[str, int]
    ) -> None:
        """Hiding the €300 savings top-up target must not move net worth."""
        _set_account_flag(engine, ledger["savings"], "is_hidden", True)
        assert _net_worth_at(client, DAY_TWO) == 50_000

    def test_closed_checking_still_counts_in_net_worth(
        self, client: TestClient, engine: Engine, ledger: dict[str, int]
    ) -> None:
        """Closing checking must not rewrite the history it holds."""
        _set_account_flag(engine, ledger["checking"], "is_active", False)
        assert _net_worth_at(client, DAY_TWO) == 50_000

    def test_hidden_card_spend_is_still_reported(
        self, client: TestClient, engine: Engine, ledger: dict[str, int]
    ) -> None:
        """The €500 card charge is spending even when the card is hidden."""
        _set_account_flag(engine, ledger["card"], "is_hidden", True)
        assert _spend_map(client) == {"Groceries": -50_000}

    def test_closed_card_spend_is_still_reported(
        self, client: TestClient, engine: Engine, ledger: dict[str, int]
    ) -> None:
        """Closing the card must not erase the spending it carried."""
        _set_account_flag(engine, ledger["card"], "is_active", False)
        assert _spend_map(client) == {"Groceries": -50_000}

    def test_hidden_checking_cashflow_is_unchanged(
        self, client: TestClient, engine: Engine, ledger: dict[str, int]
    ) -> None:
        """Hiding checking hides the account, not the cash that moved through it."""
        _set_account_flag(engine, ledger["checking"], "is_hidden", True)
        bucket = _cashflow_bucket(client)
        assert bucket["income"] == 100_000
        assert bucket["expense"] == 20_000
        assert bucket["net"] == 80_000

    def test_closed_checking_cashflow_is_unchanged(
        self, client: TestClient, engine: Engine, ledger: dict[str, int]
    ) -> None:
        """Closing checking must not drop its movements from cashflow."""
        _set_account_flag(engine, ledger["checking"], "is_active", False)
        bucket = _cashflow_bucket(client)
        assert bucket["income"] == 100_000
        assert bucket["expense"] == 20_000
        assert bucket["net"] == 80_000
