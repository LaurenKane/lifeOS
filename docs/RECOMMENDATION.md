# Life OS — Final Recommendation

**Discovery phase complete · 2026-09-30**
Companion to `docs/ARCHITECTURE-PROPOSAL.md`. Sources: `docs/research/01`–`11`.

---

## Recommended Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser (React + TS + Tailwind + shadcn/ui)                         │
│  Tailscale HTTPS  ·  read-only  ·  generates nothing it can't undo    │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │  OpenAPI codegen (build-time)
┌────────────────────────────────▼─────────────────────────────────────┐
│  FastAPI  —  thin routers, Pydantic schemas                          │
│  Session cookie + CSRF · no user table                               │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────────────────────┐
│  core/     datetime · money · blob · recurrence · preference          │
│  finance/  domain/  ·  ingestion/  ·  api/  ·  background/           │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
┌───────▼────────┐      ┌────────▼─────────┐     ┌────────▼────────┐
│  INGESTION     │      │  IDENTITY        │     │  CATEGORIZATION │
│               │      │  RESOLVER        │     │  7 layers       │
│ enable_banking│      │                 │     │  1 user rules   │
│ amex_pdf      │─────▶│ T1 provider id  │────▶│  2 merchant xfm │
│ rabobank_pdf  │ Raw  │ T2 pending→book │ raw │  3 known map    │
│ revolut_pdf   │Record│ T3 fingerprint  │copy │  4 fuzzy        │
│ manual        │      │  + review queue │     │  5 LEARNED      │
└───────────────┘      └────────┬─────────┘     │  6 LLM (opt-in) │
                                 │               │  7 review       │
                                 ▼               └─────────────────┘
                    ┌────────────────────────────┐
                    │  CANONICAL LEDGER          │
                    │  journal_entry  (header)   │
                    │    └──< journal_line (≥2)  │
                    │  double-entry, balances=0  │
                    │  transfer_match (stored)    │
                    └────────────┬───────────────┘
                                 │
                    ┌────────────▼───────────────┐
                    │  PostgreSQL                │
                    │  FDE · no column encryption│
                    │  backups restic+age        │
                    └────────────────────────────┘

  RAW IS NEVER LOST:  import_batch (raw_payload, never deleted)
                     source_record (raw_data, raw_description — immutable)
                     ⇒ replay --batch rebuilds the ledger exactly

  NEVER DOUBLES:      sign = direction · transfers excluded from spending
                     is_synthesized leg makes the Amex payment correct by construction

  PROVIDERS (v1):     enable_banking · amex_pdf · rabobank_pdf · revolut_pdf · manual
                     amex_csv retired · revolut_csv deferred · google_wallet excluded
                     rabobank_pdf / revolut_pdf declared, no adapter yet
                     → docs/adr/0002-import-provider-enum.md
