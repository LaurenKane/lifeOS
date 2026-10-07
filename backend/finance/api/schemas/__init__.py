"""finance.api.schemas - the Pydantic contract.

Re-exports `finance.public`'s read-only schemas plus the request models that
appear in the OpenAPI spec. The frontend's TypeScript types are *supposed* to be
generated from this surface, but that generation is not implemented
(ARCHITECTURE.md §7); a change here is a contract change nothing checks.

Imports are absolute. `core` and `finance` are sibling packages under the
`backend/` import root, so `from core.datetime import ...` is correct and
`from ...core` would resolve to a package that does not exist.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from finance.public import (
    AccountNature,
    AccountSummary,
    AccountType,
    CategoryKind,
    CategorySummary,
    RawRecord,
    TransactionStatus,
    TransactionSummary,
)

__all__ = [
    "AccountCreateRequest",
    "AccountNature",
    "AccountSummary",
    "AccountType",
    "BudgetCreateRequest",
    "BudgetSummary",
    "BudgetUpdateRequest",
    "CashflowBucket",
    "CategoryCreateRequest",
    "CategoryKind",
    "CategoryRuleCreateRequest",
    "CategoryRuleSummary",
    "CategorySummary",
    "ConfirmTransferRequest",
    "ImportBatchSummary",
    "ImportRequest",
    "ImportSummary",
    "ManualTransactionRequest",
    "ManualTransactionUpdate",
    "MerchantAliasCreateRequest",
    "MerchantAliasSummary",
    "MerchantAliasUpdateRequest",
    "MerchantCreateRequest",
    "MerchantSummary",
    "MerchantUpdateRequest",
    "NetWorthPoint",
    "Provider",
    "ProviderInfo",
    "RawRecord",
    "SpendByCategoryPoint",
    "TransactionStatus",
    "TransactionSummary",
    "TransferReviewCandidateOut",
    "TransferReviewItem",
    "TransferReviewLegOut",
    "TransferReviewStats",
]


class _Write(BaseModel):  # type: ignore[explicit-any]
    """Base for request models: mutable, closed to unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccountCreateRequest(_Write):  # type: ignore[explicit-any]
    """A new account.

    No `id`. The database hands it out (BIGSERIAL); a client-supplied primary key
    is a client-chosen one, and two clients choosing the same value produce a
    collision that reads as a duplicate account rather than as a bug.

    `account_type` and `account_nature` are both required and neither is
    defaulted. `account_nature` is the field the ledger's arithmetic asks, and
    guessing it would silently put an account on the wrong side of net worth;
    `account_type` is the provider's own vocabulary, which an adapter has to be
    able to write even when the account has no provider yet.

    `currency` is validated against the `currency` table rather than trusted: it
    is `journal_line.currency`'s source, and an account whose currency is wrong
    re-denominates every posting made through it.

    `system_role` designates this account as the canonical system-purpose account
    for manual expenses. The only value accepted today is 'system_expense'. It is
    optional and explicit: the role is NEVER inferred from the account's name.
    """

    name: str = Field(min_length=1, max_length=200)
    account_type: AccountType
    account_nature: AccountNature
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    is_hidden: bool = False
    system_role: str | None = Field(default=None, pattern=r"^system_expense$")


