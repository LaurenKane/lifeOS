# Research 03 — Life OS Product Architecture

**Lane:** ora-3 (oracle, Specialist E) · **Completed:** 2026-09-30 · **Status:** reconciled
**Mode:** read-only advisory, no code written.

> **Note:** the "Reconciliation & challenges" section at the end is the orchestrator's, not the
> specialist's. It flags where this lane conflicts with other lanes or with confirmed research.

---

## 1. The module question

### What a "module" is

A directory containing a **vertically integrated slice**: its own domain logic, persistence, API surface,
migrations, background jobs, and tests. It exports **only** a typed public interface (Python protocols /
TS types). Checklist for "is this a module":

- has its own migration history
- has a `domain/` or `models/` package that **no other module imports from directly**
- has a `public.py` re-exporting only what is safe for cross-module consumption
- is unit-testable in isolation
- has zero imports reaching into another module's private packages

### Future module boundaries (named only to stress-test the isolation mechanism — **not** designed here)

`finance`, `tasks`, `goals`, `habits`, `calendar`, `health`, `nutrition`, `inventory`, `home`, `journal`.

### Isolation mechanism: **one Postgres schema per module + CI import-boundary gate**

- One database, **one schema per module**. A shared `core` schema for genuinely global tables
  (`app_user`, `preference`, `audit_log`).
- **No cross-schema foreign keys.** Cross-module reads happen via views/matviews owned by a read-only
  `dashboard` module.
- **Not separate databases** — operational overhead (backups, migrations, pooling, local dev) isn't
  justified for a single user. Schemas give ~90% of isolation for ~10% of the pain.
- **Not naming conventions in `public`** — too easy to violate silently. Schemas make the boundary
  physical: an INSERT into `finance.transaction` from tasks code fails at the DB layer.

**Four enforcement layers:**

| Layer | Mechanism |
|---|---|
| DB | Per-module schema ownership + `GRANT`/`REVOKE`; cross-schema access only via `SECURITY DEFINER` or owned views |
| Python | `importlinter` / `pyarchtest` contracts: `finance` ⇏ `tasks`, both may import `core` |
| TypeScript | `eslint-plugin-boundaries` or a script checking imports against `module-boundaries.json` |
| Runtime | pytest fixture scanning `sys.modules` after a module's test suite, failing if another module's private package was imported |

> Key point: these are **static and checkable**, so an AI agent can run them before proposing a change.
> That is the property that makes this worth doing.

### Cross-module reads

A read-only **`dashboard` module** owning PostgreSQL materialized views that join across schemas
(e.g. `dashboard.net_worth_vs_weight`). Refreshed nightly or on demand.

Rules: `dashboard` never writes to other schemas; other modules never import from `dashboard`; each
module exposes its own views (e.g. `finance.v_transaction_summary`) and grants `SELECT` to `dashboard_role`.

This avoids the shared-write problem entirely: **no module mutates another's tables**, and all join
logic lives in one versioned, testable, reversible place.

---

## 2. Shared primitives — verdicts

**Principle:** *extract only when there are two **actual** consumers today, and the shared behaviour is
larger than the variation. "Might need it later" is not a consumer.*

| Candidate | Verdict | Reasoning |
|---|---|---|
| `Date` / `DateTime` / `Timezone` | **BUILD NOW** (in `core`) | Every module needs tz-aware UTC datetimes. A thin wrapper with UTC enforcement, ISO8601 serialization, and a `DateRange` value object pays off immediately. |
| `Tag` | **DEFER** | Finance tags ≠ Recipe tags (cuisine/diet) ≠ Task tags (context/energy). A global `tag` table forces false equivalence. Use per-module tagging. Extract only when 3+ modules show *identical* tag UX. |
| `Attachment` | **DEFER** | Receipts need OCR + expense linkage; journal photos need thumbnails/EXIF/privacy; home docs need versioning. **The storage backend is the genuinely shared part** → build `core.blob` (put/get/delete, metadata). The *domain entity* stays per-module. |
| `Note` | **DEFER** | Finance notes (free text on a txn), task notes (markdown/checklists), journal (the note *is* the entity). No shared behaviour yet. |
| `Category` | **REJECT (as global)** | Finance categories are hierarchical, budget-bound, and **mutually exclusive per transaction**. Recipe "categories" are really tags (a recipe is vegan AND quick AND dinner). Task categories are projects/areas. Three different meanings. Build `finance.category` concretely. |
| `Goal` | **REJECT (as global)** | Finance `SavingsGoal` = target amount, deadline, linked account, auto-contribution. Life `Goal` = qualitative, multi-milestone, habit-linked, subjective progress. Different state machines. Build separately; join later in a dashboard view. |
| `Event` | **DEFER** | Calendar events (RRULE, tz, attendees) ≠ finance recurring rules (fixed amount, day-of-month, skip weekends) ≠ habit recurrences (streak logic). **The recurrence engine is the shared utility** → `core.recurrence`. The `Event` entity is not. |
| `Entity` / EAV / knowledge graph | **REJECT — HARD** | The classic platform trap. `entity(id, type, jsonb)` + `relationship(src, tgt, type)` destroys schema enforcement, FK integrity, migration safety, query performance, type safety, and AI-assisted refactoring. Every query becomes dynamic SQL. If graph queries are later needed, add a **module-specific** typed `link` table. |
| `UserPreference` | **BUILD NOW** (in `core`) | `core.preference(user_id, key, value_jsonb, updated_at)`. Trivial, high leverage, zero regret. |

