# Life OS — Architecture Proposal

**Discovery phase deliverable · 2026-09-30**
Sources: `docs/research/01`–`09`. All external claims labelled **CONFIRMED / LIKELY / UNCERTAIN /
NEEDS USER TESTING**.

---

## A. Executive summary

**Build it yourself. Your Option A was right — but not for the reason you expected, and one of your
own assumptions was stronger than you gave yourself credit for.**

Three findings decided it:

1. **Neither reference implementation has adequate deduplication for your data.** Firefly's importer
   dedups on a bank-supplied ID only. Actual dedups on `imported_id` only. **Amex exports contain no
   stable ID at all** (CONFIRMED — Amex's own IDs are documented to change). Re-import
   `September.csv` through either and you get a complete duplicate of every row. This alone makes both
   unusable as your ingestion layer.
2. **No provider links Amex NL under PSD2 — not one.** Amex has stated that EU card accounts outside
   UK/France/Sweden/Finland are not "payment accounts" under PSD2. Enable Banking decommissioned Amex
   in SE/FI in March 2025. So Amex is **file import permanently**, and any architecture assuming
   otherwise is building on sand. Your instinct here was correct.
3. **Enable Banking explicitly permits exactly your use case, free, with no contract.** ToS (2026-01-09)
   allows "personal use of private individuals" in Production restricted mode. Rabobank is CONFIRMED.
   Revolut NL is **UNCERTAIN** and is the single go/no-go to verify first.

**On the banking projects:** BankingSync is the closest match in shape — same architecture you wanted to
build — but it is **AGPL-3.0**, written in **Go**, and keeps its dedup state in its own SQLite, which is
precisely the two-databases-plus-sync outcome you rejected. **Study its pending→booked matcher; do not
embed it.** Firefly III and Actual are full applications, not libraries. Actual is MIT, so its patterns
are free to copy.

**The one design decision that matters most:** transactions must be **double-entry**
(`JournalEntry` + ≥2 `JournalLine`s), because your Amex requirement forces it. A €50 Amex purchase is an
expense on a liability account; a later Rabobank −€50 has **no matching row** to link to — the liability
balance simply falls. A single-row model can only leave a dangling link (the €100 double-count you asked
us to design away) or synthesize a row anyway. Synthesizing a row *is* double-entry. So
`is_synthesized` / `synthesized_reason` goes in the schema on day one, and the problem is solved
permanently rather than patched later.

**Where I corrected my own specialists:** the balance trigger as written would have rejected every valid
transaction; `direction` + signed amounts is a self-contradictory encoding; `user_id` on 15 tables
contradicted our own "reject multi-user machinery" decision; and I had earlier leaned toward Actual's
simpler `transfer_id` model until testing it against the Amex case proved it wrong. Details in
`09-canonical-data-model.md`.

---

## B. Decision matrix

| | **Build ourselves** | BankingSync | Firefly III | Actual Budget | Wealthfolio | Firefly data-importer |
|---|---|---|---|---|---|---|
| **Control** | **Total** | Low (AGPL) | Low | Medium (MIT) | Low (AGPL) | Low (AGPL) |
| **Complexity** | **Moderate, ours to bound** | Moderate | **Very high** (Laravel+Doctrine+Vue) | High (monorepo+sync server) | High (Rust/Tauri) | High (Laravel) |
| **Maintainability** | **One person, understood** | Good code, 13★, 1 author, 6mo old | Community, but huge surface | Very active | Very active | Small, focused |
| **Integration effort** | **Build it (this proposal)** | Rewrite persistence + UI | n/a | Two DBs + sync — rejected by you | Two DBs + sync | Rewrite `ApiSubmitter` + all routines |
| **Life OS compatibility** | **Native, one DB** | Poor — own SQLite + own UI | Poor | Poor | Poor | Poor |
| **Privacy** | **Full local control** | Good, but stores RSA key unencrypted in SQLite | Self-hostable | Self-hostable (MIT) | Self-hostable | Self-hostable |
| **Extensibility** | **Unbounded** | Financial only | Financial only | Budgeting only | Investments only | Importer only |
| **Investment support** | Our seam now, build later | None | **None** (CONFIRMED) | **None** (off-budget accts) | **Excellent** | n/a |
| **Local-first** | Yes, by construction | Yes (SQLite) | Yes | Yes (SQLite+sync) | Yes (SQLite) | n/a |
| **Dedup adequacy for us** | **Designed for it** | Strong (3 tiers) | **Fails on Amex** | **Fails on Amex** | Good `idempotency_key` | **Fails on Amex** |
| **Licence** | Ours | **AGPL-3.0** | **AGPL-3.0** | **MIT** | **AGPL-3.0** | **AGPL-3.0** |

**Verdict: build ourselves.** Note this is *not* "feature-richest project loses" — BankingSync has
genuinely good matching and Actual is permissively licensed. They lose on **fit**, specifically on
dedup for your one provider that has no IDs, and on the single-database requirement.

### Reuse / fork / study / build

| | Decision | What |
|---|---|---|
| **Reuse directly** | ✅ Libraries only | `python-csv`, `pdfplumber` (Amex PDF), `cryptography` (Fernet), `httpx`, `apscheduler`, `zod`… Commodity utilities, no domain opinions |
| **Fork/adapt** | ❌ None | Every candidate is AGPL (would contaminate all of Life OS) or architecturally mismatched |
| **Study** | ✅ 4 projects | See below — the value is concentrated in a few specific mechanisms |
| **Build ourselves** | ✅ Everything else | Ledger, dedup, transfer matching, categorization, provider abstraction |

**What to study, specifically:**

| From | Mechanism | Licence status |
|---|---|---|
| **BankingSync** | `pending_map` keyed `date\|amount\|payee\|occurrence-index`; update-in-place `MergePatch`/`AmountPatch`; Fellegi–Sunter confidence scoring; review queue | AGPL — ideas only, no code |
| **Actual Budget** | `transfer_id` on both legs; **`Payee.transfer_acct`**; `imported_id` + imported-wins-over-manual merge; `starting_balance_flag`; `tombstone` | **MIT — copy freely** |
| **Firefly III** | Type-on-the-group-not-the-leg; asset/liability taxonomy; CAMT field-mapping layer separation | AGPL — ideas only |
| **Wealthfolio** | `Activity` 14-type enum; `Asset` kind/instrument/`instrument_key`; `Lot`+`LotDisposal` FIFO; `MonetaryValue{local,base}`; **`idempotency_key` hash + unique constraint**; `is_user_modified`/`needs_review` flags | AGPL — and it's **Rust/SQLite**, so clean-room only |

**Explicitly do not copy** (design anti-patterns found): BankingSync's fallback to `transaction_id`
(unstable — a latent bug), Firefly's tag-based import provenance (`"Data Import on 2026-09-30 @ 14:30"`,
no import table), and Wealthfolio's `CostBasisMethod{Lifo,Wac}` enum values that its calculator rejects.