class ManualTransactionRequest(_Write):  # type: ignore[explicit-any]
    """A user-entered transaction.

    `amount` is a string, not a JSON number. A JSON number would arrive as a
    float, and a float in this project is a bug regardless of the client — the
    wire format has to make the mistake impossible rather than merely discouraged.

    `account_id` is a ledger id, so it is an `int`. Required and never
    defaulted: a manual entry that cannot be attributed to an account is not a
    transaction the ledger can hold.

    `booked_date` is required and never defaulted server-side.

    **`amount` is SIGNED.** Debits are negative, credits positive, exactly as in
    `finance/domain/models/ledger.py` — so `"40.50"` is money IN and `"-40.50"`
    is money OUT. There is no server-side sign flip and no `as_debit` field: the
    Amex PDF adapter's flip is exactly the kind of hidden convention this project
    has already been bitten by
    (`docs/adr/0003-import-decisions-real-export.md` Decision 1), and a client
    that wants to express "money out" should have to say so in the payload.

    `counter_account_id` is optional and defaults to the seeded system expense
    account; see `finance.domain.services.manual_posting` for the convention and
    for what "cannot be resolved" means.
    """

    account_id: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=500)
    amount: str = Field(
        description=(
            "Major-unit decimal as a string, SIGNED: '12.34' is money in, "
            "'-12.34' is money out. Never a float."
        ),
    )
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    booked_date: date
    value_date: date | None = None
    counter_account_id: int | None = Field(default=None, ge=1)

    @field_validator("amount")
    @classmethod
    def _amount_must_be_a_finite_decimal(cls, value: str) -> str:
        """Reject a non-numeric amount here, at the edge.

        `amount_decimal` below would otherwise raise `decimal.InvalidOperation`
        from inside the handler, and an unhandled exception there is a 500 with a
        stack trace — for what is a malformed request body. This turns it into a
        422 with the offending value in the message.
        """
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            msg = f"amount must be a decimal string, got {value!r}"
            raise ValueError(msg) from exc
        if not parsed.is_finite():
            msg = f"amount must be finite, got {value!r}"
            raise ValueError(msg)
        return value

    @property
    def amount_decimal(self) -> Decimal:
        """The amount as a `Decimal`. Safe to call: validated above."""
        return Decimal(self.amount)


class ManualTransactionUpdate(_Write):  # type: ignore[explicit-any]
    """The only two things about a transaction a user is allowed to change.

    Both are LEDGER facts, not evidence (`learn` below is a flag about the
    correction, not a third editable fact):

    * `category_id` — what categorisation means. It lives on the
      `journal_line`, because `categorized` is derived from there
      (`category_id IS NOT NULL`), and setting it is what moves a transaction
      out of the uncategorized queue.
    * `entry_date` — the accounting date on the `journal_entry`.

    What is deliberately absent is the evidence. `raw_description` and
    `raw_data` are frozen by the `raw_data_immutable` trigger, and the amount,
    the currency and the booking date are not editable either: the raw side is
    what a replay reads, so changing it would make a replay produce a different
    transaction from the same input. A correction is a reversal — a new balanced
    entry — not an edit.

    Both fields are optional and an absent field means "leave alone", so this is
    a PATCH and not a PUT: a PUT would make an unspecified `category_id`
    indistinguishable from a deliberate "uncategorise this".

    `learn` teaches the system from this correction: when a `category_id` is
    set and `learn` is true, the description's stable payee pattern is stored
    as a learned rule in the same transaction, so the next identical payee
    auto-categorizes. Default off is deliberate — a correction must not
    silently teach the system; the caller says so explicitly.
    """

    category_id: int | None = Field(default=None, ge=1)
    entry_date: date | None = None
    learn: bool = False


class Provider(_Write):  # type: ignore[explicit-any]
    """One supported import source."""

    kind: str
    import_method: str
    accepts_upload: bool


class ProviderInfo(_Write):  # type: ignore[explicit-any]
    """Every supported import source, so a client does not hardcode the list."""

    providers: list[Provider]


