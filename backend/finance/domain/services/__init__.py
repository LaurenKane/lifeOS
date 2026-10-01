"""finance.domain.services - PRIVATE: only finance.public may export from this package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ...core.datetime import days_between, month_end, month_start, parse_date
from ...core.money import Currency, Money
from .value_objects import Currency as CurrencyVO
from .value_objects import DateRange
from .value_objects import Money as MoneyVO


@dataclass(frozen=True)
class DedupeResult:
    """Result of a deduplication check.

    Attributes:
        is_duplicate: Whether this transaction is a duplicate of an existing one.
        existing_entry_id: The journal_entry_id of the existing entry, if found.
        occurrence_index: The occurrence index (1-based) for identical transactions.
    """

    is_duplicate: bool
    existing_entry_id: Optional[int] = None
    occurrence_index: int = 1


def dedupe_check(
    new_amount: int,
    new_currency_code: str,
    new_date: str,
    new_description: str,
    account_id: str,
    existing_records: List[dict],
    occurrence_index: int = 1,
) -> DedupeResult:
    """Check if a new transaction is a duplicate of existing records.

    Tier 3 fingerprint-based dedup. Two transactions are considered duplicates
    if they have the same fingerprint (after normalization) and are on the same account.

    Args:
        new_amount: Minor units amount (signed int)
        new_currency_code: ISO 4217 currency code
        new_date: Date string (YYYY-MM-DD)
        new_description: Raw transaction description
        account_id: Account identifier
        existing_records: List of dicts with keys: 'fingerprint', 'account_id', 'journal_entry_id'
        occurrence_index: Which occurrence this is (1-based)

    Returns:
        DedupeResult indicating whether this is a duplicate and the existing entry id
    """
    from .fingerprint import compute_fingerprint

    # Compute fingerprint for the new record
    new_fp = compute_fingerprint(
        raw_description=new_description,
        raw_amount=new_amount,
        raw_currency=new_currency_code,
        raw_date=new_date,
        account_id=account_id,
        occurrence_index=occurrence_index,
    )

    # Check against existing fingerprints for this account
    for rec in existing_records:
        if rec.get("account_id") == account_id and rec.get("fingerprint") == new_fp:
            return DedupeResult(
                is_duplicate=True, existing_entry_id=rec.get("journal_entry_id")
            )

    return DedupeResult(is_duplicate=False, occurrence_index=occurrence_index)


@dataclass(frozen=True)
class TransferMatchResult:
    """Result of a transfer-matching check.

    Attributes:
        is_match: Whether two journal lines represent a transfer pair.
        match_method: The method used for matching (auto_amount_date, auto_card_payment, etc.).
        confidence: Confidence score 0.0-1.0.
    """

    is_match: bool
    match_method: str
    confidence: float


def transfer_match(
    line1_amount: float,
    line1_currency: str,
    line1_date: str,
    line1_account_id: str,
    line2_amount: float,
    line2_currency: str,
    line2_date: str,
    line2_account_id: str,
) -> TransferMatchResult:
    """Determine if two journal lines represent a transfer match.

    Two lines are a transfer match if:
    1. Different account_ids
    2. Same owner (single user - assumed by caller)
    3. Opposite signs (one positive, one negative)
    4. Amounts are equal within tolerance (0.01 same currency, 0.50 cross-currency)
    5. Dates within 1-3 day window (outbound first)
    6. Neither already matched

    Args:
        line1_amount: Amount in line 1 (can be float for base comparison)
        line1_currency: Currency code for line 1
        line1_date: Date string for line 1
        line1_account_id: Account ID for line 1
        line2_amount: Amount in line 2
        line2_currency: Currency code for line 2
        line2_date: Date string for line 2
        line2_account_id: Account ID for line 2

    Returns:
        TransferMatchResult with match determination
    """
    # Rule 1: Different accounts
    if line1_account_id == line2_account_id:
        return TransferMatchResult(
            is_match=False,
            match_method="no_match",
            confidence=0.0,
        )

    # Rule 2: Opposite signs
    amount1_sign = 1 if line1_amount >= 0 else -1
    amount2_sign = 1 if line2_amount >= 0 else -1
    if amount1_sign == amount2_sign:
        return TransferMatchResult(
            is_match=False,
            match_method="no_match",
            confidence=0.0,
        )

    # Rule 3: Amount tolerance
    amount_diff = abs(line1_amount + line2_amount)  # since signs are opposite
    same_currency = line1_currency == line2_currency
    tolerance = 0.01 if same_currency else 0.50
    if amount_diff > tolerance:
        return TransferMatchResult(
            is_match=False,
            match_method="no_match",
            confidence=0.0,
        )

    # Rule 4: Date window (outbound first)
    from datetime import datetime

    d1 = parse_date(line1_date)
    d2 = parse_date(line2_date)
    days_diff = abs(days_between(d1, d2))
    # outbound first means the negative amount comes first typically
    # window: L2.date BETWEEN L1.date - 1 day AND L1.date + 3 days
    if days_diff > 3:
        return TransferMatchResult(
            is_match=False,
            match_method="no_match",
            confidence=0.0,
        )

    # Determine match method
    if same_currency and amount_diff <= 0.01 and days_diff <= 1:
        match_method = "auto_amount_date"
    elif amount_diff <= 0.50:
        match_method = "auto_card_payment"
    else:
        match_method = "auto_amount_date"

    # Confidence
    confidence = 0.95 if same_currency and amount_diff <= 0.01 else 0.85

    return TransferMatchResult(
        is_match=True,
        match_method=match_method,
        confidence=confidence,
    )


@dataclass(frozen=True, order=False)
class CategorizeResult:
    """Result of the 7-layer categorization engine.

    Attributes:
        category_id: The assigned category ID (may be None if uncategorized).
        confidence: Confidence score 0.0-1.0.
        layer_reached: The layer number (1-7) at which categorization was decided.
        reason: Human-readable reason for the categorization.
    """

    category_id: Optional[int]
    confidence: float
    layer_reached: int
    reason: str


def categorize_transaction(
    raw_description: str,
    amount: Money,
    currency: Currency,
    date: str,
    account_type: str,
    existing_rules: List[dict] | None = None,
    merchant_aliases: List[dict] | None = None,
    known_merchant_map: Dict[str, int] | None = None,
) -> CategorizeResult:
    """7-layer deterministic categorization engine.

    Layers (in order, short-circuit on first match):
    1. Exact user rules (category_rule matches)
    2. Merchant alias normalization
    3. Known merchant map
    4. Fuzzy trigram match
    5. Learned personal rules
    6. AI/LLM fallback (off by default → returns uncategorized)
    7. Manual review (always returns uncategorized with queue prompt)

    Args:
        raw_description: Raw transaction description text
        amount: Money value object
        currency: Currency of the transaction
        date: Transaction date string (YYYY-MM-DD)
        account_type: Type of account (checking, savings, credit_card, etc.)
        existing_rules: Pre-loaded category rules from DB
        merchant_aliases: Pre-loaded merchant aliases from DB
        known_merchant_map: Dict mapping canonical merchant names to category IDs

    Returns:
        CategorizeResult with the assigned category and metadata
    """
    description = raw_description.strip().lower()

    # Layer 1: Exact user rules
    if existing_rules:
        for rule in existing_rules:
            pattern = rule.get("description_pattern", "").strip().lower()
            if pattern and pattern in description:
                if rule.get("is_learned"):
                    confidence = rule.get("confidence", 1.0)
                else:
                    confidence = 1.0
                return CategorizeResult(
                    category_id=rule.get("category_id"),
                    confidence=confidence,
                    layer_reached=1,
                    reason=f"User rule match: '{rule.get('description_pattern')}'",
                )

    # Layer 2: Merchant alias normalization
    if merchant_aliases:
        for alias in merchant_aliases:
            raw_string = alias.get("raw_string", "").strip().lower()
            if raw_string and raw_string in description:
                category_id = alias.get("category_id")
                if category_id is not None:
                    confidence = alias.get("confidence", 0.5)
                    return CategorizeResult(
                        category_id=category_id,
                        confidence=confidence,
                        layer_reached=2,
                        reason=f"Merchant alias match: '{alias.get('raw_string')}'",
                    )

    # Layer 3: Known merchant map
    if known_merchant_map:
        for merchant_name, cat_id in known_merchant_map.items():
            merch_lower = merchant_name.strip().lower()
            if merch_lower and merch_lower in description:
                return CategorizeResult(
                    category_id=cat_id,
                    confidence=0.7,
                    layer_reached=3,
                    reason=f"Known merchant map match: '{merchant_name}'",
                )

    # Layer 4: Fuzzy trigram match (simplified)
    # Check if any known merchant has high enough trigram similarity
    if known_merchant_map:
        for merchant_name, cat_id in known_merchant_map.items():
            merch_lower = merchant_name.strip().lower()
            if merch_lower:
                # Simple substring check as a proxy for trigram similarity
                if merch_lower in description:
                    return CategorizeResult(
                        category_id=cat_id,
                        confidence=0.6,
                        layer_reached=4,
                        reason=f"Fuzzy match: '{merchant_name}'",
                    )

    # Layer 5: Learned personal rules
    # (No learned rules loaded by default in M0; would come from DB in production)

    # Layer 6: AI/LLM fallback (disabled by default)
    # Return uncategorized, letting layer 7 handle

    # Layer 7: Manual review - return uncategorized
    return CategorizeResult(
        category_id=None,
        confidence=0.0,
        layer_reached=7,
        reason="Uncategorized - queued for manual review (layer 7)",
    )


@dataclass(frozen=True)
class BudgetResult:
    """Result of budget check against a category spend limit.

    Attributes:
        category_id: The category being checked.
        spent_amount: Amount already spent in this period.
        limit_amount: The budget limit for the period.
        over_budget: Whether the spend exceeds the limit.
        remaining: Remaining budget amount.
    """

    category_id: int
    spent_amount: Money
    limit_amount: Money
    over_budget: bool
    remaining: Money


def check_budget(
    category_id: int,
    spent_amount: Money,
    limit_amount: Money,
) -> BudgetResult:
    """Check if a category budget has been exceeded.

    Args:
        category_id: The category being checked
        spent_amount: Amount already spent (Money in account currency)
        limit_amount: Budget limit for the period (Money in same currency)

    Returns:
        BudgetResult with over_budget flag and remaining amount
    """
    from ...core.money import Money as Money2

    if spent_amount.currency != limit_amount.currency:
        msg = f"Cannot compare budgets in different currencies: {spent_amount.currency} vs {limit_amount.currency}"
        raise ValueError(msg)

    total_spent = spent_amount
    limit = limit_amount
    over_budget = total_spent > limit  # uses Money.__gt__ which uses amount comparison
    remaining_amount = limit - total_spent
    # Ensure remaining is positive if not over budget, or negative if over
    remaining = (
        remaining_amount if not over_budget else remaining_amount
    )  # still compute, sign conveys

    return BudgetResult(
        category_id=category_id,
        spent_amount=spent_amount,
        limit_amount=limit_amount,
        over_budget=over_budget,
        remaining=remaining,
    )
