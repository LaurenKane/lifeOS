# Research 08 — Actual Budget + Wealthfolio

**Lane:** lib-5 (librarian, Specialist B/3) · **Completed:** 2026-09-30 · **Status:** reconciled
**Method:** source inspection across both monorepos.
> Orchestrator reconciliation notes in the final section.

---

## Executive summary

- **Actual Budget is MIT** (© James Long) — not AGPL. Confirmed via the GitHub API
  (`spdx_id: MIT`) and the `LICENSE.txt` hash. `actual-server` is MIT too (identical file SHA).
  **This resolves the licence conflict flagged after Research 04.**
- **Wealthfolio is AGPL-3.0**, with a `TRADEMARKS.md` (© Teymz Inc.) requiring forks to rename, remove
  logos, and say "Forked from Wealthfolio".
- **⚠️ Wealthfolio is Rust/Tauri + SQLite (Diesel ORM) — NOT NestJS/Postgres.** The stack assumption in
  the brief was wrong. Its investment model lives in Rust structs, not portable Postgres entities.
- **Actual's `transfer_id` pattern is the single most reusable idea in this project:** both legs share a
  UUID and are excluded from spending by `transfer_id IS NOT NULL`.
- **Actual's `Payee` table is separate from the transaction payee string and carries `transfer_acct`** —
  the payee of a transfer leg *is* the destination account. Elegant and directly relevant to our
  transfer model.
- **Actual's import dedup is `imported_id` only — no fingerprint.** Same weakness as Firefly for Amex.
- **Wealthfolio's `idempotency_key` hash is the better pattern** and works without bank IDs.
- **Actual has no investment support at all** — investments are just off-budget accounts.
- **Actual's sync is CRDT-ish per-cell LWW with merkle trees + a sync server.** Overkill for one user.
- For a single-user Life OS: **no sync server needed.**

---

## Repo facts

| repo | last verified | last commit | activity | licence | notes |
|---|---|---|---|---|---|
| `actualbudget/actual` | 2026-09-30 | 2026-09 (active) | Very high; multiple commits/week | **MIT** © James Long | No relicensing found. No trademark/branding policy file |
| `actualbudget/actual-server` | 2026-09-30 | 2026-09 (active) | High | **MIT** © James Long | Identical `LICENSE.txt` SHA `3898f37…` |
| `wealthfolio/wealthfolio` | 2026-09-30 | 2026-09 (active) | Very high | **AGPL-3.0** | `TRADEMARKS.md` © Teymz Inc — forks must rename + attribute |

**Monorepo layout:**
- **Actual:** `packages/loot-core` (engine), `packages/sync-server` (bank sync), `packages/desktop-electron`, `packages/desktop-client` (React)
- **Wealthfolio:** `crates/core`, `crates/storage-sqlite` (Diesel), `crates/market-data`, `crates/spending`, `crates/connect`, `apps/server` (Axum), `apps/frontend` (React), `apps/tauri`

---

## Actual Budget

### Data model (`packages/loot-core/src/types/models/`)

| Entity | Fields |
|---|---|
| `Account` | `id, name, offbudget: 0\|1, closed: 0\|1, account_sync_source, last_sync, bank_sync_status` |
| `Transaction` | `id, is_parent, is_child, parent_id, account, category, amount, payee, notes, date, imported_id, imported_payee, starting_balance_flag, transfer_id, sort_order, cleared, reconciled, tombstone` |
| `Payee` | `id, name, transfer_acct?: AccountEntity['id'], favorite?, learn_categories?` |
| `Category` | `id, name, is_income, cat_group, sort_order, tombstone` |
| `CategoryGroup` | `id, name, sort_order, tombstone` |
| `Rule` | `id, stage: 'pre'\|null\|'post', conditionsOp: 'or'\|'and', conditions[], actions[], tombstone` |
| `Schedule` | `id, name, rule, next_date, completed, posts_transaction, tombstone` |
| `Tag` / `Note` | `id, name, color` / `id, content` |
| `Preferences` | per-account + global, incl. `csv-mappings-${string}`, `camt-swap-payee-memo-${string}` |

**`Payee` is a real table, not a string.** Transactions FK to `payees.id`; `imported_payee` keeps the raw
bank string. **`Payee.transfer_acct`** points at an `Account` — i.e. for a transfer leg, the payee *is*
the counterparty account.

