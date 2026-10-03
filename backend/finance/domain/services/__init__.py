"""finance.domain.services - PRIVATE: pure logic, no I/O.

This module is a re-export surface only. Every implementation lives in its own
module next door:

    transfer_match.py   the transfer-pair rule (§H)
    categorize.py       the 7-layer categorization engine (§I)
    budget.py           budget-limit arithmetic
    manual_posting.py   the manual-entry posting rule

Nothing is defined here. If you are adding logic, add it to the sibling module
and re-export the name below.
"""

from __future__ import annotations

from finance.domain.services.budget import BudgetResult, check_budget
from finance.domain.services.categorize import CategorizeResult, categorize_transaction
from finance.domain.services.transfer_match import TransferMatch, transfer_match

__all__ = [
    "BudgetResult",
    "CategorizeResult",
    "TransferMatch",
    "categorize_transaction",
    "check_budget",
    "transfer_match",
]
