"""finance's public API — the ONLY export surface.

Other modules, including the frontend's generated client, may depend on this
module and nothing else below it. `finance.domain` is PRIVATE and
`finance.ingestion` is an implementation detail.

What may appear here:
    - Protocols (interfaces, never implementations)
    - Read-only Pydantic schemas

What may not:
    - business logic (that is `finance.domain.services`)
    - database access (that is `finance.api.routes`)
    - re-exports of anything under `finance.domain` or `finance.ingestion`

Every schema is frozen. A consumer cannot mutate a shared response object, and
cannot accidentally build one and hand it to a writer.

Identifier types. Every ledger/record identifier below is an `int`, because the
tables hand them out as `BIGSERIAL` and typing them `str` would be a fiction M1
cannot honour: a `str` id is either coerced at every boundary or compared against
one and silently never matches. A non-id identifier — a fingerprint, an ISO 4217
code, a provider's own reference — stays `str`, and so does anything that is
genuinely optional, which is `int | None` rather than a `""` sentinel.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from core.money import Currency, Money
from pydantic import BaseModel, ConfigDict

__all__ = [
    "AccountNature",
    "AccountSummary",
    "AccountType",
    "CategoryKind",
    "CategorySummary",
    "Currency",
    "Money",
    "RawRecord",
    "TransactionStatus",
    "TransactionSummary",
]


# ── Enumerations ────────────────────────────────────────────────────
# These are StrEnum so they serialise as their own value in OpenAPI and JSON
# without a custom encoder, and compare equal to the plain string a TS client
# sends.


class AccountType(StrEnum):
    """The account taxonomy. Asset and liability are separate fields on purpose:

    direction + signed amounts is a self-contradictory encoding (see
    docs/adr/0005-schema-ownership.md).
    """

    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"
    LOAN = "loan"
    MORTGAGE = "mortgage"


class AccountNature(StrEnum):
    """First-class balance sign. Drives the net-worth arithmetic."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"


class TransactionStatus(StrEnum):
    """Pipeline state, stored on SourceRecord (never on JournalEntry).

    One JournalEntry can be produced by several SourceRecords over time — a
    pending row, then the booked row that updates it — so the state lives on the
    raw record and the canonical entry carries none.
    """

    IMPORTED = "imported"
    PENDING = "pending"
    POSTED = "posted"
    DUPLICATE = "duplicate"


class CategoryKind(StrEnum):
    """What a category is for. Drives sign convention and budget eligibility."""

    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    INVESTMENT = "investment"


# ── Read-only schemas ───────────────────────────────────────────────
# ConfigDict(frozen=True) is what makes these genuinely read-only. extra=
# "forbid" stops a client smuggling unknown fields into a response model.


class _ReadOnly(BaseModel):  # type: ignore[explicit-any]
    """Base for every exported schema: frozen, strict, and closed."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AccountSummary(_ReadOnly):  # type: ignore[explicit-any]
    """An account as other modules see it. No credentials, no provider IDs."""

    id: int
    name: str
    currency: str
    account_type: AccountType
    account_nature: AccountNature
    is_active: bool = True
    is_hidden: bool = False
    sort_order: int = 0
    system_role: str | None = None


class TransactionSummary(_ReadOnly):  # type: ignore[explicit-any]
    """One source record and where it ended up.

    `raw_amount` is signed minor units. `journal_entry_id` is None until the
    normalizer has run — an imported-but-unprocessed row is queryable on
    purpose, so a failed batch can be inspected and retried.

    `learned` is True only on the PATCH response that taught the system: the
    correction was stored as a learned rule in the same transaction. Every
    other response carries False, because nothing was learned there.
    """

    id: int
    account_id: int
    fingerprint: str
    raw_description: str
    raw_amount: int
    raw_currency: str
    raw_date: date
    status: TransactionStatus
    journal_entry_id: int | None = None
    transfer_match_id: int | None = None
    category_id: int | None = None
    learned: bool = False


class CategorySummary(_ReadOnly):  # type: ignore[explicit-any]
    """A category. `is_system` rows are seeded and not user-editable."""

    id: int
    name: str
    kind: CategoryKind
    is_system: bool = False


class RawRecord(_ReadOnly):  # type: ignore[explicit-any]
    """One source row, provider-agnostic.

    Every adapter emits this shape regardless of source: that is the whole
    point (docs/adr/0002-import-provider-enum.md). Only the IdentityResolver is
    provider-aware. `raw_data` is the exact row as received and is immutable
    forever - the `raw_data_immutable` rule, now enforced by a database trigger
    (docs/adr/0006-balance-trigger-and-db-invariants.md).

    `account_id` is None when the row has not been attributed to an account
    yet. It used to be `""` for exactly that case, which was a sentinel
    pretending to be a value: an empty string is a legal `str` id, and every
    consumer then had to decide for itself whether it meant "unknown".
    """

    account_id: int | None = None
    description: str
    amount_minor: int
    currency: str
    booked_date: date
    value_date: date | None = None
    provider_txn_id: str | None = None
    pending: bool = False
    line_number: int = 0
    raw_data: dict[str, str | int | float | bool | None] = {}


# ── Convenience re-exports ──────────────────────────────────────────
# Money and Currency are shared primitives from `core`, not domain internals, so
# re-exporting them here leaks nothing: a consumer that may only import this
# module still gets the types its arithmetic needs.