### Transfers — how it avoids double-counting

`transfer_id?: TransactionEntity['id']` on `Transaction`. Both legs share the UUID; the payee of each leg
is set to the *destination account*, not a real merchant. Spending queries exclude
`transfer_id IS NOT NULL`. Off-budget accounts also don't count toward budget spending.

`mergeTransactions()` (`server/transactions/merge.ts`) handles merging duplicate transfers:

```typescript
const aTransferId = a.transfer_id, bTransferId = b.transfer_id;
if (!aTransferId && !bTransferId) return mergeTransactionsNoTransfer(a, b);
const transferAccount = aTransferId ? a.payee : b.payee;
await setTransfers([a.id, b.id, aTransferId, bTransferId], null);
const transferId = await mergeTransfers(aTransferId, bTransferId);
```

### Import formats & dedup

`server/transactions/import/parse-file.ts` dispatches by extension:
`.csv/.tsv` → `parseCSV` · `.ofx/.qfx` → `parseOFX` · `.qif` → `parseQIF` · `.xml` → `parseCAMT` (CAMT.053).

**Dedup is `imported_id` only** — the bank's FIT ID from OFX/CAMT. **No hash/fingerprint.**
`mergeTransactions()` conflict rule:

```typescript
// if one is imported through bank sync and the other is manual, keep the imported transaction
if (b.imported_id && !a.imported_id) { return { keep: b, drop: a }; }
```

> Same fatal gap for us: **Amex exports have no FIT ID** (Research 02), so Actual's dedup would duplicate
> every row on re-import.

### Rules engine

`stage: 'pre' | null | 'post'`, `conditionsOp: 'or' | 'and'`, `conditions[]`, `actions[]`.
Actions: `SetRuleAction` (category/payee/account), `SetSplitAmount`, `LinkSchedule`, `PrependNote`,
`AppendNote`, `DeleteTransaction`. Conditions over `date, amount, payee, account, category, notes` with
`is, isNot, contains, matches, gt, lt, between`.
**Not learnable** — no ML, no auto-suggest from manual corrections.

### Local-first & sync

- **Storage:** SQLite (`better-sqlite3` in Electron, `sql.js` in browser).
- **Sync model:** CRDT-inspired. Every cell change is a row in `messages_crdt (timestamp, dataset, row, column, value)`.
- **Conflict resolution:** **last-write-wins per cell** (`replay.ts`: "Timestamp order gives last-write-wins per cell").
- **Anti-entropy:** merkle tree (`repair.ts` → `rebuildMerkleHash()`).
- **Sync server:** `packages/sync-server`, a Node/Express app holding `messages_crdt` + `messages_clock` per budget file. The desktop app can run a local instance.
- **Bank sync requires the sync server** but is optional for local-only use. Providers: `goCardless, simpleFin, pluggyai, enableBanking, akahu` — **Enable Banking is already a supported provider**.

### Storage & API coupling

SQLite-specific throughout (`?` placeholders, `AUTOINCREMENT`, `PRAGMA`). Migrating to Postgres means
rewriting queries. The "server API" is handler functions exposed over IPC or the sync protocol
(`/sync/get-messages`, `/sync/apply-messages`) — **not a clean REST API**.

### Investments — none

Accounts have `offbudget: 0|1`. Investments are off-budget accounts. **No lot tracking, no cost basis, no
realized/unrealized gains, no price quotes.** Mock data's `fillInvestment()` just makes random
transactions in an "Investment" off-budget account. **Actual is a budgeting tool, not an investment tracker.**

### Europe / IBAN

`sync-server/src/util/payee-name.ts` masks for display: `( + iban.slice(0,4) + ' XXX ' + iban.slice(-4) + ')`.
GoCardless normalises per bank (e.g. `american_express_aesudf1.ts` masks IBAN as PAN). CAMT.053 import
handles European formats.

---

## Wealthfolio

### ⚠️ Stack correction

**Rust (Tauri) + React + Axum; SQLite via Diesel ORM.** Schema in
`crates/storage-sqlite/src/schema.rs` (Diesel-generated): `accounts, activities, assets, lots,
lot_disposals, quotes, exchange_rates, …`
Self-hostable via Docker Compose, the Tauri app, or `apps/server` as a standalone Axum binary.
Migrations via the Diesel CLI. Optional device sync in `crates/device-sync`.

