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
| `raw_data_immutable` | `source_record.raw_data` or `.raw_description` ever UPDATE/DELETE | Python static check + CI guard; DB trigger written in M1 | `scripts/check_invariants.py` |
| `no_cross_schema_fk` | FKs crossing Postgres schemas (e.g. `finance` → `health`) | Static scan of migration SQL in `scripts/check_invariants.py`, plus Postgres grants/role setup | CI job `check-invariants` |
| `migrations_immutable` | Applied Alembic revisions ever edited in place | Manifest hash comparison (alembic heads vs recorded) | CI job `check-invariants` |
| `fingerprint_frozen` | `backend/finance/ingestion/fingerprint.py` SHA-256 hash changed | Hash pinned in `invariants.yaml`; CI fails on drift | CI job `check-invariants` |

**Not among them, deliberately.** The per-account-type balance identity
(`checking`/`savings`: `prev + credits − debits = closing`; `credit_card`:
`Vorig + Debiteringen − Crediteringen = Nieuw`) is an arithmetic identity over *parsed rows*, and the
right-hand side lives in the uploaded statement rather than in this repo. `scripts/check_invariants.py`
supports only `forbid_regex`, `hash` and `manifest` — none of which execute a parse. It is therefore
a **required adapter acceptance test**, not an invariant entry; `invariants.yaml` carries a comment
pointing at it. Rationale and rejected alternatives:
`docs/adr/0003-import-decisions-real-export.md` Decision 2.

---

## 5. M0 / M1 boundary

**M0 (now):** Repo skeleton, Docker Compose (db/api/worker/frontend), Alembic, `ARCHITECTURE.md`, `invariants.yaml`, CI with invariant + egress checks. Static checkers exist; DB-level triggers are not yet written.

**M1 (bead LifeOS-6):** Schema migrations the checkers police — the `raw_data_immutable` DB trigger, the `assert_journal_entry_balances()` deferred trigger, the `no_cross_schema_fk` grant enforcement — are written in M1. A future agent must not think the ledger is already protected at the DB level; the M0 CI gates prevent obvious breakage, but the real guards come online in M1.

---

## 6. Money rule (loudly)

**Amounts are signed BIGINT minor units, never floats.** Per-currency decimals come from the `currency` table. `currency.decimals` is the authoritative exponent: `amount / 10^decimals` gives the human-readable value. JPY (0 decimals), BTC (8), ETH (18) are defined in the currency table — there is no separate whitelist. No `NUMERIC(18,2)` anywhere — that is wrong for JPY and crypto and causes FX rounding accumulation.

---

## 7. Type contract

TS types are meant to be generated from FastAPI's OpenAPI spec via `scripts/generate_types.py`. **There is no shared-types package** (versioning headache).

⚠️ **This is not true yet, and nothing enforces it.** As of 2026-10-02 `generate_types.py` writes a 6-line `LifeOSAppConfig` skeleton that says so in its own output, `frontend/src/api/generated/` contains only a README, and no CI job checks for spec drift (9 jobs: lint, typecheck, test, import-linter, invariant-check, invariant-negative, egress-test, frontend, dependency-gate). The frontend `provider` enum in `imports/types.ts` is therefore **hand-maintained**, which is why `backend/tests/test_provider_enum_agreement.py` exists — it asserts the three hand-written copies agree, because nothing else does.

Either land real generation with a drift check, or treat the cross-copy test as the contract. Do not assume a safety net that is not there.

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