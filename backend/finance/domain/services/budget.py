"""budget.py — Budget checking against category spend limits."""

from __future__ import annotations

from dataclasses import dataclass

from ...core.money import Money


@dataclass(frozen=True)
class BudgetResult:
    """Result of budget check against a category spend limit.

    Attributes:
        category_id: The category being checked.
        spent_amount: Amount already spent in this period.
        limit_amount: The budget limit for the period.
        over_budget: Whether the spend exceeds the limit.
        remaining: Remaining budget amount (negative if over budget).
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
    if spent_amount.currency != limit_amount.currency:
        msg = f"Cannot compare budgets in different currencies: {spent_amount.currency} vs {limit_amount.currency}"
        raise ValueError(msg)

    total_spent = spent_amount.amount
    limit = limit_amount.amount
    over_budget = total_spent > limit  # compares minor unit ints
    remaining_amount = limit - total_spent

    return BudgetResult(
        category_id=category_id,
        spent_amount=spent_amount,
        limit_amount=limit_amount,
        over_budget=over_budget,
        remaining=Money(amount=remaining_amount, currency=spent_amount.currency),
    )
