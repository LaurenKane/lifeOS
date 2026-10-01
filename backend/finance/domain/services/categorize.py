"""categorize.py — 7-layer deterministic categorization engine.

See ARCHITECTURE-PROPOSAL.md §I for the layer descriptions.

Layers (in order, short-circuit on first match):
  1. Exact user rules (category_rule with merchant_id match, priority-ordered)
  2. Merchant normalization (merchant_alias exact then trigram fuzzy)
  3. Known merchant map (curated seeds for NL merchants)
  4. Fuzzy match (trigram similarity on raw_description)
  5. Learned personal rules (every manual correction creates an is_learned rule)
  6. AI/LLM fallback (optional, off by default)
  7. Manual review (uncategorized queue)

$Id: categorize.py 2026-10-01$
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ...core.money import Money
from ...domain.value_objects import Currency


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
                confidence = rule.get("confidence", 1.0)
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
                cat_id = alias.get("category_id")
                if cat_id is not None:
                    AliasConf = alias.get("confidence", 0.5)
                    return CategorizeResult(
                        category_id=cat_id,
                        confidence=AliasConf,
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

    # Layer 4: Fuzzy trigram match (simplified substring as proxy)
    if known_merchant_map:
        for merchant_name, cat_id in known_merchant_map.items():
            merch_lower = merchant_name.strip().lower()
            if merch_lower:
                if merch_lower in description:
                    return CategorizeResult(
                        category_id=cat_id,
                        confidence=0.6,
                        layer_reached=4,
                        reason=f"Fuzzy match: '{merchant_name}'",
                    )

    # Layer 5: Learned personal rules
    # (No learned rules loaded by default in M0; would come from DB corrections in production)

    # Layer 6: AI/LLM fallback (disabled by default in M0)
    # Return uncategorized, letting layer 7 handle

    # Layer 7: Manual review - return uncategorized
    return CategorizeResult(
        category_id=None,
        confidence=0.0,
        layer_reached=7,
        reason="Uncategorized - queued for manual review (layer 7)",
    )
