"""categorize.py — the one place the API turns a description into a category.

Manual create, file import and replay all categorize through here, so the
layer order is spelled once: the engine in
`finance.domain.services.categorize` owns the order, `load_rules` owns the
loading, and the pipeline normalizer below owns the canonicalisation. A
second spelling of any of the three is how a matcher starts disagreeing
with itself across entry points.

Two functions because the call sites have different shapes: single-shot
callers (`categorize_description`, which loads) and row loops
(`categorize_with_rules`, which takes preloaded rules). The loops preload
because loading the whole rule table per row would be O(rows) queries for
no reason — the same per-row discipline `_already_stored` keeps in
`routes/imports.py`. Nothing about the match changes between the two; the
normalizer and the engine call are identical.

`finance.ingestion` is imported here, not in `finance.domain`, on purpose:
the engine takes its normalizer injected (ARCHITECTURE.md §3), and this
module — sitting in `finance.api` next to the callers that own the
transaction — is where that injection is allowed to live.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from finance.domain.models.importer import SourceRecord
from finance.domain.models.ledger import JournalLine
from finance.domain.models.taxonomy import CategoryRule as CategoryRuleRow
from finance.domain.services.categorize import (
    CategorizeResult,
    CategoryRule,
    MerchantAlias,
    categorize_transaction,
    stable_payee_pattern,
)
from finance.ingestion.fingerprint import normalize_description
from finance.ingestion.rules import load_aliases, load_known_merchants, load_rules

__all__ = [
    "LEARNED_RULE_PRIORITY",
    "build_learned_rule",
    "categorize_description",
    "categorize_posted_record",
    "categorize_with_rules",
    "upsert_learned_rule",
]

#: Priority of learned rules. Higher than the hand-rule default of 100, and
#: higher-is-later here: the engine matches in ascending priority order, so
#: an explicit hand rule always outranks a learned one. A learned guess must
#: never pre-empt a rule the user wrote deliberately — the user wrote it
#: because the learned answer was wrong.
LEARNED_RULE_PRIORITY: Final = 500


def categorize_with_rules(
    rules: Sequence[CategoryRule],
    *,
    description: str,
    merchant_aliases: Sequence[MerchantAlias] = (),
    known_merchants: dict[str, int] | None = None,
) -> CategorizeResult:
    """Run the engine over preloaded inputs with the pipeline normalizer.

    The normalizer is `normalize_description`, not the engine's whitespace
    fallback: the fallback does not strip provider bookkeeping markers, so a
    rule learned under one normalizer would silently stop matching under the
    other. Injected here rather than imported by the domain, which must not
    depend on ingestion.
    """
    return categorize_transaction(
        description,
        rules=rules,
        merchant_aliases=merchant_aliases,
        known_merchants=known_merchants,
        normalize=normalize_description,
    )


def categorize_description(session: Session, *, description: str) -> CategorizeResult:
    """Run the engine over the currently stored rules, aliases and merchants.

    Loads via `load_rules`, `load_aliases` and `load_known_merchants` — the
    only loaders — and matches exactly as `categorize_with_rules` does.
    Read-only. Single-shot callers only; row loops preload once and call
    `categorize_with_rules` instead.
    """
    return categorize_with_rules(
        load_rules(session),
        description=description,
        merchant_aliases=load_aliases(session),
        known_merchants=load_known_merchants(session),
    )


def categorize_posted_record(
    session: Session,
    *,
    source_record_id: int,
    account_id: int,
    description: str,
    rules: Sequence[CategoryRule] | None = None,
    merchant_aliases: Sequence[MerchantAlias] | None = None,
    known_merchants: dict[str, int] | None = None,
) -> int | None:
    """Categorize one posted row's funding leg, when the engine is sure.

    Finds the entry through its `source_record`, takes the first funding leg
    (lowest `sort_order` on the row's own account — the same leg every read
    path reports as the transaction's category), and sets its category ONLY
    when the result `is_auto` (confidence >= 0.90). A fuzzy layer-4 match at
    0.60 must NOT stick: persisting a guess would silently drain the review
    queue served by `idx_jl_uncat`, and a wrong category is worse than an
    empty one because the user cannot see a category they did not choose.

    `rules`, `merchant_aliases` and `known_merchants` let row loops preload
    once instead of once per row; None loads, which is what single-shot
    callers want.

    Returns the persisted category id, or None when the engine declined.
    Never begins or commits; the caller owns the transaction.
    """
    record = session.get(SourceRecord, source_record_id)
    if record is None or record.journal_entry_id is None:
        return None
    line_id = session.scalar(
        select(JournalLine.id)
        .where(JournalLine.journal_entry_id == record.journal_entry_id)
        .where(JournalLine.account_id == account_id)
        .order_by(JournalLine.sort_order)
        .limit(1)
    )
    if line_id is None:
        return None
    loaded = load_rules(session) if rules is None else rules
    if merchant_aliases is None:
        loaded_aliases: Sequence[MerchantAlias] = load_aliases(session)
    else:
        loaded_aliases = merchant_aliases
    if known_merchants is None:
        loaded_merchants: dict[str, int] = load_known_merchants(session)
    else:
        loaded_merchants = known_merchants
    result = categorize_with_rules(
        loaded,
        description=description,
        merchant_aliases=loaded_aliases,
        known_merchants=loaded_merchants,
    )
    if not result.is_auto:
        return None
    line = session.get(JournalLine, int(line_id))
    if line is None:  # pragma: no cover - the id came from this session
        return None
    category_id = result.category_id
    line.category_id = category_id
    session.flush()
    return category_id


def build_learned_rule(*, description: str, category_id: int) -> CategoryRule:
    """The learned rule one correction teaches, as pure data.

    The pattern is the payee's stable prefix, lowercased: "PAYPAL XYZ 1234"
    and "PAYPAL XYZ 9999" are the same payee, and the trailing number is an
    order reference, not a merchant. `is_learned` marks it as taught rather
    than hand-authored, so a correction can be undone without losing the
    user's own learning — and so the engine can tell the two apart if it ever
    needs to. Priority and confidence are the module constants, not caller
    choices: a learned rule is always a full-confidence guess that still
    loses to any hand rule.

    Pure: no session, no writes. The caller persists it with
    `upsert_learned_rule`.

    Raises:
        ValueError: If the description reduces to no pattern at all.
    """
    pattern = stable_payee_pattern(description).lower()
    if not pattern:
        msg = "Cannot learn a category from an empty description"
        raise ValueError(msg)
    return CategoryRule(
        category_id=category_id,
        description_pattern=pattern,
        priority=LEARNED_RULE_PRIORITY,
        confidence=Decimal("1.00"),
        is_learned=True,
    )


def upsert_learned_rule(
    session: Session, *, description: str, category_id: int
) -> CategoryRuleRow:
    """Store what one correction taught, updating in place, never duplicating.

    Matched by `description_pattern` among `is_learned` rows only: a hand
    rule that happens to share the text is the user's explicit word and must
    not be rewritten as a side effect of a correction. An existing learned
    row gets the new category in place; otherwise a new row is inserted with
    the shape `build_learned_rule` defines. Either way exactly one learned
    row carries the pattern afterwards.

    Returns the stored row. Flushes but never begins or commits; the caller
    owns the transaction, because learning rides along inside the PATCH that
    taught it — one transaction or neither.
    """
    rule = build_learned_rule(description=description, category_id=category_id)
    existing = session.scalar(
        select(CategoryRuleRow)
        .where(CategoryRuleRow.is_learned.is_(True))
        .where(CategoryRuleRow.description_pattern == rule.description_pattern)
        .order_by(CategoryRuleRow.id)
        .limit(1)
    )
    if existing is not None:
        existing.category_id = rule.category_id
        existing.confidence = rule.confidence
        session.flush()
        return existing
    row = CategoryRuleRow(
        description_pattern=rule.description_pattern,
        category_id=rule.category_id,
        priority=rule.priority,
        confidence=rule.confidence,
        is_learned=True,
    )
    session.add(row)
    session.flush()
    return row