---

## C. Bank connectivity strategy

| Source | **Primary** | **Fallback** | Confidence |
|---|---|---|---|
| **Rabobank** | Enable Banking AIS | GoCardless Bank Account Data | CONFIRMED (sandbox) / LIKELY (prod) |
| **Revolut NL** | Enable Banking AIS | GoCardless Bank Account Data | **UNCERTAIN — verify first** |
| **Amex NL** | **PDF import (7yr)** then CSV (6mo) | Re-export from Amex | **CONFIRMED — no API exists** |
| **Google Wallet** | **Defer to v2**, or drop | — | LIKELY |

### Rabobank → Enable Banking
`POST /auth` → redirect to bank SCA → `POST /sessions` → poll `/accounts/{uid}/transactions`.
RS256 JWT, not HS256. **Link accounts on `identification_hash`, never `uid`** — `uid` rotates at every
re-auth. Consent ~180 days, **no refresh token**: `EXPIRED_SESSION` (401) means a full re-auth is a
*normal user flow*, not an error state. **No AIS webhooks — sync must be polled.** ~4 background
fetches/day/ASPSP unless PSU headers are sent. **History clamps to ~90 days shortly after consent, so
backfill hard at consent time or lose it.**

### Revolut NL → unconfirmed
Not in Enable Banking's sandbox list. **Gate the Revolut lane on one call:**
```bash
curl -H "Authorization: Bearer <RS256 JWT>" \
  "https://api.enablebanking.com/aspsps?country=NL&psu_type=personal"
```
If absent → Revolut falls back to **CSV import** (which has a stable `id` column, per ora-1's matrix)
and nothing in the architecture changes. **The design must not assume Revolut arrives.**

### Amex NL → file import, permanently
No aggregator exposes it. **PDF is the better source, not the fallback** — 7 years, itemized, *both*
transaction and process date, FX detail. CSV is ~6 months, one date, posted-only, no pending. Consumer
portal also offers OFX/QFX and QBO. ⚠️ The "Include all additional transaction details" checkbox is
**off by default** and drops Reference/Category/Address — onboarding must tell the user to tick it.

**Operational consequence:** the ~6-month CSV window makes **archived exports the only durable copy**.
The system must never delete an uploaded file, and "rebuild the ledger from raw" is a first-class
feature, not a promise. (PDF at 7 years makes this less urgent, but CSV is the fallback path.)

### Google Wallet → defer or drop
Amex data reaches Google, including non-Wallet transactions unless the user opts out of
"Non-Device Transactions". But it offers only recent/shared data, no posting date, no FX, no reference
ID — and merchant strings differ from Amex's, so dedup is fuzzy-only (date ±1, amount, last4). **It is
a dedup liability as much as a data source.** Recommend excluding from v1.

### Fallback if Enable Banking becomes unusable
**GoCardless Bank Account Data (ex-Nordigen)** — self-serve, free tier (~25 accounts), strong NL
coverage. Then Yapily or TrueLayer (both have developer sandboxes). **Avoid Tink** (enterprise-only,
no self-serve). Plaid: **not viable for NL** — Amex is US/CA/UK, and its NL coverage is ING/Rabobank/
ABN AMRO.

### Abstraction layer
```python
class SourceAdapter(Protocol):
    provider: str            # 'enable_banking' | 'amex_pdf' | 'amex_csv' | 'revolut_csv' | 'manual'
    def fetch(self, link, since) -> Iterator[RawRecord]: ...
    def parse(self, blob) -> Iterator[RawRecord]: ...
```
Every adapter emits `RawRecord` — the same shape regardless of source. **The provider-agnostic part is
the schema; only the resolver is provider-aware.** No plugin registry: three classes, and an ABC if a
fourth appears.

---

## D. Repository structure

Your sketch (`apps/`, `domain/`, `integrations/`, `importers/`, `workers/`, `database/`, `tests/` at root)
encourages exactly the coupling we want to avoid — a shared root `domain/` invites cross-module imports,
and `integrations/`/`importers/` detach adapters from the module that owns the domain. Revised:

```
life-os/
├── AGENTS.md                  # agent rules; points to ARCHITECTURE.md + invariants.yaml
├── ARCHITECTURE.md            # module map, layer diagram, invariants (≤200 lines)
├── invariants.yaml            # machine-checkable invariants
├── docker-compose.yml         # db, api, worker, frontend
├── Makefile                   # dev, test, migrate, generate-types, check-invariants
├── pyproject.toml             # uv
├── docs/
│   ├── ARCHITECTURE-PROPOSAL.md   # this document
│   ├── RECOMMENDATION.md          # final recommendation, risks, decisions required
│   ├── research/                  # the 9 research reports
│   └── adr/                       # one ADR per consequential decision
├── backend/
│   ├── main.py                 # app factory, mounts routers
│   ├── config.py               # pydantic-settings
│   ├── core/                   # shared primitives ONLY: datetime, money, blob, recurrence, preference
│   │   └── alembic/
│   ├── finance/                # the module
│   │   ├── alembic/            # migrations for the finance schema
│   │   ├── domain/             # PRIVATE
│   │   │   ├── models/         # SQLAlchemy ORM
│   │   │   ├── value_objects/  # Money, Currency, DateRange
│   │   │   └── services/       # pure logic: dedupe, transfer_match, categorize, budget
│   │   ├── ingestion/          # THE COMPLEXITY LIVES HERE
│   │   │   ├── adapters/       # enable_banking.py, amex_pdf.py, amex_csv.py, revolut_csv.py, manual.py
│   │   │   ├── normalize.py
│   │   │   ├── fingerprint.py  # FROZEN once shipped (invariant)
│   │   │   ├── identity.py     # IdentityResolver — 3 tiers
│   │   │   ├── dedupe.py
│   │   │   ├── transfer_match.py
│   │   │   └── categorize.py   # 7-layer engine
│   │   ├── api/
│   │   │   ├── routes/         # accounts, transactions, imports, review, categories
│   │   │   └── schemas/        # Pydantic = the public contract
│   │   ├── public.py           # exports ONLY protocols + read-only schemas
│   │   ├── background/         # scheduler + jobs + CLI
│   │   └── tests/{unit,integration,fixtures}/
│   └── tests/                  # cross-module only
├── frontend/
│   ├── src/
│   │   ├── api/generated/      # OpenAPI codegen — DO NOT EDIT
│   │   ├── components/         # shadcn/ui
│   │   ├── features/finance/   # transactions, review, imports, budgets
│   │   ├── lib/
│   │   └── routes/
│   └── tests/
├── scripts/
│   ├── generate_types.py
│   ├── check_invariants.py
│   └── migrate.sh
└── .github/workflows/          # lint, typecheck, test, invariant-check, egress-test
```

**One Postgres database, one schema per module** (`finance`, later `health`, `tasks`…). No cross-schema
FKs; cross-module reads via views owned by a read-only `dashboard` module — **deferred until a second
module exists.** Enforced by DB grants + `importlinter` contracts. This is checkable by an agent, which
is the point.

**Types:** generate TS from FastAPI's OpenAPI at build time. No shared-types package (versioning
headache). CI fails if the spec changed without regeneration.

