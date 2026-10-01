"""Money and Currency value objects for the finance module.

PRIVATE — only finance.public may import from this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, kw_only=True)
class Currency:
    """ISO 4217 currency code with decimal precision metadata."""

    code: str
    name: str | None = None
    decimals: int = 2
    is_active: bool = True

    # Whitelist of known currencies with non-standard decimals
    _NON_STANDARD_DECIMALS: ClassVar[set[str]] = {
        "JPY",  # 0 decimals
        "BTC",  # 8 decimals (satoshi)
        "ETH",  # 18 decimals (wei)
    }

    def __post_init__(self) -> None:
        if len(self.code) != 3:
            msg = f"Currency code must be 3 letters, got '{self.code}'"
            raise ValueError(msg)
        code_upper = self.code.upper()
        if self.decimals == 2 and code_upper in self._NON_STANDARD_DECIMALS:
            pass  # decimals set explicitly is authoritative

    @property
    def is_minor_unit_zero(self) -> bool:
        return self.decimals == 0


@dataclass(frozen=True, kw_only=True)
class Money:
    """Money value object: amount in minor units (int), with currency.

    Never uses float. Amount is always an integer number of minor units.
    The sign conveys direction (positive = credit, negative = debit).
    """

    amount: int  # minor units, signed
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int):
            msg = f"Money amount must be int (minor units), got {type(self.amount)}"
            raise TypeError(msg)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            msg = f"Cannot add Money in different currencies: {self.currency} vs {other.currency}"
            raise ValueError(msg)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            msg = f"Cannot subtract Money in different currencies: {self.currency} vs {other.currency}"
            raise ValueError(msg)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, multiplier: int | float) -> Money:
        if isinstance(multiplier, int):
            return Money(amount=self.amount * multiplier, currency=self.currency)
        from decimal import Decimal

        d = (
            Decimal(str(multiplier))
            * Decimal(self.amount)
            / Decimal(10**self.currency.decimals)
        )
        return Money(amount=int(d), currency=self.currency)

    def __rmul__(self, multiplier: int | float) -> Money:
        return self.__mul__(multiplier)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.amount == other.amount and self.currency == other.currency

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))

    def __str__(self) -> str:
        sign = "-" if self.amount < 0 else ""
        abs_amount = abs(self.amount)
        integer_part = abs_amount // (10**self.currency.decimals)
        fractional_part = abs_amount % (10**self.currency.decimals)
        fmt = f"{sign}{integer_part}.{fractional_part:0{self.currency.decimals}d}"
        return fmt

    def __repr__(self) -> str:
        return f"Money(amount={self.amount}, currency=Currency(code='{self.currency.code}'))"

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

    def formatted(self) -> str:
        """Human-readable formatted string."""
        return str(self)


def from_amount_str(amount_str: str, currency: Currency) -> Money:
    """Parse a Money from a string like '12.34' or '-5.67' in the given currency."""
    from decimal import ROUND_HALF_UP, Decimal

    d = Decimal(amount_str).quantize(
        Decimal("0." + "0" * currency.decimals), rounding=ROUND_HALF_UP
    )
    minor = int(d * (10**currency.decimals))
    return Money(amount=minor, currency=currency)
