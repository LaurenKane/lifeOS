"""budget.py — budget-limit arithmetic.

Pure integer arithmetic on signed minor units. No percentages, no rollover, no
currency conversion: a budget is a limit on one category in one currency, and
anything fancier belongs in M9 where the rollover rules get decided.

Comparison happens on minor units only. `spent > limit` on floats is how a
budget ends up off by a cent per transaction and permanently wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.money import Money

__all__ = ["BudgetResult", "check_budget"]


@dataclass(frozen=True)
class BudgetResult:
    """The state of one category against its limit for a period."""

    category_id: int
    limit: Money
    spent: Money
    remaining: Money
    over_budget: bool

    @property
    def used_ratio(self) -> int:
        """Spend as a whole percentage of the limit.

        An integer percentage, not a float: this is a progress bar, and a float
        here would invite someone to compare it against a float budget. A zero
        limit reports 0 rather than dividing by zero — spending nothing against
        no limit is not 100% overspent.
        """
        if self.limit.amount == 0:
            return 0
        return round(self.spent.amount * 100 / self.limit.amount)


def check_budget(*, category_id: int, spent: Money, limit: Money) -> BudgetResult:
    """Compare spend against a limit.

    Args:
        category_id: The category being checked.
        spent: Amount spent in the period. May be negative if refunds exceed
            spend, which is a real outcome and counts towards the limit.
        limit: The limit for the period, same currency as `spent`.

    Returns:
        A `BudgetResult`. `remaining` is negative when over budget.

    Raises:
        ValueError: If the two amounts are in different currencies. Comparing
            across currencies needs a rate and a conversion date, and guessing
            one silently produces a wrong budget.
    """
    if spent.currency != limit.currency:
        msg = (
            "Cannot compare budget across currencies: "
            f"{spent.currency.code} vs {limit.currency.code}"
        )
        raise ValueError(msg)

    remaining_amount = limit.amount - spent.amount
    return BudgetResult(
        category_id=category_id,
        limit=limit,
        spent=spent,
        remaining=Money(amount=remaining_amount, currency=limit.currency),
        over_budget=spent.amount > limit.amount,
    )
