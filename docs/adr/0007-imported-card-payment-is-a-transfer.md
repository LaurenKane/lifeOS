# ADR 0007: an imported card payment is a transfer, not an expense

**Date:** 2026-10-03
**Status:** Accepted
**Decision Drivers:** the equity-only contra-leg rule is correct for a typed expense and wrong for
a debt repayment; routing an import through it does not fail — it **silently mis-posts**; and the
paying account is not printed on the statement, so any choice of one is a guess about where the
user's money went.

## Context

A monthly card payment credits the card and debits the checking account. The card is a
**liability** and checking is an **asset**, so the contra-leg of the card's own leg is an asset.
That is not an expense, and it is not an equity movement either.

The posting code enforces exactly one contra-leg convention. `resolve_default_counter_account()`
(`domain/services/manual_posting.py:272`) requires `account.is_equity`, and
`account_nature` is the closed list `('asset','liability','equity')`. So there is no rule that can
accept an asset contra-leg.

### The failure is silent, and that is the whole point

The obvious reading — "reuse `build_expense_legs()`, it will raise" — is wrong, and it is wrong in
the direction that matters.

`build_expense_legs()` does **not** check `account_nature`. It checks currency, `rate > 0`, and
that funding and counter differ. The equity requirement lives one layer up, in the resolver the
route calls first (`api/routes/transactions.py:338`).

So an imported card payment sent through the manual default does not reliably raise
`CounterAccountUnresolvedError`. It raises only when no active equity expense account exists. When
one does exist — the normal case for a user who has ever posted a manual transaction — the payment
is **booked against equity**. Paying off a debt is recorded as spending.

Three properties make this worse than an exception:

1. **It is balanced.** Both legs exist and sum to zero, so the balance trigger
   (`ADR 0006`) accepts it. There is no invariant violation to notice.
2. **It is permanent.** `source_record.raw_data` is immutable by trigger, and the journal entry it
   produces is a real, balanced, plausible-looking expense.
3. **It is invisible in aggregate.** Expenses inflate by the monthly card payment while the card
   liability stays understated. The statement still reconciles; the balance sheet is simply wrong.

This is precisely the failure `manual_posting.py:42-52` exists to refuse — "a silently mis-posted
expense is far worse than a refused one" — reached through the path that bypasses the refusal.

### Scale

Measured against the nine real statements, not estimated:

| Source | Rows | Card payments |
|---|---|---|
| Amex (4 PDFs) | 121 | **4** |
| Rabobank (4 PDFs) | 106 | 0 |

Rabobank has none. The defect is Amex-only in practice, but the *rule* is not — any imported row
routed through the manual default inherits the same convention. It is only visible on Amex
because Amex is the only source that produces card payments.

### The schema already anticipated this

`journal_line` carries `is_synthesized BOOLEAN` and
`synthesized_reason TEXT CHECK (synthesized_reason IN ('card_payment','sepa_dd','investment','opening_balance'))`,
and the migration comments the column as `-- the Amex card-payment leg`
(`alembic/versions/0001_finance_ledger_schema.py:386`). The intended shape of this fix was
designed before the parser existed. So the complete solution needs **no new ledger structure**.

### What the transfer code does not give us

`domain/models/transfers.py` cannot be reused as the cheap path it appears to be. It models a
*stored match between two existing journal lines*; `transfer_match.py` only decides whether two
lines already present look like a pair. Neither can choose the missing checking account nor
synthesize a missing leg. Its result also carries `entry_id` while the database match table stores
`journal_line_id` — resolve that before relying on it.

## Decision

### 1. A flagged card payment is a transfer, and the paying account is never guessed

`raw_data['is_card_payment']` is the discriminator, set by the adapter at parse time. A row
carrying it is dispatched **before** posting and never reaches the manual expense resolver.
Selecting an arbitrary asset account, or matching by account name, is refused for the same reason
`resolve_default_counter_account()` refuses two same-named accounts: a coin flip that decides
where the user's money goes is worse than a refusal.

### 2. Until the paying account is known, the row is stored unposted and the batch is partial

`source_record` already permits an `imported`/`pending` row with no `journal_entry_id`, and
`import_batch` supports `partial`. The source row is still written — accurately parsed and
redacted per `ADR 0003` and `commit 67e3c1f` — with an actionable `error_message`. The user is
told which row needs an account.

A partial import is honest here. Dropping the row silently while reporting the batch complete is
not: the statement then fails to reconcile, and a missing payment reads as missing history.

### 3. Once resolved, book a liability leg against a synthesized asset leg

Resolution is by explicit user choice or a saved **account-id** mapping — never by name, for the
reasons in `LifeOS-5qh`. The card leg is positive (the liability decreases); the checking leg is
negative and carries `is_synthesized=True, synthesized_reason='card_payment'`, so a later import
of the checking statement can be matched against it rather than double-booked.

### Why not refuse card payments outright (the original option (a))

It leaves a real user unable to import the account they most need. Skipping the payment while
calling the batch complete is worse than either. Refusal is the right behaviour only in the
specific form above — one row, loudly, with the rest of the file intact.

## Consequences

- **Amex imports are partial by default** until the user supplies the paying account. That is a
  visible, explained state, not a failure and not a silent wrong answer.
- **The double-booking guard is required, not optional.** The same real payment appears on the
  Amex statement and on the checking statement. Whichever is imported second must match the
  synthesized leg rather than create a second entry. Tests must cover both import orders.
- **`manual_posting.build_expense_legs()` stays scoped to manual expenses.** The import path gets
  its own leg builder rather than a widened equity rule, so a manual expense cannot acquire a
  transfer-shaped hole.
- **A green test run is not evidence this works.** Acceptance that imports only a Rabobank file
  exercises zero card payments. The Amex path needs its own test, and `LifeOS-yfi` is the related
  gap: the reconciliation suite asserts nothing about descriptions at all.
- **`LifeOS-5qh` is unaffected and stays separate.** A stable account *role* would fix the manual
  resolver's name-based lookup, but it still would not identify which checking account paid a
  given card. Those are two different questions.