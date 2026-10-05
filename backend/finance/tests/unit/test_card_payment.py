"""Unit tests for the pure card-payment leg builder.

No database: the arithmetic and the window are the whole subject, and both are
pure by construction. The end-to-end posting behaviour is asserted against
Postgres in `tests/integration/test_card_payment_db.py`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from core.money import Currency, Money

from finance.domain.services.card_payment import (
    CARD_PAYMENT_REASON,
    CARD_PAYMENT_WINDOW_DAYS_AFTER,
    build_card_payment_legs,
    card_payment_window,
    is_in_card_payment_window,
)
from finance.domain.services.manual_posting import (
    ManualPostingError,
    PostingAccount,
)


def _account(
    account_id: int,
    *,
    nature: str = "asset",
    currency: str = "EUR",
) -> PostingAccount:
    return PostingAccount(
        id=account_id,
        name=f"account {account_id}",
        currency=currency,
        account_nature=nature,
    )


CARD = 2001
CHECKING = 2002


def _money(amount: int, currency: str = "EUR") -> Money:
    return Money(amount=amount, currency=Currency(code=currency))


class TestBuildCardPaymentLegs:
    def test_card_positive_paying_negative_and_sums_to_zero(self) -> None:
        card_leg, paying_leg = build_card_payment_legs(
            card_account=_account(CARD, nature="liability"),
            paying_account=_account(CHECKING),
            amount=_money(76567),
            rate=Decimal(1),
        )
        assert card_leg.amount == 76567
        assert paying_leg.amount == -76567
        assert card_leg.amount_base == Decimal("765.6700")
        assert paying_leg.amount_base == Decimal("-765.6700")
        assert card_leg.amount_base + paying_leg.amount_base == Decimal(0)
        assert card_leg.sort_order == 0
        assert paying_leg.sort_order == 1

    def test_both_legs_are_real_by_default(self) -> None:
        """The builder never decides which side is missing; the writer does."""
        card_leg, paying_leg = build_card_payment_legs(
            card_account=_account(CARD, nature="liability"),
            paying_account=_account(CHECKING),
            amount=_money(5000),
            rate=Decimal(1),
        )
        for leg in (card_leg, paying_leg):
            assert leg.is_synthesized is False
            assert leg.synthesized_reason is None
        assert CARD_PAYMENT_REASON == "card_payment"

    def test_zero_amount_is_refused(self) -> None:
        with pytest.raises(ManualPostingError):
            build_card_payment_legs(
                card_account=_account(CARD, nature="liability"),
                paying_account=_account(CHECKING),
                amount=_money(0),
                rate=Decimal(1),
            )

    def test_negative_amount_is_refused(self) -> None:
        """A negative figure is the checking debit passed without a sign flip."""
        with pytest.raises(ManualPostingError):
            build_card_payment_legs(
                card_account=_account(CARD, nature="liability"),
                paying_account=_account(CHECKING),
                amount=_money(-76567),
                rate=Decimal(1),
            )

    def test_same_account_is_refused(self) -> None:
        with pytest.raises(ManualPostingError):
            build_card_payment_legs(
                card_account=_account(CARD, nature="liability"),
                paying_account=_account(CARD),
                amount=_money(5000),
                rate=Decimal(1),
            )

    def test_cross_currency_pair_is_refused(self) -> None:
        with pytest.raises(ManualPostingError):
            build_card_payment_legs(
                card_account=_account(CARD, nature="liability", currency="EUR"),
                paying_account=_account(CHECKING, currency="USD"),
                amount=_money(5000, "EUR"),
                rate=Decimal(1),
            )

    def test_non_positive_rate_is_refused(self) -> None:
        with pytest.raises(ManualPostingError):
            build_card_payment_legs(
                card_account=_account(CARD, nature="liability"),
                paying_account=_account(CHECKING),
                amount=_money(5000),
                rate=Decimal(0),
            )


class TestWindow:
    def test_window_runs_from_card_date_to_three_days_later(self) -> None:
        assert CARD_PAYMENT_WINDOW_DAYS_AFTER == 3
        assert card_payment_window(dt.date(2026, 7, 28)) == (
            dt.date(2026, 7, 28),
            dt.date(2026, 7, 31),
        )

    @pytest.mark.parametrize("days", [0, 1, 2, 3])
    def test_measured_offsets_are_inside(self, days: int) -> None:
        card = dt.date(2026, 7, 28)
        assert is_in_card_payment_window(card, card + dt.timedelta(days=days))

    def test_a_day_before_the_card_is_outside(self) -> None:
        """This is the direction `transfer_match` gets wrong for card payments."""
        card = dt.date(2026, 7, 28)
        assert not is_in_card_payment_window(card, card - dt.timedelta(days=1))

    def test_four_days_later_is_outside(self) -> None:
        card = dt.date(2026, 7, 28)
        assert not is_in_card_payment_window(card, card + dt.timedelta(days=4))
