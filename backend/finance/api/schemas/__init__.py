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
from decimal import Decimal

from core.money import Currency
from pydantic import BaseModel, ConfigDict, Field

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


class ManualTransactionRequest(_Write):  # type: ignore[explicit-any]
    """A user-entered transaction.

    `amount` is a string, not a JSON number. A JSON number would arrive as a
    float, and a float in this project is a bug regardless of the client — the
    wire format has to make the mistake impossible rather than merely discouraged.

    `booked_date` is required and never defaulted server-side.
    """

    account_id: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=500)
    amount: str = Field(
        description="Major-unit decimal as a string, e.g. '12.34'. Never a float.",
    )
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    booked_date: date
    value_date: date | None = None

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)


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
        description="One of: enable_banking, amex_csv, amex_pdf, revolut_csv, manual"
    )
    import_method: str = Field(description="One of: api, csv, pdf, manual")
    account_id: str | None = None
    source_filename: str | None = None
    force: bool = False