### Investment domain model ⭐

**`Account`** — `id, name, account_type, group, currency, is_default, is_active, platform_id,
account_number, meta, provider, provider_account_id, is_archived, tracking_mode`
(`TrackingMode = Transactions | Holdings | NotSet`)

**`Asset`** (the security/instrument) — `id, kind, name, display_code, quote_mode, quote_ccy,
instrument_type, instrument_symbol, instrument_exchange_mic, instrument_key, provider_config, metadata, is_active`
- `AssetKind` = `Investment | Property | Vehicle | Collectible | PreciousMetal | PrivateEquity | Liability | Other | Fx`
- `QuoteMode` = `Market | Manual`
- `InstrumentType` = `Equity | Crypto | Fx | Option | Metal | Bond`
- `instrument_key` is a **DB-generated** composite, e.g. `"SEC:AAPL@XNAS"`, `"CRYPTO:BTC/USD"`
- `metadata` carries `OptionSpec`, `BondSpec`, `identifiers.isin`

**`Activity`** (the investment transaction ledger):
```
id, account_id, asset_id (NULL for pure cash movements), activity_type, activity_type_override,
subtype (DRIP, STAKING_REWARD, BONUS, REBATE, …), status (Posted|Pending|Draft|Void),
activity_date, settlement_date, quantity, unit_price, amount, fee, tax, currency, fx_rate,
notes, metadata, source_system (SNAPTRADE|PLAID|MANUAL|CSV), source_record_id, source_group_id,
idempotency_key, import_run_id, is_user_modified, needs_review
```

**`ActivityType`** (14): `Buy, Sell, Dividend, Interest, Deposit, Withdrawal, TransferIn, TransferOut,
Fee, Tax, Split, Credit, Adjustment`

**`Holding`** — `id, account_id, holding_type (Cash|Security|AlternativeAsset), is_closed, instrument,
asset_kind, quantity, open_date, lots, contract_multiplier, local_currency, base_currency, fx_rate,
market_value, cost_basis, unrealized_gain, realized_gain, total_gain, income, day_change, total_return,
weight, as_of_date`

**`Lot`** — `id, position_id, acquisition_date, quantity, remaining_quantity, cost_basis, fx_rate_to_account`

**Cost basis config** — `CostBasisMethod {Fifo, Lifo, Wac}` (only FIFO actually supported),
`CostBasisProfile {Generic, CanadaAcb}` (only Generic), `PoolingScope {Account, Portfolio}` (only Account).

### Cost basis, gains, FX, net worth

- **FIFO lot-based.** `HoldingsCalculator` processes activities, maintaining lots; `LotDisposal` records
  when a sell consumes a lot.
- **Gains** live on `Holding`: `unrealized_gain, realized_gain, total_gain, income, total_return` — each
  a `MonetaryValue { local, base }`.
- **FX:** `FxService` + `ExchangeRate` repo + `CurrencyConverter`. Activities store the `fx_rate` at
  transaction time (point-in-time rates, not recomputed).
- **Net worth:** `NetWorthService` aggregates across accounts; `AssetKind::Liability` **reduces** net
  worth. Alternative assets (Property/Vehicle/Collectible) use manual valuation and are excluded from
  TWR/IRR.
- **Dividends/fees in gains:** dividends are `ActivityType::Dividend` with `amount` → income, not gain.
  Fees are `ActivityType::Fee`, reducing net contribution. `flow_classifier.rs` splits flows into
  `External` (Buy/Sell/Deposit/Withdrawal/Transfer*) vs `Internal`.

### Data providers & caching

`crates/market-data/src/provider/`: **Yahoo Finance** (equities/ETF/crypto/FX), **Alpha Vantage**
(free tier 5 calls/min), **Metal Price API** (XAU/XAG), **US Treasury** (bonds), plus a custom scraper.
Routing via `ResolverChain` + `RulesResolver` mapping canonical `(symbol, MIC)` → provider symbols;
`CircuitBreaker` + `RateLimiter` for failure isolation. Quotes cached in the `quotes` table.
**Privacy:** self-hosted SQLite; providers receive **only symbol lookups, never user data**.

### CSV import — strategy-based