**Build in `core`:** `datetime`, `blob`, `recurrence`, `preference`, `rich_text` (if needed).
**Everything else:** per-module concrete typed tables.

---

## 3. Layering inside Finance

### Where the complexity actually is

Not CRUD. It is the **ingestion → normalization → dedup → transfer-match → categorization** pipeline:

```
Raw CSV/OFX/PDF
   ↓
[Adapter] → normalized ImportRow (strict Pydantic)
   ↓
[Dedupe] → fingerprint → ImportBatch + ImportRow (new/dup/transfer_candidate)
   ↓
[TransferMatcher] → pairs withdrawals/deposits across accounts within a window
   ↓
[Categorizer] → rules → proposed category_id
   ↓
[Review UI] → user confirms/edits → commits to finance.transaction
```

Stateful, iterative, user-in-the-loop → deserves its own sub-package.

### Proposed internal structure

```
finance/
├── alembic/                    # migrations for the finance schema only
├── config.py
├── domain/                     # PRIVATE. No external imports.
│   ├── models/                 # SQLAlchemy ORM
│   ├── value_objects/          # Money, Currency, DateRange, CategoryPath
│   ├── entities/               # plain dataclasses/pydantic for domain logic (not ORM)
│   ├── events/                 # simple dataclasses (TransactionCreated, BudgetExceeded)
│   └── services/               # pure logic: categorization_rules, transfer_matcher, budget_calculator
├── ingestion/                  # THE COMPLEXITY LIVES HERE
│   ├── adapters/               # every adapter implements ImporterProtocol
│   ├── normalize.py            # raw dict → ImportRow (strict)
│   ├── fingerprint.py          # deterministic hash for dedupe (pure, unit-testable)
│   ├── dedupe.py
│   ├── transfer_match.py
│   └── categorize.py
├── api/                        # thin FastAPI routers
│   ├── routes/                 # accounts, transactions, import_review, budgets
│   └── schemas/                # Pydantic = the public API contract
├── public.py                   # exports ONLY protocols, read-only schemas, enums
├── background/                 # scheduler + jobs + CLI
└── tests/{unit,integration,fixtures}/
```

### What to avoid

| Pattern | Verdict | Why |
|---|---|---|
| Full hexagonal / ports-and-adapters | **AVOID** | One `ImporterProtocol` is enough. No `Repository` interface with one implementation. |
| Separate `application`/use-case layer | **AVOID** | A thin router calling `domain.services` is fine; the use case *is* the router function. |
| Event sourcing / CQRS | **AVOID** | An `audit_log` table suffices for history. |
| Repository pattern | **AVOID** | SQLAlchemy *is* the repository. |
| Domain events + message bus | **DEFER** | Plain dataclasses + in-process `dispatch()` now. Cross-module needs → dashboard matviews. |

### Background jobs

APScheduler, no Redis/Celery. ~5 jobs for a single user does not justify a broker. `job_run`
idempotency table prevents double-runs on restart.

### Local-first v1

- **Server Postgres is the source of truth.** No offline mutation, no CRDTs, no sync engine.
- React SPA talking to the local API over **Tailscale**.
- **No native mobile in v1.** Browser on phone works.
- **Replay from source is first-class**: `import_batch` stores raw bytes + sha256 + parser version, and
  a `replay --batch-id` command re-runs the pipeline. This is the safety net for schema and
  categorization-rule changes.