---

## E. Database schema

Corrections applied from `research/09` §Reconciliation: **deferred** balance trigger, **no** `direction`
column, **no** `user_id`, **no** `account.provider_account_id`, **no** `journal_entry.source_record_id`,
**no** empty investment tables.

```sql
-- ═══ Reference ═══
CREATE TABLE currency (
  code        CHAR(3) PRIMARY KEY,
  name        TEXT NOT NULL,
  decimals    SMALLINT NOT NULL DEFAULT 2,   -- authority for BIGINT minor-unit interpretation
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE exchange_rate (
  id              BIGSERIAL PRIMARY KEY,
  date            DATE NOT NULL,
  base_currency   CHAR(3) NOT NULL REFERENCES currency(code),   -- always 'EUR'
  quote_currency  CHAR(3) NOT NULL REFERENCES currency(code),
  rate            NUMERIC(20,10) NOT NULL,
  source          TEXT NOT NULL DEFAULT 'ecb',
  UNIQUE (date, base_currency, quote_currency)
);
CREATE TABLE institution (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  provider_key  TEXT NOT NULL UNIQUE,          -- 'rabobank' | 'revolut' | 'amex_nl'
  country_code  CHAR(2) NOT NULL DEFAULT 'NL'
);

-- ═══ Accounts — asset vs liability first-class ═══
CREATE TABLE account (
  id                 BIGSERIAL PRIMARY KEY,
  institution_id     BIGINT REFERENCES institution(id),
  name               TEXT NOT NULL,
  account_type       TEXT NOT NULL CHECK (account_type IN
                       ('checking','savings','credit_card','cash','investment','loan','mortgage')),
  account_nature     TEXT NOT NULL CHECK (account_nature IN ('asset','liability','equity')),
  currency           CHAR(3) NOT NULL REFERENCES currency(code),
  iban               TEXT,
  masked_pan         TEXT,
  is_active          BOOLEAN NOT NULL DEFAULT TRUE,
  is_hidden          BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order         INT NOT NULL DEFAULT 0,
  security_id        BIGINT          -- investment seam; no FK until the table exists
);
CREATE INDEX idx_account_nature ON account (account_nature) WHERE is_active;

CREATE TABLE provider_account_link (
  id                    BIGSERIAL PRIMARY KEY,
  institution_id        BIGINT NOT NULL REFERENCES institution(id),
  account_id            BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  provider_account_id   TEXT NOT NULL,       -- IBAN / masked PAN, exactly as the provider sends it
  provider_account_name TEXT,
  UNIQUE (institution_id, provider_account_id)
);

-- ═══ Import pipeline ═══
CREATE TABLE import_batch (
  id                BIGSERIAL PRIMARY KEY,
  institution_id    BIGINT REFERENCES institution(id),
  account_id        BIGINT REFERENCES account(id),
  provider          TEXT NOT NULL CHECK (provider IN
                      ('enable_banking','amex_csv','amex_pdf','revolut_csv','manual','google_wallet')),
  import_method     TEXT NOT NULL CHECK (import_method IN ('api','csv','pdf','manual')),
  status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                      ('pending','processing','completed','failed','partial')),
  source_filename   TEXT,
  source_checksum   CHAR(64),
  raw_payload       JSONB,                    -- gzipped if large; NEVER deleted
  stats             JSONB NOT NULL DEFAULT '{}',
  started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at      TIMESTAMPTZ
);

CREATE TABLE source_record (
  id                 BIGSERIAL PRIMARY KEY,
  import_batch_id    BIGINT NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  account_id         BIGINT NOT NULL REFERENCES account(id),
  provider_txn_id    TEXT,                    -- entry_reference / Revolut id; NULL for Amex
  fingerprint        BYTEA NOT NULL,          -- computed for ALL providers
  occurrence_index   INT  NOT NULL DEFAULT 1, -- distinguishes genuinely identical rows
  raw_data           JSONB NOT NULL,          -- the exact row, immutable forever
  raw_description    TEXT NOT NULL,           -- immutable, user edits NEVER touch this
  raw_amount         BIGINT NOT NULL,         -- minor units, signed
  raw_currency       CHAR(3) NOT NULL REFERENCES currency(code),
  raw_date           DATE NOT NULL,
  raw_posting_date   DATE,
  status             TEXT NOT NULL DEFAULT 'imported'
                       CHECK (status IN ('imported','pending','posted','duplicate')),
  journal_entry_id   BIGINT REFERENCES journal_entry(id),
  error_message      TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_sr_batch_providertxn UNIQUE (import_batch_id, provider_txn_id)
       -- partial: unique only when provider_txn_id is not null
);
-- Postgres needs a partial unique index rather than a table constraint:
CREATE UNIQUE INDEX uq_sr_providertxn ON source_record (import_batch_id, provider_txn_id)
  WHERE provider_txn_id IS NOT NULL;
CREATE UNIQUE INDEX uq_sr_fingerprint ON source_record (import_batch_id, fingerprint);
CREATE INDEX idx_sr_fingerprint_lookup ON source_record (fingerprint, account_id);
CREATE INDEX idx_sr_account_status ON source_record (account_id, status);
CREATE INDEX idx_sr_desc_trgm ON source_record USING gin (raw_description gin_trgm_ops);
CREATE INDEX idx_sr_open_pending ON source_record (account_id, raw_date)
  WHERE status = 'pending' AND journal_entry_id IS NULL;

-- ═══ Canonical ledger — double-entry ═══
CREATE TABLE journal_entry (
  id                 BIGSERIAL PRIMARY KEY,
  entry_date         DATE NOT NULL,
  description        TEXT,
  is_transfer        BOOLEAN NOT NULL DEFAULT FALSE,
  is_split           BOOLEAN NOT NULL DEFAULT FALSE,
  user_verified_at   TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_je_date ON journal_entry (entry_date DESC);

CREATE TABLE journal_line (
  id                  BIGSERIAL PRIMARY KEY,
  journal_entry_id    BIGINT NOT NULL REFERENCES journal_entry(id) ON DELETE CASCADE,
  account_id          BIGINT NOT NULL REFERENCES account(id),
  amount              BIGINT NOT NULL,        -- account currency, minor units, SIGNED
  currency            CHAR(3) NOT NULL REFERENCES currency(code),  -- snapshot of account.currency
  amount_base         NUMERIC(18,4) NOT NULL, -- EUR, signed, converted at import time
  exchange_rate       NUMERIC(20,10) NOT NULL DEFAULT 1.0,
  foreign_amount      BIGINT,                 -- original foreign amount, e.g. USD (Amex PDF)
  foreign_currency    CHAR(3) REFERENCES currency(code),
  category_id         BIGINT REFERENCES category(id),
  merchant_id         BIGINT REFERENCES merchant(id),
  transfer_match_id   BIGINT REFERENCES transfer_match(id),
  is_synthesized      BOOLEAN NOT NULL DEFAULT FALSE,  -- the Amex card-payment leg
  synthesized_reason  TEXT CHECK (synthesized_reason IN
                        ('card_payment','sepa_dd','investment','opening_balance')),
  security_id         BIGINT,                 -- investment seam
  units               NUMERIC(24,12),
  price_per_unit      NUMERIC(20,10),
  sort_order          INT NOT NULL DEFAULT 0
);
-- Sign carries direction. Debits negative, credits positive. No `direction` column.
CREATE INDEX idx_jl_entry  ON journal_line (journal_entry_id);
CREATE INDEX idx_jl_acct   ON journal_line (account_id, entry_id_journal()) ;  -- see note
CREATE INDEX idx_jl_cat    ON journal_line (category_id) WHERE category_id IS NOT NULL;
-- uncategorized review queue — the single most important partial index:
CREATE INDEX idx_jl_uncat  ON journal_line (account_id, journal_entry_id)
  WHERE category_id IS NULL AND transfer_match_id IS NULL AND amount_base < 0;
-- unmatched outbound legs, for the transfer-matching sweep:
CREATE INDEX idx_jl_unmatched ON journal_line (account_id)
  WHERE transfer_match_id IS NULL AND amount_base < 0;
CREATE INDEX idx_jl_transfer ON journal_line (transfer_match_id) WHERE transfer_match_id IS NOT NULL;

-- Deferred constraint trigger: fires at COMMIT, when all lines of the entry exist.
CREATE OR REPLACE FUNCTION assert_journal_entry_balances() RETURNS TRIGGER AS $$
DECLARE eid BIGINT := COALESCE(NEW.journal_entry_id, OLD.journal_entry_id);
BEGIN
  IF EXISTS (SELECT 1 FROM journal_entry WHERE id = eid) THEN
    IF (SELECT COALESCE(SUM(amount_base),0) FROM journal_line WHERE journal_entry_id = eid) <> 0 THEN
      RAISE EXCEPTION 'JournalEntry % does not balance', eid;
    END IF;
  END IF;
  RETURN NULL;
END; $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_journal_balances
  AFTER INSERT OR UPDATE OR DELETE ON journal_line
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION assert_journal_entry_balances();

-- ═══ Merchant ═══
CREATE TABLE merchant (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,          -- canonical: 'Albert Heijn'
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE merchant_alias (
  id            BIGSERIAL PRIMARY KEY,
  merchant_id   BIGINT NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
  raw_string    TEXT NOT NULL,                 -- 'ALBERT HEIJN 1234 AMSTERDAM'
  confidence    NUMERIC(3,2) NOT NULL DEFAULT 0.50,
  usage_count   INT NOT NULL DEFAULT 0,
  last_seen     DATE NOT NULL DEFAULT CURRENT_DATE,
  UNIQUE (raw_string)
);
CREATE INDEX idx_alias_raw ON merchant_alias USING gin (raw_string gin_trgm_ops);

-- ═══ Category ═══
CREATE TABLE category (
  id          BIGSERIAL PRIMARY KEY,
  parent_id   BIGINT REFERENCES category(id),
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('expense','income','transfer','investment')),
  sort_order  INT NOT NULL DEFAULT 0,
  is_system   BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (parent_id, name)
);
CREATE TABLE category_rule (
  id                  BIGSERIAL PRIMARY KEY,
  priority            INT NOT NULL DEFAULT 100,
  account_id          BIGINT REFERENCES account(id),
  merchant_id         BIGINT REFERENCES merchant(id),
  description_pattern TEXT,                    -- trigram/ILIKE match on raw_description
  category_id         BIGINT NOT NULL REFERENCES category(id),
  is_learned          BOOLEAN NOT NULL DEFAULT FALSE,
  confidence          NUMERIC(3,2) NOT NULL DEFAULT 1.00,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rule_lookup ON category_rule (merchant_id, description_pattern, priority);

-- ═══ TransferMatch — stored, never recomputed ═══
CREATE TABLE transfer_match (
  id                   BIGSERIAL PRIMARY KEY,
  journal_line_id_out  BIGINT NOT NULL REFERENCES journal_line(id),
  journal_line_id_in   BIGINT NOT NULL REFERENCES journal_line(id),
  match_method         TEXT NOT NULL CHECK (match_method IN
                         ('auto_amount_date','auto_card_payment','auto_sepa_dd',
                          'user_confirmed','user_created')),
  confidence           NUMERIC(3,2) NOT NULL DEFAULT 1.00,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  confirmed_at         TIMESTAMPTZ,
  UNIQUE (journal_line_id_out, journal_line_id_in),
  CHECK (journal_line_id_out <> journal_line_id_in)
);
CREATE INDEX idx_tm_out ON transfer_match (journal_line_id_out);
CREATE INDEX idx_tm_in  ON transfer_match (journal_line_id_in);

-- ═══ Recurring — detection output, user-confirmed ═══
CREATE TABLE recurring_series (
  id                  BIGSERIAL PRIMARY KEY,
  account_id          BIGINT NOT NULL REFERENCES account(id),
  merchant_id         BIGINT REFERENCES merchant(id),
  category_id         BIGINT REFERENCES category(id),
  amount_base         NUMERIC(18,4) NOT NULL,
  currency            CHAR(3) NOT NULL REFERENCES currency(code),
  frequency           TEXT NOT NULL CHECK (frequency IN
                        ('weekly','biweekly','monthly','quarterly','yearly')),
  first_date          DATE NOT NULL,
  last_date           DATE,
  detection_confidence NUMERIC(3,2),
  status              TEXT NOT NULL DEFAULT 'detected'
                        CHECK (status IN ('detected','confirmed','dismissed'))
);
CREATE UNIQUE INDEX uq_recurring ON recurring_series
  (account_id, merchant_id, amount_base, frequency, first_date);
```

