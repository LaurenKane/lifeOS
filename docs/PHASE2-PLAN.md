# Phase 2 — Collapse to Target Shape, and Make the App Actually Work

Status: plan, not yet executed. Phase 1 (dead-code + process removal) must be
merged and green first.

## Part 0 — Read this before touching anything

This document is written for an agent who has not seen the Phase 1 audit. The
short version of why it exists:

The repo has 17,413 lines of Python backend, 9,174 of them tests (53%), and
roughly 1,388 lines of source (32% of non-test code) that cannot be reached from
any entry point. Meanwhile **the app cannot yet read a single real bank
statement.** Everything below follows from that gap.

Two facts that shaped every decision:

1. `backend/finance/ingestion/adapters/amex_pdf.py:155` — `extract_pdf_text()`
   is `payload.decode("utf-8", errors="replace").splitlines()`. It is a
   placeholder that decodes a PDF as text. There is no PDF library in
   `pyproject.toml` at all.
2. `backend/finance/ingestion/adapters/enable_banking.py:4` — "the HTTP client,
   the RS256 signing and the consent flow are M5." There is no consent flow, no
   signing, no network. It is a JSON-shape parser for a payload the backend
   never fetches.

So the priority order for Phase 2 is **not** "make it pretty." It is:
make it read real data, then collapse the structure around whatever that
requires.

### Non-negotiable: SAFETY.md

Read `SAFETY.md` before running any destructive command. An agent destroyed a
live Enable Banking RSA private key with `rm -rf` on 2026-09-30. A private key
cannot be regenerated. Rules:

- Never `rm -rf` in this tree. Use `python3 tools/rm_guard.py <path>` (dry run
  by default).
- Never delete a file you did not create in this session without reading it.
- Credentials live in `~/.config/lifeos/`, never inside the repo.
- No bulk glob deletes.
- Before any destructive action, state exactly what you delete and why.

---

## Part 0b — Decisions already made (do not relitigate)

| Item | Decision | Reason |
|---|---|---|
| Budgets (`domain/services/budget.py` + frontend stub) | **KEEP** | The arithmetic is money-correct minor-unit integer work, already written and tested (~350 LOC incl. tests). Rebuilding it later means rewriting it. Note it still has no `/budgets` endpoint — wiring one is a separate task, not a cleanup task. |
| Amex CSV + Revolut CSV adapters | **DELETE** (~370 LOC) | PDF is the chosen import path. Unreachable in production today — `_FILE_ADAPTERS` has one entry. Preserve the Amex "no stable ID" knowledge as a comment on the dedup code (Part 4b step 4). |
| `background/worker.py` | **DELETE now, re-add with Enable Banking** | Empty job registry. The one thing that genuinely needs it is scheduled bank polling, which arrives with the PSD2 integration. See Part 3c. |
| `core/alembic` + `core` Postgres schema | **KEEP** | Habit tracking is a confirmed second LifeOS module. Zero revisions today, so there is nothing to reconcile. Deleting now means a real schema rename later. Collapsing to one schema is deferred until module two starts. |
| Enable Banking integration | **Undecided — needs an explicit A/B/C choice** | See Part 3c. Also: Revolut NL reachability is unconfirmed. |
| `httpx` runtime dep | **KEEP** | Currently unused at runtime but required by option (A) in Part 3c. Removing it now guarantees a needless round-trip later. |

---

## Part 1 — What must survive, and why

These are not up for simplification. Do not "improve" them.

| Keep | Why |
|---|---|
| `backend/core/money.py` | Minor-unit integers. `currency.decimals` is the *only* authority and is deliberately not derived from the ISO code, so a historical redefinition cannot silently reinterpret stored amounts. No floats anywhere. |
| The 3 DB triggers in the finance migration | `assert_journal_entry_balances` (Σ legs = 0 at COMMIT), `assert_journal_entry_has_lines` (≥2 lines), `assert_source_record_raw_immutable`. Enforced in the one place application code cannot route around. Moving these into Python is strictly weaker. |
| Double-entry structure + counter-leg convention | A spend that doesn't balance against a contra-leg is indistinguishable from a transfer. `manual_posting.py:18-50` names the failure exactly: a sandwich would read as money moving between your own accounts. |
| `source_record.raw_data` / `raw_description` immutability | This is what makes replay exact. Delete the columns and every import is unrecoverable from the provider. |
| `backend/finance/ingestion/fingerprint.py` — frozen | Old `raw_data` must stay parseable by the same normalisation. Never edit the algorithm once real data exists. |
| Statement reconciliation test | `test_balance_invariant.py` reconciles parsed rows against the totals the statement prints on its own front page, at delta 0. This caught every parsing bug during real-export verification. Expand this; never delete it. |
| Zero-egress guarantee | For an app holding a live PSD2 credential, proving the file-import path makes zero `connect()` calls is a real control. Keep the claim; the implementation is shrinkable. |
| `tools/rm_guard.py`, `SAFETY.md`, `make verify-no-secrets` | See above. |
| `tools/probe_aspsps.py` | The only code that actually talks to Enable Banking. |
| All 9 runtime deps in `pyproject.toml` | Every one is load-bearing. This is the one dimension with nothing to cut. |

