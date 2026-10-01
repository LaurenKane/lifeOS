"""transfer_match.py — Transfer-matching logic for double-entry ledger.

Determines whether two JournalLine records represent a transfer pair between
different accounts, with opposite signs and amount/date tolerance.

Rule (ARCHITECTURE-PROPOSAL.md §H):
  Two JournalLines are transfer-linked iff:
  1. different account_id, same owner (single user)
  2. opposite signs
  3. ABS(L1.amount_base + L2.amount_base) <= 0.01 (same currency)
     or <= 0.50 (cross-currency, FX spread)
  4. L2.date BETWEEN L1.date - 1 day AND L1.date + 3 days (outbound first)
  5. neither already matched

$Id: transfer_match.py 2026-10-01$
"""

from __future__ import annotations

from ...core.datetime import parse_date


def transfer_match(
    line1: dict,
    line2: dict,
) -> dict:
    """Determine if two journal lines represent a transfer match.

    Args:
        line1: Dict with keys: amount (int minor units), currency (str), date (str), account_id (str)
        line2: Dict with keys: amount (int minor units), currency (str), date (str), account_id (str)

    Returns:
        Dict with keys: is_match (bool), match_method (str), confidence (float)
    """
    amount1 = line1["amount"]
    currency1 = line1["currency"]
    date1 = line1["date"]
    account1 = line1["account_id"]

    amount2 = line2["amount"]
    currency2 = line2["currency"]
    date2 = line2["date"]
    account2 = line2["account_id"]

    # Rule 1: Different accounts
    if account1 == account2:
        return {"is_match": False, "match_method": "no_match", "confidence": 0.0}

    # Rule 2: Opposite signs
    sign1 = 1 if amount1 >= 0 else -1
    sign2 = 1 if amount2 >= 0 else -1
    if sign1 == sign2:
        return {"is_match": False, "match_method": "no_match", "confidence": 0.0}

    # Rule 3: Amount tolerance
    # amounts are in minor units (int); same currency tolerance = 1 minor unit
    # cross-currency tolerance = 50 minor units (0.50 EUR)
    amount_diff = abs(amount1 + amount2)  # since signs are opposite
    same_currency = currency1 == currency2
    tolerance = 1 if same_currency else 50
    if amount_diff > tolerance:
        return {"is_match": False, "match_method": "no_match", "confidence": 0.0}

    # Rule 4: Date window (outbound first)
    d1 = parse_date(date1)
    d2 = parse_date(date2)
    days_diff = abs((d2 - d1).days)
    # window: L2.date BETWEEN L1.date - 1 day AND L1.date + 3 days
    if days_diff > 3:
        return {"is_match": False, "match_method": "no_match", "confidence": 0.0}

    # Determine match method
    if same_currency and amount_diff <= 1 and days_diff <= 1:
        match_method = "auto_amount_date"
    elif amount_diff <= 50:
        match_method = "auto_card_payment"
    else:
        match_method = "auto_amount_date"

    # Confidence
    confidence = 0.95 if same_currency and amount_diff <= 1 else 0.85

    return {"is_match": True, "match_method": match_method, "confidence": confidence}