`ImportTemplate` with `TemplateKind` = `CsvActivity | CsvHoldings | BrokerActivity`; user-configurable
`field_mappings: HashMap<String, FieldMappingValue>`.
**Idempotency:** `compute_idempotency_key()` hashes
`account_id + activity_type + date + asset_id + quantity + amount + fee + source_record_id`,
with a **DB unique constraint** on `idempotency_key`. Flow: parse → validate → resolve assets →
compute keys → dedupe → bulk insert. `ActivityImport` carries `duplicate_of_id`, `force_import`, `is_draft`.

---

## Steal / avoid

### Steal from Actual
1. **`transfer_id` linking** — both legs share a UUID, excluded from spending. Simple and correct.
2. **Separate `payees` table with `transfer_acct`** — clean merchant normalization *and* a natural
   transfer representation.
3. **`imported_id` + imported-wins-over-manual merge rule.**
4. **`tombstone` soft-delete** everywhere — recoverable, sync-friendly.
5. **`starting_balance_flag`** — distinguishes opening balance from real transactions.
6. **`Rule` shape** — `stage` + `conditionsOp` + `conditions[]` + `actions[]`.
7. **CAMT.053 import** (not needed for v1, but a known-good reference).

### Steal from Wealthfolio
1. **`Activity`** — 14-type enum with `subtype`, `status`, `source_system`, `idempotency_key`.
2. **`Asset`** — `kind` + `instrument_type` + generated `instrument_key` models diverse asset classes well.
3. **Lot-based FIFO cost basis** — `Lot` + `LotDisposal`.
4. **`MonetaryValue { local, base }`** — multi-currency done right.
5. **`idempotency_key` hash + unique constraint** — works without bank IDs.
6. **`HoldingType` enum** — `Cash | Security | AlternativeAsset` with different valuation logic.
7. **Provider abstraction** — `ResolverChain` + `CircuitBreaker` + `RateLimiter`.

### Avoid from Actual
1. **Sync server architecture** — `messages_crdt`, merkle trees, multi-device sync. Massive overkill.
2. **SQLite-coupled SQL** — hard to move to Postgres.
3. **Investment attempts** — don't stretch off-budget accounts into investment tracking.
4. **Rules engine as-is** — not learnable, no auto-suggest.

### Avoid from Wealthfolio
1. **Rust/Diesel coupling** — cannot copy structs into Python/SQLAlchemy.
2. **SQLite-only schema** — Postgres would need a full rewrite.
3. **AGPL** — copying code triggers copyleft.
4. **Complexity** — `HoldingsCalculator` with `SideEffectBuffer`, `ProjectionRun`, `SnapshotRecalcMode`
   is sophisticated and heavy for one user.

---

## Licensing

| repo | SPDX | Copyright | Implication |
|---|---|---|---|
| `actual` | **MIT** | James Long | ✅ Copy freely. No copylink. No trademark policy found |
| `actual-server` | **MIT** | James Long | ✅ Same |
| `wealthfolio` | **AGPL-3.0** | FSF text / Teymz Inc (trademark) | ⚠️ Copying = AGPL. Forks must rename, remove logos, say "Forked from Wealthfolio" |

---

## Fact confidence

**CONFIRMED:** Actual MIT (GitHub API `spdx_id` + LICENSE hash); actual-server MIT (identical SHA);
Wealthfolio AGPL-3.0 (LICENSE + API); Wealthfolio `TRADEMARKS.md` (© Teymz Inc); `transfer_id` linking
(`transaction.ts` + `merge.ts`); import formats (`parse-file.ts`); CRDT LWW sync (`sync/index.ts` +
`replay.ts`); Actual has no investment support; Wealthfolio is Rust/Tauri/SQLite (`Cargo.toml`,
`crates/storage-sqlite/`, `apps/tauri/`); Diesel ORM (`schema.rs`); 14-value `ActivityType`; FIFO cost
basis (`CostBasisMethod::Fifo` default, `ensure_supported_for_calculation` rejects others); provider
directory contents; `compute_idempotency_key()`; Actual bank-sync provider list; rules pre/post stage and
non-learnability; `AssetKind::Liability` in `net_worth_service.rs`; Actual has no branding policy.

---

# Reconciliation & challenges (orchestrator)

## 1. ✅ Licence conflict **resolved**: Actual is MIT