---

## Part 2 — Two bugs to fix before any refactor

These are correctness issues, not bloat. Fix them early because they corrupt
data.

### 2a. The counter-leg is resolved by exact name match

`backend/finance/domain/services/manual_posting.py:38-40` — the system expense
account is found by matching `SYSTEM_EXPENSE_ACCOUNT_NAME` as an exact string.
Rename that account in the UI and every subsequent posting fails.

**Fix:** add a stable key column (e.g. `is_system BOOLEAN` or a
`system_key TEXT UNIQUE` column) and resolve on that. Keep the loud failure
tests currently at `test_manual_transactions_db.py:802,889`.

### 2b. Fuzzy categorisation guesses at threshold 0.6

`backend/finance/domain/services/categorize.py:55` — trigram similarity matches
merchant descriptions. The file's own docstring (`:21-22`) says "a wrong category
is worse than an empty one: the user cannot see a category they did not choose."
It ships the fuzzy guess anyway, with a confidence number that looks
authoritative.

**Fix:** delete the fuzzy layer. Keep explicit rules, learned corrections from
user feedback, and the uncategorized queue. If a merchant is not confidently
matched, leave it uncategorized and let the user choose.

---

## Part 3 — Make the app work (do this BEFORE the collapse)

The collapse is only worth doing once you know what the app needs to be. Build
these first, then reshape around the result.

### 3a. Real PDF extraction

**Why first:** `extract_pdf_text()` is the single blocking gap. Amex PDF is the
only registered upload adapter, so today the app's main import path cannot read
a PDF.

**Do:**
1. Add `pdfplumber` to runtime deps. Do not hand-roll PDF text extraction — the
   format has compressed streams, font encodings, and per-page positioning.
2. Replace the body of `extract_pdf_text()` in `amex_pdf.py:155` with a
   `pdfplumber`-backed implementation. Keep the function signature — it is the
   injectable seam the egress test relies on.
3. **Get a real Amex PDF statement.** Use it as a committed test fixture
   (redact account numbers, keep transaction rows). A synthetic fixture will
   not catch layout drift between statements.
4. Test against the real file, not just a synthetic one.

**Verify:** parse the real statement and assert the row count matches the
printed transaction count on the statement itself.

### 3b. Statement reconciliation, per provider

`test_balance_invariant.py` already implements the right idea for Amex: reconcile
parsed rows against the balance the statement prints on its front page, delta 0,
with sabotage cases proving the check is not vacuous.

**Do:** replicate that pattern for every adapter you keep. This is the test
suite that earns its keep. Everything else is optional.

**Verify:** each new reconciliation test fails when you deliberately break the
parser.

### 3c. Decide the Enable Banking question

`tools/probe_aspsps.py` is a 556-line standalone script that does RS256 signing
with stdlib `urllib` and lives *outside* the backend. The backend adapter is a
shape parser with no client.

You have three honest options. Pick one; do not leave it half-built.

- **(A) Go live.** Move the signing/consent flow into the backend. Add `httpx`
  (already declared, currently unused at runtime) and a JWT library. Handle
  `PDNG` pending states properly — the adapter's Tier 2 identity logic exists for
  exactly this and is currently unreachable.
- **(B) Stay manual.** Delete `enable_banking.py` and the unreachable Tier 2
  identity code. Import PDFs and CSVs by hand. Accept that bank sync never
  happens.
- **(C) Defer explicitly.** Keep the script as a spike, delete the backend
  adapter, and write one line in `ARCHITECTURE.md` saying when this will be
  revisited.

Option (B) is the ponytail answer for a single-user app: no bank connection, no
OAuth/consent expiry, no credential rotation, no pending-state machine. Option
(A) is right if you want automatic sync badly enough to own that maintenance.

