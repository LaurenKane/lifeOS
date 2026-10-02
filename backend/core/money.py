"""Money — signed BIGINT minor units. Never floats.

The project's loudest rule (ARCHITECTURE.md section 6): an amount is an integer
number of minor units, and `currency.decimals` is the authoritative exponent.
`amount / 10**decimals` is the human-readable value. There is no
`NUMERIC(18,2)` anywhere and no separate "non-standard currency" whitelist —
JPY (0), BTC (8) and ETH (18) are rows in the currency table like any other.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

# Minor-unit scale per exponent. Computed, never tabulated.
_MAX_DECIMALS: Final = 18


@dataclass(frozen=True, kw_only=True)
class Currency:
    """ISO 4217 currency code plus the decimal exponent for its minor units.

    `decimals` mirrors the `currency.decimals` column and is the only authority
    for how to interpret a minor-unit integer. It is deliberately NOT derived
    from the code: a future currency may be added with any exponent, and a
    historical redefinition must not silently reinterpret stored amounts.
    """

    code: str
    decimals: int = 2
    name: str | None = None
    is_active: bool = True

    def __post_init__(self) -> None:
        if len(self.code) != 3 or not self.code.isalpha():
            msg = f"Currency code must be 3 letters, got {self.code!r}"
            raise ValueError(msg)
        if not 0 <= self.decimals <= _MAX_DECIMALS:
            msg = f"decimals must be 0..{_MAX_DECIMALS}, got {self.decimals}"
            raise ValueError(msg)

    @property
    def scale(self) -> int:
        """10**decimals — the divisor between minor units and major units."""
        return int(10**self.decimals)

    @property
    def has_minor_units(self) -> bool:
        """False for zero-decimal currencies (JPY), where minor == major."""
        return self.decimals > 0

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, kw_only=True)
class Money:
    """An amount in signed minor units, with the currency that gives it meaning.

    Positive is a credit (money in), negative a debit (money out). Never store
    a float: `Money(amount=10.0, ...)` is not representable because `amount` is
    an `int`. Convert at the edges with `from_decimal` / `to_decimal`.
    """

    amount: int
    currency: Currency

    def __post_init__(self) -> None:
        # A bool is an int subclass; reject it so `Money(amount=True)` is not a
        # silent 1 minor unit.
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            msg = f"Money.amount must be int minor units, got {type(self.amount)}"
            raise TypeError(msg)

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(amount=0, currency=currency)

    def _require_same_currency(self, other: Money, op: str) -> None:
        if self.currency != other.currency:
            msg = (
                f"Cannot {op} Money in different currencies: "
                f"{self.currency.code} vs {other.currency.code}"
            )
            raise ValueError(msg)

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other, "add")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other, "subtract")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)

    def __mul__(self, factor: int) -> Money:
        """Scale by an integer factor only.

        Deliberately no float multiplier: `Money * 0.5` invites a float into the
        ledger. Use `Money.from_decimal` and re-quantise if a ratio is needed.
        """
        if isinstance(factor, bool) or not isinstance(factor, int):
            msg = f"Money factor must be int, got {type(factor)}"
            raise TypeError(msg)
        return Money(amount=self.amount * factor, currency=self.currency)

    __rmul__ = __mul__

    @classmethod
    def from_decimal(cls, value: Decimal, currency: Currency) -> Money:
        """Quantise a decimal major-unit value into minor units.

        The single sanctioned entry point for turning a parsed "12.34" into
        storage. Rounds half-up, which is what a human reading a bank statement
        expects, and is applied exactly once — at the edge.
        """
        quantised = value.quantize(Decimal(1).scaleb(-currency.decimals), ROUND_HALF_UP)
        return cls(amount=int(quantised.scaleb(currency.decimals)), currency=currency)

    def to_decimal(self) -> Decimal:
        """Exact major-unit decimal. Never a float."""
        return Decimal(self.amount).scaleb(-self.currency.decimals)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def abs(self) -> Money:
        return Money(amount=abs(self.amount), currency=self.currency)

    def __str__(self) -> str:
        sign = "-" if self.amount < 0 else ""
        major, minor = divmod(abs(self.amount), self.currency.scale)
        if not self.currency.has_minor_units:
            return f"{sign}{major}"
        return f"{sign}{major}.{minor:0{self.currency.decimals}d}"

    def __repr__(self) -> str:
        return f"Money({str(self)} {self.currency.code})"
