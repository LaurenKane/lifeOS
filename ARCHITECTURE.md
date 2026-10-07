# ARCHITECTURE.md — Life OS

> **Product truth lives in [`PRODUCT.md`](PRODUCT.md).** This file is the engineering contract — how
> it is built and why, not who it is for or what it refuses. Where the two appear to disagree, read
> PRODUCT.md for intent and check whether this file is stale.

## 1. What this is
A personal financial single-user OS for EU residents. Tracks transactions across Rabobank, Amex, Revolut. Double-entry ledger, fingerprint dedup, file import, no telemetry.

Single-user is a **refusal**, not an unfinished feature: no sharing, no permissions, no second
user. Two things often mistaken for refusals are not — cloud sync and automatic categorisation are
both wanted, and are recorded in PRODUCT.md as explicitly undecided. Read that file before treating
any scope boundary here as fixed.

---

## 2. Module map

```
life-os/
├── AGENTS.md                  # agent rules; points to ARCHITECTURE.md + SAFETY.md
├── ARCHITECTURE.md            # this file
├── SAFETY.md                  # read before any destructive command
├── docker-compose.yml         # db, api, frontend
├── Makefile                   # dev, test, migrate, verify-no-secrets
├── pyproject.toml             # uv
├── docs/
│   ├── adr/                       # one ADR per consequential decision
│   ├── ENABLE-BANKING-SETUP.md
│   └── legal/
├── backend/
│   ├── main.py                 # app factory, mounts routers
│   ├── config.py               # pydantic-settings
│   ├── core/                   # shared primitives ONLY: datetime, money, blob, recurrence, preference
│   │   └── alembic/
│   ├── finance/                # the finance module (complete)
│   ├── life/                   # the second module (ADR 0011): Thought/Goal/Action/Upkeep
│   │   ├── alembic/            # migrations for the life schema
│   │   ├── domain/             # PRIVATE: models + services (parse, upkeep_state, today)
│   │   ├── api/routes/         # capture, thoughts, actions, goals, upkeeps, vision, pixels, today
│   │   ├── api/schemas/        # Pydantic = the public contract
│   │   ├── local.py            # LOCAL_TIMEZONE "today" helpers (user-local day)
│   │   ├── public.py           # exports ONLY protocols + read-only schemas
│   │   └── tests/{unit,integration}/
│   │   │   ├── ingestion/      # THE COMPLEXITY LIVES HERE
│   │   │   ├── adapters/       # enable_banking.py, amex_pdf.py, manual.py
│   │   │   │                   # + rabobank_pdf.py, revolut_pdf.py (all v1
│   │   │   │                   #   providers, docs/adr/0002-import-provider-enum.md).
│   │   │   │                   #   amex_csv.py / revolut_csv.py were deleted:
│   │   │   │                   #   retired/deferred per ADR 0002.
│   │   │   │   ├── normalize.py
│   │   │   │   ├── fingerprint.py  # FROZEN once shipped (see §4)
│   │   │   │   ├── identity.py     # IdentityResolver — 3 tiers
│   │   │   │   ├── dedupe.py
│   │   │   │   ├── transfer_match.py
│   │   │   │   └── categorize.py   # 7-layer engine
│   │   │   ├── api/
│   │   │   │   ├── routes/         # accounts, transactions, imports, review, categories
│   │   │   │   └── schemas/        # Pydantic = the public contract
│   │   │   ├── public.py           # exports ONLY protocols + read-only schemas
│   │   │   └── tests/{unit,integration,fixtures}/
│   └── tests/                  # cross-module only
├── frontend/
│   ├── src/
│   │   ├── components/         # AppShell + hand-rolled primitives (no UI library)
│   │   ├── features/finance/   # transactions, review, imports, budgets
│   │   ├── lib/
│   │   └── routes/
│   └── tests/
└── .github/workflows/ci.yml    # check (ruff, mypy, pytest) + db (pytest -m db)
```

---

## 3. Layer/dependency diagram (critical: arrows point inward)

```
          frontend TS (hand-written; see §7)
                         ↓
                          api
                         / \
                        /   \
                       /     \
                      /       \
      core/ ← finance/ ← ingestion/ ← adapters
      (money, datetime)   (domain models, services)  (normalize, fingerprint, identity)
           ▲                       ▲
           │                       │
           └─────── core/ never imports finance ─────┘
```

- `api` → depends on `ingestion` + `finance/domain` + `core`
- `finance/domain` is **PRIVATE** — only `finance/public.py` is the export surface
- `core/` never imports `finance/`
- `finance/public.py` exports only protocols + read-only schemas

⚠️ **These four rules are conventions, not gates.** Nothing in CI checks them;
mypy and ruff do not reason about layering. A violation is caught in review, or
by a test that happens to import both sides, or not at all. That was deliberately
accepted over the old static import checker: the contracts it enforced cost more
than they
caught, and one of them (`domain` must not import `ingestion`) had caused real
duplication — two copies of `trigram_similarity`, one returning `Decimal` and one
returning `float` — rather than preventing any.