**Confirmed target banks: Rabobank and Revolut (NL).** One caveat worth
resolving before you commit to an integration shape — the research docs record
Revolut NL reachability as **uncertain**: whether `/aspsps?country=NL` lists
Revolut at all was never confirmed. Verify that first. It decides whether Revolut
needs an adapter at all or is simply unavailable, and that is a different design.

**Worker service (DECIDED: delete now, re-add with option (A)).**
`backend/finance/background/worker.py` (87 LOC) currently has an empty job
registry — `list_jobs()` returns `{"jobs": [], "note": "job registry lands with
M1"}`. It is a compose service and a console script doing nothing.

It is genuinely the right home for scheduled bank polling *once Enable Banking
lands*: a consent expires, tokens refresh, and a "sync now" button you must
remember to click is worse than a nightly poll. But nothing needs it today, and
it costs a second container, a second entry point, and a compose health check for
zero functionality. Deleting it now and re-adding it with option (A) costs one
87-line file.

If you take option (B) or (C), **do not re-add a worker at all.** A cron trigger
or `systemd timer` hitting an inline route inside the API process is enough for
one user with two banks. Only a separate worker is justified if sync becomes
background work the API must not block on.

### 3d. Close the silent FX residual

`transfer_match.py:36` — `CROSS_CURRENCY_TOLERANCE_MINOR = 50`, plus
`manual_posting.py:443` `absorb_fx_residual()` which quietly books the difference
into a leg. Absorbing silently converts a real FX loss into an apparent transfer
match. Book the residual explicitly as an FX gain/loss leg, or refuse the match.

---

## Part 4 — The collapse

Target: **~2,400 backend LOC, 9 modules, ~50 tests** (from 17,413 / 47 / 414).

Do Part 3 first. Then collapse.

### 4a. Target shape

```
backend/
  main.py        50    FastAPI app, mount router
  db.py          80    engine + session
  money.py      120    Money, Currency — COPIED VERBATIM from core/money.py
  models.py     400    ONE flat ORM module (was 7 files, 1,085 LOC)
  ingest.py     550    adapters + normalize + fingerprint + dedupe, ONE module
  post.py       400    posting rules + double-entry build
  reconcile.py  120    statement reconciliation, callable from CLI and test
  api.py        700    ALL routes in one file (was 5 files, 1,312 LOC)
  migrations/   716    ONE alembic setup, ONE schema. Trigger SQL VERBATIM.
```

Note `models.py` collapses 7 files but keeps **all 14 tables**, including the 6
with no current code path (`Institution`, `Merchant`, `MerchantAlias`,
`CategoryRule`, `RecurringSeries`, `ProviderAccountLink`). Dropping a table means
a migration; keeping it costs ~100 lines. Keep them.

### 4b. Order of operations

Do these in this order. Each step leaves the app working.

1. **Collapse `api/routes/` into `api/api.py`.** Pure move, no logic change. Run
   tests.
2. **Collapse `domain/models/` into `models.py`.** Pure move. Run tests. Confirm
   `test_models_match_schema.py` still passes — it is the guard here.
3. **Do NOT merge the two Alembic setups.** DECIDED: keep `core/alembic` as-is.
   Habit tracking is a confirmed second LifeOS module, and the cost of keeping
   the second schema while it is still empty is one `env.py` plus the
   `search_path` convention. Deleting it now means renaming an existing schema
   later, which is a real migration. `core/alembic/versions/` has zero
   revisions today, so there is nothing to reconcile. **Collapsing the two
   schemas into one is deferred until habit tracking actually starts.**
   Consequence: `adr/0005-schema-ownership.md` and the per-connection
   `search_path` in `config.pg_connect_args()` both stay. When the second
   module lands, that is the moment to revisit — and at that point there will
   be a real reason for the boundary rather than a promise of one.
4. **Merge `ingestion/` into `ingest.py`.** Remove the duplicated
   `normalize_description` — `fingerprint.py:78` is the canonical one, hash-pinned.
   **Keep this knowledge comment** (currently `amex_csv.py:1-11`, being deleted
   in Phase 1) attached to the dedup code where the constraint actually bites:

   > Amex exports contain no stable ID at all. Amex's own transaction
   > identifiers are documented to change between exports, so re-importing
   > `September.csv` produces a complete duplicate of every row. This is why
   > fingerprint dedup (Tier 3) is mandatory for Amex, not optional.

   Do not lose this. A duplicate import bug takes an afternoon to diagnose from
   a stack trace and one sentence here prevents it.