> **Two authoring notes.** (1) `idx_jl_acct` in the listing above is illustrative — the real index should
> be `journal_line (account_id, journal_entry_id)` plus a join to `journal_entry.entry_date`, or a
> denormalized `entry_date` on the line. Decide at implementation time; do not leave a broken expression
> in the migration. (2) The partial unique index on `provider_txn_id` **must** be a `CREATE UNIQUE
> INDEX ... WHERE`, not a table `CONSTRAINT` — Postgres does not support partial uniqueness in table
> constraints.

**Key indexes and why:**

| Query | Index |
|---|---|
| Monthly spend by category | `idx_jl_acct` + `idx_jl_cat` (partial) |
| Uncategorized review queue | `idx_jl_uncat` (partial — the hottest screen in the app) |
| Transfer-leg lookup | `idx_tm_out`, `idx_tm_in` |
| Dedup scan | `uq_sr_fingerprint`, `uq_sr_providertxn` (partial) |
| Pending→booked sweep | `idx_sr_open_pending` (partial) |
| Merchant alias learning | `idx_alias_raw` (trigram) |

**Money:** `BIGINT` minor units for the authoritative account-currency amount; `NUMERIC(18,4)` for the
EUR reporting value; `currency.decimals` defines the exponent. **The sign is the direction** — no
`direction` column. Never `float`, never `NUMERIC(18,2)` (wrong for JPY and crypto, and FX rounding
accumulates).