Research 04 flagged this as "the one licence where a wrong answer would actually matter." Settled:
**Actual is MIT.** No copyleft, no trademark policy, no relicensing found. `actual-server` is MIT with
an identical licence file.

**But MIT does not change the architectural decision.** Actual is a full app with a SQLite database, a
Electron shell, and a sync server. Using it as a backend means exactly what the user said they do not
want: a separate financial database plus a synchronization layer between two independently-evolving
domain models. **MIT makes copying its *patterns* free; it does not make it the right shape.**

The real payoff of MIT is narrower and still valuable: **we may copy Actual's `transfer_id` pattern and
`Payee.transfer_acct` design without any licence concern whatsoever.** No clean-room discipline needed.
That is worth having.

## 2. ⚠️ Correcting the brief: Wealthfolio is Rust + SQLite, not NestJS + Postgres

Both the user's framing and my own briefing to this lane assumed a JS/Postgres stack. It is **Rust (Tauri)
+ Axum + SQLite via Diesel**. Consequence: its investment model — the best one available — **cannot be
lifted even in principle**. Everything must be clean-room reimplemented in Python/SQLAlchemy.

This *raises* the value of Research 08 and *lowers* the cost of ignoring it: we were never going to copy
it, so treat it purely as a design reference. The concepts (Activity / Asset / Holding / Lot /
idempotency_key / MonetaryValue) are excellent and worth reimplementing later.

## 3. Two patterns worth adopting **immediately and without hesitation** (MIT, ~20 lines each)

**a) `transfer_id` on the transaction** — both legs share a UUID; spending queries exclude
`transfer_id IS NOT NULL`. This is the simplest correct answer to "transfers must not count as
spending" that any of the four projects offers. It competes directly with Firefly's three-tier
double-entry model, and it is *far* cheaper. **This is my leading candidate for the design.**

**b) `Payee.transfer_acct`** — the payee of a transfer leg is the destination *account*. This makes
transfers queryable and the payee table do double duty, with no extra machinery.

**c) `starting_balance_flag`** — trivially small, and it prevents a class of reporting bug where an
opening balance is counted as income. Adopt.

## 4. `idempotency_key` **validates our Tier-3 fingerprint design**

Wealthfolio independently arrived at: hash a canonical tuple of
`account + type + date + asset + quantity + amount + fee + source_record_id`, with a **DB unique
constraint**. That is precisely the Tier-3 fingerprint + occurrence counting that ora-1 designed and that
Research 07 made provider-aware. **Third independent confirmation** (after BankingSync's
`date|amount|payee|occurrence-index` and Firefly's `entryReference`) that content-hash identity is the
industry-standard answer. Settled.

## 5. Cautionary detail: don't declare enum values you don't implement

`CostBasisMethod {Fifo, Lifo, Wac}` — but `ensure_supported_for_calculation` **rejects Lifo and Wac**.
A permissive-sounding enum that lies at runtime is a trap. When we add investments, we should either
implement FIFO only (and say so) or genuinely implement the others. Worth remembering when the
investment module eventually arrives.

## 6. `is_user_modified` + `needs_review` — direct hits on our requirements

Wealthfolio's `Activity` carries `is_user_modified: bool` and `needs_review: bool` as first-class
columns. These map exactly onto the user's "user overrides" and "review queue" requirements, and they
confirm the flags belong in the schema rather than being derived. Adopt both names.

## 7. Actual's bank sync already supports Enable Banking

`useBuiltInBankSyncProviders.ts` lists `goCardless, simpleFin, pluggyai, enableBanking, akahu`. Useful in
two ways: it corroborates that Enable Banking is a mainstream integration target, and GoCardless being
a first-class provider there is a mild positive signal for our fallback (Research 01).

But recall Research 04's finding: **Actual's sync path performs no transfer detection** — everything
lands as a payment to a payee. So even using Actual, transfer matching would be ours to build. Another
reason the decision is "build our own."

## 8. Reject the sync server, definitively

Actual's CRDT (per-cell LWW + merkle anti-entropy + a sync server) exists to reconcile *multiple devices
mutating the same data concurrently*. We have one user and one server. Research 03 reached the same
conclusion independently. **Settled: server is the source of truth, thin client, no sync layer.** The
future-Life-OS offline story is a genuinely different problem and should not be pre-solved here.
