"""card_payment.py — the RULES for posting an imported card payment.

**Pure.** No database, no HTTP, no ingestion, no clock, no randomness. The
writer that calls this owns the session and the writes, exactly as
`manual_posting.py` is separated from `routes/transactions.py`.

NOT re-exported from `finance/domain/services/__init__.py`, deliberately, for
the same reason `manual_posting.py` is not: that package's contract is a
re-export surface, so adding an entry is a change to a cross-module contract
rather than to this module.

WHY THIS IS NOT `build_expense_legs`
====================================

A monthly card payment credits the card (a LIABILITY) and debits the paying
account (an ASSET). That is a transfer between two real accounts, and it is not
an expense, so it cannot go through the expense builder for two independent
reasons:

* **The counter-leg is not forced into EUR.** `build_expense_legs` requires the
  counter-leg to be in `BASE_CURRENCY` because `amount_base` is EUR and an
  equity contra-leg in another currency is not representable without a second
  rate. Here the contra-leg is a real account, denominated in that account's own
  currency, and forcing it to EUR would be the expense convention leaking into a
  place it does not hold.
* **The leg carries `is_synthesized`.** `PostingLeg` has no such field, and the
  whole point of the synthesized leg is that a later import of the other
  statement can recognise it and attach rather than double-book
  (`docs/adr/0007-imported-card-payment-is-a-transfer.md`).

THE SIGN
========

The card statement prints the payment as a CREDIT: the card owes less, so the
card leg is POSITIVE. The paying account loses the money, so the paying leg is
NEGATIVE. `amount` here is the card credit in minor units and MUST be positive;
the caller flips a checking-statement debit into this shape before calling,
once, where the sign was decided.

THE WINDOW
==========

The measured direction is card first, checking 0-2 days later: 765.67 card
06-29 / bank 06-30, 332.43 card 07-28 / bank 07-30, 272.48 card 08-28 / bank
08-28. `transfer_match.py` encodes the OPPOSITE assumption ("outbound precedes
inbound", -1/+3) and would reject the 332.43 pair, so this module states its own
window: a counterpart may be dated in `[card_date, card_date + 3]`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from core.money import Money

from finance.domain.services.manual_posting import (
    ManualPostingError,
    PostingAccount,
    to_base_scale,
)

__all__ = [
    "CARD_PAYMENT_REASON",
    "CARD_PAYMENT_WINDOW_DAYS_AFTER",
    "CardPaymentLeg",
    "build_card_payment_legs",
    "card_payment_window",
    "is_in_card_payment_window",
]


#: The value `journal_line.synthesized_reason` carries for a card-payment leg.
#: The migration's CHECK is the authority for the vocabulary; this is the one
#: member this module writes.
CARD_PAYMENT_REASON: Final[str] = "card_payment"

#: How many days AFTER the card credit a checking debit may fall. Measured, not
#: chosen: 0, +1 and +2 all occur in the four real statements. See the module
#: docstring for why this is not `transfer_match`'s window.
CARD_PAYMENT_WINDOW_DAYS_AFTER: Final[int] = 3


@dataclass(frozen=True)
class CardPaymentLeg:
    """One row of `journal_line` for a card payment, before it has an id.

    The fields mirror `PostingLeg` and add the two columns that make a
    synthesized leg recognisable: `is_synthesized` and `synthesized_reason`.
    They default to the real-leg pair (`False`/`None`), which is also the only
    combination migration 0003's CHECK accepts for a real leg. The WRITER owns
    the decision of which leg is synthesized; this builder never makes it.
    """

    account_id: int
    currency: str
    amount: int
    amount_base: Decimal
    exchange_rate: Decimal
    sort_order: int
    is_synthesized: bool = False
    synthesized_reason: str | None = None


def card_payment_window(card_date: dt.date) -> tuple[dt.date, dt.date]:
    """The inclusive window a checking counterpart may fall in.

    Anchored on the CARD date, because that is the earlier leg in every one of
    the four measured pairs.
    """
    return card_date, card_date + dt.timedelta(days=CARD_PAYMENT_WINDOW_DAYS_AFTER)


def is_in_card_payment_window(card_date: dt.date, counterpart_date: dt.date) -> bool:
    """Whether `counterpart_date` is a plausible checking side for `card_date`."""
    earliest, latest = card_payment_window(card_date)
    return earliest <= counterpart_date <= latest


def build_card_payment_legs(
    *,
    card_account: PostingAccount,
    paying_account: PostingAccount,
    amount: Money,
    rate: Decimal,
) -> tuple[CardPaymentLeg, CardPaymentLeg]:
    """The two legs of a card payment, card side first.

    Args:
        card_account: The liability the payment reduces.
        paying_account: The asset that pays it. Its currency MUST equal the
            card's, because the paying leg is the exact negation of the card leg
            in minor units; a second currency would need a second rate, and
            inventing one is worse than refusing.
        amount: The card CREDIT, positive minor units in the card account's
            currency. A card statement prints the payment as a credit; a
            checking statement prints it as a debit and the caller negates it
            before calling.
        rate: Units of the transaction currency per 1 EUR, as
            `write_posted_transaction` reads it. `1.0` for a EUR payment.

    Returns:
        `(card_leg, paying_leg)`, `sort_order` 0 and 1, both real
        (`is_synthesized=False`). The writer marks the missing side.

    Raises:
        ManualPostingError: For a non-positive amount, a currency mismatch, a
            rate that is not strictly positive, the same account on both sides,
            or an amount that quantises to no representable EUR value.
    """
    if amount.amount <= 0:
        raise ManualPostingError(
            "A card payment is a CREDIT to the card: `amount` must be positive "
            f"minor units, got {amount.amount}. A negative figure here means the "
            "caller passed the checking debit without flipping the sign."
        )
    if card_account.currency != amount.currency.code:
        raise ManualPostingError(
            f"Card account {card_account.id} holds {card_account.currency} but "
            f"the payment is in {amount.currency.code}. `journal_line.currency` "
            "is a snapshot of the account's own currency, so this is a mistake "
            "in the request, not a conversion."
        )
    if paying_account.currency != card_account.currency:
        raise ManualPostingError(
            f"Paying account {paying_account.id} is in "
            f"{paying_account.currency} but the card is in "
            f"{card_account.currency}; the paying leg is the exact negation of "
            "the card leg, and a cross-currency pair would need a second rate "
            "this rule does not have."
        )
    if rate <= 0:
        raise ManualPostingError(
            f"Exchange rate must be strictly positive, got {rate}."
        )
    if card_account.id == paying_account.id:
        raise ManualPostingError(
            f"Account {card_account.id} is both the card and the paying "
            "account, so the entry would have both legs on one row."
        )

    base = to_base_scale(amount.to_decimal() / rate)
    if base == 0:
        raise ManualPostingError(
            f"{amount} at rate {rate} is {base} EUR: the payment has no "
            "representable base value and cannot be posted."
        )

    return (
        CardPaymentLeg(
            account_id=card_account.id,
            currency=card_account.currency,
            amount=amount.amount,
            amount_base=base,
            exchange_rate=rate,
            sort_order=0,
        ),
        CardPaymentLeg(
            account_id=paying_account.id,
            currency=paying_account.currency,
            amount=-amount.amount,
            amount_base=to_base_scale(-base),
            # Same rate on both legs: both are in the transaction currency, and
            # `amount_base * exchange_rate == amount` in major units holds for
            # each of them.
            exchange_rate=rate,
            sort_order=1,
        ),
    )
