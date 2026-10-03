"""finance.api.schemas - the Pydantic contract.

Re-exports `finance.public`'s read-only schemas plus the request models that
appear in the OpenAPI spec. The frontend's TypeScript types are generated from
this surface by `scripts/generate_types.py`; a change here is a contract change.

Imports are absolute. `core` and `finance` are sibling packages under the
`backend/` import root, so `from core.datetime import ...` is correct and
`from ...core` would resolve to a package that does not exist.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from core.money import Currency
from pydantic import BaseModel, ConfigDict, Field, field_validator

from finance.public import (
    AccountNature,
    AccountSummary,
    AccountType,
    CategoryKind,
    CategorySummary,
    DedupeOutcome,
    FingerprintResult,
    RawRecord,
    TransactionStatus,
    TransactionSummary,
    TransferLink,
)

__all__ = [
    "AccountCreateRequest",
    "AccountNature",
    "AccountSummary",
    "AccountType",
    "CategoryKind",
    "CategorySummary",
    "CurrencyInfo",
    "DedupeOutcome",
    "FingerprintResult",
    "ImportRequest",
    "ManualTransactionRequest",
    "ManualTransactionUpdate",
    "Provider",
    "ProviderInfo",
    "RawRecord",
    "TransactionStatus",
    "TransactionSummary",
    "TransferLink",
]


class _Write(BaseModel):  # type: ignore[explicit-any]
    """Base for request models: mutable, closed to unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CurrencyInfo(_Write):  # type: ignore[explicit-any]
    """A currency and the exponent that gives its minor units meaning."""

    code: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    decimals: int = Field(ge=0, le=18)
    name: str | None = None

    def to_domain(self) -> Currency:
        return Currency(code=self.code, decimals=self.decimals, name=self.name)


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
    """

    name: str = Field(min_length=1, max_length=200)
    account_type: AccountType
    account_nature: AccountNature
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    is_hidden: bool = False


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

    Both are LEDGER facts, not evidence:

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
    """

    category_id: int | None = Field(default=None, ge=1)
    entry_date: date | None = None


class Provider(_Write):  # type: ignore[explicit-any]
    """One supported import source."""

    kind: str
    import_method: str
    accepts_upload: bool


class ProviderInfo(_Write):  # type: ignore[explicit-any]
    """Every supported import source, so a client does not hardcode the list."""

    providers: list[Provider]


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