class ImportSummary(BaseModel):  # type: ignore[explicit-any]
    """What one import produced. Read-only in practice, frozen to say so.

    `frozen=True` and `extra="forbid"` stated here rather than inherited, because
    the only base in this module is `_Write` and that is the wrong one: it is
    mutable, and it exists for request bodies. A response model that could be
    mutated in place is a contract nobody can rely on.

    `created`, `duplicated` and `failed` are the counts that matter, and they
    describe WHAT PERSISTED, not what the parser read. Those are different claims
    and the difference is the whole reason this schema carries them separately
    from `record_count`: a parser can return 40 rows with no failures while the
    writer posts 39 of them and refuses one, because that one was a card payment
    with no paying account (`docs/adr/0007-imported-card-payment-is-a-transfer
    .md`). `record_count` is the parse; the three counts are the ledger.

    They do not always sum to `record_count`. `created + duplicated + failed`
    does — a row is in exactly one of the three — and a client that wants "did
    everything land" should ask whether `created + duplicated` equals
    `record_count`, not whether the status says `completed`.

    `status` follows the same rule: it is the `import_batch.status` that was
    written, so `partial` here means rows were persisted and some were not
    posted, and `completed` means every parsed row is now in the ledger. The
    frontend's imports slice consumes none of the three counts today, and nothing
    in CI catches the drift (ARCHITECTURE.md §7) — which is worth stating
    rather than leaving as a surprise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    import_method: str
    status: str
    record_count: int
    source_checksum: str | None = None
    created: int = 0
    duplicated: int = 0
    failed: int = 0
    failures: list[str] = []


class ImportBatchSummary(BaseModel):  # type: ignore[explicit-any]
    """One persisted import run, as the batch list reads it.

    Read-only and frozen like every other response model here. `provider` and
    `status` are plain strings rather than enums, for the same reason
    `ImportSummary` keeps them that way: the CHECK values live in the database,
    and a closed enum here would turn a stored value this build has not heard
    of into a 500 on a read-only list.

    `status` carries whatever was WRITTEN, including `partial` — the normal
    outcome of a real import — so a client must accept every value the column
    allows rather than the four the batch-creation path starts from.

    The filename rides as `sourceFilename` on the wire (a serialization alias;
    the attribute stays snake_case like every other field in this module)
    because that is the name the frontend's `ImportBatchSchema` parses. It is
    None when no file was recorded — a manual batch never had one — and the
    client renders a fallback rather than a blank.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    provider: str
    status: str
    source_filename: str | None = Field(
        default=None, serialization_alias="sourceFilename"
    )


class ImportRequest(_Write):  # type: ignore[explicit-any]
    """A request to run an import.

    `provider` and `import_method` are constrained by the adapter that will run,
    not just by this schema — the CHECK values live in the database, and this
    mirrors them so a bad request fails at the edge with a useful message.
    """

    provider: str = Field(
        description=(
            "One of: enable_banking, amex_pdf, rabobank_pdf, revolut_pdf, manual"
        )
    )
    import_method: str = Field(description="One of: api, csv, pdf, manual")
    account_id: int | None = None
    source_filename: str | None = None
    force: bool = False


# ──────────────────────────────────────────────────────────────────────
# Analytics response schemas
# ──────────────────────────────────────────────────────────────────────
#
# Every amount below is an INTEGER count of EUR cents, never a decimal and
# never a float. That is not a formatting preference: it is the only shape the
# frontend can render (`ARCHITECTURE.md` §6), and a second money type on the
# wire is a second place for rounding to disagree with the ledger.
#
# The sign is the ledger's own. A negative amount is money out, which is what
# `Amount` already reads that way in the UI — so an expense arrives already
# coloured, signed and labelled correctly, with no per-report convention to
# learn.


class NetWorthPoint(BaseModel):  # type: ignore[explicit-any]
    """One day's net worth. Assets plus liabilities, in EUR cents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: ISO 8601 `YYYY-MM-DD`.
    date: str
    #: Signed. Negative means the user owes more than they hold.
    net_worth: int


class SpendByCategoryPoint(BaseModel):  # type: ignore[explicit-any]
    """One category's spend. Expenses are negative; income is positive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category_id: int
    category_name: str
    kind: CategoryKind
    amount: int


