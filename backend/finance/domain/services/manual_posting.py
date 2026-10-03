"""manual_posting.py — the RULES for posting a user-entered transaction.

**Pure.** No database, no HTTP, no ingestion, no clock, no randomness. Every
function here is a decision about arithmetic and about which account the money
lands in; the route that calls it owns the session and the writes. That split is
the whole reason this file exists next to `routes/transactions.py`: the rule that
decides where the other half of the entry goes is testable without Postgres, and
the code that writes the rows has nothing left to decide.

NOT re-exported from `finance/domain/services/__init__.py`, deliberately.
That package's contract is "re-export surface only", so adding an entry here is a
change to a cross-module contract rather than to this module.


WHY A COUNTER-LEG CONVENTION EXISTS AT ALL
==========================================

`account_nature` is `(asset | liability | equity)`. There is no expense account
type, and there is not going to be one: a real P&L chart of accounts would need
income and expense accounts, and this schema deliberately has neither — spending
is derived from a signed amount and a category, not from an account type.

So a EUR 40.50 expense paid out of a checking account is NOT one row. It is:

    checking   (asset)   -40.50
    counter    (equity)  +40.50

Two rows, summing to zero, in one entry, because that is what double entry is.
The question this module answers is only ever: **which account is the counter?**

The convention
--------------

**The counter-leg is a seeded account of nature `equity`, named exactly
`SYSTEM_EXPENSE_ACCOUNT_NAME`.** It is resolved by that exact name, among active
accounts, and nothing else is ever substituted for it:

* **Why `equity`.** It is the third of the three natures, and it is the only one
  whose sign direction makes a spending entry come out right: the asset side
  falls and the equity side rises. Putting the contra-leg on another asset
  (`savings`) would balance just as well arithmetically and be completely wrong
  semantically — it would make a sandwich look like money moving between the
  user's own accounts, which is the single most misleading thing this ledger can
  report.
* **Why the exact name, not "the first equity account".** "The first one" is a
  guess that silently succeeds today and mis-posts tomorrow, when the user adds
  their own equity account. An exact name is a contract: it either resolves to
  one row or it raises.
* **Why it raises instead of falling back.** A silently mis-posted expense is far
  worse than a refused one. Every failure mode here raises
  `CounterAccountUnresolvedError` with the expected name in the message, so the
  caller is told exactly what to create.
* **Why `account_type` is `cash`.** The CHECK on `finance.account.account_type`
  is closed and contains no "expense" or "equity" value, so the seeded row has
  to name *something* from that list. `cash` is the least-committal member: it is
  a real-world bucket with no provider semantics, whereas `checking`/`savings`/
  `credit_card` would each assert that the account is a bank product this ledger
  did not receive. The field that actually carries the meaning is
  `account_nature`; `account_type` here is a formality forced by a closed
  vocabulary, and this docstring is the record of that.

A caller may name the counter-leg explicitly instead
----------------------------------------------------

`resolve_counter_account_by_id` honours an explicit request field, and still
validates it: the account must exist, be active, differ from the funding account,
and be of nature `equity`. The same rule, not a bypass of it — a transfer between
two assets is a different fact with a different entry (`is_transfer`), and it is
not something this function is allowed to quietly produce.


THE SIGN
========

Sign carries direction and there is no `direction` column (see
`finance/domain/models/ledger.py`). Debits are negative, credits positive.
`ManualTransactionRequest.amount` is therefore SIGNED, exactly like every other
amount in this project: `"40.50"` is money IN and `"-40.50"` is money OUT. The
route does not flip a sign, because a hidden flip is how the Amex PDF adapter
got the wrong answer once already
(`docs/adr/0003-import-decisions-real-export.md` Decision 1).


THE RESIDUAL
============

`journal_line.amount_base` is `NUMERIC(18,4)` and the balance trigger tolerates
`abs(sum) <= 0.005`. The tolerance is a safety net for FX, not the mechanism.
`absorb_fx_residual` makes the stored data sum to exactly zero: the
largest-magnitude leg is recomputed as the negation of the sum of all the others.
For the two-leg manual entry this is an exact no-op, because the counter-leg is
already the exact negation of the funding leg; it is written generally because the
moment a third leg appears (an FX fee, a split) it is the thing that keeps the
sum at zero rather than merely inside tolerance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from core.money import Money

__all__ = [
    "BASE_CURRENCY",
    "BASE_SCALE",
    "SYSTEM_EXPENSE_ACCOUNT_NAME",
    "SYSTEM_EXPENSE_ACCOUNT_NATURE",
    "SYSTEM_EXPENSE_ACCOUNT_TYPE",
    "CounterAccountUnresolvedError",
    "ManualPostingError",
    "PostingAccount",
    "PostingLeg",
    "absorb_fx_residual",
    "build_expense_legs",
    "resolve_counter_account_by_id",
    "resolve_default_counter_account",
]


# ---------------------------------------------------------------------------
# The convention, as data
# ---------------------------------------------------------------------------


#: The currency `journal_line.amount_base` is denominated in. The column comment
#: in migration 0001 says EUR; this is where that becomes something the code can
#: enforce rather than a comment nobody checks.
BASE_CURRENCY: Final[str] = "EUR"

#: `NUMERIC(18,4)`, as a Decimal exponent. Quantising to this BEFORE summing is
#: what makes "sums to exactly 0.0000" a property of stored data rather than a
#: property of the arithmetic that happened to produce it: PostgreSQL rounds on
#: the way in, and a Python-side sum of unquantised Decimals would not
#: necessarily agree with the sum of the stored values.
BASE_SCALE: Final[Decimal] = Decimal("0.0001")

#: The counter-leg account for a manual expense. See the module docstring.
SYSTEM_EXPENSE_ACCOUNT_NAME: Final[str] = "Expenses (system)"

#: The nature the counter-leg account MUST have. Checked, not assumed.
SYSTEM_EXPENSE_ACCOUNT_NATURE: Final[str] = "equity"

#: The `account_type` the seeded row carries. The vocabulary is closed and has
#: no expense member; see the module docstring for why this one.
SYSTEM_EXPENSE_ACCOUNT_TYPE: Final[str] = "cash"


class ManualPostingError(ValueError):
    """A manual transaction cannot be posted, and the reason is known.

    Raised for a condition the caller can fix by changing the request: a
    zero-amount entry, a currency that disagrees with the account, a
    non-positive rate. A `ValueError`, matching `AdapterParseError` — every one
    of these is "the input is wrong", not "the system is broken".
    """


class CounterAccountUnresolvedError(ManualPostingError):
    """The counter-leg account could not be identified. Never guessed.

    Carries the expected name and what was actually found, because "no equity
    account" is not actionable and "no ACTIVE account named 'Expenses (system)'"
    is.
    """


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostingAccount:
    """The subset of `finance.account` this module reasons about.

    A plain frozen dataclass rather than the ORM row, because this module is
    pure and must stay that way: the route maps rows to these, and a test can
    build one without a database.
    """

    id: int
    name: str
    currency: str
    account_nature: str
    is_active: bool = True

    @property
    def is_equity(self) -> bool:
        return self.account_nature == SYSTEM_EXPENSE_ACCOUNT_NATURE


@dataclass(frozen=True)
class PostingLeg:
    """One row of `journal_line`, before it has an id.

    `amount` is signed minor units in `currency`; `amount_base` is the signed
    EUR value at `NUMERIC(18,4)` scale. They are separate columns in the schema
    for a reason — history must not re-price when a rate feed changes its mind —
    and they are separate fields here.

    `exchange_rate` is carried rather than left to the caller: it is the rate
    that produced this leg's `amount_base`, stored alongside it so the
    conversion is reproducible from the row alone. For a leg already in
    `BASE_CURRENCY` it is exactly 1.
    """

    account_id: int
    currency: str
    amount: int
    amount_base: Decimal
    exchange_rate: Decimal
    sort_order: int


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_default_counter_account(
    accounts: Sequence[PostingAccount],
    *,
    name: str = SYSTEM_EXPENSE_ACCOUNT_NAME,
) -> PostingAccount:
    """Find THE counter-leg account: the active account with exactly `name`.

    Args:
        accounts: Every candidate. The caller passes all of them, active and
            not, so the error message can distinguish "no such account" from
            "it exists but is deactivated" — two very different mistakes.
        name: The exact, case-sensitive name to look for.

    Returns:
        The one matching account.

    Raises:
        CounterAccountUnresolvedError: If there is no such account, if it is
            inactive, if its nature is not `equity`, or if MORE THAN ONE account
            answers to the name. The last case matters as much as the first: two
            rows with the same name is a half-finished setup, and picking one
            would be a coin flip that decides where the user's money goes.
    """
    matches = [account for account in accounts if account.name == name]
    if not matches:
        raise CounterAccountUnresolvedError(
            f"No account named {name!r} exists, so there is nowhere to book the "
            "other side of a manual transaction. Create exactly one account with "
            f"name={name!r}, account_type={SYSTEM_EXPENSE_ACCOUNT_TYPE!r} and "
            f"account_nature={SYSTEM_EXPENSE_ACCOUNT_NATURE!r}, or pass "
            "counter_account_id explicitly."
        )

    active = [account for account in matches if account.is_active]
    if not active:
        raise CounterAccountUnresolvedError(
            f"Account {name!r} (id={matches[0].id}) exists but is not active, so "
            "it cannot receive the counter-leg. Activate it, or pass "
            "counter_account_id explicitly."
        )
    if len(active) > 1:
        ids = sorted(account.id for account in active)
        raise CounterAccountUnresolvedError(
            f"{len(active)} active accounts are named {name!r} (ids={ids}). The "
            "counter-leg is not guessed: keep exactly one, or pass "
            "counter_account_id explicitly."
        )

    account = active[0]
    if not account.is_equity:
        raise CounterAccountUnresolvedError(
            f"Account {name!r} (id={account.id}) has nature "
            f"{account.account_nature!r}; the counter-leg of an expense must be "
            f"{SYSTEM_EXPENSE_ACCOUNT_NATURE!r}."
        )
    return account


def resolve_counter_account_by_id(
    accounts: Sequence[PostingAccount],
    counter_account_id: int,
) -> PostingAccount:
    """Find the counter-leg account the caller named explicitly.

    Validated exactly as the default is, because an explicit id that resolves to
    a wrong-shaped account is not a way around the rule — it is the same mistake
    with an extra step.

    Raises:
        CounterAccountUnresolvedError: If no account has that id, it is inactive, it
            is not of nature `equity`, or it IS the funding account (which would
            make both legs of the entry the same row twice).
    """
    matches = [account for account in accounts if account.id == counter_account_id]
    if not matches:
        raise CounterAccountUnresolvedError(
            f"counter_account_id={counter_account_id} names no account."
        )
    account = matches[0]
    if not account.is_active:
        raise CounterAccountUnresolvedError(
            f"counter_account_id={counter_account_id} (name={account.name!r}) is "
            "not an active account."
        )
    if not account.is_equity:
        raise CounterAccountUnresolvedError(
            f"counter_account_id={counter_account_id} (name={account.name!r}) has "
            f"nature {account.account_nature!r}; the counter-leg of an expense "
            f"must be {SYSTEM_EXPENSE_ACCOUNT_NATURE!r}."
        )
    return account


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def to_base_scale(value: Decimal) -> Decimal:
    """Quantise a EUR figure to `NUMERIC(18,4)` scale, half-up.

    Applied to every leg before it is written and therefore before anything is
    summed, so the Python-side balance and the stored balance are the same
    number rather than two numbers that happen to be close.
    """
    return value.quantize(BASE_SCALE, rounding=ROUND_HALF_UP)


def build_expense_legs(
    *,
    funding_account: PostingAccount,
    counter_account: PostingAccount,
    amount: Money,
    rate: Decimal,
    counter_decimals: int | None = None,
) -> tuple[PostingLeg, PostingLeg]:
    """The two legs of a manual expense, funding side first.

    Args:
        funding_account: The account the money left. Its currency MUST be the
            transaction currency: `journal_line.currency` is a snapshot of
            `account.currency`, so a mismatch is not a conversion opportunity, it
            is a user who typed the wrong currency.
        counter_account: The resolved contra-account. MUST be in
            `BASE_CURRENCY`, because `amount_base` is EUR and a contra-leg
            denominated in anything else would need a second rate to be
            representable at all.
        amount: The transaction, signed minor units in the funding account's
            currency. `"40.50"` EUR is a credit of +4050.
        rate: Units of the transaction currency per 1 EUR — the inverse of what
            `amount_base` needs, so it is inverted here exactly once. `1.0` for a
            EUR transaction.
        counter_decimals: `currency.decimals` for the counter account. Only
            needed when it is not the 2-decimal default; the caller has the
            currency table and this module must not query it.

    Returns:
        `(funding_leg, counter_leg)`, `sort_order` 0 and 1.

    Raises:
        ManualPostingError: For a zero amount, a currency mismatch, a
            counter-leg that is not in the base currency, or a rate that is not
            strictly positive. A zero or negative rate turns a signed amount
            into a nonsense one, and the arithmetic that suffers for it happens
            far from the request that caused it.
    """
    if amount.amount == 0:
        raise ManualPostingError(
            "A zero-amount manual transaction has no double-entry meaning: both "
            "legs would be 0.00 and the entry would record that nothing happened. "
            "Post a non-zero amount, or delete the transaction that was wrong."
        )
    if funding_account.currency != amount.currency.code:
        raise ManualPostingError(
            f"Account {funding_account.id} holds {funding_account.currency} but "
            f"the transaction is in {amount.currency.code}. `journal_line.currency` "
            "is a snapshot of the account's own currency, so this is a mistake in "
            "the request, not a conversion."
        )
    if counter_account.currency != BASE_CURRENCY:
        raise ManualPostingError(
            f"counter-account {counter_account.id} is in "
            f"{counter_account.currency}; the counter-leg must be in "
            f"{BASE_CURRENCY}, because amount_base is EUR and a contra-leg in "
            "another currency cannot be stated without a second rate."
        )
    if rate <= 0:
        raise ManualPostingError(
            f"Exchange rate must be strictly positive, got {rate}."
        )
    if counter_account.id == funding_account.id:
        raise ManualPostingError(
            f"Account {funding_account.id} is both the funding and the counter "
            "account, so the entry would have both legs on one row."
        )

    # EUR value of the funding side. One division, one inversion, one
    # quantisation: `amount_base` is the only column a balance can sum across
    # currencies.
    base = to_base_scale(amount.to_decimal() / rate)
    if base == 0:
        # A rate so extreme that a real amount quantises to nothing in EUR.
        # Writing it would produce two legs of 0.0000 and an entry that records
        # a transaction which did not happen.
        raise ManualPostingError(
            f"{amount} at rate {rate} is {base} EUR: the transaction has no "
            "representable base value and cannot be posted."
        )

    exponent = 2 if counter_decimals is None else counter_decimals
    counter_amount = int(
        -base.scaleb(exponent).to_integral_value(rounding=ROUND_HALF_UP)
    )

    return (
        PostingLeg(
            account_id=funding_account.id,
            currency=funding_account.currency,
            amount=amount.amount,
            amount_base=base,
            exchange_rate=rate,
            sort_order=0,
        ),
        PostingLeg(
            account_id=counter_account.id,
            currency=counter_account.currency,
            amount=counter_amount,
            amount_base=to_base_scale(-base),
            # The counter-leg is in BASE_CURRENCY by the check above, so its
            # conversion is the identity. Stating 1 rather than `rate` keeps the
            # row self-describing: `amount_base * exchange_rate == amount/100`
            # holds for EVERY leg, not only the foreign ones.
            exchange_rate=Decimal(1),
            sort_order=1,
        ),
    )


def absorb_fx_residual(legs: Sequence[PostingLeg]) -> tuple[PostingLeg, ...]:
    """Make the legs sum to exactly zero, and say so in the stored data.

    The largest-magnitude leg is recomputed as the negation of every other leg.
    Largest, because that is where a rounding residual does the least relative
    damage; ties break on `sort_order`, so the result is deterministic rather
    than dependent on the order a `set` or a dict happened to iterate in.

    The ±0.005 tolerance in the balance trigger is a safety net for a genuinely
    multi-currency import. Stored data that sits inside the tolerance and not on
    zero is a ledger that balances only approximately, and every downstream sum
    inherits the drift.

    Raises:
        ManualPostingError: With fewer than two legs. There is nothing to
            absorb into, and returning the input unchanged would be a silent
            no-op that looks like success.
    """
    if len(legs) < 2:
        raise ManualPostingError(
            f"absorb_fx_residual needs at least 2 legs, got {len(legs)}."
        )

    quantised = [
        replace(leg, amount_base=to_base_scale(leg.amount_base)) for leg in legs
    ]

    # Largest magnitude wins; the lowest sort_order breaks a tie. Both keys, so
    # the answer does not depend on the order `legs` arrived in.
    def _rank(index: int) -> tuple[Decimal, int]:
        return abs(quantised[index].amount_base), -quantised[index].sort_order

    largest = max(range(len(quantised)), key=_rank)
    others = sum(
        (leg.amount_base for index, leg in enumerate(quantised) if index != largest),
        start=Decimal("0.0000"),
    )
    residual = to_base_scale(-others)
    if residual == 0:
        # `Decimal("-0.0000")` and `Decimal("0.0000")` compare equal but print
        # differently, and a ledger that prints "-0.0000" has invited a bug.
        residual = Decimal("0.0000")
    adjusted = list(quantised)
    adjusted[largest] = replace(adjusted[largest], amount_base=residual)
    return tuple(adjusted)