---

## F. Transaction lifecycle

Six states were proposed. **Four are worth persisting**, all on `source_record.status` — the *raw* side
pipeline — not on the canonical side.

| State | Persisted? | Meaning |
|---|---|---|
| `raw` | No | Existence of a `SourceRecord` **is** the raw state |
| `imported` | **Yes** | Landed, normalizer not yet run (this is what makes a failed batch queryable) |
| `pending` | **Yes** | Bank says pending — **Enable Banking only**; Amex excludes pending |
| `posted` | **Yes** | Bank confirmed; the `JournalEntry` is final |
| `duplicate` | **Yes** | Dedup hit; `journal_entry_id` points at the canonical entry |
| `matched` | No | Derived: `transfer_match_id IS NOT NULL` |
| `categorized` | No | Derived: `category_id IS NOT NULL` |
| `reconciled` | No | **Replaced by** `journal_entry.user_verified_at` — one flag, not a state |

**Why status lives on `SourceRecord` and not `JournalEntry`:** one `JournalEntry` can be produced by
several `SourceRecord`s over time (a pending row, then the booked row that updates it). The canonical
entry has no meaningful pipeline state; the raw records do.

---

## G. Deduplication algorithm

**Three tiers. The schema is provider-agnostic; only the resolver is provider-aware.**

### Tier 1 — provider ID (fast path, API only)
Key: `(account_id, provider_txn_id)`. Enable Banking `entry_reference`; Revolut `id`.
**`entry_reference` is unique per account, never globally** — always scope by account via
`ProviderAccountLink`. Note Enable Banking exposes `account.uid`, which **rotates at every re-auth**;
link on `identification_hash`.

### Tier 2 — pending → booked (Enable Banking only)
⚠️ **`entry_reference` usually only materialises on `BOOK`.** Provider IDs *cannot* be relied on for this
correlation. Some ASPSPs do supply a stable one on `PDNG` — detect at runtime; if both rows carry an
equal `provider_txn_id`, that's a Tier-1 exact match. Otherwise:

**Candidate generation** (same account, same currency, existing not already matched):
- `ABS(new.amount - existing.amount) <= 1` minor unit
- `new.date BETWEEN existing.date - 3 days AND existing.date + 1 day` (outbound precedes inbound)

**Confidence score:**
```
score = 1.0
  - 0.30  if amount matches exactly (not merely within tolerance)
  - 0.20  if date matches exactly
  - 0.20  if trigram similarity of raw_description > 0.8
  - 0.10  if both resolve to the same merchant_alias
  - 0.10  if the existing row is still 'pending' (strong signal)
  - 0.10  if no other candidate in the window (uniqueness)
```
**≥0.85 auto-link · 0.50–0.85 review queue · <0.50 new transaction.**

**On match:** update the existing `JournalEntry` in place — refresh amount, date, balance. Never
create a second entry, never delete-and-reinsert. Preserve any user-set category/merchant; only fill
empty fields. (This is BankingSync's `MergePatch` behaviour, which is correct and worth copying as a
*behaviour*.)