class CashflowBucket(BaseModel):  # type: ignore[explicit-any]
    """One period's cash movement.

    `income` and `expense` are both positive magnitudes — "€42 spent" reads
    better than "-42" on a chart — while `net` keeps the sign, so a deficit is
    negative. That asymmetry is deliberate and is the one place these three
    schemas do not share a convention.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: `YYYY-MM-DD` for day, the Monday for week, `YYYY-MM` for month.
    period: str
    income: int
    expense: int
    net: int


# ──────────────────────────────────────────────────────────────────────
# Transfer-review queue schemas (M4)
# ──────────────────────────────────────────────────────────────────────
#
# The JSON contract for `GET /review/transfers` and its three decisions.
# Field names are snake_case, matching every other schema in this module.
# Amounts are integer minor units, like the analytics schemas above — never
# a decimal and never a float.


class TransferReviewLegOut(BaseModel):  # type: ignore[explicit-any]
    """One side of a queued transfer question, fully described.

    Read-only, frozen and closed like every other response model here: a
    response that could be mutated in place is a contract nobody can rely on.
    `description` is the `journal_entry`'s, because the entry carries the
    human text and the line carries only the arithmetic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_line_id: int
    description: str
    amount_minor: int
    currency: str
    booked_date: date
    account_id: int
    account_name: str


class TransferReviewCandidateOut(TransferReviewLegOut):  # type: ignore[explicit-any]
    """A possible incoming half, with the matcher's confidence attached.

    `confidence` is recomputed live from the pure `transfer_match` rule on the
    (outbound, candidate) pair — it is not a stored column, because the linker
    stores no score on the review row. A pair that no longer satisfies the rule
    (e.g. an `entry_date` moved after queueing) reports `0.00`.
    """

    confidence: Decimal


class TransferReviewItem(BaseModel):  # type: ignore[explicit-any]
    """One pending transfer question, with its outbound leg and candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    outbound: TransferReviewLegOut
    candidates: list[TransferReviewCandidateOut]
    reason: Literal["multi_candidate", "low_confidence"]
    created_at: datetime


class TransferReviewStats(BaseModel):  # type: ignore[explicit-any]
    """Queue depth by reason. `total` is the pending count, always the sum."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    multi_candidate: int
    low_confidence: int
    total: int


class ConfirmTransferRequest(_Write):  # type: ignore[explicit-any]
    """Which candidate a confirm decision picks.

    Required when the review holds more than one candidate; the single
    candidate of a `low_confidence` review is used when this is absent. An id
    that is not on the review's stored candidate list is a 400, never a guess.
    """

    candidate_journal_line_id: int | None = None


# ──────────────────────────────────────────────────────────────────────
# Categories and categorization rules
# ──────────────────────────────────────────────────────────────────────
#
# The rule set is plain text on purpose (section I): a substring plus a
# priority integer, readable on one screen and editable by hand. These
# schemas are that text on the wire — never the domain `CategoryRule`
# dataclass, which carries matcher behaviour (`matches()`) that has no
# business in a request body or a JSON response.


class CategoryCreateRequest(_Write):  # type: ignore[explicit-any]
    """A user-created category.

    No `id`: the database hands it out, and a client-chosen id would collide
    the way account ids do (see `AccountCreateRequest`). `kind` is the
    closed `CategoryKind` enum, so an unknown kind fails at the edge with a
    422 rather than at the migration's CHECK with a 500-shaped surprise.
    `is_system` is absent on purpose: only seeded rows carry it, and a
    request must not be able to mint one.
    """

    name: str = Field(min_length=1, max_length=200)
    kind: CategoryKind
    parent_id: int | None = Field(default=None, ge=1)


class CategoryRuleCreateRequest(_Write):  # type: ignore[explicit-any]
    """A hand-authored categorization rule.

    `description_pattern` is a plain substring, stripped of surrounding
    whitespace on the way in — so a blank pattern fails `min_length` here
    with a 422 instead of landing as a row that matches nothing (or, worse,
    everything). `priority` defaults to 100, the hand-rule tier that always
    outranks learned rules (see `LEARNED_RULE_PRIORITY`). `is_learned` is
    absent on purpose: a rule authored here is never "learned", and the
    router forces that rather than trusting the body.
    """

    description_pattern: str = Field(min_length=1)
    category_id: int = Field(ge=1)
    priority: int = 100