**Deliberately deferred:** offline-mobile sync (ElectricSQL/Replicache/PowerSync). It would distort the
schema (UUIDs everywhere, conflict-resolution columns, tombstones). Only justify when actually demanded.

### Migration policy

1. **Alembic.**
2. **Applied migrations are immutable** — never edit one that has run anywhere.
3. **Destructive changes = two-step expand/contract:** add+backfill+dual-write → then drop.
4. **Data rewrites** use a dedicated idempotent Python script invoked via alembic `run_python`, stored
   under `finance/migrations/rewrites/YYYYMMDD_description.py`, logging every change.
5. **Replay capability built into v1.**

---

## 4. AI-assisted development as an architectural constraint

### Structure that maximizes agent success

- **Shallow, wide directories** over deep hierarchies.
- **Explicit `__init__.py` exports** — `from finance.domain import Account` over deep paths.
- **One concept per file.**
- **No circular imports**, enforced by `importlinter` (layered: `api → domain/services → domain/models`).
- **Protocols over ABCs** — structural typing, easier for an agent to implement correctly.

### Conventions to write into `AGENTS.md` / `CLAUDE.md`

```markdown
## Python
- ORM: DeclarativeBase + Mapped annotations. No raw Column.
- Money: Money(amount: Decimal, currency: Currency). NEVER float.
- Dates: timezone-aware, UTC-normalized everywhere. No os.getenv in business logic.
- Pydantic: ConfigDict(frozen=True, extra='forbid', from_attributes=True).
- Importers: every new adapter implements ImporterProtocol.
- Migrations: alembic revision --autogenerate. NEVER edit applied migrations.
           Dropping a column requires 2-step expand/contract.
- Tests: tests/unit (no DB) vs tests/integration (DB).

## TypeScript
- Types generated from OpenAPI. Never hand-write API types.
- State: TanStack Query for server state. No global store unless proven necessary.

## Cross-cutting
- No `any` without a reason comment. No print/console.log. structlog.
- Settings validated at startup via pydantic-settings.
```

### Failure modes and guardrails

| Agent failure mode | Guardrail |
|---|---|
| Inventing a second overlapping `Transaction` model | `importlinter` forbids `finance.domain.models` imports outside `finance/` |
| Breaking the raw-data invariant | Explicit invariant list the agent must acknowledge |
| Adding an unnecessary dependency (`pandas` for one CSV parse) | `deptry`/`pip-audit` in CI; new deps require human approval |
| Duplicating an existing helper | Small `core/utils.py` with `__all__`; agents import from there |
| Weakening a unique constraint | CI script scanning migrations for `drop_constraint` / `drop not null` |
| Writing a destructive migration | Invariant: no drop without a prior expand migration |

### Machine-readable architecture description

Two files:

1. **`ARCHITECTURE.md`** (≤200 lines) — module map, layer diagram, invariant list, migration policy, jobs.
2. **`invariants.yaml`** — machine-checkable, each with a concrete check:

```yaml
invariants:
  - id: raw_data_immutable
    description: "finance.import_row.raw_data is never deleted or overwritten after insert"
    check: "sql: information_schema lookup for raw_data, is_nullable='NO'"
  - id: no_cross_schema_fk
    description: "No foreign keys across PostgreSQL schemas"
    check: "sql: information_schema.table_constraints scan"
  - id: finance_no_import_tasks
    check: "importlinter forbidden_import_patterns"
  - id: migrations_immutable
    check: "script: check_migrations_immutable.py"
  - id: dedup_fingerprint_stable
    description: "compute_fingerprint() algorithm is frozen"
    check: "hash: finance/ingestion/fingerprint.py == <stored_sha256>"
```

Agent reads both before a multi-file change and reports "Invariant check: PASS".

### Off-limits operations

| Operation | Rule |
|---|---|
| Migration dropping a column/table | FORBIDDEN without human approval |
| Modify `fingerprint.py` | FORBIDDEN without human approval — breaks replay |
| Add a new external dependency | REQUIRES APPROVAL |
| Breaking OpenAPI change | REQUIRES TS regeneration + frontend test pass |

> A small explicit invariant list (5–8 items) catches most silent corruption. **Agents follow rules
> better than they infer intent.**

---

## 5. Repository structure

