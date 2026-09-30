# Research 05 — Firefly III + data-importer

**Lane:** lib-4 (librarian, Specialist B/2) · **Completed:** 2026-09-30 · **Status:** reconciled
**Method:** source inspection. Many claims are explicitly marked UNCERTAIN by the lane — those are real
gaps in what was read, not hedges.
> Orchestrator reconciliation notes in the final section.

---

## Executive summary

- **Firefly III's three-tier model is the one big idea worth copying:**
  `TransactionGroup` → `TransactionJournal` → `Transaction`. The UI-level "transaction" is a *group*;
  each *journal* is one double-entry event; each *Transaction* row is one account-side leg.
- **Transfers are a journal *type*, not a special row.** `TransactionTypeEnum::TRANSFER` with two
  `Transaction` rows on two asset accounts. `isTransfer()` checks the journal's type. Transfers therefore
  *structurally cannot* be spending.
- **data-importer is NOT standalone.** Hardwired to Firefly III's REST API via `FFIIIApiSupport`.
  Repointing it at our own API means rewriting `ApiSubmitter` (29 KB) and every `RoutineManager`.
- **Its dedup is identifier-only and weak.** "Cell" method: search Firefly for a matching
  `external_id` (or description/reference/notes); skip if found. **No fuzzy matching, no date+amount
  matching, no update path — skip-or-create only.**
- **No XLSX, PDF, OFX or QIF importers exist.** So data-importer is useless for our Amex path.
- **Firefly III has no investment model at all.** No `Investment`, `Security` or `Holding`. `PiggyBank`
  is savings goals. Confirmed gap.
- **Import provenance is a tag**, literally `"Data Import on 2026-09-30 @ 14:30"`. There is no
  `import_batch` table. This is **inadequate** for our "never lose provenance" requirement.
- **Both repos are AGPL-3.0**, © `james@firefly-iii.org`.

---

## Repo facts

| | firefly-iii | data-importer |
|---|---|---|
| Last verified | 2026-09-30 | 2026-09-29 |
| Last commit | 2026-09-30 | 2026-09-29 |
| Open issues | 168 | 1 |
| Stars / forks | 24,783 / 2,312 | 839 / 135 |
| Language | PHP | PHP |
| **License** | **AGPL-3.0** | **AGPL-3.0** |
| Copyright | `james@firefly-iii.org` | `james@firefly-iii.org` |

Both actively maintained (pushed within 24h). Full AGPL text at `LICENSE`; every source header carries the
copyright + AGPL notice. **No additional trademark/attribution clause found in the licence file itself.**

---

## The three-tier transaction model ⭐

| Table | Model | Fillable | Role |
|---|---|---|---|
| `TransactionGroup` | `app/Models/TransactionGroup.php` | `user_id, user_group_id, title` | The user-facing "transaction." A container for 1+ journals |
| `TransactionJournal` | `app/Models/TransactionJournal.php` | `transaction_type_id, date, description, transaction_currency_id, bill_id, completed, order` | One double-entry event. **The type lives here** |
| `Transaction` | `app/Models/Transaction.php` | `account_id, transaction_journal_id, description, amount, native_amount, native_foreign_amount, identifier, transaction_currency_id, foreign_currency_id, foreign_amount, reconciled` | One account-side leg |

**Shapes:**
- **Withdrawal** → 1 journal, 2 `Transaction` rows: one on the asset account (negative), one on the expense account (positive).
- **Transfer** → 1 journal, 2 `Transaction` rows: one on each asset account.
- **Split** → 1 journal, 3+ rows: one on the asset account, several on different expense accounts.

**`journal_id` vs `group_id`:** `Transaction.transaction_journal_id` → the double-entry event.
`TransactionJournal.transaction_group_id` → the user-facing group. Usually 1:1; the split lets one group
hold several journals under one title.

**Transaction types** (`app/Enums/TransactionTypeEnum.php`): `DEPOSIT, WITHDRAWAL, TRANSFER,
OPENING_BALANCE, RECONCILIATION, LIABILITY_CREDIT, INVALID`.

---

## Accounts

`AccountTypeEnum` — 14 flat values:
- **Asset-like:** `ASSET, DEFAULT, CASH, RECONCILIATION`
- **Liability-like:** `CREDITCARD, DEBT, LOAN, MORTGAGE, LIABILITY_CREDIT`
- **Special:** `EXPENSE, REVENUE, BENEFICIARY, INITIAL_BALANCE, IMPORT`

`Account` model: `account_type_id, name, active, virtual_balance, iban, native_virtual_balance, order`.

**Net worth** = assets add, liabilities subtract. That distinction is the essential idea.
`account_role` was **not found** in the `Account` model fillable — possibly in a migration. **UNCERTAIN.**

