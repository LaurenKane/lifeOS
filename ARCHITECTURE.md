# ARCHITECTURE.md — Life OS

## 1. What this is
A personal financial single-user OS for EU residents. Tracks transactions across Rabobank, Amex, Revolut. Double-entry ledger, fingerprint dedup, file import, no telemetry.

---

## 2. Module map (from §D)

```
life-os/
├── AGENTS.md                  # agent rules; points to ARCHITECTURE.md + invariants.yaml
├── ARCHITECTURE.md            # module map, layer diagram, invariants (≤200 lines)
├── invariants.yaml            # machine-checkable invariants
├── docker-compose.yml         # db, api, worker, frontend
├── Makefile                   # dev, test, migrate, generate-types, check-invariants
├── pyproject.toml             # uv
├── docs/
│   ├── ARCHITECTURE-PROPOSAL.md   # companion: the full design this summarises
│   ├── RECOMMENDATION.md          # final recommendation, risks, decisions required
│   ├── research/                  # the 11 research reports
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
│   │   │   ├── adapters/       # enable_banking.py, amex_pdf.py, manual.py
│   │   │   │                   # + rabobank_pdf.py, revolut_pdf.py — declared providers,
│   │   │   │                   #   adapters to come (docs/adr/0002-import-provider-enum.md).
│   │   │   │                   #   amex_csv.py / revolut_csv.py remain for replay but are
│   │   │   │                   #   unreachable via V1_PROVIDERS.
│   │   │   │   ├── normalize.py
│   │   │   │   ├── fingerprint.py  # FROZEN once shipped (invariant)
│   │   │   │   ├── identity.py     # IdentityResolver — 3 tiers
│   │   │   │   ├── dedupe.py
│   │   │   │   ├── transfer_match.py
│   │   │   │   └── categorize.py   # 7-layer engine
│   │   │   ├── api/
│   │   │   │   ├── routes/         # accounts, transactions, imports, review, categories
│   │   │   │   └── schemas/        # Pydantic = the public contract
│   │   │   ├── public.py           # exports ONLY protocols + read-only schemas
│   │   │   ├── background/         # scheduler + jobs + CLI
│   │   │   └── tests/{unit,integration,fixtures}/
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

---

## 3. Layer/dependency diagram (critical: arrows point inward)

```
          frontend TS (generated from OpenAPI)
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

---

## 4. Four machine-checked invariants (table)

| Name                | What it forbids                                                                 | How enforced (static check / manifest / hash pin) | Checker location |
|---|---|---|---|
| `raw_data_immutable` | `source_record.raw_data` or `.raw_description` ever UPDATE/DELETE | Python static check + CI guard + **DB trigger (M1)** | `scripts/check_invariants.py` |
| `no_cross_schema_fk` | FKs crossing Postgres schemas (e.g. `finance` → `health`) | Static scan of migration SQL only — the `GRANT`s in the bootstrap do **not** enforce this | CI job `check-invariants` |
| `migrations_immutable` | Applied Alembic revisions ever edited in place | Manifest hash comparison (alembic heads vs recorded) | CI job `check-invariants` |
| `fingerprint_frozen` | `backend/finance/ingestion/fingerprint.py` SHA-256 hash changed | Hash pinned in `invariants.yaml`; CI fails on drift | CI job `check-invariants` |

**Not among them, deliberately.** The per-account-type balance identity is an arithmetic identity
over *parsed rows* whose right-hand side lives in the uploaded statement, not in this repo, and no
checker kind available (`forbid_regex`, `hash`, `manifest`) executes a parse. It is a **required
adapter acceptance test**, not an invariant entry. Full rationale:
`docs/adr/0003-import-decisions-real-export.md` Decision 2.

---

## 5. M0 / M1 boundary

**M0 (done):** Repo skeleton, Docker Compose (db/api/worker/frontend), Alembic, `ARCHITECTURE.md`,
`invariants.yaml`, CI with invariant + egress checks. Static checkers only — no DB-level triggers.

**M1 (bead LifeOS-6, landed 2026-10-03):** revision `0001_finance_ledger_schema.py` is applied to
the dev database and hash-pinned in `scripts/migrations.lock.json`; every balance case was verified
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
- **Two invariants are enforced by a weaker mechanism than their name implies.** `raw_data_immutable`
  is trigger-enforced (a row trigger cannot reject an *identical-value* assignment, so a privilege
  layer is deferred); `no_cross_schema_fk` is enforced by the regex, not by grants.

Rationale: `docs/adr/0006-balance-trigger-and-db-invariants.md`,
`docs/adr/0005-schema-ownership.md`.

---

## 6. Money rule (loudly)

**Amounts are signed BIGINT minor units, never floats.** Per-currency decimals come from the `currency` table. `currency.decimals` is the authoritative exponent: `amount / 10^decimals` gives the human-readable value. JPY (0 decimals), BTC (8), ETH (18) are defined in the currency table — there is no separate whitelist. No `NUMERIC(18,2)` anywhere — that is wrong for JPY and crypto and causes FX rounding accumulation.

---

## 7. Type contract

**There is no shared-types package** (versioning headache). TS types are *intended* to be generated
from FastAPI's OpenAPI spec via `scripts/generate_types.py`, with `frontend/src/api/generated/`
regenerated-only — but that script emits a fixed 6-line skeleton and no real type, so the frontend
API layer is hand-written and zod-validated at runtime.

⚠️ **Codegen is unimplemented and nothing enforces the contract.** No CI job checks for spec drift
(10 jobs: lint, typecheck, test, test-db, import-linter, invariant-check, invariant-negative,
egress-test, frontend, dependency-gate). Two consequences follow. A change to a Pydantic contract is
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

- `docs/ARCHITECTURE-PROPOSAL.md` — the full rationale
- `docs/RECOMMENDATION.md` — final recommendation, risks, decisions required
- `docs/adr/0001-deployment-topology.md` — Netcup VPS, Docker Compose, Tailscale
- `docs/adr/0002-import-provider-enum.md` — the v1 provider list; what is retired, deferred, and
  declared-but-not-uploadable
- `docs/adr/0003-import-decisions-real-export.md` — Amex sign flip, per-account-type balance identity
  and its acceptance test, Rabobank type codes, Revolut Deposit→IBAN hold, advisory `Periode`
- `docs/ENABLE-BANKING-SETUP.md` — Enable Banking connection guide
- `SAFETY.md` — rules for touching this repo
- `invariants.yaml` — machine-checkable invariants
- `AGENTS.md` — agent context rules
- `docs/research/` — the 9 research reports

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

---