class CategoryRuleSummary(BaseModel):  # type: ignore[explicit-any]
    """One stored rule, hand or learned, as the rule screen reads it.

    Read-only and frozen like every other response model here. `confidence`
    is the `NUMERIC(3,2)` the matcher scores with, rendered as a decimal —
    never a float — because a second money-adjacent float on the wire is a
    second place for rounding to disagree with the ledger.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    description_pattern: str | None
    priority: int
    category_id: int
    is_learned: bool
    confidence: Decimal


# ──────────────────────────────────────────────────────────────────────
# Merchants and merchant aliases (LifeOS-8 layers 2-4 curation)
# ──────────────────────────────────────────────────────────────────────
#
# The rows the matcher reads: `load_known_merchants` resolves merchants to a
# category (layer 3) and `load_aliases` resolves raw strings to a category
# (layer 2). These schemas are those rows on the wire — never the domain
# dataclasses, which carry matcher behaviour (`matches()`) that has no
# business in a request body or a JSON response.
#
# Response models are frozen `BaseModel`s, request models inherit `_Write`:
# the same split the categories section above uses. There is no separate
# `_ReadOnly` base in this module; frozen-plus-forbid IS the read-only idiom.


class MerchantSummary(BaseModel):  # type: ignore[explicit-any]
    """One canonical merchant, as the curation screen reads it.

    `category_id` is None when the name is known but unfiled: such a row
    feeds neither layer 3 nor layer 4, which is the honest answer for a name
    nobody categorised.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    name: str
    category_id: int | None


class MerchantCreateRequest(_Write):  # type: ignore[explicit-any]
    """A new canonical merchant, optionally filed to a category already.

    No `id`: the database hands it out, and a client-chosen id would collide
    the way account ids do (see `AccountCreateRequest`). A blank name fails
    `min_length` here with a 422 instead of landing as a row the substring
    match can never meaningfully use.
    """

    name: str = Field(min_length=1, max_length=500)
    category_id: int | None = Field(default=None, ge=1)


class MerchantUpdateRequest(_Write):  # type: ignore[explicit-any]
    """What a merchant edit may change.

    Both fields optional and an absent field means "leave alone", so this is
    a PATCH and not a PUT: an explicit `category_id: null` CLEARS the
    category (back to known-but-unfiled) while an absent field leaves it —
    read via `model_fields_set`, never via the default. A blank name is
    still a 422.
    """

    name: str | None = Field(default=None, min_length=1, max_length=500)
    category_id: int | None = Field(default=None, ge=1)