```
life-os/
├── .github/workflows/          # CI: lint, typecheck, test, migration + invariant checks
├── docs/
│   ├── ARCHITECTURE.md
│   ├── invariants.yaml
│   ├── DECISIONS/              # ADRs, one per consequential decision
│   └── api/                    # generated OpenAPI, committed for diffing
├── docker-compose.yml          # db, api, worker, frontend (dev)
├── docker-compose.prod.yml
├── Makefile
├── pyproject.toml              # uv
├── package.json                # root, for type-gen scripts only
├── backend/
│   ├── core/                   # shared primitives + its own alembic
│   ├── finance/                # the module
│   ├── tasks/                  # future skeleton
│   ├── dashboard/              # read-only cross-module views
│   ├── main.py                 # app factory
│   ├── config.py
│   └── tests/
├── frontend/
│   ├── src/{api/generated,components,features,lib,routes}
│   └── tests/
├── scripts/
│   ├── generate_types.py
│   ├── check_invariants.py
│   └── migrate.sh
└── AGENTS.md                   # references ARCHITECTURE.md + invariants.yaml
```

### Why not the user's sketch

| Sketch | Problem |
|---|---|
| `apps/` | Implies multiple deployable apps; we have one API + one SPA |
| root `domain/` | Encourages shared domain models across modules → coupling. `core/` is for *true* primitives only |
| `integrations/`, `importers/` | Vague. Adapters belong to the module that owns the domain |
| `workers/` | Implies a separate codebase; jobs are co-located with their module |
| `database/` | Migrations belong beside the schema that owns them |
| root `tests/` | Hard to scope; per-module tests + cross-cutting in `backend/tests/` |

### Type sharing: OpenAPI codegen, not a shared package

A `shared-types` package creates a versioning headache. Instead: generate `openapi.json` at build
time → `scripts/generate_types.py` → commit generated TS into `frontend/src/api/generated/`. CI fails
if the spec changed without regeneration. **Build-time only, zero runtime coupling.**

---

## 6. Build vs. reuse vs. defer

| Dependency type | Real cost | Verdict |
|---|---|---|
| Frameworks (FastAPI, React, SQLAlchemy) | Low | **Always** |
| Auth SaaS (Clerk/Auth0/Supabase) | Medium (lock-in) | **Defer** — own `core.auth`, ~200 lines |
| UI kit (shadcn/ui + Tailwind) | Low | **Use** (copy-paste, not a dependency) |
| CSV/PDF/OFX parsers | Low, commodity | **Reuse** |
| Bank APIs | High (access, tokens, compliance) | Use via adapter; **own the adapter, not the data model** |
| Budgeting engine (envelope/YNAB) | Medium | **Build** — your rules ≠ theirs |
| Investment price fetching | High (tax lots, corporate actions) | Reuse a price library; **own the lot model** |
| Full SaaS (Firefly III/Actual/…) | Extreme | **NEVER** — the "not another generic budgeting app" constraint |

**Principle:** *Own the domain model. Reuse commodity utilities. Never adopt a foreign data model.*

- **Domain model** = Transaction, Account, Budget, SavingsGoal, ImportBatch, TransferMatch — encodes
  *your* mental model. No external library matches your evolution.
- **Commodity utility** = CSV parsing, PDF text extraction, RRULE, hashing, date math, JWT.
- **Foreign data model** = "here's a 40-column Transaction table you must map your CSV into." That is the
  trap. **Wrap the external API in your adapter; map to your own ImportRow immediately.**

### Proposed v1 scope

**Build:** `core` primitives; finance accounts/transactions/import pipeline/dedupe/transfer-match/
categorization/budgets/savings goals/net worth snapshots; dashboard matviews; self-owned auth
(email+password+TOTP); finance UI incl. review queue; Docker Compose, Tailscale, Alembic, CI.

**Defer to v2+:** `core` tagging/notifications/webhooks; investments (lots, dividends, tax);
auto-sync; multi-currency; forecasting; cross-module dashboards; OAuth/passkeys; tasks/habits/
calendar/nutrition/inventory/journal; K8s, observability, mobile.

---

## 7. Open questions for the user

1. **Server topology** — separate always-on machine (accessed from laptop/phone) or everything on one
   laptop? Affects Tailscale vs. localhost, backup strategy, and what "local-first" means.