---

## 4. Integrity rules, and what actually enforces them

There is no static invariant checker. There are database triggers and tests. If a rule
below is not enforced by one of those two, it is enforced by nothing but review,
and the table says so.

| Rule | What it forbids | Enforced by | Since |
|---|---|---|---|
| `raw_data_immutable` | `source_record.raw_data` or `.raw_description` ever UPDATE/DELETE | **DB row trigger**, asserted by `pytest -m db` (`test_balance_db.py`) | M1 |
| balance identity | a journal entry that does not sum to zero at commit; an entry with <2 legs; a re-parented line | **DB constraint trigger**, asserted by `pytest -m db` | M1 |
| schema ownership | FKs crossing Postgres schemas (e.g. `finance` → `health`) | **DB event trigger** `trg_no_cross_schema_fk` on `ddl_command_end` (installed by `backend/db_bootstrap.sql`), asserted by `pytest -m db` (`test_no_cross_schema_fk_db.py`) | LifeOS-ceu |
| `fingerprint_frozen` | `backend/finance/ingestion/fingerprint.py` changed | The golden digest in `backend/finance/tests/unit/test_fingerprint.py` | M1 |

`migrations_immutable` — applied Alembic revisions must never be edited in place —
has **no** enforcement. It was previously a hash manifest over an empty set of
revisions, which asserted nothing. Treat editing a released migration as a bug
caught in review.

**Not a rule, deliberately.** The per-account-type balance identity is an
arithmetic identity over *parsed rows* whose right-hand side lives in the uploaded
statement, not in this repo, so no schema constraint can express it. It is a
**required adapter acceptance test**
(`backend/finance/tests/integration/test_balance_invariant.py`). Full rationale:
`docs/adr/0003-import-decisions-real-export.md` Decision 2.

---

## 5. M0 / M1 boundary

**M0 (done):** Repo skeleton, Docker Compose (db/api/frontend), Alembic, `ARCHITECTURE.md`,
CI with lint/typecheck/pytest and the zero-egress check.

**M1 (bead LifeOS-6, landed 2026-10-03):** revision `0001_finance_ledger_schema.py` is applied to
the dev database; every balance case was verified
against a live Postgres 17 *before* the pin was recorded. Before M1 the ledger was **not** protected
at the DB level. It now enforces: a journal entry sums to zero **at commit**; an entry has ≥2 legs
including the zero-leg case; re-parenting a line re-checks *both* ends so it cannot orphan an entry;
`raw_data`/`raw_description` never change; nothing lands in the wrong schema. Every table is in
schema `finance`, `core` holding only `alembic_version`.

Three traps, each verified by execution rather than assumed:

- **The check is deferred** (`DEFERRABLE INITIALLY DEFERRED`; per-row would reject the first leg of
  every valid entry) and uses a ±0.005 tolerance on `amount_base` rather than `<> 0`, which would
  reject a valid entry carrying an FX residual. EUR-base only — `account_nature` has no
  income/expense, so no single currency can sum to zero.
- **The triggers are not a complete integrity boundary.** `TRUNCATE` bypasses them, `AUTOCOMMIT`
  reverts the check to per-row, and `SET CONSTRAINTS ALL IMMEDIATE` breaks multi-line writes. Every
  journal write belongs in an explicit transaction.
- **Two rules are enforced by a weaker mechanism than their name implies.** `raw_data_immutable`
  is trigger-enforced (a row trigger cannot reject an *identical-value* assignment, so a privilege
  layer is deferred); `no_cross_schema_fk` is enforced by the `trg_no_cross_schema_fk` event
  trigger on `ddl_command_end` — which a superuser can drop, so it binds cooperating writers,
  not an attacker holding the role — see §4.

Rationale: `docs/adr/0006-balance-trigger-and-db-invariants.md`,
`docs/adr/0005-schema-ownership.md`.

---

## 6. Money rule (loudly)

**Amounts are signed BIGINT minor units, never floats.** Per-currency decimals come from the `currency` table. `currency.decimals` is the authoritative exponent: `amount / 10^decimals` gives the human-readable value. JPY (0 decimals), BTC (8), ETH (18) are defined in the currency table — there is no separate whitelist. No `NUMERIC(18,2)` anywhere — that is wrong for JPY and crypto and causes FX rounding accumulation.

---

## 7. Type contract

**There is no shared-types package** (versioning headache), and **no codegen at
all**. The OpenAPI spec is not turned into TypeScript; the frontend API layer is
hand-written and zod-validated at runtime.

⚠️ **Nothing enforces the contract.** Neither CI job (`check`, `db`) checks for
spec drift. Two consequences follow. A change to a Pydantic contract is
not caught by CI at all; and a zod schema that disagrees with the server rejects the whole response
array rather than one field, so the failure surfaces far from its cause.

