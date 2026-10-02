# Research 09 — Canonical Data Model (Specialist C)

**Lane:** ora-1 (oracle) · **Completed:** 2026-09-30 · **Status:** reconciled
**Method:** three design rounds, the last two driven by confirmed Enable Banking / Amex facts.

> This file records the **design and its rationale**. The corrected, final DDL lives in
> `docs/ARCHITECTURE-PROPOSAL.md` §E. Where this lane and I disagree, the disagreement is recorded here
> and resolved in the proposal.

---

## The design in brief

**Core chain:**

```
ImportBatch (1) ──< SourceRecord (raw, immutable, fingerprint ALWAYS)
                        │
                        ▼
                   IdentityResolver  (3 dedup tiers)
                        │
                        ▼
JournalEntry (canonical, double-entry) ──< JournalLine (≥2 per entry)
                                                 │
                                                 ├── amount / currency
                                                 ├── category_id
                                                 ├── merchant_id
                                                 └── transfer_match_id
```

**Key structural decisions the lane made:**

| Decision | Choice |
|---|---|
| Transaction shape | **Full double-entry** — `JournalEntry` header + ≥2 `JournalLine` legs |
| Naming | `JournalEntry` / `JournalLine` (not `Entry`/`Posting`) — least confusing for a developer who knows accounting basics |
| Raw data | `SourceRecord` is **immutable** and survives forever; one row per provider row |
| Provenance | Decomposed into `ImportBatch` + `SourceRecord` + `ProviderAccountLink` (not one `TransactionSource`) |
| Merchant | `Merchant` + `MerchantAlias` (confidence-scored, learned from user corrections) |
| Categories | Adjacency list (`parent_id`), 2-level default, `kind` column, `ltree` deferred |
| Transfers | `TransferMatch` links two `JournalLine`s, **stored not recomputed**, with `match_method` + `confidence` + `confirmed_at` |
| Recurring | `RecurringSeries` — detection *output* by a nightly job, user-confirmed |
| Investment seam | `security` / `holding` / `investment_transaction` / `cost_basis_lot` tables created empty |
| Amex card payment | **Option (a): synthesize the liability leg**, marked `is_synthesized` + `synthesized_reason` |

### Money representation

| Column | Type | Rationale |
|---|---|---|
| `journal_line.amount_account` | `BIGINT` | Exact integer arithmetic in the account's native currency (cents for EUR). No float. |
| `journal_line.amount_base` | `NUMERIC(18,4)` | EUR reporting value; converted at import time, stored |
| `exchange_rate.rate` | `NUMERIC(18,8)` | FX needs 8 decimals |
| `currency.decimals` | `SMALLINT` | Defines the minor-unit exponent — the authority for BIGINT interpretation |

Currency: `account.currency` is the account's native currency; `journal_line.currency` is a **snapshot**
taken at transaction time. Reporting converts nothing at query time.

### Dedup — three tiers

**Tier 1 — provider ID.** `(account, provider_txn_id)`. Enable Banking `entry_reference`; Revolut `id`
*(CSV only — unverified, doc 11 §7; Tier 1 is unreachable on every v1 PDF path)*.
Must be scoped by account — `entry_reference` is **not** globally unique.

**Tier 2 — pending→booked, Enable Banking only.** Candidate window: same account, same currency,
`ABS(amount_diff) <= 0.01`, `new.date BETWEEN existing.date - 3d AND existing.date + 1d`,
existing not already matched. Score components: exact amount, exact date, trigram description
similarity > 0.8, merchant-alias agreement, existing-is-pending, candidate-uniqueness.
**≥0.85 auto-link · 0.50–0.85 review queue · <0.50 treat as new.**

**Tier 3 — content fingerprint, all providers.** SHA256 over canonicalised
`description|amount|currency|date|account_id|occurrence_index`. **Computed for API rows too**, as a
backstop if `entry_reference` ever changes on a historical re-fetch.

**Occurrence index** solves the identical-coffee problem:
```sql
ROW_NUMBER() OVER (PARTITION BY account_id, normalized_description, amount, currency, date
                   ORDER BY import_batch_id, raw_data_line_number)
```

**Review queue UX** (ephemeral `pending_match` table): `CONFIRM` → merge into existing `JournalEntry` ·
`REJECT` → new entry · `IGNORE` → leave (30-day TTL). A user decision raises `MerchantAlias.confidence`
or creates a `CategoryRule`.

**Design principle, stated well by the lane:** *default to "create new + queue for review" rather than
"guess and merge." User confirmation is ground truth.*

### The Amex card payment — the lane's answer

1. Amex purchase €50 → `JournalEntry` E1: line 1 Amex liability `+50`, line 2 Expense:Groceries `−50`.
   Liability *increases*.
2. Rabobank `−50 "American Express"` → `JournalEntry` E2: line A Rabobank asset `−50`,
   **line B Amex liability `+50` — SYNTHESIZED**, `is_synthesized = TRUE`,
   `synthesized_reason = 'card_payment'`.
