"""rules.py — read the categorization inputs out of the database.

Three projections, not a policy. Each row becomes the dataclass
`finance.domain.services.categorize` matches on, with its fields carried over
verbatim. Which input wins is the engine's decision, and it is made where the
engine lives — nothing here ranks, filters, or otherwise decides what a
description means.

- `load_rules`: every `category_rule` row with a description pattern.
- `load_aliases`: every `merchant_alias` row with a category, ordered by
  raw_string. A row with no `category_id` cannot feed layer 2 and is skipped.
- `load_known_merchants`: every `merchant` row with a category, as
  `{lowercased name: category_id}`. The engine matches on the lowercased name
  substring and sorts itself, so dict order here is irrelevant; a merchant
  with no category feeds nothing and is skipped.

A rule row with no `description_pattern` is skipped: the dataclass requires
a string pattern, and a pattern-less row has nothing to match with. That is
a shape requirement, not a judgement about the row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance.domain.models.taxonomy import (
    CategoryRule as CategoryRuleRow,
)
from finance.domain.models.taxonomy import (
    Merchant as MerchantRow,
)
from finance.domain.models.taxonomy import (
    MerchantAlias as MerchantAliasRow,
)
from finance.domain.services.categorize import CategoryRule, MerchantAlias

__all__ = ["load_aliases", "load_known_merchants", "load_rules"]


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


def load_aliases(session: Session) -> list[MerchantAlias]:
    """Every stored alias with a category, as the engine reads it.

    Args:
        session: The caller's session. Read-only; nothing is begun here and
            nothing is written.

    Returns:
        The `MerchantAlias` dataclasses, ordered by raw_string for a stable
        read. The engine sorts them itself before matching, so this order is
        a convenience for a human reader rather than a contract. Rows with
        no `category_id` are skipped: layer 2 resolves aliases to a category,
        and a category-less row has nothing to resolve to.
    """
    rows = session.scalars(
        select(MerchantAliasRow)
        .where(MerchantAliasRow.category_id.is_not(None))
        .order_by(MerchantAliasRow.raw_string)
    ).all()
    aliases: list[MerchantAlias] = []
    for row in rows:
        category_id = row.category_id
        if category_id is None:  # pragma: no cover - filtered by the query
            continue
        aliases.append(
            MerchantAlias(
                raw_string=row.raw_string,
                category_id=int(category_id),
                confidence=row.confidence,
            )
        )
    return aliases


def load_known_merchants(session: Session) -> dict[str, int]:
    """Every categorized merchant, as the engine's layer 3-4 map.

    Args:
        session: The caller's session. Read-only; nothing is begun here and
            nothing is written.

    Returns:
        `{lowercased merchant name: category_id}`. The engine matches on the
        lowercased name substring, so the key is lowered here once rather
        than per comparison. Merchants with no `category_id` are skipped:
        a known-but-unfiled name feeds neither layer 3 nor layer 4.
    """
    rows = session.scalars(
        select(MerchantRow).where(MerchantRow.category_id.is_not(None))
    ).all()
    merchants: dict[str, int] = {}
    for row in rows:
        category_id = row.category_id
        if category_id is None:  # pragma: no cover - filtered by the query
            continue
        merchants[row.name.lower()] = int(category_id)
    return merchants