---

## Rules engine

`Rule` fillable: `rule_group_id, order, active, title, description, user_id, user_group_id, strict`.
Has-many `ruleTriggers()` + `ruleActions()`; belongsTo `RuleGroup`. Has `stop_processing` and `strict`
boolean casts.

- **No JSON DSL** — structured DB records with trigger/action sub-records. CONFIRMED.
- **User-editable** via full CRUD (title/description/active/order/active suggest UI management).
- **Exact trigger/action vocabulary: UNCERTAIN** (not read).
- **Execution engine class: UNCERTAIN** (not located).
- **Learnability: UNCERTAIN** — no evidence that a manual category correction auto-creates a rule.

> The user explicitly wants "if I manually correct something, the system remembers." **Firefly III does
> not appear to do this.** That is a genuine gap, not a licensing question.

---

## Categories, tags, budgets, recurring

| Concept | Model | Note |
|---|---|---|
| Categories | `Category` | `belongsToMany` transactions. **LIKELY flat, not a tree** — no parent/child fields seen. A transaction can have multiple categories |
| Tags | `Tag` | Free-form M2M labels |
| Budgets | `Budget`, `BudgetLimit` | Limits relate to date ranges + amounts. `AutoBudget` model suggests auto-generation |
| Recurring | `Recurrence`, `RecurrenceTransaction`, `RecurrenceRepetition`, `RecurrenceMeta` + `RecurringTransactionTrait` (13 KB) | 4 models + a large trait. Complex |
| Attachments | `Attachment` | Polymorphic `morphMany` via `attachable` — can attach to journals, accounts, etc. |

> Firefly's categories being **flat and many-to-many** conflicts with the user's stated need for
> `category` + `subcategory` (one mutually-exclusive hierarchy per transaction).

---

## Multi-currency — four fields

`Transaction` carries `amount, foreign_amount, native_amount, native_foreign_amount` plus
`transaction_currency_id, foreign_currency_id`. Rates live in `CurrencyExchangeRate`.

- `amount` — in the transaction's currency
- `foreign_amount` — in the foreign currency
- `native_amount` — converted to the user's default
- `native_foreign_amount` — foreign amount converted to default

Robust, but the native/foreign pair is **over-engineered for EUR-default with occasional foreign Amex
spend.** Simpler: `amount` + `currency_code` + `amount_base` (converted) + an exchange-rate table.

---

## Investments — no support

**No `Investment`, `Security`, or `Holding` model.** No investment/brokerage account type. `PiggyBank`
models are savings goals. **Verdict: Firefly III does not do investments.** Confirmed gap — and one reason
Wealthfolio (Research 06) matters more for that domain.

---

## Import provenance — a tag, not a table

`ApiSubmitter::parseTag()` + `addTagToGroups()` create a tag like
`"Data Import on 2026-09-30 @ 14:30"` on every imported group. **There is no `import` table.** CONFIRMED
(absence of import models in `app/Models/`).

> For our requirement — every transaction traceable to its import batch, provider, and raw payload —
> **this is inadequate.** We need a real `import_batch` + `import_row` pair. Another point for building
> our own.

---

## data-importer architecture

**Standalone: NO.** Separate Laravel app, but communication is **exclusively** Firefly III's REST API
via `GrumpyDictator/FFIIIApiSupport` (`ApiSubmitter.php` imports `GetSearchTransactionsRequest`,
`PostTransactionRequest`, `PutTransactionRequest`, `PostTagRequest`, `PostFinishBatchRequest`).
`ConversionRoutineFactory` builds managers that all submit to Firefly III. Repointing = rewrite
`ApiSubmitter` (29 KB) + all `RoutineManager` classes.

**Formats** (`ConversionRoutineFactory.php` + `app/Services/`):

| Kind | Supported |
|---|---|
| File | CSV, **CAMT.052 / CAMT.053** (auto-detected by `FileContentSherlock`) |
| API / Open Banking | Nordigen, EnableBanking, Sophtron, SimpleFIN, LunchFlow, Akahu |
| **Not present** | **XLSX, PDF, OFX, QIF** |

> The docs/topics mention gocardless/nordigen/salt-edge/spectre/psd2, but the actual service directories
> are: `Akahu, CSV, Camt, EnableBanking, LunchFlow, Nordigen, Session, Shared, SimpleFIN, Sophtron`.
> Trust the directories.

---

## data-importer dedup — the "cell" method

`ApiSubmitter::uniqueTransaction()`:

1. Only runs when `configuration->getDuplicateDetectionMethod() === 'cell'`.
2. For each import line, extracts `uniqueColumnType` (default `external_id`; also `description`,
   `internal_reference`, `notes`).
