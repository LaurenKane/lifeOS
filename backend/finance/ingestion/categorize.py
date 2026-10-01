"""categorize.py — pipeline-side categorization.

Wraps the domain seven-layer engine with the pipeline's job of getting the rules
in and the outcome out:

    load rules -> classify -> record the decision -> route to review

The layer logic itself lives in `finance.domain.services.categorize`, where it
is pure. What is here is orchestration: the batch-level summary and the
learned-rule write-through that turns a user's correction into layer 5.

Two behaviours worth stating, because they are the whole point of the review
queue:

- A row the engine cannot justify is **kept**, not dropped, with
  `needs_review=True`. It is invisible to spending reports until categorised,
  and present in the queue until then.
- A confirmed correction is written back as a learned rule *and* raises the
  alias confidence to 1.0, so the next occurrence of that payee resolves
  without asking again.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from finance.domain.services.categorize import (
    CategorizeResult,
    CategoryRule,
    MerchantAlias,
    categorize_transaction,
    learn_from_correction,
    stable_payee_pattern,
)
from finance.ingestion.fingerprint import normalize_description

__all__ = [
    "CategorizeResult",
    "CategoryRule",
    "MerchantAlias",
    "CategorizationBatchResult",
    "PendingCategory",
    "categorize_batch",
    "categorize_record",
    "learn_from_correction",
]


@dataclass(frozen=True)
class PendingCategory:
    """A row queued for manual review."""

    record_id: str
    raw_description: str
    amount_minor: int
    currency: str
    booked_date: str
    reason: str


@dataclass(frozen=True)
class CategorizationBatchResult:
    """What happened across one import batch."""

    categorized: dict[str, CategorizeResult] = field(default_factory=dict)
    pending_review: list[PendingCategory] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.categorized)

    @property
    def auto_categorized(self) -> list[str]:
        """Record IDs resolved confidently enough not to interrupt anyone."""
        return [
            record_id
            for record_id, result in self.categorized.items()
            if result.is_auto
        ]

    @property
    def review_count(self) -> int:
        return len(self.pending_review)


def categorize_record(
    raw_description: str,
    *,
    rules: Sequence[CategoryRule] = (),
    merchant_aliases: Sequence[MerchantAlias] = (),
    known_merchants: dict[str, int] | None = None,
) -> CategorizeResult:
    """Classify one record. Thin pass-through to the domain engine.

    Exists as a distinct name so the pipeline reads as `categorize_record` and
    the rule set reads as `categorize_transaction` — the domain module is
    private, and a pipeline module should not look like it re-exports it.

    Injects the pinned fingerprint normalisation so the pipeline and the dedup
    rule agree on what a canonical description is. Without that they would drift,
    and a description matching no rule here would still fingerprint-match there.
    """
    return categorize_transaction(
        raw_description,
        rules=rules,
        merchant_aliases=merchant_aliases,
        known_merchants=known_merchants,
        normalize=normalize_description,
    )


def categorize_batch(
    records: Iterable[PendingCategory],
    *,
    rules: Sequence[CategoryRule] = (),
    merchant_aliases: Sequence[MerchantAlias] = (),
    known_merchants: dict[str, int] | None = None,
) -> CategorizationBatchResult:
    """Classify every record in a batch and split the outcomes.

    Args:
        records: The rows to classify. Each needs at least a `record_id` and a
            `raw_description`; the rest is carried into the review queue so it
            can be displayed.
        rules: Known user and learned rules.
        merchant_aliases: Known payee strings.
        known_merchants: Curated NL merchant seeds.

    Returns:
        A `CategorizationBatchResult`. Records that could not be categorized are
        in `pending_review`, not dropped.
    """
    categorized: dict[str, CategorizeResult] = {}
    pending: list[PendingCategory] = []

    for record in records:
        result = categorize_record(
            record.raw_description,
            rules=rules,
            merchant_aliases=merchant_aliases,
            known_merchants=known_merchants,
        )
        if result.category_id is None:
            pending.append(record)
        else:
            categorized[record.record_id] = result

    return CategorizationBatchResult(categorized=categorized, pending_review=pending)


def correction_to_rules(
    raw_description: str,
    *,
    category_id: int,
    existing_aliases: Sequence[MerchantAlias] = (),
) -> tuple[MerchantAlias, CategoryRule]:
    """Build the writes a confirmed manual correction implies.

    Layer 5: every manual correction produces BOTH a learned alias and a learned
    rule at full confidence. Producing only one is why a review queue never fully
    drains — the next occurrence would miss on whichever layer was skipped.

    Both are built from the same payee prefix, so the alias and the rule can
    never disagree about what the payee is called.

    Returns:
        `(alias, rule)` for the caller to persist. Nothing is written here; this
        module has no database access.
    """
    pattern = normalize_pattern(raw_description)
    alias = learn_from_correction(
        pattern,
        category_id=category_id,
        existing_aliases=existing_aliases,
    )
    rule = CategoryRule(
        category_id=category_id,
        description_pattern=pattern,
        # Above the seeded rules' default priority, so a learned correction wins
        # over a general rule the user wrote years ago.
        priority=10,
        confidence=Decimal("1.00"),
        is_learned=True,
    )
    return alias, rule


def normalize_pattern(raw_description: str) -> str:
    """Reduce a description to the stable prefix a rule or alias should match on.

    Applies the pinned fingerprint normalisation first, so a trailing REF block
    cannot become part of a learned pattern, then reduces to the leading tokens.
    """
    return stable_payee_pattern(normalize_description(raw_description))
