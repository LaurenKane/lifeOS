"""normalize - turn provider rows into the one shape the pipeline understands.

Provider-agnostic by construction: every adapter hands over a RawRecord and this
module decides what a canonical transaction looks like. The rules that matter:

- **Amounts become signed minor units.** A provider that reports debits as
  positive numbers and credits as negative gets its sign convention mapped here,
  once, instead of in every consumer.
- **`raw_data` is preserved verbatim.** It is what replay reads from, so it is
  never normalised in place.
- **Normalisation never invents information.** If a field is absent it stays
  None; a missing description is not silently replaced with the payee.

Amount parsing is exact: `Decimal` with the currency's exponent, never float.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from core.datetime import parse_date
from core.money import Currency, Money

__all__ = [
    "NormalizedRecord",
    "AmountSignConvention",
    "normalize_amount",
    "normalize_currency",
    "normalize_description",
    "normalize_european_number",
    "normalize_record",
    "normalize_reference",
]

# Whitespace, including the non-breaking and zero-width characters that arrive
# in bank exports. Collapse runs of these to a single space.
_WHITESPACE: Final = re.compile(r"[\s ]+")

# Trailing provider bookkeeping: a reference block and/or a bare marker
# character. Deliberately anchored at the end and matched case-insensitively, so
# a REF number in the middle of a payee name survives.
#
# THIS IS THE ONE COPY. `fingerprint.py` imports this pattern rather than
# defining its own, and that is the whole point: the two used to hold separate
# copies which silently DIVERGED — this one required a colon in every REF
# alternative while the fingerprint's also stripped the colon-less `#REF 000123`
# and `*REF0123456` forms that Amex actually prints. The same merchant
# description could therefore normalise differently here than it fingerprinted,
# and a card statement and a bank statement of one purchase could produce
# different fingerprints — defeating Tier-3 dedup with no error anywhere, which
# is the exact failure the dedup tiers exist to prevent.
#
# If you need to change what counts as a trailing marker, change it HERE and
# let fingerprint.py pick it up. Do not reintroduce a second copy. The guard
# against that is an identity assertion in `tests/unit/test_fingerprint.py`, not
# a comment.
#
# Examples, all of which must normalise to "starbucks":
#   "Starbucks *REF:0123456"
#   "STARBUCKS  #REF 0123456"
#   "Starbucks * #REF:0123456"
#   "Starbucks*" / "Starbucks #"
_TRAILING_MARKERS: Final = re.compile(
    r"""
    \s*
    (?:
        \#?\s*REF\s*:\s*\S+          # #REF:000123, REF:abc, *REF:abc
      | \#?\s*REF\s+\S+              # #REF 000123 (colon-less variant)
      | \*\s*REF\s*\S+               # *REF0123456
      | KAASACHTELNR\s*:?\s*\S+      # ING customer reference
      | CARD\s*:\s*\S+               # bank card token
      | MNDT\s*\d+                   # SEPA mandate reference
      | \*+                          # bare trailing asterisk
      | \#+                          # bare trailing hash
    )
    \s*
    """,
    re.VERBOSE | re.IGNORECASE,
)


class AmountSignConvention(StrEnum):
    """How a provider encodes debit/credit in its amount field.

    `SIGNED` — negative is money out (the AIS/ISO convention).
    `POSITIVE_IS_DEBIT` — positive is money out (Amex CSV and Revolut CSV).
    """

    SIGNED = "signed"
    POSITIVE_IS_DEBIT = "positive_is_debit"


@dataclass(frozen=True)
class NormalizedRecord:
    """A provider row after normalization, ready for dedup.

    `raw_amount` is minor units and signed under the project convention:
    negative is a debit. `provider_txn_id` stays None for Amex, which supplies
    no stable ID — that is exactly why Tier 3 exists.

    `account_id` is None when the row is not yet attributed to an account. It
    was `""` before, which read as an id and compared equal to every other
    unattributed row.
    """

    account_id: int | None
    description: str
    amount: Money
    booked_date: dt.date
    value_date: dt.date | None = None
    provider_txn_id: str | None = None
    pending: bool = False
    line_number: int = 0
    raw_data: dict[str, str | int | float | bool | None] = field(default_factory=dict)

    @property
    def is_debit(self) -> bool:
        return self.amount.is_negative

    @property
    def is_credit(self) -> bool:
        return self.amount.is_positive


def normalize_european_number(value: str) -> str:
    """Rewrite a European-formatted number into a form `Decimal` accepts.

    Every provider this project imports is European, so amounts arrive as
    `8,50` and sometimes `1.234,56`. `Decimal` rejects both, and silently
    rewriting them inside `normalize_amount` would be worse: `1.234,56` is
    genuinely ambiguous — in an English locale it is 1234.56, in a German one it
    is 1.23456.

    This is explicit and separate on purpose. `normalize_amount` stays strict
    about `8.50`, and the adapters for European providers call this first, so
    which locale a file is in is a decision made in the adapter that knows the
    provider's format rather than a guess made in shared code.

    Args:
        value: The number as the provider wrote it.

    Returns:
        A plain `1234.56`-style string. Anything already in that form is
        returned with whitespace stripped.
    """
    text = value.strip().replace(" ", "").replace(" ", "")
    comma = text.rfind(",")
    dot = text.rfind(".")
    if comma < 0 and dot < 0:
        return text
    # Whichever separator comes last is the decimal mark; the other is a
    # thousands separator. That is what distinguishes English "1,234.56" from
    # European "1.234,56" without having to be told which locale the file is in.
    if comma > dot:
        return text.replace(".", "").replace(",", ".")
    return text.replace(",", "")


def normalize_currency(code: str) -> Currency:
    """Build a Currency from a code.

    Only the code is interpreted; `decimals` defaults to 2 because the currency
    table is the authority and this is the pre-database path. A JPY row must go
    through `Currency(code="JPY", decimals=0)`.
    """
    cleaned = code.strip().upper()
    if len(cleaned) != 3 or not cleaned.isalpha():
        msg = f"Not an ISO 4217 alphabetic currency code: {code!r}"
        raise ValueError(msg)
    return Currency(code=cleaned)


def normalize_amount(
    value: str | Decimal | int,
    currency: Currency,
    convention: AmountSignConvention = AmountSignConvention.SIGNED,
) -> Money:
    """Parse a provider amount into signed minor units.

    Args:
        value: The provider's amount. A string is parsed as a decimal — never
            via float, because `"0.1" + 0.2` style error compounds over a ledger.
        currency: Supplies the exponent.
        convention: Whether the provider encodes a debit as positive.

    Returns:
        Money in minor units, negative for a debit.

    Raises:
        ValueError: If `value` is not a parseable decimal.
    """
    if isinstance(value, bool):
        msg = "Amount must not be a bool"
        raise ValueError(msg)
    if isinstance(value, float):
        # A float reaches here from a JSON client, which is the one path that can
        # put one into the ledger. Refusing it loudly is better than accepting it,
        # because `str(0.1)` looks fine while the underlying value already lost
        # precision before it got here.
        msg = "Amount must be a string, Decimal or int minor units, not a float"
        raise ValueError(msg)
    if isinstance(value, int):
        # An int is already minor units. Reinterpreting it as a major-unit
        # value would silently scale every integer amount by 100.
        minor = value
    else:
        try:
            decimal_value = (
                value if isinstance(value, Decimal) else Decimal(str(value).strip())
            )
        except InvalidOperation as exc:
            msg = f"Cannot parse amount from {value!r}"
            raise ValueError(msg) from exc
        if not decimal_value.is_finite():
            msg = f"Amount must be finite, got {value!r}"
            raise ValueError(msg)
        exponent = Decimal(1).scaleb(-currency.decimals)
        quantised = decimal_value.quantize(exponent, rounding=ROUND_HALF_UP)
        minor = int(quantised.scaleb(currency.decimals))

    if convention is AmountSignConvention.POSITIVE_IS_DEBIT:
        minor = -abs(minor)
    return Money(amount=minor, currency=currency)


def normalize_reference(reference: str | None) -> str | None:
    """Clean a provider reference, or None if it carries no information.

    Revolut's `id` and Enable Banking's `entry_reference` are the Tier-1 keys,
    so whitespace is stripped. An empty or placeholder string is None rather
    than "", because "" would match every other "" in a uniqueness check.
    """
    if reference is None:
        return None
    cleaned = reference.strip()
    return cleaned or None


def normalize_description(description: str | None) -> str:
    """Clean a description for matching: collapse whitespace, drop bookkeeping
    markers, and trim.

    Case is preserved. Fingerprint and categorization both lower-case, and doing
    it once here would make the raw column disagree with what we match on.
    """
    if not description:
        return ""
    text = _TRAILING_MARKERS.sub("", description)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_record(
    *,
    account_id: int | None,
    description: str | None,
    amount: str | Decimal | int,
    currency_code: str,
    booked_date: str | dt.date | dt.datetime,
    value_date: str | dt.date | dt.datetime | None = None,
    provider_txn_id: str | None = None,
    pending: bool = False,
    line_number: int = 0,
    sign_convention: AmountSignConvention = AmountSignConvention.SIGNED,
    raw_data: dict[str, str | int | float | bool | None] | None = None,
) -> NormalizedRecord:
    """Normalize one provider row.

    `raw_data` defaults to the normalized field values, so a caller that does
    not have the original payload still gets something replayable. It is never
    modified afterwards.
    """
    currency = normalize_currency(currency_code)
    money = normalize_amount(amount, currency, sign_convention)
    preserved: dict[str, str | int | float | bool | None] = dict(raw_data or {})
    preserved.setdefault("description", description or "")
    preserved.setdefault("amount_minor", money.amount)
    preserved.setdefault("currency", currency.code)
    return NormalizedRecord(
        account_id=account_id,
        description=normalize_description(description),
        amount=money,
        booked_date=parse_date(booked_date),
        value_date=parse_date(value_date) if value_date is not None else None,
        provider_txn_id=normalize_reference(provider_txn_id),
        pending=pending,
        line_number=line_number,
        raw_data=preserved,
    )
