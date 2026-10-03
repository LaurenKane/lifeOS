"""analytics router — the read side, aggregated.

Three questions a ledger can answer that no single row answers: what am I worth,
where did the money go, and did I have anything left. All three are computed here
from `journal_line.amount_base` and none of them is stored, because a stored total
is a total that disagrees with the ledger the moment a row is corrected.

**The rules this file exists to enforce.** Each one is a way the answer can come
out plausible and wrong, which is worse than an error because nothing flags it:

* **Sum `amount_base`, never `amount`.** `amount` is per-account-currency minor
  units; adding those together adds euros to dollars. `amount_base` is the EUR
  value (`ledger.py`).

* **Net worth is asset + liability, with no subtraction.** Liabilities are already
  stored negative — a card charge writes a negative line so it balances against
  the positive equity line on the other side (`manual_posting.py`). Subtracting
  the liability column again reports money you owe as money you have.

* **Equity is excluded from net worth.** Spending is parked in a seeded equity
  contra-account. Counting it makes every purchase raise the figure it should
  lower.

* **Transfers are treated differently by each report, on purpose.** Net worth
  includes them, because they net to zero across accounts and excluding them
  would need per-leg knowledge to work out which cancel. Spend-by-category
  excludes them, because moving money to savings is not spending. Cashflow
  includes them on ASSET accounts only: a savings top-up cancels against its own
  second asset leg, while a card payment correctly reads as cash leaving, since
  the card is a liability and is not counted on the other side.

Nothing here filters on `is_active` or `is_hidden`. A closed account still held a
balance on the day it closed, so dropping it would silently rewrite history, and
`is_hidden` reads as "do not list this account" rather than "do not count it".
That is a product question, not a settled one.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from finance.api.deps import get_session
from finance.api.schemas import (
    CashflowBucket,
    NetWorthPoint,
    SpendByCategoryPoint,
)
from finance.domain.models.accounts import Account
from finance.domain.models.ledger import JournalEntry, JournalLine
from finance.domain.models.taxonomy import Category
from finance.domain.services.analytics import (
    PERIODS,
    net_worth_series,
    quantise,
    root_ancestor,
    summarise_cashflow,
)
from finance.public import CategoryKind

router = APIRouter(tags=["finance"], prefix="/analytics")

SessionDep = Annotated[Session, Depends(get_session)]

#: A month of history when the caller names no range. A one-day default would
#: return a single point, which draws as a flat line and reads as "no data".
_DEFAULT_WINDOW_DAYS = 30

#: How long a range may be before it is refused, so a stray missing `to` cannot
#: ask for one point per day since 1970 instead of erroring.
_MAX_DAYS = 3660


def _resolve_range(
    from_: dt.date | None, to: dt.date | None
) -> tuple[dt.date, dt.date]:
    """Default the range, then refuse the ones that cannot be served.

    Raises:
        HTTPException: 422 when the range is inverted or longer than `_MAX_DAYS`.
    """
    end = to or dt.date.today()
    start = from_ or (end - dt.timedelta(days=_DEFAULT_WINDOW_DAYS - 1))
    if start > end:
        raise HTTPException(status_code=422, detail="`from` must not be after `to`")
    if (end - start).days > _MAX_DAYS:
        raise HTTPException(
            status_code=422, detail=f"range must not exceed {_MAX_DAYS} days"
        )
    return start, end


@router.get(
    "/net-worth",
    summary="Net worth over time",
    response_model=list[NetWorthPoint],
)
def net_worth(
    session: SessionDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query()] = None,
) -> list[NetWorthPoint]:
    """Net worth as at each date in the range, in integer EUR cents.

    Every day in the range appears, including days with no postings, because a
    chart line that breaks on a quiet week reads as missing data rather than as
    a flat one.
    """
    start, end = _resolve_range(from_, to)

    daily = session.execute(
        select(JournalEntry.entry_date, func.sum(JournalLine.amount_base))
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.account_nature.in_(("asset", "liability")))
        .where(JournalEntry.entry_date <= end)
        .group_by(JournalEntry.entry_date)
    ).all()

    series = net_worth_series({row[0]: row[1] for row in daily}, start, end)
    return [
        NetWorthPoint(date=day.isoformat(), net_worth=minor)
        for day, minor in series
    ]


@router.get(
    "/spend-by-category",
    summary="Spend grouped by category",
    response_model=list[SpendByCategoryPoint],
)
def spend_by_category(
    session: SessionDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query()] = None,
    rollup: Annotated[bool, Query()] = False,
) -> list[SpendByCategoryPoint]:
    """What was spent, per category, as signed integer cents.

    Expenses are negative, because that is the ledger's sign and the frontend's
    `Amount` component already reads a negative as money out. Re-signing here
    would make the sign mean something different in this one response than
    everywhere else.

    Transfers are excluded: moving money to savings is not spending, and
    `ledger.py` calls getting that wrong "the single most misleading thing this
    application can do".

    `rollup=false` reports leaf categories. `rollup=true` walks each one up to its
    top-level ancestor, so a chart can show 'Groceries' rather than one bar per
    shop.
    """
    start, end = _resolve_range(from_, to)

    rows = session.execute(
        select(
            Category.id,
            Category.name,
            Category.kind,
            func.sum(JournalLine.amount_base),
        )
        .join(JournalLine, JournalLine.category_id == Category.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(JournalEntry.is_transfer.is_(False))
        .where(JournalEntry.entry_date.between(start, end))
        .group_by(Category.id, Category.name, Category.kind)
    ).all()

    # The whole tree, not just the categories that happened to spend: a rollup
    # target is frequently a parent with no spending of its own, and looking the
    # label up from the spending rows would print "Category 42" for it.
    tree = session.execute(
        select(Category.id, Category.name, Category.kind, Category.parent_id)
    ).all()
    labels = {row[0]: (row[1], row[2]) for row in tree}
    parents = {row[0]: row[3] for row in tree}

    totals: dict[int, Decimal] = {}
    if rollup:
        for category_id, _name, _kind, total in rows:
            target = root_ancestor(category_id, parents)
            totals[target] = totals.get(target, Decimal(0)) + total
    else:
        for category_id, _name, _kind, total in rows:
            totals[category_id] = total

    points: list[SpendByCategoryPoint] = []
    for category_id, total in totals.items():
        name, kind = labels.get(
            category_id, (f"Category {category_id}", CategoryKind.EXPENSE)
        )
        points.append(
            SpendByCategoryPoint(
                category_id=category_id,
                category_name=name,
                kind=CategoryKind(kind),
                amount=quantise(total),
            )
        )
    # Largest spend first. A chart reads as a ranked list, and the alternative
    # is a database GROUP BY whose row order Postgres does not promise.
    return sorted(points, key=lambda point: point.amount)


@router.get(
    "/cashflow",
    summary="Income, expense and net per period",
    response_model=list[CashflowBucket],
)
def cashflow(
    session: SessionDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query()] = None,
    period: Annotated[str, Query()] = "month",
) -> list[CashflowBucket]:
    """Cash in, cash out and the difference, bucketed by `period`.

    Restricted to ASSET accounts. The equity contra-leg carries the opposite sign
    to every real movement, so counting both sides would net each period to zero
    and every bar would be the same height.

    Transfers are deliberately INCLUDED here, unlike spend-by-category. A savings
    top-up is two asset legs that cancel; a card payment is an asset leg out and a
    liability leg that is not counted, so it correctly reads as cash leaving.
    Excluding transfers would hide the payment, and a card-funded purchase would
    then never appear in cashflow at all.

    A period with no movement is omitted rather than emitted as a zero row; the
    chart can render a gap and cannot invent one.
    """
    if period not in PERIODS:
        raise HTTPException(
            status_code=422, detail=f"`period` must be one of {', '.join(PERIODS)}"
        )
    start, end = _resolve_range(from_, to)

    rows = session.execute(
        select(JournalEntry.entry_date, JournalLine.amount_base)
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.account_nature == "asset")
        .where(JournalEntry.entry_date.between(start, end))
    ).all()

    points = summarise_cashflow([(row[0], row[1]) for row in rows], period)
    return [
        CashflowBucket(
            period=point.period,
            income=point.income,
            expense=point.expense,
            net=point.net,
        )
        for point in points
    ]