class MerchantAliasSummary(BaseModel):  # type: ignore[explicit-any]
    """One stored alias, as the curation screen reads it.

    `confidence` is the `NUMERIC(3,2)` layer 2 scores with, rendered as a
    decimal — never a float — for the same reason `CategoryRuleSummary`
    states: a curated alias is a deliberate mapping, so it defaults to
    1.00 and clears the `is_auto` bar.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    raw_string: str
    merchant_id: int | None
    category_id: int | None
    confidence: Decimal


class MerchantAliasCreateRequest(_Write):  # type: ignore[explicit-any]
    """A new raw-string mapping.

    At least one of `category_id` / `merchant_id` is required — enforced in
    the router, not here, because "at least one of two" is not a shape either
    field carries alone. `confidence` defaults to 1.00: a curated alias is a
    deliberate mapping, not a guess, and must clear layer 2's `is_auto` bar
    of 0.90. A blank `raw_string` fails `min_length` with a 422.
    """

    raw_string: str = Field(min_length=1, max_length=500)
    category_id: int | None = Field(default=None, ge=1)
    merchant_id: int | None = Field(default=None, ge=1)
    confidence: Decimal = Field(
        default=Decimal("1.00"), ge=Decimal("0"), le=Decimal("1")
    )


class MerchantAliasUpdateRequest(_Write):  # type: ignore[explicit-any]
    """What an alias edit may change.

    All fields optional with PATCH semantics: an explicit null clears while
    an absent field leaves the row alone — read via `model_fields_set`. The
    raw string itself is immutable: it is the match key, and renaming it is
    a delete plus a create, not an edit.
    """

    category_id: int | None = Field(default=None, ge=1)
    merchant_id: int | None = Field(default=None, ge=1)
    confidence: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))


# ──────────────────────────────────────────────────────────────────────
# Budgets
# ──────────────────────────────────────────────────────────────────────
#
# The wire shapes behind `GET /api/v1/budgets`, which the frontend budgets
# page already reads (`frontend/src/features/finance/budgets/types.ts`).
# Money is an INTEGER count of minor units like every other amount in this
# module — a budget compared on floats drifts a cent per transaction and
# never looks wrong until the totals disagree (ARCHITECTURE.md §6).


class BudgetSummary(BaseModel):  # type: ignore[explicit-any]
    """One stored budget, as the budgets screen reads it.

    Read-only and frozen like every other response model here.

    `name` is the budgeted CATEGORY's name, not a column: a budget is a limit
    on one category, so the category's own name is the label — reading it
    through the join means renaming the category updates every screen with no
    second copy to drift. `category_id` rides along for the same reason
    `MerchantSummary` carries it: two branches of the tree are each entitled
    to their own category of the same name, so `name` alone cannot address
    the row.

    `amount_minor` rides as `amountMinor` on the wire (a serialization alias),
    because that is the name the frontend's `BudgetSchema` parses — the same
    rule `ImportBatchSummary` states for `sourceFilename`.

    `period` stays a plain string rather than an enum: the CHECK in migration
    0007 owns the closed set, and a stored value this build has not heard of
    must not turn a read-only list into a 500.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    name: str
    category_id: int
    amount_minor: int = Field(serialization_alias="amountMinor")
    currency: str
    period: str


class BudgetCreateRequest(_Write):  # type: ignore[explicit-any]
    """A new budget: one category, one limit, one period.

    No `id` (the database hands it out) and no `name` (see `BudgetSummary`).
    `category_id` must name an existing category — the router checks it and
    answers 404, because a request pointing at nothing is a client mistake
    and the foreign key's own message says less about it.

    `amount_minor` is deliberately NOT bounded here. The `amount > 0` CHECK in
    migration 0007 is the authority, and the router translates its refusal to
    422 — the same rule `routes/categories.py` applies to duplicates: the
    constraint decides, not a pre-check that could grow a second opinion and
    leave the CHECK's own path dead.

    `period` is the closed set the page renders, so an unknown period fails
    at the edge with a 422, before any write — same treatment as a category's
    `kind`.
    """

    category_id: int = Field(ge=1)
    amount_minor: int
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    period: Literal["monthly", "quarterly", "yearly"]


class BudgetUpdateRequest(_Write):  # type: ignore[explicit-any]
    """What a budget edit may change.

    PATCH semantics via `model_fields_set`: absent means "leave alone".
    Nothing here is clearable — a budget with no limit, no currency or no
    period is not a budget — so an explicit `null` is a 422 from the router
    rather than a clear, and these fields read `int | None` only so that the
    wire can express the mistake at all.

    `category_id` is absent on purpose: the category is what the budget is
    ABOUT, so retargeting it is a different budget — create it and delete the
    old one — the same rule `MerchantAliasUpdateRequest` applies to its
    immutable `raw_string`.
    """

    amount_minor: int | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    period: Literal["monthly", "quarterly", "yearly"] | None = None