3. `TransferMatch` links A→B, `match_method = 'auto_card_payment'`, `confidence = 0.95`.
4. Net effect on the Amex liability for the pair: `+50 − 50 = 0`. Correct.

**Why synthesis is required, not a shortcut:** double-entry demands every entry balance. The liability
account *must* show the paydown. Without line B, E2 is unbalanced.

### Direct debit vs own-account transfer

Deterministic rule: **if the inbound leg resolves to an account you own → transfer. Otherwise if the
description matches a SEPA creditor ID / mandate reference → direct debit (expense). Otherwise → review
queue** ("is this your own account or a bill?").

### Lifecycle states

Persisted on `SourceRecord.status` only: `imported, normalized, pending, posted, reconciled, duplicate`.
`matched` and `categorized` are **not** persisted — they are derived from FK predicates
(`transfer_match_id IS NOT NULL`, `category_id IS NOT NULL`).

### Addenda from the real exports (2026-10-02)

Applied on top of this lane's design. Full rationale in
`docs/adr/0003-import-decisions-real-export.md`; decisions that change the schema are in
`docs/adr/0002-import-provider-enum.md`.

- **The sign convention inversion is explicit.** Correction 2 above removed `direction` because the
  sign is the direction. That makes the source→ledger flip load-bearing and previously unstated: the
  ledger is *debits negative, credits positive* (`docs/ARCHITECTURE-PROPOSAL.md` §E) and the Amex source
  is *charges positive* (`R02:59`) — and the Amex **PDF carries no sign at all**, encoding direction
  in a separate `CR` marker line beneath the amount (doc 11 §3.2, §3.3). An adapter flips explicitly
  and emits signed minor units (`AmountSignConvention.SIGNED`).
- **The balance identity is per-account-type, not generic.** `checking`/`savings`:
  `prev + credits − debits = closing`. `credit_card`:
  `Vorig + Debiteringen − Crediteringen = Nieuw` — **a charge increases the amount owed** (doc 11 §3.3).
  No column stores a statement balance, so this is a required **adapter acceptance test**, not an
  `invariants.yaml` entry; see Decision 2 there for why the static checker cannot express it.
- **The provider enum is five values:** `enable_banking · amex_pdf · rabobank_pdf · revolut_pdf ·
  manual`. `amex_csv` retired, `revolut_csv` deferred, `google_wallet` excluded. Tier 1 below is
  therefore reachable **only** via `enable_banking` — `End-to-End ID` is not a Rabobank PDF key
  (doc 11 §3.4: three mutually inconsistent shapes across 60 of 106 rows).
- **`import_batch.account_id` is not the row's account.** One Revolut file covers two products and
  two own IBANs without saying which is which, so one file may produce several batches and
  `source_record.account_id` is authoritative per row. Ambiguous ownership is HELD, never inferred.
- **Rabobank type codes are an enum** sourced from the statement's printed legend (doc 11 §4).
  Unknown code → **warn, never guess**.

### Deferred / rejected

Event sourcing, generic rules DSL, importer plugin system, full 5-type GL, materialized views, tag
hierarchy, ML categorization, entity graph, FX revaluation, envelope budgeting, in-module
attachment/goal tables (reference-only FKs instead).

### Contingent assumptions the lane flagged

`entry_reference` stability across re-auth · Revolut NL stable `id` · Amex CSV columns · Rabobank
"American Express" description reliably identifying card payments · SEPA creditor ID parseability ·
ECB daily rates sufficing · single-user forever · PostgreSQL 15+.

**Settled 2026-10-01/02 by the real exports** (`docs/research/11-real-export-verification.md`):

| Assumption | Verdict |
|---|---|
| Rabobank "American Express" description identifies card payments | **CONFIRMED** — the string appears in 4 of 4 statements and the amounts match Amex `Te betalen` exactly every month (doc 11 §4) |
| Revolut NL stable `id` | **STILL UNVERIFIED** — no Revolut CSV was ever supplied (doc 11 §7). `revolut_pdf` is the path instead |
| Amex CSV columns | **MOOT** — the Amex app exports PDF only (`LifeOS-18`) |
| `T-OWN-ACCOUNT-TRANSFERS` holds per account | **FALSE for Revolut** — 34 `To Savings` against 9 `From Savings`; counter-legs live in the Deposit section (doc 11 §3.6) |
| SEPA creditor-ID branch is reachable | **Partly** — Rabobank records do carry `Mandate Identifier / Creditor ID` (36 of 106 rows), but the match to an inbound leg was not tested (doc 11 §7) |

---

# Reconciliation (orchestrator) — 8 corrections

The design is strong. These are the changes I'm making to it, and why.

## 1. 🔴 The balance trigger is broken and would reject every valid entry

As written:

