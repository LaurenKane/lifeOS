"""rules.py — read the categorization rules out of the database.

A projection, not a policy. Each `finance.category_rule` row becomes the
`CategoryRule` dataclass `finance.domain.services.categorize` matches on, with
its fields carried over verbatim. Which rule wins is the engine's decision, and
it is made where the engine lives — nothing here ranks, filters by account or
merchant, or otherwise decides what a description means.

A row with no `description_pattern` is skipped: the dataclass requires a string
pattern, and a pattern-less row has nothing to match with. That is a shape
requirement, not a judgement about the row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance.domain.models.taxonomy import CategoryRule as CategoryRuleRow
from finance.domain.services.categorize import CategoryRule

__all__ = ["load_rules"]


def load_rules(session: Session) -> list[CategoryRule]:
    """Every stored rule with a description pattern, as the engine reads it.

    Args:
        session: The caller's session. Read-only; nothing is begun here and
            nothing is written.

    Returns:
        The `CategoryRule` dataclasses, in stored priority order. The engine
        sorts them itself before matching, so this order is a convenience for a
        human reader rather than a contract.
    """
    rows = session.scalars(
        select(CategoryRuleRow).order_by(CategoryRuleRow.priority, CategoryRuleRow.id)
    ).all()
    rules: list[CategoryRule] = []
    for row in rows:
        if row.description_pattern is None:
            continue
        rules.append(
            CategoryRule(
                category_id=row.category_id,
                description_pattern=row.description_pattern,
                priority=row.priority,
                confidence=row.confidence,
                is_learned=row.is_learned,
            )
        )
    return rules