### Tier 3 — content fingerprint (primary for files; backstop for API)
```
fingerprint = SHA256(
   lower(trim(collapse_ws(raw_description))) || '|' ||   -- strip trailing * # REF:...
   raw_amount || '|' || raw_currency || '|' || raw_date || '|' ||
   account_id || '|' || occurrence_index
)
```
`occurrence_index` = `ROW_NUMBER() OVER (PARTITION BY account_id, normalized_description, amount,
currency, date ORDER BY import_batch_id, raw_line_number)`.

**This is what solves two identical €3.20 coffees on the same day** — the multiset answer, independently
validated by BankingSync's `occurrence-index` key and Wealthfolio's `idempotency_key`. Re-importing the
same CSV preserves line order, so indices are stable; reordering breaks it, which is why the anchor is
`raw_line_number`, not file order.

**Computed for API rows too** — cheap, and it backstops Tier 1 if `entry_reference` ever changes on a
historical re-fetch.

**Cross-batch dedup** is keyed on `(fingerprint, account_id)`, not batch. This is what makes **importing
a 7-year Amex PDF and then a 6-month CSV non-destructive**: the CSV rows already exist from the PDF and
fingerprint-match.

### Confidence weighting by import method
PDF extraction is inherently noisier (column alignment, OCR): `pdf −0.15`, `csv 0.00`, `api +0.10`.

### What is impossible — and what we do instead
Perfect automatic dedup is not achievable when a provider gives no ID and two genuinely identical
transactions occur the same day at the same merchant. **Default to "create new + queue for review", never
"guess and merge."** The review queue shows the new row, the candidate, the score, and the reason;
`CONFIRM` / `REJECT` / `IGNORE`. **Every confirmation raises `merchant_alias.confidence` to 1.0 or creates
a learned `category_rule`** — so the queue drains itself over time.

### Location & testability
`finance/ingestion/identity.py`, `fingerprint.py`. Fingerprint and scoring are **pure functions** —
fully unit-testable with no database. `fingerprint.py` is **frozen by hash** once shipped
(`invariants.yaml`), because changing it invalidates every stored fingerprint and breaks replay.

---

## H. Transfer-matching algorithm

### The rule
Two `JournalLine`s are transfer-linked iff:
1. different `account_id`, same owner (single user)
2. opposite signs
3. `ABS(L1.amount_base + L2.amount_base) <= 0.01` (same currency) or `<= 0.50` (cross-currency, FX spread)
4. `L2.date BETWEEN L1.date - 1 day AND L1.date + 3 days` (outbound first)
5. neither already matched

### The Amex card payment — the reason for this whole section
> €50 Albert Heijn on Amex. Later, Rabobank shows −€50 "American Express".

**There is no second row to link.** The liability balance simply falls. This is why the model is
double-entry and why a synthesized leg is mandatory.

| | JournalLine | account | amount_base | synthesized? |
|---|---|---|---|---|
| **E1** (Amex import) | C | Amex *(liability)* | **+50** | no |
| | D | Expense : Groceries | **−50** | no |
| **E2** (Rabobank import) | A | Rabobank *(asset)* | **−50** | no |
| | B | Amex *(liability)* | **+50** | **YES**, `synthesized_reason='card_payment'` |

- E1 balances: `+50 − 50 = 0` ✓ · E2 balances: `−50 + 50 = 0` ✓
- Amex liability across the pair: `+50 (owed) − 50 (paid) = 0` ✓
- **Spending = −50 once.** The €100 double-count is impossible by construction.
- `TransferMatch` links A→B, `match_method='auto_card_payment'`, `confidence=0.95`.
- E1 is an **expense**; E2 is a **transfer**. Spending queries exclude `transfer_match_id IS NOT NULL`.

**Detection:** `raw_description ILIKE '%american express%'` on an asset account where a linked liability
account exists. Brittle — so **it learns**: after user confirmation, promote the observed description
into a rule (`match_method='user_confirmed'` records this), reusing the merchant-alias learning
mechanism. By month three it's learned from three real strings.

### SEPA direct debit vs own-account transfer
**Deterministic rule:** if the inbound leg resolves to an account you own → **transfer**. Else if the
description matches a SEPA creditor ID / mandate reference → **direct debit (expense)**. Else →
**review queue**: *"Is this a payment to your own account, or a bill?"*

This is the ambiguous case that matters most, and it is why a review queue is mandatory rather than
optional.

### Ambiguity
| Situation | Resolution |
|---|---|
| Several candidates, same amount & window | Nearest date wins; genuine tie → review queue |
| Amount mismatch beyond tolerance | Review queue (FX fee? partial?) |
| Currency mismatch | Compare `amount_base` (converted at import time) |
| Other leg not yet imported | Leave `transfer_match_id NULL`; nightly sweep re-scans unmatched outbound legs |

**Deliberately omitted: the Hungarian one-to-one assignment algorithm** from BankingSync. It solves
batched ambiguous matching — a problem that does not exist at 3 accounts and a few hundred transactions
a month, and it costs real comprehension budget. **Reconsider only if the review queue demonstrates
genuine ambiguous clusters.**

### Stored, not recomputed
`TransferMatch` is written once and never recomputed. A later import may reveal the other leg and create
the match *then*. User confirmation sets `confirmed_at` and locks it. Recomputation would flip
confirmed matches and would be non-deterministic across runs.

---

## I. Categorization architecture

Seven layers, deterministic, **no LLM required for the core to work**:

| # | Layer | Mechanism |
|---|---|---|
| 1 | **Exact user rules** | `category_rule` with `merchant_id` match, priority-ordered |
| 2 | **Merchant normalization** | `merchant_alias` — exact, then trigram fuzzy; raises confidence over time |
| 3 | **Known merchant map** | curated `merchant → category` seeds for NL merchants (Albert Heijn, Jumbo, NS, Ziggo…) |
| 4 | **Fuzzy match** | trigram similarity on `raw_description` against known merchants |
| 5 | **Learned personal rules** | **every manual correction creates an `is_learned=TRUE` rule or sets `merchant_alias.confidence=1.0`** |
| 6 | **AI/LLM fallback** | *optional, off by default, never required* |
| 7 | **Manual review** | the uncategorized queue (`idx_jl_uncat`) |

**Layer 5 is the one the reference projects lack.** Firefly has no evidence of learnability; Actual has
no auto-suggest. *If I correct `PayPal XYZ` → Entertainment: Music, the system remembers* — that is a
core requirement and it is ours to build.

