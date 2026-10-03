"""analytics — the read side of the ledger, as numbers a chart can draw.

Everything in here is PURE: it takes values and returns values, and it never
touches a database. The SQL lives in `finance.api.routes.analytics`, because
`routes/__init__.py` is explicit that the routers are the only place in the
module allowed to do that. The split is not tidiness — the quantisation and the
parent-tree rollup are exactly the code where a sign error silently reports a
user's spending as income, so they have to be reachable by a test that needs no
database at all.

**Three things here are settled decisions, not preferences:**

1. **Base currency, always.** Every figure is EUR, because `journal_line.amount_base`
   is the only column a balance can sum across currencies (`ledger.py`). Summing
   `journal_line.amount` would add euros to dollars.

2. **Equity is not money.** Spending is parked in a seeded equity contra-account
   (`manual_posting.py`). Counting it would make every purchase RAISE the number
   it is supposed to lower, so net worth is asset + liability only.

3. **Liabilities are already negative.** A card charge writes a negative line so
   it balances against the positive equity line (`manual_posting.py`). There is
   no subtraction to apply — see `net_worth_series`.

Money leaves this module as an integer count of cents, because that is the only
shape the frontend can render (`ARCHITECTURE.md` §6) and because a second money
type would be a second place to get rounding wrong. The rounding itself is
`Money.from_decimal`, which is half-up, because that is what a human comparing
the number to a bank statement expects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from core.money import Currency, Money

__all__ = [
    "BASE_CURRENCY",
    "PERIODS",
    "CashflowPoint",
    "bucket_key",
    "date_range",
    "net_worth_series",
    "quantise",
    "root_ancestor",
    "summarise_cashflow",
]

#: Aggregates are EUR. `amount_base` is defined as the EUR value of a line, so
#: this is the currency the number already is in — naming it keeps the rounding
#: decision at the edge instead of implicit in a bare `scaleb`.
BASE_CURRENCY = Currency(code="EUR", decimals=2)

#: The closed period vocabulary. Cashflow buckets on exactly these and rejects
#: anything else, because a silently-accepted typo would return a series on the
#: wrong axis that still looks like a chart.
PERIODS = ("day", "week", "month")


def quantise(value: Decimal) -> int:
    """EUR decimal major units to an integer count of cents, half-up.

    The single sanctioned crossing point between the ledger's 4-decimal
    `amount_base` and the integer minor units every consumer reads.
    """
    return Money.from_decimal(value, BASE_CURRENCY).amount


def date_range(start: date, end: date) -> list[date]:
    """Every calendar date from `start` to `end`, inclusive.

    A net-worth chart needs a point per day even on days nothing was posted, or
    the line breaks wherever the user had a quiet spell. Emitting the gaps here
    means the query only has to report days that actually moved.
    """
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def net_worth_series(
    daily_totals: Mapping[date, Decimal], start: date, end: date
) -> list[tuple[date, int]]:
    """A running net worth for every date in `[start, end]`.

    `daily_totals` is the movement PER DAY already restricted to asset and
    liability accounts by the caller. This carries it forward, so a day with no
    postings repeats the previous day's figure instead of dropping to zero.

    Why there is no subtraction here: liabilities are stored negative. A charge
    on a card writes a negative line precisely so that it balances against the
    positive equity line on the other side of the entry
    (`manual_posting.py`). Adding the two natures together is therefore already
    `assets - liabilities`. Subtracting the liability column again is the
    plausible-looking sign flip that reports a credit card balance as money you
    have, and it is the reason equity is excluded rather than merely deprioritised
    — the equity leg is the mirror of every real movement, so including it nets
    the whole figure to approximately zero.
    """
    running = Decimal(0)
    cumulative: dict[date, Decimal] = {}
    for day in sorted(daily_totals):
        running += daily_totals[day]
        cumulative[day] = running

    if not cumulative:
        return [(day, 0) for day in date_range(start, end)]

    first = min(cumulative)
    carried = Decimal(0) if first > start else cumulative[first]
    series: list[tuple[date, int]] = []
    for day in date_range(start, end):
        if day in cumulative:
            carried = cumulative[day]
        series.append((day, quantise(carried)))
    return series


def root_ancestor(category_id: int, parents: Mapping[int, int | None]) -> int:
    """Walk `parent_id` up to a top-level category.

    `parents` maps a category id to its parent, or to None for a root.

    The walk is guarded because nothing in the schema forbids a loop: the tree
    is a plain self-referencing foreign key, so a category can name itself as its
    own parent, or two can name each other. Unguarded that is not a wrong answer,
    it is a request that never returns — and it is a hang rather than a crash, so
    it would be found by nobody until a user reported a spinning page. On a loop
    this returns the last node reached before the repeat, deterministically.
    """
    seen = {category_id}
    node = category_id
    parent = parents.get(node)
    while parent is not None and parent not in seen:
        node = parent
        seen.add(node)
        parent = parents.get(node)
    return node


@dataclass(frozen=True, slots=True)
class CashflowPoint:
    """One period's cash movement, in integer cents.

    `income` and `expense` are both positive magnitudes — "€42 spent" reads
    better than "-42" on a chart — and `net` keeps the ledger's own sign, so a
    deficit is negative. Restricting the input to ASSET accounts is what makes
    income and expense separable at all: the equity contra-leg carries the
    opposite sign to every real movement, so counting both sides would net each
    period to zero and every bar would be the same height.
    """

    period: str
    income: int
    expense: int
    net: int

    @classmethod
    def from_signed(
        cls, period: str, income: Decimal, expense: Decimal
    ) -> CashflowPoint:
        """Build from raw signed sums, with `expense` expected negative."""
        return cls(
            period=period,
            income=quantise(income),
            expense=quantise(-expense),
            net=quantise(income + expense),
        )


def bucket_key(day: date, period: str) -> str:
    """The label a date falls under for a given period length.

    Weeks are bucketed by the Monday that starts them, so a week is a stable
    label rather than "the seven days since the first request".
    """
    if period == "month":
        return f"{day.year:04d}-{day.month:02d}"
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    return day.isoformat()


def summarise_cashflow(
    movements: Iterable[tuple[date, Decimal]], period: str
) -> list[CashflowPoint]:
    """Group signed asset-side movements into per-period income and expense.

    Split on sign rather than on `category.kind`, because a line's kind lives on
    a different row than its amount, and the amount is what a chart is drawn
    from. A period with no movement at all is omitted rather than emitted as a
    zero row — a bar chart does not need a category for a month the user did not
    spend in, and the caller can tell the two apart by an absent label.

    Raises:
        ValueError: when `period` is not in `PERIODS`. Reached only if a caller
            skips the route's validation; `bucket_key` would otherwise fall
            through to per-day buckets for an unknown string and return a series
            that looks correct and is on the wrong axis.
    """
    if period not in PERIODS:
        msg = f"Unknown period {period!r}; expected one of {PERIODS}"
        raise ValueError(msg)

    income: dict[str, Decimal] = {}
    expense: dict[str, Decimal] = {}
    for day, amount in movements:
        key = bucket_key(day, period)
        if amount > 0:
            income[key] = income.get(key, Decimal(0)) + amount
        elif amount < 0:
            expense[key] = expense.get(key, Decimal(0)) + amount

    return [
        CashflowPoint.from_signed(
            key, income.get(key, Decimal(0)), expense.get(key, Decimal(0))
        )
        for key in sorted(income.keys() | expense.keys())
    ]