2. **Bank connectivity** — is CSV/OFX import the primary path for v1?
3. **Investments in v1** — just manual holding snapshots, or automatic price fetching + lot accounting?
4. **Backup/restore** — do you want a one-click "export entire Life OS to encrypted archive" in v1?
5. **Mobile** — is a native Android app a hard requirement within 6 months? (If yes, sync architecture
   must be discussed **now**; if no, deferring is correct.)

---

## 8. Summary of consequential decisions

| Decision | Recommendation |
|---|---|
| Module isolation | Postgres schemas + importlinter + dashboard views |
| Shared primitives | **Build:** datetime, blob, recurrence, preference, rich_text. **Reject:** Entity, Category, Goal, Tag (global). **Defer:** Attachment, Note, Event. |
| Finance layering | Ingestion pipeline first-class. Thin API, domain services, no hexagonal ceremony. |
| Background jobs | APScheduler worker. No Redis/Celery. |
| Local-first v1 | Server = source of truth. SPA + API over Tailscale. Replay from raw = first-class. |
| Migrations | Alembic, immutable, expand/contract, replay built in. |
| AI guardrails | ARCHITECTURE.md + invariants.yaml + CI import boundaries + off-limits list. |
| Repo structure | Monorepo: `backend/{core,finance,dashboard}`, `frontend/`, TS types generated from OpenAPI. |
| Reuse principle | Own the domain model. Reuse commodity utils. Never adopt a foreign data model. |

---

# Reconciliation & challenges (orchestrator)

## Where this lane contradicts confirmed research

1. **"Use Plaid (or GoCardless/Bankin')" — Plaid is wrong for this user.** The Amex lane
   (`02-amex-nl.md`) confirmed Plaid covers Amex in **US/CA/UK only** and its Netherlands coverage is
   ING/Rabobank/ABN AMRO. Plaid is not a viable Netherlands aggregator. **Drop it.** The correct
   provider set from confirmed research is **Enable Banking (primary)** and **GoCardless Bank Account
   Data (fallback)**. *Bankin'* was named but not researched — flagged as unverified, worth a look since
   it is Dutch-native.

2. **"APScheduler in-process (same container as FastAPI)" contradicts its own compose file**, which shows
   a separate `worker` service. **Resolve as: a separate worker *process* running one APScheduler
   instance**, so API restarts don't kill jobs and a scheduler crash doesn't take down the API. Not
   in-process, but also not Redis/Celery. Single instance only — two schedulers would double-run jobs.

3. **"Build `core.auth` with argon2 + JWT + TOTP, 200 lines" is a real security-surface decision and
   needs cross-checking against the security lane's threat model.** A single-user self-hosted app may
   correctly need *less* than a full auth system (e.g. reverse-proxy auth, or simply "do not expose it").
   **Do not adopt either recommendation until the security lane is reconciled.** This is a live conflict.

4. **Dashboard materialized views are listed as v1 scope.** With one module there is nothing to join.
   **Defer `dashboard` entirely to v2.** Creating an empty cross-module read layer now is exactly the
   premature abstraction this lane warns against elsewhere — it contradicts its own advice.

5. **`Money(amount: Decimal, currency: Currency)` as the mandate vs. the data-model lane's storage
   question.** Decimal-in-Python and NUMERIC-in-Postgres must be reconciled: whether the DB stores
   minor units (`BIGINT`) or `NUMERIC(precision, scale)`. **This is the single most consequential
   unresolved schema decision** and must be settled in the synthesis, not left to two lanes to disagree.

## Where this lane is strong and should be adopted largely as-is

- **The invariant-list + `ARCHITECTURE.md` pattern.** Best idea in the whole discovery phase for the
  stated constraint "I am vibe-coding with AI agents." Static and checkable beats prose.
- **Freezing the fingerprint function by hash.** Directly protects the "rebuild from raw" guarantee that
  the Amex 6-month CSV window makes non-optional.
- **Rejecting `Entity`/EAV, global `Category`, and global `Goal`.** Correct, and well argued.
- **Replay-from-raw as a v1 first-class feature.** The Amex research makes this mandatory, not optional.
- **Own the domain model, reuse commodity utilities, never adopt a foreign data model.** The correct
  governing principle for the whole project.

## Open questions to put to the user

ora-3's five questions are good, and Q1 (server topology) is genuinely blocking for the deployment
design. Note that Q2 is already answered by the research: **file import is the primary v1 path** (Amex
has no API at all; the aggregator is a later milestone).
