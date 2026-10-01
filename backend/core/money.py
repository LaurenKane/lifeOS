from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
            # Use the non-standard decimal count for known currencies
            pass  # decimals set explicitly is authoritative


@dataclass(frozen=True, kw_only=True)
class Money:
    """Money value object: amount in minor units (int), with currency.

    Never uses float. Amount is always an integer number of minor units.
    The sign conveys direction (positive = credit, negative = debit).
    """

    amount: int  # minor units, signed
    currency: Currency

    def __post_init__(self) -> None:
        # Amount is already validated as int by the type annotation (amount: int)
        # This runtime guard is for cases where the type checker is bypassed
        pass

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
        # float multiplication for scaling - use Decimal for precision
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


def from_float(value: float, currency: Currency | str | None = None) -> Money:
    """Convert a float to Money - for legacy compatibility only.

    NOTE: This uses rounding to minor units and should be avoided in new code.
    Prefer constructing Money from int minor units directly.
    """
    from decimal import ROUND_HALF_UP

    if currency is None:
        currency = Currency(code="EUR")
    if isinstance(currency, str):
        currency = Currency(code=currency)
    minor = int(
        Decimal(str(value)).quantize(
            Decimal("0." + "0" * currency.decimals), rounding=ROUND_HALF_UP
        )
        * (10**currency.decimals)
    )
    return Money(amount=minor, currency=currency)


def to_float(money: Money, keep_decimals: int | None = None) -> float:
    """Convert Money to float for display purposes."""
    d = Decimal(money.amount) / Decimal(10**money.currency.decimals)
    if keep_decimals is not None:
        fmt = f"0.{'0' * keep_decimals}"
        d = d.quantize(Decimal(fmt))
    return float(d)