5. **Delete `public.py`.** Its "only export surface" claim was never true: 8 call
   sites import `finance.domain.*` directly. With one module there is no
   boundary to defend.
6. **Now delete `.importlinter` and the layering ADRs.** With one module there
   are no layers. The moment module two appears, reintroduce them.

### 4c. Reduce test volume by composition, not by count

The target is ~50 tests / ~1,200 LOC. What changes is *which* tests exist:

| Suite | Tests | Rationale |
|---|---|---|
| `reconcile.py` per real provider | 5 | The crown jewel. One per bank, real exported statement fixture. Expand first. |
| double-entry + DB triggers | 6 | Verify the trigger fires and nothing is written. |
| fingerprint replay | 6 | Import → replay → byte-identical entry. |
| dedup occurrence index | 6 | Two identical €3.20 coffees on one day. |
| money | 8 | Not 34. |
| end-to-end import → ledger → API | 4 | One path, real fixture. |
| egress | 3 | Keep the claim, shrink the harness. |

**The rule:** every remaining test must be able to fail because *the money is
wrong*. A test that fails because a module moved is the type-checker's job — and
`mypy --strict` already does that job.

Delete: `test_import_smoke.py` (done in Phase 1), `test_provider_enum_agreement.py`
(218 LOC proving three hand-copied copies of a provider enum agree — the enum
itself shrinks to what exists), and any test whose failure mode is "a function
moved."

### 4d. Shrink the provider enum to what exists

`routes/imports.py:57-63` declares 5 providers; `_FILE_ADAPTERS` (`:71`) implements
1; and that one cannot parse a PDF. A user with a Rabobank statement gets
`400 Unknown provider` (`:133`) — after 218 lines of test have carefully kept
three copies of that lie in sync.

**Never advertise a capability you don't have.** Cut the enum to providers with
a working adapter behind Part 3a.

### 4e. Infra

- `Makefile`: 45 targets → ~10 (`up`, `down`, `test`, `test-db`, `migrate`,
  `lint`, `ci`, `clean`, `verify-no-secrets`, `help`). Keep `verify-no-secrets`.
- `docker-compose.yml`: 4 services → 2 (`db`, `app`). Drop the worker unless
  Part 3c option (A) or real background work exists. Run Vite on the host.
- CI: 2 jobs on `push` only. No `pull_request` trigger — there are no other
  contributors.
- `ARCHITECTURE.md`: one living doc, ~150 lines. Delete the other 5 ADRs or fold
  the load-bearing decisions (deployment topology, money-as-minor-units) into it.
- Dependencies: use `uv sync --frozen` + `git diff --exit-code uv.lock`. That is
  the entire dependency gate. The 459-line script is redundant.

---

## Part 5 — Known risk the design papers over

### 5a. Occurrence-index dedup can silently eat a real transaction

`backend/finance/ingestion/dedupe.py:14-17` admits it: "Getting this wrong either
eats a real transaction or creates a phantom one." Two identical €3.20 coffees on
the same day are genuinely indistinguishable. This is irreducible.

**Make the ambiguity visible in the UI rather than resolving it silently**, and
log every occurrence-index assignment so you can audit a month later.

### 5b. The synthesised Amex payment leg is unmodelled liability

`transfer_match.py:19-25` synthesises the credit-card payment leg. The liability
identity (`Vorig + Debiteringen − Crediteringen = Nieuw saldo`) is checked by
nothing in the repo. This is a real balance-drift risk that surfaces only when
you compare against your bank statement — which is exactly why Part 3b is
mandatory per adapter.

### 5c. `search_path` is per-connection

`config.pg_connect_args()` returns `{"options": "-csearch_path=..."}` and must be
used by the app *and* every engine. A hand-typed `psql` session that forgets it
errors out confusingly. Part 4b step 3 eliminates this class of bug entirely.

---

## Part 6 — Definition of done

- A real Amex PDF statement imports and reconciles to delta 0 against the
  balance printed on the statement.
- Backend is ≤ 3,000 LOC with ≤ 10 modules.
- Test suite is ≤ 60 tests and every one can fail because money is wrong.
- Zero files exist solely to enforce structure (no importlinter, no invariant
  checker, no smoke test).
- `make ci` runs in under 3 minutes.
- One PostgreSQL schema. One alembic setup. One `ARCHITECTURE.md`.
- `SAFETY.md`, `rm_guard.py`, and `verify-no-secrets` intact.
- The `SYSTEM_EXPENSE_ACCOUNT_NAME` lookup is a stable key, not a string match.
- No fuzzy category guesses.