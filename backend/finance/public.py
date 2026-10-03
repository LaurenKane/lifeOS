"""finance's public API — the ONLY export surface.

Other modules, including the frontend's generated client, may depend on this
module and nothing else below it. `finance.domain` is PRIVATE and
`finance.ingestion` is an implementation detail.

What may appear here:
    - Protocols (interfaces, never implementations)
    - Read-only Pydantic schemas

What may not:
    - business logic (that is `finance.domain.services`)
    - database access (that is `finance.api.routes` / `finance.background`)
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
from typing import Protocol, runtime_checkable

from core.money import Currency, Money
from pydantic import BaseModel, ConfigDict

__all__ = [
    "AccountNature",
    "AccountSummary",
    "AccountType",
    "CategoryKind",
    "CategorySummary",
    "DedupeOutcome",
    "FingerprintResult",
    "ICategoryClassifier",
    "IDeduplicator",
    "ITransferMatcher",
    "MoneyLike",
    "RawRecord",
    "TransactionStatus",
    "TransactionSummary",
    "TransferLink",
]


# ── Enumerations ────────────────────────────────────────────────────
# These are StrEnum so they serialise as their own value in OpenAPI and JSON
# without a custom encoder, and compare equal to the plain string a TS client
# sends.


class AccountType(StrEnum):
    """The account taxonomy. Asset and liability are separate fields on purpose:

    direction + signed amounts is a self-contradictory encoding (see
    ARCHITECTURE-PROPOSAL.md section E).
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


class TransactionSummary(_ReadOnly):  # type: ignore[explicit-any]
    """One source record and where it ended up.

    `raw_amount` is signed minor units. `journal_entry_id` is None until the
    normalizer has run — an imported-but-unprocessed row is queryable on
    purpose, so a failed batch can be inspected and retried.
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


class CategorySummary(_ReadOnly):  # type: ignore[explicit-any]
    """A category. `is_system` rows are seeded and not user-editable."""

    id: int
    name: str
    kind: CategoryKind
    is_system: bool = False


class FingerprintResult(_ReadOnly):  # type: ignore[explicit-any]
    """A computed Tier-3 fingerprint.

    Exposed so another module can check a fingerprint it computed elsewhere
    against ours without importing `finance.ingestion`.
    """

    fingerprint: str
    occurrence_index: int = 1


class DedupeOutcome(_ReadOnly):  # type: ignore[explicit-any]
    """What the resolver decided about one incoming record.

    `duplicate_of` names the canonical record when `is_duplicate` is True.
    `needs_review` marks the 0.50-0.85 confidence band: the pipeline creates the
    transaction AND queues it, rather than guessing a merge.
    """

    is_duplicate: bool
    tier: int
    duplicate_of: int | None = None
    confidence: float = 0.0
    needs_review: bool = False


class TransferLink(_ReadOnly):  # type: ignore[explicit-any]
    """A matched pair of journal lines, both sides of the same movement."""

    outbound_entry_id: int
    inbound_entry_id: int
    match_method: str
    confidence: float


class RawRecord(_ReadOnly):  # type: ignore[explicit-any]
    """One source row, provider-agnostic.

    Every adapter emits this shape regardless of source: that is the whole
    point (ARCHITECTURE-PROPOSAL.md section C). Only the IdentityResolver is
    provider-aware. `raw_data` is the exact row as received and is immutable
    forever — the `raw_data_immutable` invariant.

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


# ── Protocols ───────────────────────────────────────────────────────
# runtime_checkable so a caller can isinstance-check a duck-typed
# implementation without importing the class.


@runtime_checkable
class IDeduplicator(Protocol):
    """Decides whether an incoming record duplicates an existing one.

    Implementations own all three tiers (ARCHITECTURE-PROPOSAL.md section G).
    """

    def identify(self, record: RawRecord) -> DedupeOutcome:
        """Classify one incoming record against what is already stored."""
        ...


@runtime_checkable
class ITransferMatcher(Protocol):
    """Decides whether two journal lines are the two halves of one movement.

    Implementations must default to "create new + queue for review" over
    "guess and merge".
    """

    def match(self, outbound_id: int, inbound_id: int) -> TransferLink | None:
        """Return the link if these two lines are a transfer pair, else None."""
        ...


@runtime_checkable
class ICategoryClassifier(Protocol):
    """Assigns a category, or says it cannot.

    A `None` category with `needs_review` is a valid, expected answer.
    """

    def classify(self, record: RawRecord) -> CategorySummary | None:
        """Return the chosen category, or None to queue for manual review."""
        ...


# ── Convenience types ───────────────────────────────────────────────
# Money and Currency are shared primitives from `core`, not domain internals, so
# re-exporting them here leaks nothing. `MoneyLike` is the alias other modules
# should import rather than reaching past this surface.
MoneyLike = Money
CurrencyLike = Currency


def is_valid_currency_code(code: str) -> bool:
    """Whether `code` looks like an ISO 4217 alphabetic code.

    Shape only. Whether the currency is one we actually hold is a database
    question, answered against the `currency` table.
    """
    return len(code) == 3 and code.isalpha() and code.isupper()