**Rules must stay understandable and editable.** `description_pattern` with a priority integer and a
`is_learned` flag — no generic rules DSL, no YAML logic engine, no expression evaluator. You should be
able to read the entire rule set in one screen and edit it by hand.

**AI constraints (from the security lane):** explicit per-session opt-in; strip IBAN, account numbers
and merchant names before any call; **local model by default** (Ollama/llama.cpp), cloud only behind a
second opt-in with a warning; never send `raw_description`. The ledger stays fully correct with AI
disabled.

---

## J. Security model

### Credential inventory — smaller than it first appears
⚠️ **Enable Banking issues no access or refresh tokens to us.** The "access token" *is* the RS256 JWT we
sign ourselves; ASPSP token refresh is Enable Banking's internal business. What we actually hold:

| Secret | Where | Rotate |
|---|---|---|
| **RSA private key (4096)** — signs every JWT to Enable Banking | **OS keyring (libsecret), never the DB** | Quarterly, or immediately on device loss |
| `app_id` (JWT `kid`) | config, non-secret | — |
| `session_id` per consent | DB — opaque, revocable at the bank | On consent re-auth (~180d) |
| DB password, `APP_SECRET` | keyring | — |

**That is one long-lived secret, not three.** (This also makes BankingSync's unencrypted-key-in-SQLite
worse than it first appears.)

### The property that must hold
> PSD2 credentials are never available to us, and a compromise of Life OS does not yield transferable
> bank authority.

**Verified: holds.** The user authenticates at the bank; we receive only an authorization code. **No
passwords, PINs or eIDAS certificates ever touch our code.**
⚠️ *NEEDS USER TESTING:* whether any NL ASPSP uses a non-redirect auth method — check `auth_methods` in
`/aspsps?country=NL`. **Architectural mitigation either way: refuse to implement credential-based bank
auth in code, fail loud.**

### Key decisions
- **LUKS full-disk encryption. No application-level column encryption.** Dedup must query and index
  `raw_description`, `merchant`, and amounts in plaintext; column encryption would break indexes and
  deterministic fingerprinting. (Two lanes independently reached this conclusion — settled.)
- **Exposure default: localhost only.** Escalate to Tailscale for remote access. **Never** public.
- **Auth: signed session cookie + CSRF, no user table.** A single-user self-hosted app does not need
  Argon2id + TOTP; a hand-rolled auth system is a *worse* outcome than none. Add real auth when a second
  user or health data appears.
- **Secrets policy** → `SECURITY.md`; dev uses `.env` (0600), production uses the keyring.
- **Backups:** restic + age, 3-2-1, **monthly restore test**. Backup key ≠ app secrets.
- **Raw payloads live inside the encrypted volume** and are never deleted.

### Logging policy — never log
Full IBANs (log `NL**1234`) · any token or `session_id` · keys or keyring contents · full raw payloads
(log SHA-256 + size only) · merchant + amount together · PII.
**Allowed:** request id, timestamp, endpoint, status, latency, error class, payload hash.

### Two guardrails worth calling out
1. **Zero-egress CI test.** `docker run --network=none` + `strace -e network`, asserting zero
   `connect()` calls, while file import + dedup still work. Proves "no telemetry" and "offline file
   import" simultaneously. Best single idea in the security report.
2. **Dependency gate.** CI fails if a lockfile changes without an `APPROVED:` line. An AI agent adding a
   random PyPI package is a real, under-appreciated risk channel.

### P0 checklist (abridged)
Key in keyring · non-root container · LUKS · restic + tested restore · log redaction verified by grep ·
CSP + HSTS + secure cookies + CSRF on all mutating routes · hash-pinned lockfiles · digest-pinned base
images + `trivy`. Full 25-item list in `research/06-security-privacy.md`.

---

## K. MVP roadmap

Small vertical slices. Each is independently useful and independently testable.

| # | Milestone | Done when | Why this order |
|---|---|---|---|
| **M0** | **Project skeleton + invariants** | Repo, Docker Compose (db/api/worker), Alembic, `ARCHITECTURE.md`, `invariants.yaml`, CI with invariant + egress checks | Guardrails before data. Prevents the agent from breaking invariants on day one |
| **M1** | **Schema + manual transactions** | Migrations applied; `journal_entry`/`journal_line` balance trigger green; CRUD via API; simplest UI | Proves the double-entry core and the balance invariant before any ingestion complexity |
| **M2** | **Amex CSV import + fingerprint dedup** | Upload CSV → `import_batch` + `source_record` rows → normalized entries. **Import the same file twice → zero new transactions** | File import is the *only* proven-available path. Proves Tier 3, the hardest dedup |
| **M3** | **Merchant normalization + categorization** | `merchant_alias` fuzzy matching; 7-layer engine; **manual correction creates a learned rule**; uncategorized review queue | Makes the data usable; layer 5 is our differentiator |
| **M4** | **Transfer matching + Amex payment** | Card payment auto-detect + synthesized leg; own-account transfers; SEPA-DD vs transfer rule; review queue | The correctness requirement. Once this works, spending totals are trustworthy |
| **M5** | **Enable Banking: Rabobank** | RS256 client, consent flow, `/aspsps` check, backfill at consent time, polling sync, `EXPIRED_SESSION` re-auth, Tier 1 + Tier 2 dedup | Requires M0–M4; depends on the `/aspsps` gate clearing |
| **M6** | **Rebuild-from-raw** | `replay --batch-id` re-runs the whole pipeline; fingerprint frozen by hash | Turns raw retention into a safety net. **Do this before any schema churn** |
| **M7** | **Amex PDF importer** | 7-year itemized import; PDF/CSV overlap produces no duplicates | PDF > CSV in value; needs the fingerprint machinery from M2 |
| **M8** | **Revolut** | **Conditional on `/aspsps?country=NL`.** If absent → Revolut CSV importer instead | Gated, not assumed |
| **M9** | **Budgets + recurring detection** | Monthly budgets; `recurring_series` nightly job, user-confirmed | Needs clean categorized data |
| **M10** | **Net worth snapshots** | `asset − liability` over time, from the account model | Already correct from M1's `account_nature`; just aggregate |