3. `searchField($field, $value)` queries Firefly's search API with `field:"value"`.
4. Match found → **skip** (return false). Also checks soft-deleted groups via `searchFieldUsingCount()`.
5. No match → proceed.

**Second layer:** Firefly itself can return a validation error containing `"Duplicate of transaction #"`
(`isDuplicationError()`).

### What it does not do

| Missing | Consequence for us |
|---|---|
| Fuzzy matching | Any bank without a stable `external_id` → **duplicates on every re-import** |
| Date + amount matching | No structural fallback |
| **Update path** | **Skip-or-create only.** A re-fetched transaction whose description changed is skipped, never updated. No amount correction on booking |

> **For Amex this dedup fails completely** — Amex exports have no stable ID (Research 02). Every re-import
> of `September.csv` would duplicate every row.
>
> **This is weaker than BankingSync's design** (Research 04), which has a three-layer scheme with a
> pending map and an update-in-place path. Clear evidence that neither project's dedup is adequate for us.

---

## CAMT support

Library `genkgo/camt` (`Genkgo\Camt\Camt052\DTO\Report`, `Camt053\DTO\Statement`).
`AbstractTransaction` (20 KB) is the field-mapping layer, using `getFieldByIndex($field, $index)`.

| Level | Mapped fields |
|---|---|
| A (message) | `messageId` |
| B (statement) | `statementId`, `statementCreationDate`, `statementAccountIban`, `statementAccountNumber` |
| C (entry) | `entryReference`, `entryAmount`, `entryAmountCurrency`, `entryValueDate`, `entryBookingDate`, `entryBtcDomainCode`, `entryBtcFamilyCode`, `entryBtcSubFamilyCode`, `entryAccountServicerReference`, `entryAdditionalInfo` |
| D (detail) | `entryDetailAmount`, `entryDetailAmountCurrency`, `entryDetailOpposingAccountIban`, `entryDetailOpposingAccountNumber`, `entryDetailOpposingAccountName`, `entryDetailRemittanceInformationUnstructured`, `...StructuredBlock...`, `entryDetailEndToEndId`, `entryDetailUuidEndToEndReference`, `entryDetailAccountServicerReference` |

**Account identification:** via `statementAccountIban` (Level B) and `entryDetailOpposingAccountIban`
(Level D). `getOpposingParty()` decides creditor vs debtor from the amount sign.
**Dedup:** `entryReference` or `entryAccountServicerReference` becomes the `external_id`.

> Note: `entryReference` here is the CAMT standard field — **the same field Enable Banking surfaces as
> `entry_reference`** (Research 01). Consistent. CAMT.053 gives us a well-specified identity field that
> plain bank CSVs do not.

---

## Ideas to adopt

1. **The group → journal → leg tiering.** The single most valuable pattern. Separates "what the user sees"
   from "one double-entry event" from "one account leg."
2. **Type on the journal, not the leg** — makes "a transfer is not spending" structural rather than a
   convention someone must remember.
3. **Double-entry for every transaction**, so the ledger self-balances and is auditable.
4. **Asset vs liability account classification** — essential for net worth.
5. **A small transaction-type enum** (`WITHDRAWAL, DEPOSIT, TRANSFER, …`) as a closed vocabulary.
6. **Trigger/action rule model with ordering + stop-processing** — a good shape for layered categorization.
7. **CAMT field-mapping architecture** — a dedicated mapping layer (not logic in the parser) is the right
   structure for our importers.
8. **Polymorphic attachments** — a good pattern if we ever want receipts linked to transactions.

## Ideas to reject (over-engineered for one person)

1. **14 account types** → we need ~5 (asset, liability, expense, revenue, equity/initial).
2. **`user_group_id` everywhere** — multi-user machinery; we are one user.
3. **The webhook model family** (6 models) — we need none.
4. **Piggy banks** — savings goals are a different concern from the ledger.
5. **Bills** — recurring tracking, not core ledger.
6. **`AuditLogEntry`** — compliance, not personal use.
7. **Object groups / `GroupMembership`** — account categorization we don't need.
8. **The full rules engine** — the user asked for a 7-layer engine with learnability; Firefly has neither
   learnability nor a simpler model. Ordered "first match wins" is better for us.
9. **`Recurrence*` (4 models + 13 KB trait)** — a simple "suggest recurring from amount+description+period"
   is sufficient.
10. **`SoftDeletes` on every model** — hard deletes plus backups are simpler and safer for a personal ledger.
11. **Four-field currency** — simplify to `amount` + `currency_code` + `amount_base`.

---

## Licensing

Both **AGPL-3.0**, © `james@firefly-iii.org`. No additional trademark clause in the licence file.

