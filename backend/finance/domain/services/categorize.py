"""categorize.py — the seven-layer categorization engine.

ARCHITECTURE.md §10 (M3). Deterministic, with no LLM required for the
core to work.

    1  exact user rules        category_rule, priority-ordered
    2  merchant normalization  merchant_alias, exact then fuzzy
    3  known merchant map      curated NL merchant seeds
    4  fuzzy match             trigram similarity on raw_description
    5  learned personal rules  every manual correction creates one
    6  AI/LLM fallback         optional, off by default, never required
    7  manual review           the uncategorized queue

Layer 5 is the one the reference implementations lack, and the reason a manual
correction is worth storing: fix `PAYPAL XYZ` once and it stays fixed.

Two rules the engine obeys without exception:

- **Short-circuit on first match, in priority order.** Layer 4's trigram match
  is deliberately fuzzy, so it must never pre-empt an exact rule the user wrote.
- **Declining is a success.** Layer 7 is a real answer, not a failure. Nothing
  here guesses past its confidence, because a wrong category is worse than an
  empty one: the user cannot see a category they did not choose.

Layers 6 and 7 are represented as explicit outcomes rather than being merged, so
the queue that drives them stays inspectable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "CategorizeResult",
    "CategoryRule",
    "MerchantAlias",
    "categorize_transaction",
    "learn_from_correction",
    "stable_payee_pattern",
    "trigram_similarity",
    "word_similarity",
]

# Word-similarity above which a fuzzy match is accepted (section I, layer 4).
#
# 0.6 rather than Postgres's `similarity > 0.8` because this compares the best
# token window of the description against the merchant name, not two whole
# strings: a bank description is always longer than a merchant name, so a
# whole-string threshold calibrated for `merchant_alias` rows would reject every
# real description. A one-character typo in a two-word name scores 0.70 here;
# unrelated words score 0.0, so there is a wide empty band between them.
FUZZY_THRESHOLD = 0.6

# Confidence assigned to each layer. The two that involve guessing stay low, so
# a low-confidence result is visibly distinguishable from a rule the user wrote.
_CONFIDENCE: dict[int, Decimal] = {
    1: Decimal("1.00"),
    2: Decimal("0.90"),
    3: Decimal("0.75"),
    4: Decimal("0.60"),
    5: Decimal("1.00"),
}


@dataclass(frozen=True)
class CategoryRule:
    """A user-written rule: substring match, priority-ordered, editable.

    Deliberately a substring plus a priority integer, not a regex and not a DSL.
    The whole rule set must be readable on one screen and editable by hand
    (section I).
    """

    category_id: int
    description_pattern: str
    priority: int = 100
    confidence: Decimal = Decimal("1.00")
    is_learned: bool = False

    def matches(self, description: str) -> bool:
        """Case-insensitive substring match. An empty pattern never matches."""
        pattern = self.description_pattern.strip()
        return bool(pattern) and pattern.lower() in description


@dataclass(frozen=True)
class MerchantAlias:
    """A raw payee string mapped to a merchant, and its confidence.

    Confidence rises to 1.0 on every confirmed correction, which is how the
    alias table gets better without a retraining step.
    """

    raw_string: str
    category_id: int
    confidence: Decimal = Decimal("0.50")

    def matches(self, description: str) -> bool:
        raw = self.raw_string.strip()
        return bool(raw) and raw.lower() in description


@dataclass(frozen=True)
class CategorizeResult:
    """What the engine decided.

    `category_id is None` with `layer_reached == 7` is the expected outcome for
    anything the engine cannot justify — it routes to the review queue.
    """

    category_id: int | None
    confidence: Decimal
    layer_reached: int
    reason: str

    @property
    def needs_review(self) -> bool:
        return self.category_id is None

    @property
    def is_auto(self) -> bool:
        """Whether this was confident enough not to bother the user."""
        return self.category_id is not None and self.confidence >= Decimal("0.90")


def _default_normalize(description: str) -> str:
    """Collapse whitespace and lowercase. The fallback when no other rule is given.

    Deliberately simpler than the fingerprint's normalisation: this one does not
    strip provider bookkeeping markers, because it has no access to that pinned
    rule. The pipeline always injects the pinned one.
    """
    return " ".join(description.split()).lower()


def trigram_similarity(left: str, right: str) -> float:
    """Dice coefficient over character trigrams, in 0.0-1.0.

    A cheap stand-in for Postgres `pg_trgm`, so layer 4 is testable without a
    database. Identical strings score 1.0; anything shorter than three characters
    compares by equality only, since trigrams of a two-character string are noise.
    """
    return _dice(left.lower(), right.lower())


def _dice(a: str, b: str) -> float:
    """The Dice coefficient itself, on two already-lowercased strings."""
    if a == b:
        return 1.0
    if len(a) < 3 or len(b) < 3:
        return 0.0
    grams_a = {a[i : i + 3] for i in range(len(a) - 2)}
    grams_b = {b[i : i + 3] for i in range(len(b) - 2)}
    shared = len(grams_a & grams_b)
    return float(Decimal(2 * shared) / Decimal(len(grams_a) + len(grams_b)))


def word_similarity(description: str, merchant: str) -> float:
    """Best trigram score for `merchant` against any window of `description`.

    This is `pg_trgm`'s `word_similarity`, and it exists for the same reason
    Postgres has it: a plain whole-string comparison punishes a merchant name for
    appearing inside a longer description.

    "albert hejin amsterdam" against "albert heijn" scores 0.47 whole-string —
    below any usable threshold — because the extra words dilute the trigrams.
    Scored against the best two-word window it scores 0.70, which is what the
    matcher actually means. Without this, every bank description carrying a
    terminal or a city would miss every merchant and land in the review queue
    forever.
    """
    description = description.lower()
    merchant = merchant.lower()
    if not description or not merchant:
        return 0.0
    words = description.split()
    window = len(merchant.split())
    if window == 0 or len(words) < window:
        return trigram_similarity(description, merchant)
    return max(
        _dice(" ".join(words[start : start + window]), merchant)
        for start in range(len(words) - window + 1)
    )


def _best_fuzzy(
    description: str,
    merchant_categories: dict[str, int],
) -> tuple[int, str] | None:
    """Layer 4: the closest known merchant above the similarity threshold.

    Ties resolve on the merchant name, so the same input always produces the
    same category regardless of dict ordering.
    """
    best: tuple[float, str] | None = None
    for merchant in merchant_categories:
        score = word_similarity(description, merchant)
        if score < FUZZY_THRESHOLD:
            continue
        if best is None or score > best[0] or (score == best[0] and merchant < best[1]):
            best = (score, merchant)
    if best is None:
        return None
    return merchant_categories[best[1]], best[1]


def categorize_transaction(
    raw_description: str,
    *,
    rules: Sequence[CategoryRule] = (),
    merchant_aliases: Sequence[MerchantAlias] = (),
    known_merchants: dict[str, int] | None = None,
    account_type: str | None = None,
    normalize: Callable[[str], str] = _default_normalize,
) -> CategorizeResult:
    """Assign a category, or decline to.

    Args:
        raw_description: The provider's description, unmodified.
        rules: User-written rules. Applied in `priority` order, ascending, so a
            user can put a specific rule above a general one.
        merchant_aliases: Known raw payee strings.
        known_merchants: Curated merchant -> category seeds for NL merchants.
        account_type: The source account's type. Unused today; reserved so the
            signature does not have to change when it does.
        normalize: How to canonicalise the description before matching. The
            default collapses whitespace and lowercases. The pipeline passes
            `finance.ingestion.fingerprint.normalize_description`, which also
            strips provider bookkeeping markers — injected rather than imported
            because `finance.domain` must not depend on `finance.ingestion`
            (ARCHITECTURE.md §3). Two copies of a normalisation rule
            is how a matcher starts disagreeing with itself, so the caller
            supplies it rather than this module redefining it.

    Returns:
        A `CategorizeResult`. Layer 7 with `category_id=None` means "queued for
        review", which is a correct outcome, not an error.
    """
    description = normalize(raw_description)

    # Layer 1: exact user rules. Highest precedence.
    for rule in sorted(rules, key=lambda r: (r.priority, r.description_pattern)):
        if rule.matches(description):
            return CategorizeResult(
                category_id=rule.category_id,
                confidence=_CONFIDENCE[1],
                layer_reached=1,
                reason=f"user rule {rule.description_pattern!r}",
            )

    # Layer 2: merchant alias, exact substring.
    for alias in sorted(merchant_aliases, key=lambda a: a.raw_string):
        if alias.matches(description):
            return CategorizeResult(
                category_id=alias.category_id,
                confidence=alias.confidence,
                layer_reached=2,
                reason=f"merchant alias {alias.raw_string!r}",
            )

    merchants = known_merchants or {}

    # Layer 3: curated merchant map, exact substring.
    for merchant in sorted(merchants):
        if merchant and merchant.lower() in description:
            return CategorizeResult(
                category_id=merchants[merchant],
                confidence=_CONFIDENCE[3],
                layer_reached=3,
                reason=f"known merchant {merchant!r}",
            )

    # Layer 4: fuzzy trigram match against known merchants.
    fuzzy = _best_fuzzy(description, merchants)
    if fuzzy is not None:
        category_id, merchant = fuzzy
        return CategorizeResult(
            category_id=category_id,
            confidence=_CONFIDENCE[4],
            layer_reached=4,
            reason=f"fuzzy match on {merchant!r}",
        )

    # Layer 5: learned rules arrive in `rules` with is_learned=True, already
    # served by layer 1. Kept as an explicit step so the layer numbering in the
    # proposal maps onto the code.

    # Layer 6: AI fallback is off by default and requires a local model plus an
    # explicit opt-in. The ledger stays correct with it disabled, so there is no
    # seam here to call into.

    # Layer 7: manual review.
    return CategorizeResult(
        category_id=None,
        confidence=Decimal("0.00"),
        layer_reached=7,
        reason="no rule matched — queued for manual review",
    )


def stable_payee_pattern(raw_description: str, *, words: int = 4) -> str:
    """The prefix of a description that identifies the payee across imports.

    Bank descriptions append noise that changes between exports: an order number,
    a terminal id, a card token. An alias built on the whole string stops matching
    the next time the same merchant appears, which the user reads as "the
    learning is broken".

    Two steps, in this order:

    1. **Drop trailing numeric tokens.** "PAYPAL XYZ 1234" and "PAYPAL XYZ 9999"
       are the same payee; the number is the order reference, not the merchant.
       Truncating to a fixed token count cannot do this — it would cut "ALBERT
       HEIJN 1234 AMSTERDAM" mid-name or keep an order number, depending on the
       count chosen.
    2. **Keep at most `words` leading tokens.** Enough to tell Albert Heijn from
       Jumbo, short enough that a changed city or terminal suffix does not break
       the match. A single token would collide on "AMSTERDAM".

    Lowercased, because the result is a *matching key* rather than something to
    display. Storing it verbatim would make "PayPal XYZ" and "PAYPAL XYZ" two
    different aliases for the same payee, and duplicate rows in the alias table
    are exactly what layer 5 exists to avoid.
    """
    tokens = raw_description.lower().split()
    while len(tokens) > 1 and _is_numeric_token(tokens[-1]):
        tokens.pop()
    return " ".join(tokens[:words])


def _is_numeric_token(token: str) -> bool:
    """Whether a token is a bare number, i.e. an order or terminal reference."""
    stripped = token.strip(".,-/")
    return stripped.isdigit()


def learn_from_correction(
    raw_description: str,
    *,
    category_id: int,
    existing_aliases: Sequence[MerchantAlias] = (),
    words: int = 4,
) -> MerchantAlias:
    """Turn one manual correction into a learned merchant alias.

    Layer 5's mechanism, and the reason the review queue drains itself. A
    confirmed correction stores the payee's stable prefix at full confidence, so
    the next occurrence of the same payee resolves without asking again.

    Args:
        raw_description: The description the user just corrected.
        category_id: What they corrected it to.
        existing_aliases: To update rather than duplicate an existing alias.
        words: How many leading tokens identify the payee.

    Returns:
        The alias to store — an updated copy of an existing one, or a new one.
        The caller writes it; nothing here touches a database.
    """
    pattern = stable_payee_pattern(raw_description, words=words).lower()
    if not pattern:
        msg = "Cannot learn a category from an empty description"
        raise ValueError(msg)

    for alias in existing_aliases:
        if alias.raw_string.strip().lower() == pattern:
            return MerchantAlias(
                raw_string=alias.raw_string,
                category_id=category_id,
                confidence=Decimal("1.00"),
            )
    return MerchantAlias(
        raw_string=pattern,
        category_id=category_id,
        confidence=Decimal("1.00"),
    )
