"""Domain-facing name for the money value objects.

PRIVATE - only finance.public may import from this module.

The implementation is `core.money.Money` / `core.money.Currency`. This module
exists so the domain layer can say `from finance.domain.value_objects.money
import Money` and mean the one shared primitive. A second Money class here
would be two answers to "what is a cent", which is exactly the bug this
prevents.

Re-exported rather than redefined, so `isinstance` works across both paths.
"""

from __future__ import annotations

from core.money import Currency, Money

__all__ = ["Currency", "Money"]