| Use | Consequence |
|---|---|
| Personal use | **Unrestricted.** No obligation. |
| Copying code into Life OS | Life OS must be **AGPL-3.0** |
| Derived work / network service | AGPL §13 — must offer source to users interacting over a network |
| Study / clean-room reimplementation | **Safe.** Copyright protects expression, not ideas |
| data-importer repointed at our API | Still a derivative work → AGPL-3.0 |
| If Life OS ever goes public/commercial | Must open-source the entire application |

**Recommendation:** reference for domain-model ideas; write our own code. Do not copy files.

---

## Fact confidence

**CONFIRMED (file path / API):** AGPL-3.0 both repos; copyright holder; the three-tier model and all three
`$fillable` arrays; `AccountTypeEnum` 14 values; `TransactionTypeEnum` 7 values; `Rule` model structure;
`RuleTrigger`/`RuleAction` existence; no JSON DSL; no investment models; data-importer API-only coupling;
identifier-based dedup in `ApiSubmitter::uniqueTransaction()`; no update-existing logic; CAMT via
`genkgo/camt`; no XLSX/PDF/OFX/QIF importers; the six Open Banking service directories; tag-based
provenance; repo metadata.

**UNCERTAIN (real gaps — not read):** exact trigger/action vocabulary; the rule-execution engine class;
whether `account_role` exists in migrations; whether manual category changes create rules.

**LIKELY:** categories are flat; no dedicated import table; Firefly release cadence.

---

# Reconciliation & challenges (orchestrator)

## 1. Firefly III's dedup is **worse** than BankingSync's, and unusable for Amex

| | BankingSync (04) | Firefly data-importer (05) |
|---|---|---|
| Provider ID | 3 layers, DB-enforced | 1 identifier search |
| Pending → booked | Yes, update-in-place | No |
| Fuzzy matching | Yes (Fellegi–Sunter) | **No** |
| Update existing | Yes (`MergePatch`/`AmountPatch`) | **No — skip-or-create only** |
| Amount correction on booking | Yes | **No** |

For Amex, which has **no stable IDs at all** (Research 02), Firefly's approach produces **a full duplicate
of every row on every re-import**. This is a decisive, concrete reason to build our own ingestion — stronger
than the licensing argument alone.

## 2. data-importer is ruled out twice over

- **Licence:** AGPL-3.0, and repointing it at our API still yields a derivative work.
- **Capability:** it has **no PDF, XLSX, OFX or QIF importer** — i.e. it cannot read the Amex files that
  are our primary data source.

Both reasons independently disqualify it. No further analysis needed.

## 3. ⚠️ Provenance: Firefly is a model of what **not** to do

Firefly tags transactions `"Data Import on 2026-09-30 @ 14:30"` and has **no import table**. That is fine
for Firefly; it is **insufficient** for the user's explicit requirement that raw data never be lost and
every transaction be traceable to its batch and raw payload. We need real `import_batch` + `import_row`
tables with the raw bytes retained. Research 04's "replay from raw" recommendation is therefore not
optional — it is the correct answer to a weakness in the reference implementation.

## 4. The three-tier model: adopt the *concept*, question the *cost*

Firefly's `Group → Journal → Leg` is elegant, but it is three tables and two joins for a single €3.20
coffee, in an app that must stay "understandable by one person." Full double-entry is a real
philosophical commitment (a general ledger, with balancing invariants to maintain).

**Open for the synthesis:** whether we take the *full* three-tier model, or the **narrower lesson** —
which is the genuinely portable insight:

> **Put the transaction type on the grouping, not on the leg, so "a transfer is not spending" is
> structural rather than a convention.**

That principle can be honoured in a simpler schema. The data-model lane (ora-1) was explicitly asked to
pick one and defend it; its answer governs, and I will reconcile it against this.

## 5. Two mismatches with the user's stated requirements

| User wants | Firefly III | Action |
|---|---|---|
| `category` + `subcategory`, one mutually-exclusive hierarchy per transaction | Categories are **flat** and **many-to-many** | We build a real 2-level hierarchy with a single-category-per-transaction constraint |
| Categorization that **learns from manual corrections** | No evidence of learnability | Layer 5 of the requested 7-layer engine is ours to build; neither reference implements it |

## 6. Useful confirmation: `entryReference` is the *same* concept across both sources

Firefly's CAMT `entryReference` and Enable Banking's `entry_reference` are the same ISO 20022 field. That
means the CAMT.053 identity model and the Enable Banking identity model **converge**, which lets one
provider-agnostic identity strategy serve both — a good argument for a single `source_record` abstraction
keyed on `(provider, external_id)` rather than provider-specific columns.

**Not relevant for v1**, though: the user's Dutch banks are reached via Enable Banking, not by
downloading CAMT files. Worth remembering if a bank ever offers a CAMT download.