The frontend `provider` enum in `imports/types.ts` is one such hand-maintained copy.
`backend/tests/test_provider_enum_agreement.py` asserts the three hand-written copies agree, because
nothing else does. Be clear about its reach: it guards that one enum and nothing more. It is not a
general contract check, and it will not catch drift in any other shape.

Either land real generation with a drift check, or extend the cross-copy test and stop counting it as
coverage. Do not assume a safety net that is not there.

---

## 8. Zero-egress

The app must run with `--network=none`. This is a deliberate design property, not an accident. `docker run --network=none` combined with `strace -e network` asserting zero `connect()` calls proves "no telemetry" and "offline file import + dedup" simultaneously while the offline pipeline runs. **Enable Banking ingestion (M5 / bead LifeOS-10) makes live HTTPS calls to the provider API** — network egress is expected on that path. The `--network=none` guard proves the local file-import path is telemetry-free and offline-capable, not that the app never touches the network.

---

## 9. Pointers

- `docs/adr/0001-deployment-topology.md` — Netcup VPS, Docker Compose, Tailscale
- `docs/adr/0002-import-provider-enum.md` — the v1 provider list; what is retired, deferred, and
  declared-but-not-uploadable
- `docs/adr/0003-import-decisions-real-export.md` — Amex sign flip, per-account-type balance identity
  and its acceptance test, Rabobank type codes, Revolut Deposit→IBAN hold, advisory `Periode`
- `docs/adr/0004-psycopg-binary-distribution.md` — why `psycopg[binary]`
- `docs/adr/0005-schema-ownership.md` — one schema per module, no cross-schema FKs
- `docs/adr/0006-balance-trigger-and-db-invariants.md` — the deferrable balance trigger, and what
  it does *not* stop
- `docs/adr/0009-multi-module-host-and-backups.md` — one host for every module; pinned backup retention
- `docs/adr/0010-adding-a-lifeos-module.md` — the module boundary contract; how to add a module
- `docs/ENABLE-BANKING-SETUP.md` — Enable Banking connection guide
- `SAFETY.md` — rules for touching this repo
- `AGENTS.md` — agent context rules

The pre-implementation design proposal and the eleven research spikes have been
deleted; their conclusions live in the ADRs above and in the code. Nothing points
at them any more, deliberately.

---

## 10. Roadmap — M1→M10 at a glance

| Milestone | Done when | Why this order |
|---|---|---|
| **M1** | Schema + manual transactions | Proves double-entry core + balance trigger before ingestion |
| **M2** | Manual + Amex PDF import + fingerprint dedup | Same statement twice → 0 new transactions; proves Tier 3 |
| **M3** | Merchant normalization + categorization | 7-layer engine; manual correction creates learned rule |
| **M4** | Transfer matching + Amex payment | Card payment auto-detect + synthesized leg; spending correct |
| **M5** | Enable Banking: Rabobank | RS256 client, consent flow, Tier 1 + Tier 2 dedup |
| **M6** | Rebuild-from-raw | `replay --batch-id` re-runs whole pipeline; fingerprint frozen |
| **M7** | Rabobank + Revolut PDF importers | Itemized statement import; no provider ID → Tier 3 only; per-account-type balance identity holds at delta 0 |
| **M8** | Revolut API | Conditional on `/aspsps?country=NL` gate |
| **M9** | Budgets + recurring detection | Monthly budgets; `recurring_series` nightly job |
| **M10** | Net worth snapshots | `asset − liability` over time; already correct from M1 |

> Roadmap as of 2026-10-02 (beads `LifeOS-18`, `LifeOS-3pe`). **M2 was Amex CSV and is retired** —
> the Amex app exports PDF only, so PDF is the Amex path and moved up. M7 was "Amex PDF importer";
> it now carries the two PDF paths that have no adapter. Provider enum:
> `enable_banking · amex_pdf · rabobank_pdf · revolut_pdf · manual`
> (`docs/adr/0002-import-provider-enum.md`).

### Module status

**Finance V1 (the file-import path) is COMPLETE as of 2026-10-06.** The offline pipeline —
manual plus Amex/Rabobank/Revolut PDF import, fingerprint dedup, categorization, transfer
matching — is landed and working. What remains is tracked, not missing.

Deferred items ("deferred" does not mean "missing" — each is a bead with an owner and a
milestone, not a gap discovered later):

- M5 live ingestion — `LifeOS-10`
- Seed/demo ledger — `LifeOS-clw`
- `exchange_rate` — `LifeOS-l3j`
- Budgets, net-worth over time — `LifeOS-14`, `LifeOS-15`
- P4 hardening — `LifeOS-2h0`, `LifeOS-35o`
- Product scope decisions — `LifeOS-2vv`
- Enable Banking production probe — `LifeOS-17`

The module boundary contract for whatever comes next is `docs/adr/0010-adding-a-lifeos-module.md`:
one vertical slice, one schema, composition at the app layer.

---