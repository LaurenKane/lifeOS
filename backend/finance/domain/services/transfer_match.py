"""transfer_match.py — the transfer-pair rule.

ARCHITECTURE-PROPOSAL.md section H. Two journal lines are transfer-linked iff:

1. different account, same owner (single user, so no owner check here)
2. opposite signs
3. |L1 + L2| <= 1 minor unit (same currency) or <= 50 (cross-currency FX spread)
4. L2.date BETWEEN L1.date - 1 day AND L1.date + 3 days (outbound first)
5. neither already matched

The window is asymmetric on purpose: money leaves before it arrives. A symmetric
window would match a Friday card payment against the following Monday's
unrelated grocery.

Rule 5 is passed in rather than read from the database, because whether a line
is already matched is the caller's transaction to own. This module stays pure.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "JournalLineRef",
    "TransferMatch",
    "is_transfer_pair",
    "transfer_match",
    "transfer_window",
]

# Amount tolerances in MINOR UNITS. 1 cent same-currency, 50 cents for an FX
# spread across currencies. These are not floats and never become floats.
SAME_CURRENCY_TOLERANCE_MINOR = 1
CROSS_CURRENCY_TOLERANCE_MINOR = 50

# Outbound precedes inbound.
WINDOW_DAYS_BEFORE = 1
WINDOW_DAYS_AFTER = 3


@dataclass(frozen=True)
class JournalLineRef:
    """The parts of a journal line the transfer rule looks at.

    `amount_minor` is in the line's own currency. Base-currency comparison is
    the caller's job, because it needs an exchange rate and this module must
    stay pure.
    """

    entry_id: int
    account_id: int
    amount_minor: int
    currency: str
    booked_date: dt.date
    already_matched: bool = False


@dataclass(frozen=True)
class TransferMatch:
    """A confirmed transfer pair."""

    outbound: int
    inbound: int
    match_method: str
    confidence: Decimal

    @property
    def is_auto(self) -> bool:
        """True when confident enough to link without asking the user.

        Anything below this is a suggestion, not a merge: a wrong link silently
        changes a card payment into a transfer and destroys the spending
        breakdown the user actually wants.
        """
        return self.confidence >= Decimal("0.90")


def transfer_window(outbound_date: dt.date) -> tuple[dt.date, dt.date]:
    """The inclusive window an inbound leg may fall in for an outbound date."""
    return (
        outbound_date - dt.timedelta(days=WINDOW_DAYS_BEFORE),
        outbound_date + dt.timedelta(days=WINDOW_DAYS_AFTER),
    )


def is_transfer_pair(outbound: JournalLineRef, inbound: JournalLineRef) -> bool:
    """Whether two lines are the two halves of one movement.

    Rule 5 ("neither already matched") is checked here, so a caller that passes
    in unmatched lines gets a pure predicate and does not have to remember to
    filter first.
    """
    if outbound.account_id == inbound.account_id:
        return False
    if outbound.already_matched or inbound.already_matched:
        return False
    if outbound.amount_minor == 0 or inbound.amount_minor == 0:
        return False
    if (outbound.amount_minor > 0) == (inbound.amount_minor > 0):
        return False
    tolerance = (
        SAME_CURRENCY_TOLERANCE_MINOR
        if outbound.currency == inbound.currency
        else CROSS_CURRENCY_TOLERANCE_MINOR
    )
    if abs(outbound.amount_minor + inbound.amount_minor) > tolerance:
        return False
    earliest, latest = transfer_window(outbound.booked_date)
    return earliest <= inbound.booked_date <= latest


def transfer_match(
    outbound: JournalLineRef, inbound: JournalLineRef
) -> TransferMatch | None:
    """Return the link if these two lines are a transfer pair, else None.

    Args:
        outbound: The line expected to carry the negative amount.
        inbound: The line expected to carry the positive amount. The window is
            asymmetric, so the order of the arguments matters.

    Returns:
        A `TransferMatch`, or None when the rules do not all hold.
    """
    if not is_transfer_pair(outbound, inbound):
        return None

    same_currency = outbound.currency == inbound.currency
    exact_amount = outbound.amount_minor + inbound.amount_minor == 0
    exact_date = outbound.booked_date == inbound.booked_date

    # A card payment is the case where the two halves live in different
    # currencies or differ by an FX spread, so anything short of an exact
    # same-currency offset is reported as a card payment.
    method = (
        "auto_amount_date" if same_currency and exact_amount else "auto_card_payment"
    )

    # Exact same-currency, same-amount, same-day is the strongest signal there
    # is. Anything else, including every cross-currency pair, gets the lower
    # score and therefore `is_auto is False`.
    confidence = (
        Decimal("0.95")
        if same_currency and exact_amount and exact_date
        else Decimal("0.85")
    )
    return TransferMatch(
        outbound=outbound.entry_id,
        inbound=inbound.entry_id,
        match_method=method,
        confidence=confidence,
    )
