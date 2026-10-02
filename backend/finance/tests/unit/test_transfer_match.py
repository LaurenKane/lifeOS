"""Transfer-matching tests.

Section H. The rule is stated, and the Amex card payment is the reason it
exists: a EUR 50 card purchase is an expense on a liability account, and weeks
later the checking account shows -50 "American Express". There is no second row
to link, which is why the model is double-entry and why a leg is synthesised.

Synthetic data throughout.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from finance.domain.services.transfer_match import (
    CROSS_CURRENCY_TOLERANCE_MINOR,
    JournalLineRef,
    is_transfer_pair,
    transfer_match,
    transfer_window,
)


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def line(
    *,
    entry_id: str = "je-out",
    account_id: str = "checking",
    amount_minor: int = -5000,
    currency: str = "EUR",
    booked_date: str = "2026-03-15",
    already_matched: bool = False,
) -> JournalLineRef:
    return JournalLineRef(
        entry_id=entry_id,
        account_id=account_id,
        amount_minor=amount_minor,
        currency=currency,
        booked_date=_date(booked_date),
        already_matched=already_matched,
    )


class TestWindow:
    def test_window_is_asymmetric(self) -> None:
        """Outbound first: the inbound leg lands 1-3 days later."""
        earliest, latest = transfer_window(_date("2026-03-15"))
        assert earliest == _date("2026-03-14")
        assert latest == _date("2026-03-18")

    def test_same_day_is_inside(self) -> None:
        assert is_transfer_pair(
            line(booked_date="2026-03-15"),
            line(account_id="card", amount_minor=5000, booked_date="2026-03-15"),
        )


class TestRules:
    def test_same_account_is_not_a_transfer(self) -> None:
        """Moving money within one account is a reclassification, not a transfer."""
        assert not is_transfer_pair(
            line(account_id="checking"), line(account_id="checking", amount_minor=5000)
        )

    def test_same_sign_is_not_a_transfer(self) -> None:
        """Two debits are two spends, however similar."""
        assert not is_transfer_pair(
            line(amount_minor=-5000), line(account_id="card", amount_minor=-5000)
        )

    def test_zero_amount_is_not_a_transfer(self) -> None:
        """A zero leg carries no direction, so there is nothing to match."""
        assert not is_transfer_pair(
            line(amount_minor=0), line(account_id="card", amount_minor=0)
        )

    def test_exact_offset_is_a_transfer(self) -> None:
        assert is_transfer_pair(line(), line(account_id="card", amount_minor=5000))

    def test_one_cent_tolerance(self) -> None:
        """Documented: ABS(L1 + L2) <= 1 minor unit."""
        assert is_transfer_pair(line(), line(account_id="card", amount_minor=4999))
        assert not is_transfer_pair(line(), line(account_id="card", amount_minor=4998))

    def test_cross_currency_tolerance_is_wider(self) -> None:
        """50 minor units, for an FX spread.

        50 cents apart across currencies is accepted; the same 50 cents within
        one currency is not, because same-currency legs are supposed to net
        exactly and a gap there means something else happened.
        """
        assert CROSS_CURRENCY_TOLERANCE_MINOR == 50
        assert is_transfer_pair(
            line(),
            line(account_id="card", amount_minor=4950, currency="USD"),
        )
        assert not is_transfer_pair(
            line(),
            line(account_id="card", amount_minor=4950, currency="EUR"),
        )

    def test_cross_currency_beyond_tolerance_is_refused(self) -> None:
        """The tolerance is 50 cents, not "any amount".

        A USD credit 5.00 away from a EUR 50.00 debit is not an FX spread; it is
        a different amount, and matching it would mis-state where the money went.
        """
        assert not is_transfer_pair(
            line(),
            line(account_id="card", amount_minor=4500, currency="USD"),
        )

    def test_already_matched_lines_are_excluded(self) -> None:
        """A line can belong to at most one transfer pair."""
        assert not is_transfer_pair(
            line(already_matched=True), line(account_id="card", amount_minor=5000)
        )
        assert not is_transfer_pair(
            line(),
            line(account_id="card", amount_minor=5000, already_matched=True),
        )

    @pytest.mark.parametrize("days_later", [0, 1, 2, 3])
    def test_inside_window(self, days_later: int) -> None:
        assert is_transfer_pair(
            line(),
            line(
                account_id="card",
                amount_minor=5000,
                booked_date=f"2026-03-{15 + days_later:02d}",
            ),
        )

    @pytest.mark.parametrize("inbound_date", ["2026-03-19", "2026-03-20"])
    def test_outside_window_later(self, inbound_date: str) -> None:
        assert not is_transfer_pair(
            line(), line(account_id="card", amount_minor=5000, booked_date=inbound_date)
        )

    @pytest.mark.parametrize("inbound_date", ["2026-03-14", "2026-03-13"])
    def test_outside_window_earlier(self, inbound_date: str) -> None:
        """Inbound before outbound is outside the window in both directions.

        The window is -1 to +3, so one day earlier is the earliest allowed and
        anything before that is not.
        """
        outbound = line(booked_date="2026-03-15")
        candidate = line(account_id="card", amount_minor=5000, booked_date=inbound_date)
        assert is_transfer_pair(outbound, candidate) == (inbound_date == "2026-03-14")


class TestMatchResult:
    def test_exact_same_day_is_auto(self) -> None:
        match = transfer_match(line(), line(account_id="card", amount_minor=5000))
        assert match is not None
        assert match.confidence == Decimal("0.95")
        assert match.is_auto
        assert match.match_method == "auto_amount_date"

    def test_same_amount_later_day_is_not_auto(self) -> None:
        """Exact amount but two days apart.

        Same amount and same currency is strong, but a date mismatch is exactly
        the case where a coincidental pair of equal transactions happens, so the
        user sees it rather than it being merged silently.
        """
        match = transfer_match(
            line(),
            line(account_id="card", amount_minor=5000, booked_date="2026-03-17"),
        )
        assert match is not None
        assert match.confidence == Decimal("0.85")
        assert not match.is_auto

    def test_cross_currency_is_a_card_payment(self) -> None:
        """The Amex case: liability settlement across accounts.

        Reported as a card payment rather than an amount/date match, because the
        two halves are in different currencies and the amounts only agree after
        an FX conversion this module deliberately does not perform.
        """
        match = transfer_match(
            line(), line(account_id="card", amount_minor=5000, currency="USD")
        )
        assert match is not None
        assert match.match_method == "auto_card_payment"
        assert not match.is_auto

    def test_no_match_returns_none(self) -> None:
        assert (
            transfer_match(line(), line(account_id="card", amount_minor=5000))
            is not None
        )
        assert (
            transfer_match(line(), line(account_id="checking", amount_minor=5000))
            is None
        )

    def test_entry_ids_are_carried_through(self) -> None:
        match = transfer_match(
            line(entry_id="je-a"),
            line(entry_id="je-b", account_id="card", amount_minor=5000),
        )
        assert match is not None
        assert match.outbound == "je-a"
        assert match.inbound == "je-b"