**Deliberately not milestones:** investments (seam is in M1, tables come later), Google Wallet,
multi-user, mobile client, sync engine, LLM categorization, budgets-vs-actual with rollover,
dashboard materialized views.

**Recommendation: M0 → M1 → M2 as the first implementation block.** M2 is where correctness is proven
and where the most research risk is retired.

---

## L. Testing strategy

The unit of correctness is the import. Tests below the line are the ones that matter.

### Idempotency & dedup
| Test | Assertion |
|---|---|
| Import same CSV twice | Second import: 0 new `journal_entry`, all rows `status='duplicate'` |
| Import CSV, then overlapping CSV | Only the non-overlapping window creates entries |
| **Import 7yr PDF, then 6mo CSV** | **0 new entries; every CSV row accounted for** ← the PDF/CSV overlap case |
| Two identical €3.20 coffees, same day | **2 entries**, `occurrence_index` 1 and 2 |
| Same coffee, different days | 2 entries, different fingerprints |
| Re-import after row reordering | No duplicates; `occurrence_index` stable via `raw_line_number` |
| Partial CSV re-import (subset of original) | 0 new entries |
| 100× identical re-import | Exactly 1 entry, ever |

### Pending → booked
| Test | Assertion |
|---|---|
| PDNG then BOOK, same `entry_reference` | 1 entry, updated in place, `status='posted'` |
| PDNG then BOOK, **no** `entry_reference` | Tier 2 matches at ≥0.85; 1 entry |
| PDNG then BOOK, low similarity | 1 entry + review-queue item; **no silent merge** |
| BOOK amount differs from PDNG by 1 cent | Entry updated, `amount` corrected, no duplicate |
| BOOK changes description | `raw_description` on the original `SourceRecord` **unchanged** |
| User set category before BOOK | Category **preserved** (merge fills only empty fields) |
| Pending never booked (expired) | Stays pending; sweepable; excluded from spending |
| `entry_reference` changes on re-fetch | Tier 3 fingerprint backstop catches it |

### Transfers & the Amex problem
| Test | Assertion |
|---|---|
| **Amex €50 purchase → Rabobank −€50** | **Total spending = €50, not €100** ← the headline test |
| Amex purchase, payment not yet imported | Liability balance correct; spending = €50 |
| Rabobank → Revolut €500 | Transfer, excluded from spending; both legs `transfer_match_id` set |
| Revolut → Rabobank €200 | Transfer, correct direction |
| Two €50 transfers same day | 2 distinct `TransferMatch`es, not 1 merged pair |
| SEPA DD to third party | Expense with category; **no** `TransferMatch` |
| Ambiguous DD vs transfer | Review queue, not a guess |
| Cross-currency transfer with FX spread | Matched within 0.50 tolerance |
| Match one leg twice | Second attempt rejected by `UNIQUE` |
| Unmatch a confirmed transfer | `confirmed_at` cleared, legs return to sweep |

### Money & currency
| Test | Assertion |
|---|---|
| JPY account (0 decimals) | `decimals=0` honoured; no precision loss |
| Crypto-style 8 decimals | No rounding error |
| USD purchase on EUR Amex card | `amount` EUR exact, `foreign_amount` USD, rate stored, `amount_base` correct |
| Sum of `amount_base` per entry | Always exactly 0 (also enforced by trigger) |
| Float never used | Static check / mypy strict |

### Raw data integrity (**highest-priority invariant**)
| Test | Assertion |
|---|---|
| User edits merchant | `source_record.raw_description` **byte-identical** |
| Parser version changes | Old `raw_data` still parseable; replay reproduces the entry |
| `fingerprint.py` edited | **CI blocks it** (hash invariant) |
| `import_batch.raw_payload` deleted | Impossible — no code path deletes it |
| Rebuild from raw after schema change | Entries reproduce exactly |

### Import robustness
| Test | Assertion |
|---|---|
| Import fails mid-file | `import_batch.status='failed'` or `'partial'`; **already-committed entries survive** (transactional per-batch) |
| Malformed CSV | Clean error, no partial corruption, no stack trace to the user |
| Wrong date format | Explicit adapter error, not silent misparse |
| Missing `amount` column | Rejected with a clear message |
| Empty file | No-op, not a crash |
| Concurrent imports of the same file | Unique constraints hold; no duplicates |
| GDPR-style delete-all | Hard delete + `imports/` wipe; backups documented honestly |

### Property & fuzz (high value, cheap)
Generate random transaction streams with injected duplicates, re-orders, pending→booked transitions and
format changes; assert **invariants hold**: no duplicate `(fingerprint, account_id)` produces two live
entries; every entry balances; spending never double-counts a card payment; `raw_description` never
mutates. This is where the real bugs live.

---

## M. Open questions

**Gating — answer before M5:**
1. **Does Enable Banking support Revolut NL?** One `GET /aspsps?country=NL` call. If no → Revolut CSV
   importer instead. *Affects roadmap, not architecture.*
2. **Does Rabobank emit `PDNG` pending status at all?** If never → Tier 2 is dead code for you and
   simplification is possible. *Affects M5 scope.*

**Affecting the Amex importers (M2/M7):**
3. **Exact Amex NL CSV columns, date format, sign convention** — download one and check.
4. **Is the `Reference` column present and stable-looking?** Amex's own IDs change, but worth seeing.
5. **Does any NL ASPSP use a non-redirect auth method?** (security posture)
6. **Amex PDF text layer: selectable, or scanned?** If scanned, PDF import needs OCR — a much larger
   project than planned.

**Product decisions (yours to make, not mine):**
7. **Deployment topology** — always-on server + Tailscale, or single machine? Changes exposure, backups,
   and what "local-first" means. *This is the one genuinely blocking product question.*
8. **How far back does history matter?** Enable Banking clamps to ~90 days post-consent. If you want
   years, bank CSVs become a first-class historical source, not just an Amex fallback.
9. **Is native mobile a hard requirement within 6 months?** If yes, sync architecture must be discussed
   now. If no, deferring is correct.
10. **Will this ever be public or open-source?** If yes → all of Life OS must be permissively licensed
    (which our build-our-own decision already guarantees). If no, forking BankingSync under AGPL
    becomes a legitimate alternative to the ~2 weeks of ingestion work it would save.
11. **Savings goals in v1?** The `savings_goal` concept was deliberately deferred; say if you want it.