```

---

## Recommended Stack

| Layer | Choice | Note |
|---|---|---|
| Language | **Python 3.12+** | Your preference; confirmed adequate |
| API | **FastAPI** | OpenAPI generation gives us TS types for free |
| Validation | **Pydantic v2** | `frozen=True, extra='forbid'` |
| ORM | **SQLAlchemy 2.0** (`Mapped`/`mapped_column`) | No raw `Column` |
| Migrations | **Alembic** | Immutable once applied; expand/contract for destructive changes |
| Database | **PostgreSQL 16+** | Needs `pg_trgm` for merchant matching; partial indexes for dedup |
| Money | **`NUMERIC`/`Decimal` in Python, `BIGINT` minor units + `NUMERIC(18,4)` in Postgres** | Never `float` |
| Frontend | **React + TypeScript + Vite + Tailwind + shadcn/ui** | Your preference; confirmed |
| Server state | **TanStack Query** | No global store unless proven necessary |
| Types | **OpenAPI codegen** (`openapi-typescript` / `hey-api`) | Never hand-write API types |
| Scheduling | **APScheduler**, single worker process | Not in-process (API restarts kill jobs); not Redis/Celery |
| HTTP client | **httpx** | Async |
| Secrets | **OS keyring (libsecret)**, age-encrypted fallback | Never in the DB |
| Backups | **restic + age** | 3-2-1, monthly restore test |
| Parsers | **`pdfplumber`** (Amex PDF), stdlib `csv` | Commodity — reuse, don't build |
| Deploy | **Docker Compose**: `db`, `api`, `worker` (+ `web` in prod) | LUKS on the host |
| CI | **ruff · mypy --strict · pytest · importlinter · trivy · invariant check · zero-egress test** | |
| Lint gates | `importlinter` module contracts, `deptry` dependency gate | |

**One change from your stated preferences:** none. All held up. The only additions are
`pg_trgm` (needed for merchant matching), `APScheduler`, and the invariant/egress CI gates.

---

## Reuse

**Commodity libraries (no opinions, no lock-in):** `pdfplumber` · stdlib `csv` · `httpx` · `apscheduler` ·
`cryptography` (Fernet) · `pydantic-settings` · `structlog` · `zod` · `shadcn/ui`.

**Design patterns to study (not to copy unless MIT):**

| From | Take | Licence |
|---|---|---|
| **Actual Budget** | `Payee.transfer_acct`; `imported_id` + imported-wins-over-manual; `starting_balance_flag`; `tombstone`; `Rule{stage, conditionsOp, conditions[], actions[]}` | **MIT — copy freely** |
| **BankingSync** | `pending_map` keyed `date\|amount\|payee\|occurrence-index`; update-in-place merge preserving user edits; confidence-scored candidate selection; review queue | AGPL — ideas only |
| **Firefly III** | Type-on-the-group-not-the-leg; asset/liability taxonomy; CAMT field-mapping as a separate layer | AGPL — ideas only |
| **Wealthfolio** | `Activity` type enum; `Asset` kind/instrument/`instrument_key`; `Lot`+`LotDisposal` FIFO; `MonetaryValue{local,base}`; `idempotency_key` hash + unique constraint; `is_user_modified`/`needs_review` | AGPL **and** Rust — clean-room only |

---

## Build

1. **The double-entry ledger** — `journal_entry` + `journal_line`, balance-enforced by a deferred
   trigger. Not negotiable: the Amex requirement forces it.
2. **The three-tier identity resolver** — provider ID / pending→booked / content fingerprint with
   occurrence counting, plus a review queue that drains itself via learning.
3. **Transfer matching** — including the synthesized liability leg that makes card payments correct.
4. **The provider-agnostic ingestion layer** — `SourceAdapter` protocol, one adapter per source.
5. **The 7-layer categorization engine** — with **layer 5, learning from manual corrections**, which no
   reference project implements.
6. **Amex PDF importer** — the **only** Amex path, not a first choice among two. The web portal's CSV
   is **retired**: the Amex app exports PDF only (ADR 0002, `LifeOS-18`).
7. **Replay-from-raw** — `replay --batch-id`. Turns raw retention into a safety net.
8. **The invariant system** — `ARCHITECTURE.md` + `invariants.yaml` + CI. For an AI-vibe-coded project
   this is infrastructure, not bureaucracy.

---

## Defer

| Deferred | Trigger to revisit |
|---|---|
| **Investments** (seam is in M1) | When you actually want to track holdings |
| **Google Wallet** | If you need recent Amex data between exports — and accept fuzzy dedup |
| **Revolut** | The **API** path is gated on the `/aspsps?country=NL` answer. The **file** path is not deferred work — `revolut_pdf` is a declared v1 import path (ADR 0002) |
| **Budgets, recurring, net worth** | M9/M10, after categorized data is clean |
| **LLM categorization** | Optional forever. Core must work without it |
| **Hungarian one-to-one matching** | Only if the review queue shows real ambiguous clusters |
| **Native mobile / offline sync** | Only when demanded. Designing it now distorts the schema |
| **Second Life OS module** (`tasks`, `health`…) | When you want one |
| **`dashboard` matviews, shared `core` tagging** | When there are ≥2 modules |
| **Multi-user / authN beyond a cookie** | Second user, or health data |
| **Envelope budgeting, FX revaluation, tag hierarchy, entity graph** | Probably never |

---

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **1** | **Revolut NL unavailable via Enable Banking** | **~40%** | Low | **`revolut_pdf` importer fallback** — the user already holds a full-year statement (2026-01-01→10-01). Not a CSV importer: the PDF has no stable ID, so Tier-1 dedup does not apply (doc 11 §3.6, §7; ADR 0003 Decision 4). Run the one `GET /aspsps` call before M5. **Retire this risk first** |
| **2** | **Rabobank emits no `PDNG` pending status** | Medium | Low | Tier 2 becomes dead code. Simplify. Detect at runtime, don't assume |
| **3** | ~~**Amex PDF text layer is scanned images, not text**~~ — **RESOLVED 2026-10-01: it is selectable text.** All four statements extract as clean, correctly ordered text, no OCR. **M7 is a go**, and `Bedrag in vreemde valuta` / `Nieuwe transacties voor:` are real parseable fields | ~~Medium~~ **None** | ~~**High**~~ **Low** | No OCR project. The real remaining Amex risk moved: the PDF carries **no sign** (direction is a separate `CR` marker line) and each statement mixes the card payment with real refunds, so payment-vs-refund is description-only. → doc 11 §2, §3.2; `docs/adr/0003-import-decisions-real-export.md` Decisions 1–2 |
| **4** | **Deduplication produces silent duplicates or over-merges** | **High** | **High** | Occurrence counting, confidence thresholds, mandatory review queue, property/fuzz tests. **Default to "new + review", never "guess + merge"** |
| **5** | **Card-payment detection misses a phrasing** | Medium | Medium | `confidence=0.95` with user rejection + the pattern **learns** from confirmations |
| **6** | **Enable Banking goes away / gets expensive / changes ToS** | Low | Medium | ToS permits personal use today. Adapter is ~200 lines. GoCardless Bank Account Data is the fallback with a free tier |
| **7** | **Consent expiry (~180d) is handled badly → user loses visibility** | Medium | Medium | Design re-auth as a **normal expected flow**, not an error. Warn 30d ahead. Store `session_ended_reason` |
| **8** | **Fingerprint algorithm changes, invalidating all stored fingerprints** | Medium | **High** | **Frozen by hash in `invariants.yaml`; CI blocks edits.** Changing it requires a migration |
| **9** | **Schema churn during vibe-coding destroys real financial history** | **High** | **High** | Immutable migrations, expand/contract, and **replay-from-raw (M6) before any schema churn** |
| **10** | **AI agent silently breaks an invariant or adds a rogue dependency** | **High** | Medium | `invariants.yaml` + `importlinter` + dependency approval gate + `AGENTS.md` off-limits list |
| **11** | **LUKS not enabled / backups never restored** | Medium | **Critical** | Checklist items 3–4 are P0. **Monthly restore test is not optional** |
| **12** | **Over-engineering creeps in** (the failure mode of a "platform") | Medium | Medium | Explicit defer list above; `invariants.yaml` forbids empty-layer creation; every new abstraction needs two *actual* consumers |

---

## Decisions Required

**Answer these before M5 (gating):**

1. ~~**Do you want Revolut via API, or is Revolut CSV acceptable?**~~ — **ANSWERED 2026-10-02.**
   **Neither: `revolut_pdf` is the v1 import path.** `revolut_csv` is **deferred** — no Revolut CSV has
   ever been seen and its stable `id` is unverified (doc 11 §7). The PDF has **no stable ID** at all
   (one file covers two products and two own IBANs), so **Tier-1 dedup is Enable-Banking only**
   (doc 11 §3.6). Current and Deposit are **separate accounts**, and because the file never states
   which IBAN the Deposit belongs to, ambiguous ownership is **held for explicit user confirmation,
   never inferred**. → `docs/adr/0002-import-provider-enum.md`,
   `docs/adr/0003-import-decisions-real-export.md` Decision 4.
   *(The `/aspsps?country=NL` gate above still decides whether the **API** path also exists. That
   question is open; this one is not.)*
2. **How much history do you actually need?** Enable Banking clamps to ~90 days post-consent. If you
   want years of Rabobank history, **bank CSV export becomes a first-class historical source**, not just
   an Amex fallback. This materially changes the ingestion priority order.

**Answer before M2 (affects the first real code):**

3. ~~**Download one Amex CSV and one Amex PDF and look at them.** The column set, date format, and whether
   the PDF text layer is selectable determine M2 and M7. Ten minutes of your time retires Risk #3 and
   four "NEEDS USER TESTING" items at once.~~ — **DONE 2026-10-01, by the real exports.** Four Amex
   statements and five Rabobank/Revolut statements were parsed and reconciled to their own printed
   totals at delta 0. Risk #3 is retired (selectable text, no OCR); there is **no Amex CSV to
   download**, and no `NEEDS USER TESTING` items remain for Amex. What the statements settled instead:
   the PDF's two dates differ on **42 of 121 rows (35%)**, and the amount column carries **no sign** —
   direction is a separate `CR` marker line. → `docs/research/11-real-export-verification.md` §1–§3.2;
   ADR 0003 Decisions 1–2.
4. **Deployment topology: always-on server + Tailscale, or everything on one machine?** Changes exposure
   model, backup strategy, and what "local-first" means in practice. **This is the one genuinely
   blocking product question.**

**Product decisions (no wrong answer, but they scope v1):**

5. **Will this ever be public or open-source?** If **no** → forking BankingSync under AGPL is a
   legitimate option that would save roughly two weeks of ingestion work. If **yes** → build-our-own is
   mandatory, and our choice already guarantees a permissive licence. *My recommendation stands either
   way, because BankingSync is Go and carries its own database — but you should make this call knowing
   the licence isn't the blocker it appears to be for a private app.*
6. **Native mobile within 6 months?** Yes → sync architecture must be discussed now. No → defer.
7. **Savings goals in v1?** Deliberately deferred; say if you want them early.
8. **Which module is second — tasks, health, or food?** Only needed when you want to add one; it
   stress-tests the module boundaries for real.

---

## Next Step

**One concrete first implementation milestone:**

> **M0 + M1: the skeleton, the invariants, and the double-entry core.**
>
> - Repo structure per §D; Docker Compose with `db`, `api`, `worker`; LUKS noted as a host prerequisite.
> - `ARCHITECTURE.md` (≤200 lines) + `invariants.yaml` with the first four invariants:
>   1. `raw_data_immutable` — `source_record.raw_data` and `raw_description` are never updated or deleted
>   2. `no_cross_schema_fk` — no foreign keys across Postgres schemas
>   3. `migrations_immutable` — applied migrations are never edited
>   4. `fingerprint_frozen` — `fingerprint.py` hash-pinned
> - Full schema from §E applied, with the **deferred** balance trigger working — verified by a test that
>   a two-line entry commits and an unbalanced entry is rejected at commit.
> - Manual transaction CRUD through the API + the simplest possible UI.
> - CI green: ruff, mypy strict, pytest, invariant check, zero-egress test.

**Why this and not the importer first:** the importer is where the risk is, but the invariants are what
keep an AI-vibe-coded project honest, and M1 proves the double-entry balance invariant before any
ingestion complexity sits on top of it. **M2 (Amex PDF + fingerprint dedup) is the next block** and is
where the project earns its correctness.

**Your first action, in parallel: ~~download an Amex CSV and a PDF and check the columns~~ — already
done 2026-10-01.** Four real Amex statements were parsed and reconciled at delta 0; there is no Amex
CSV to download (`LifeOS-18`). That exercise retired Risk #3 and produced the per-account-type balance
identity every adapter must now satisfy. → `docs/research/11-real-export-verification.md` §1.

---

## Appendix A — Fact confidence

| Claim | Confidence | Source |
|---|---|---|
| Enable Banking ToS permits free personal use, restricted mode, no contract | **CONFIRMED** | terms (2026-01-09) |
| Auth is RS256 (not HS256) with self-signed cert + RSA key | **CONFIRMED** | API reference |
| `entry_reference` is the dedup key; `transaction_id` is unstable | **CONFIRMED** | EB FAQ |
| `entry_reference` usually only present on `BOOK` | **CONFIRMED** | EB FAQ / OpenAPI |
| `account.uid` rotates at re-auth; `identification_hash` is stable | **CONFIRMED** | EB FAQ |
| Consent ~180d, no refresh token, `EXPIRED_SESSION` 401 | **CONFIRMED** | EB FAQ |
| No AIS webhooks — polling required | **CONFIRMED** | API reference |
| History clamps ~90d after consent; `strategy=longest` | **CONFIRMED** | EB FAQ |
| ~4 background fetches/day/ASPSP without PSU headers | **CONFIRMED** | EB FAQ |
| Pagination via `continuation_key`, session-bound | **CONFIRMED** | EB FAQ |
| Rabobank in EB sandbox ASPSP list | **CONFIRMED** | sandbox docs |
| **Revolut NL support** | **UNCERTAIN** | absent from sandbox list — **verify** |
| **Amex NL not available via any PSD2 aggregator** | **CONFIRMED** | Amex statement (2021) + EB SE/FI deprecation (2025-03) |
| Amex exports contain no stable transaction IDs | **CONFIRMED** | TrueLayer help (2024-07) |
| ~~Amex CSV available, ~6mo, posted-only~~ — **RETIRED.** The web portal does offer a CSV (~6mo, posted-only) but the Amex **app** exports PDF only, so no CSV is produced (ADR 0002, `LifeOS-18`) | ~~**CONFIRMED** / **LIKELY**~~ **N/A** | NL accounting services 2024–2026 (portal capability, not our workflow) |
| Amex PDF itemized, 7yr, both txn + process date, FX detail. **⚠️ The two dates are not interchangeable — they differ on 42 of 121 rows (35%), so `raw_posting_date` is load-bearing.** The FX column was **empty in all four statements** (no non-EUR code), so FX stays unverifiable | **CONFIRMED** / **LIKELY** (7yr) | PDF converters 2026; **measured** — `docs/research/11-real-export-verification.md` §2, §3.1 |
| Amex **app** export formats: **PDF only.** The *web portal* additionally offers CSV, OFX/QFX and QBO (XLSX uncertain) — none in our workflow | **CONFIRMED** (PDF) / **UNCERTAIN** (XLSX) | Amex NL portal; `LifeOS-18` |
| Google Takeout carries Amex transaction JSON | **LIKELY** | Google + Amex ToS |
| BankingSync is AGPL-3.0, Go, own SQLite, 13★, last commit 2026-09-28 | **CONFIRMED** | LICENSE + GitHub API |
| BankingSync pending→booked is update-in-place via `pending_map` | **CONFIRMED** | `main.go`, `budget/reconcile.go` |
| Firefly III + data-importer are AGPL-3.0 | **CONFIRMED** | LICENSE + GitHub API |
| Firefly data-importer dedup is identifier-only, no update path | **CONFIRMED** | `ApiSubmitter::uniqueTransaction()` |
| Firefly has no investment model | **CONFIRMED** | `app/Models/` |
| Firefly has no PDF/XLSX/OFX/QIF importer | **CONFIRMED** | `ConversionRoutineFactory` |
| **Actual Budget is MIT** | **CONFIRMED** | GitHub API `spdx_id: MIT` |
| Actual `transfer_id` links both legs | **CONFIRMED** | `transaction.ts` + `merge.ts` |
| Actual has no investment support | **CONFIRMED** | `account.ts`, `transaction.ts` |
| Wealthfolio is AGPL-3.0 + TRADEMARKS.md (Teymz Inc) | **CONFIRMED** | LICENSE + TRADEMARKS.md |
| Wealthfolio is **Rust/Tauri + SQLite (Diesel)**, not NestJS/Postgres | **CONFIRMED** | `Cargo.toml`, `crates/storage-sqlite/` |
| Wealthfolio `Activity` 14-type enum, `idempotency_key` | **CONFIRMED** | `activities_model.rs`, `idempotency.rs` |
| Dutch banks support redirect-only PSD2 auth | **LIKELY** | EB NL docs — **verify via `/aspsps`** |
| ~~Amex CSV exact columns / date format~~ — **MOOT.** No Amex CSV exists in the workflow, so there is nothing to test. The PDF's own format *is* now measured: two dates, `DD.MM.YY`, Dutch amount with dot thousands and comma decimal, and **no sign in the amount** — direction is a separate `CR` marker line. → doc 11 §3.1, §3.2; ADR 0003 Decision 1 | ~~**LIKELY**~~ **MEASURED** | `docs/research/11-real-export-verification.md` §1, §3 |

## Appendix B — Version, activity, licence

| Project | Last verified | Last commit | Activity | Licence | Copyright |
|---|---|---|---|---|---|
| Enable Banking | 2026-09-30 | samples 2026-09-17 | Active | Proprietary SaaS; **personal use permitted** | Enable Banking Oy (FI) |
| RomanSpies/BankingSync | 2026-09-30 | 2026-09-28 | Daily; created 2026-04-02; 13★ | **AGPL-3.0** | Roman Spies |
| firefly-iii/firefly-iii | 2026-09-30 | 2026-09-30 | Very active; 24,783★ | **AGPL-3.0** | `james@firefly-iii.org` |
| firefly-iii/data-importer | 2026-09-29 | 2026-09-29 | Active; 839★ | **AGPL-3.0** | `james@firefly-iii.org` |
| actualbudget/actual | 2026-09-30 | 2026-09 (active) | Very active; 29,225★ | **MIT** | James Long |
| actualbudget/actual-server | 2026-09-30 | 2026-09 (active) | Active | **MIT** | James Long |
| wealthfolio/wealthfolio | 2026-09-30 | 2026-09 (active) | Very active | **AGPL-3.0** + TRADEMARKS | Teymz Inc (trademark) |
| maybe-finance/maybe | 2026-09-30 | 2025-07-24 | **Archived** | AGPL-3.0 | — |
| ghostfolio | 2026-09-30 | 2026-09-29 | Active; 9,384★ | AGPL-3.0 | — |

## Appendix C — Licensing conclusion

**Life OS will be permissively licensed, and we copy no AGPL code.** The three AGPL projects
(BankingSync, Firefly III, Wealthfolio) are studied for design only; their mechanisms are reimplemented.
No code is copied, so no derivative work is created and no copyleft obligation arises.

**Actual Budget is MIT**, so its patterns (`transfer_id`, `Payee.transfer_acct`, `imported_id` merge
semantics, `starting_balance_flag`, `tombstone`) may be used directly with no attribution requirement —
though we implement our own versions.

**One honest caveat, offered because you asked us not to hide uncertainty:** AGPL is *not* a blocker for a
purely private personal app — copyleft triggers only on conveyance. If Life OS will never be public or
commercial, forking BankingSync under AGPL would be legally fine and would save roughly two weeks of
ingestion work. **We do not recommend it**, because BankingSync is written in Go and keeps its dedup
state in a separate SQLite database, so you would inherit a port *and* the two-database architecture you
explicitly rejected. But the licence is the weakest of the four reasons, and you should know that.
