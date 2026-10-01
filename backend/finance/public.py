"""finance module public API — the ONLY export surface.

Protocols + read-only schemas. This is the interface that other modules (including
frontend) may depend on. Nothing from finance.domain or finance.ingestion should
be imported by external modules.

Design rules:
- Only export protocols (ABCs) and Pydantic read-only schemas
- No business logic — that lives in finance.domain.services
- No DB access — that lives in finance.background or finance.api.routes
- All types are hashable and suitable for use as dict keys or in sets
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Protocol

from pydantic import BaseModel

from ...core.money import Money

# ── Read-only schemas (public contract) ────────────────────────────


class TransactionSummary(BaseModel):
    """Read-only schema for a transaction summary — public API contract."""

    id: int
    account_id: str
    fingerprint: str
    raw_description: str
    raw_amount: int  # minor units, signed
    raw_currency: str
    raw_date: date
    status: str
    journal_entry_id: Optional[int] = None
    transfer_match_id: Optional[int] = None

    model_config = {"frozen": True, "str_strict": True}


class AccountSummary(BaseModel):
    """Read-only schema for an account summary."""

    id: str
    name: str
    currency: str
    is_active: bool
    sort_order: int = 0

    model_config = {"frozen": True, "str_strict": True}


class CategorySummary(BaseModel):
    """Read-only schema for a category."""

    id: int
    name: str
    kind: str  # expense, income, transfer, investment
    is_system: bool = False

    model_config = {"frozen": True, "str_strict": True}


class FingerprintResult(BaseModel):
    """Schema for fingerprint computation result — public contract."""

    fingerprint: str  # SHA-256 hex digest

    model_config = {"frozen": True, "str_strict": True}


# ── Protocols (interfaces, not implementations) ────────────────────


class ICategoryRule(Protocol):
    """Protocol for a categorization rule.

    This protocol defines the interface that category rules must satisfy.
    Implementations live in finance.domain.services or the DB layer.
    """

    description_pattern: str
    category_id: int
    confidence: float
    is_learned: bool

    def matches(self, description: str) -> bool:
        """Check if this rule matches a given description."""
        ...


class ITransferMatcher(Protocol):
    """Protocol for transfer matching logic."""

    def match(self, line1: dict, line2: dict) -> dict:
        """Determine if two journal lines are a transfer pair.

        Returns dict with keys: is_match, match_method, confidence.
        """
        ...


class IDeduper(Protocol):
    """Protocol for deduplication logic."""

    def check(
        self,
        raw_description: str,
        raw_amount: int,
        raw_currency: str,
        raw_date: str,
        account_id: str,
        occurrence_index: int = 1,
    ) -> dict:
        """Check if a transaction is a duplicate.

        Returns dict with keys: is_duplicate, existing_entry_id, occurrence_index.
        """
        ...


# ── Convenience types ──────────────────────────────────────────────

MoneyLike = Money  # alias for public API; always Money from core.money


def is_valid_currency(code: str) -> bool:
    """Check if a currency code is valid (3 letters)."""
    return len(code) == 3 and code.isalpha()