```sql
CREATE TRIGGER trg_journal_entry_balance
AFTER INSERT OR UPDATE ON journal_line
FOR EACH ROW EXECUTE FUNCTION check_journal_entry_balance();
```

A **row-level `FOR EACH ROW` trigger** fires once per line. Inserting line 1 of a two-line entry checks
`SUM(amount_base)` = ±50 ≠ 0 → raises → **the insert fails. No transaction could ever be written.**

**Fix — a deferred constraint trigger, which fires at commit when all lines exist:**

```sql
CREATE CONSTRAINT TRIGGER trg_journal_entry_balance
AFTER INSERT OR UPDATE ON journal_line
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_journal_entry_balance();
```

This is the kind of bug that looks fine in review and is fatal in production. Good catch on review.

## 2. 🔴 `direction` + signed amount is a redundant, self-contradictory encoding

The DDL has `amount_account BIGINT` with
`CHECK (direction='debit' AND amount_account > 0) OR (direction='credit' AND amount_account < 0)`,
while the prose writes `direction='debit'` together with `amount_base = -50`. So the sign convention
flips between the two amount columns, and `direction` is derivable from the sign anyway. Two encodings
of one fact that can disagree = a permanent bug source.

**Fix — drop `direction` entirely. The sign is the direction.** Debits negative, credits positive,
uniformly across both amount columns. Delete the CHECK. This removes an entire class of bug.

## 3. 🟡 Six lifecycle states is one or two too many

`normalized` is a transient value with no user-facing query, and `reconciled` duplicates
`journal_entry.user_verified_at`, which already exists. **Reduce to four:**
`imported` → `pending` → `posted`, plus `duplicate`. Verification lives on the `JournalEntry`.
Keeps "which rows landed but failed the normalizer?" queryable, which is the one thing `imported` buys.

## 4. 🟡 Drop `user_id` from every table

The lane put `user_id` on ~15 tables. **This directly contradicts our own Research 03 decision** to
reject Firefly III's `user_group_id`-on-everything as unnecessary multi-user machinery for a single-user
app. Two of my own lanes disagreed and I am resolving it: **drop it.** You are one person; it is noise on
every row and every query. If multi-user ever arrives, that is a migration regardless.

## 5. 🟡 `Account.provider_account_id` is redundant with `ProviderAccountLink`

Both carry the provider's account identifier. Worse, `UNIQUE (user_id, provider_account_id)` on
`account` will break the moment one real account is visible to two providers — which is your exact
situation, since the Amex card appears in both Amex and Google Wallet. **Keep the identifier only in
`ProviderAccountLink`**, whose unique constraint is correctly scoped
`(institution_id, provider_account_id)`.

## 6. 🟡 Circular FK between `journal_entry` and `source_record`

`journal_entry.source_record_id` and `source_record.journal_entry_id` reference each other. That is a
circular dependency at insert time and one of them is always redundant. **Drop
`journal_entry.source_record_id`** — the FK from `source_record` is the only direction needed.

## 7. 🟡 Defer the empty investment tables

`security`, `holding`, `cost_basis_lot` created empty contradicts the same premature-abstraction rule we
applied to the `dashboard` module in Research 03. **Keep the actual seam** —
`account.account_type = 'investment'` in the CHECK, and nullable `security_id` / `units` /
`price_per_unit` on `journal_line` — using the same unconstrained-BIGINT pattern the lane already used
for `attachment_id` / `goal_id`. **Create the tables when the investment module actually arrives.**

## 8. ✅ I was wrong in Research 08, and the lane is right about double-entry

In Research 08 I wrote that Actual's `transfer_id` was "my leading candidate" and beat Firefly's
three-tier model. **That was wrong, and testing it against your Amex requirement is what shows it.**

Actual's `transfer_id` works because it links *two existing rows*. For the Amex payment there is no
second row to link: the liability balance simply falls with no transaction behind it. Under a single-row
model you must either synthesize a row anyway — which is double-entry by another name — or leave a
dangling `transfer_id`, which is exactly the €100-double-count bug you asked us to design away.

`is_synthesized` + `synthesized_reason` is not ceremony. **It is the mechanism that makes the Amex
payment correct**, and it is the single strongest argument for the double-entry design. BankingSync and
Actual both avoid this problem only because BankingSync's Actual adapter does no transfer detection at
all.

**Keeping double-entry.** The extra cost is one table and a deferred trigger.

---

## One refinement worth adding

**Card-payment detection should learn.** The lane's rule is
`raw_description ILIKE '%american express%'`, which is honest but brittle. After the user confirms the
first match, promote the observed description into a `CategoryRule`-style pattern
(`match_method='user_confirmed'` already records this). By the third month the rule is
`ILIKE 'AMERICAN EXPRESS%'` learned from three real strings rather than guessed once.
Same mechanism the merchant-alias learning already uses — reuse it rather than inventing a new one.
