# Research 04 — BankingSync Architecture Review

**Lane:** lib-2 (librarian, Specialist B/1) · **Completed:** 2026-09-30 · **Status:** reconciled
**Method:** actual source inspection (Go files, schema, migrations), not just README.
> Orchestrator reconciliation notes are in the final section.

---

## Executive summary

- **Verdict: (d) use purely as a reference implementation.** Study the design, build our own ingestion
  layer. Do not embed, fork, or run as a sidecar — unless you're willing to put all of Life OS under
  AGPL-3.0.
- **The pending→booked problem is solved genuinely well**: update-in-place via a `pending_map` keyed by
  `date|amount|payee|occurrence-index`, backed by a **Fellegi–Sunter record-linkage matcher with
  Hungarian assignment**. This is the most valuable thing in the repo.
- **AGPL-3.0 is the decisive blocker for embedding.** Incorporating any of its code makes Life OS a
  derivative work → Life OS must be AGPL-3.0.
- **It is Go, not Python.** Another reason lifting code is unattractive.
- **The separate-database problem is structural.** Its dedup state (`imported_refs`, `pending_map`,
  `match_reviews`) lives in its own SQLite file, separate from the budget backend. It is a standalone
  bridge app, not a library.
- **Internal architecture is better than expected** — clean package layering, a real `budget.Store` port
  interface, a backend-neutral matching engine. But not modular enough to lift cleanly without the whole app.
- **The Enable Banking client is the most extractable part** (`enablebanking/`, ~5 files, hand-rolled JWT,
  no SDK) — but small enough that re-implementing from the API docs is a weekend's work.
- **Repo is very young:** created **2026-04-02**, daily commits, last commit **2026-09-28**, **13★**, 1 fork,
  0 issues, one primary author. High velocity, unproven longevity.
- **No permissively-licensed project in this niche does "Enable Banking → own DB → own REST API" as an
  embeddable library.**

---

## Repo facts

| repo | last verified | version | last commit | activity | license |
|---|---|---|---|---|---|
| RomanSpies/BankingSync | 2026-09-30 | no tagged release; `main` @ `3795c96b`, Docker `latest` | 2026-09-28 | created 2026-04-02; daily commits; 13★, 1 fork, 0 issues | **AGPL-3.0** |
| actualbudget/actual | 2026-09-30 | — | 2026-09-29 | 29,225★, 3,050 forks, 257 issues | **MIT** (⚠️ see reconciliation) |
| maybe-finance/maybe | 2026-09-30 | — | 2025-07-24 | 54,261★; archived/redirected | AGPL-3.0 |
| ghostfolio | 2026-09-30 | — | 2026-09-29 | 9,384★, 323 issues | AGPL-3.0 |

- **Zero GitHub releases** (releases API returns `[]`). Version injected at build time via `-ldflags`.
- Distribution is the Docker image `romanspies/bankingsync:latest` only.
- GitLab-backed (commits arrive via GitLab MRs); author Roman Spies.

---

## Architecture

**Go 1.25**, single static binary, SQLite via `modernc.org/sqlite` (pure Go, no CGO). No web framework —
`net/http` + embedded HTML templates.

```
enablebanking/   client.go, oauth.go, balances.go, errors.go, pem.go, sepa.go
budget/          linkage.go, reconcile.go, assign.go, calibration.go, refit.go,
                 promotion.go, anchors.go, store.go, types.go
actual/          client.go, db.go, adapter.go, rules.go, hulc.go, proto.go
firefly/         client.go, store.go, map.go, decode.go, id.go
store/           store.go (schema+migrations), settings.go, telemetry.go
web/             server.go (64 KB), templates/
internal/        iban, payeematch, linkagegen, fireflylive
main.go          (76 KB) sync orchestration;  state.go  in-memory runtime state
```

**Layering is genuine.** `budget.Store` is a Go interface; `actual.Adapter` and `firefly.Store` both
implement it (compile-time assertions `var _ budget.Store = (*Adapter)(nil)`). The matching engine knows
nothing about either backend — it operates on `budget.Transaction` values and calls port methods.
The Enable Banking client is *not* behind an interface (used concretely by `Syncer`) but is
fully package-isolated.

---

## Pending → booked: what the code actually does ⭐

**The highest-value finding in the entire discovery phase.** Path: `main.go` `Syncer.run()` +
`budget/reconcile.go`. It is **update-in-place** — not delete-and-reinsert, not a second row.

**Step 1 — `PDNG` arrives.** If the pending key isn't already in `pending_map`, it's queued and flushed
through `budget.ReconcileBatch`. The matcher either adopts it onto an existing row, holds it for review,
or **creates a new pending row** (`cleared=false`). `state.SetPending(bank_accountID, pendingKey, txnID, date)`
records the mapping.

**Step 2 — `BOOK` arrives with the same key.** Looks up `existingTxn := knownByID[txnID]`, then updates
in place:

```go
if existingTxn.AmountCents != amountCents {
    s.ac.Update(ctx, existingTxn, budget.AmountPatch(existingTxn, amountCents))
}
s.ac.Update(ctx, existingTxn, budget.MergePatch(existingTxn, booked, pol.PayeePrefixes))
s.state.DeletePending(acct.ID, matchedKey, s.st)
s.state.AddImportedRef(acct.ID, ref, date, s.st)
```

`MergePatch` sets `Cleared = true`, fills notes only if empty, updates payee **only if not user-renamed**,
and **never touches reconciled rows**. `AmountPatch` corrects the amount if booking differs from
authorisation. Then the pending entry is deleted and the reference recorded. **One row, promoted in place.**

**Step 3 — key does not match** (fields changed, or no reference). Goes through the **Fellegi–Sunter
matcher** (`budget/linkage.go` + `reconcile.go`): weighs every candidate in a **15-day window**, arranges
the whole batch under a **one-to-one constraint via the Hungarian method** (`budget/assign.go` `Solve`),
then **adopts ≥90%**, **holds for review 50–90%**, or **creates new <50%**.

### `entryReference` handling

`getEntryRef` (`enablebanking/client.go`) reads `entry_reference`, **falling back to `transaction_id`**.
References persist in `imported_refs` with `PRIMARY KEY (bank_account_id, ref)`. A booked transaction
already present in `imported_refs` is skipped outright.

The `pending_map` fallback key (`importKeys` in `main.go`) is
**`date|amount|payee|occurrence-index`**, counted per status so a pending row and its booking collide
on the same key.

---

## Dedup, transfer detection, categorization

**Dedup — three layers, all DB-enforced:**

| Layer | Constraint |
|---|---|
| `imported_refs` (reference fast path) | `PRIMARY KEY (bank_account_id, ref)` |
| `pending_map` (pending→booked) | `PRIMARY KEY (bank_account_id, key)` |
| `match_reviews` (review queue) | `UNIQUE (bank_account_id, pending_key)` — prevents duplicate held rows |
| Backend-level | Firefly: `error_if_duplicate_hash` on create, `external_id_is` search on merge. Actual: `financial_id` lookup |

**Transfer detection — Firefly backend only.** `firefly/store.go` `splitPayload`: if
`ownAssetByIBAN(in.CounterpartyIBAN)` returns a *different* account ID, the split becomes
`type: "transfer"` with `source_id`/`destination_id`. Otherwise a withdrawal/deposit with a payee name.

> **Actual Budget has no transfer detection.** Everything is imported as a payment to a payee
> (confirmed in both the README comparison table and `actual/adapter.go`).

**Categorization — delegated to the backend.** No independent rules engine. For Actual, `actual/rules.go`
reads Actual's own `rules` table and evaluates conditions/actions (category, payee, notes, cleared).
For Firefly, the server-side rule engine runs via `apply_rules` on create/update, booked only.
BankingSync itself has **no category model and no auto-categorization logic**.

---

## Enable Banking coupling & credentials

- **Client is hand-rolled, no SDK.** RS256 JWT with the app's RSA private key (`makeHeaders`),
  `apiBase = "https://api.enablebanking.com"`.
- `FetchTransactions` paginates via `continuation_key`, **100-page cap**, repeated-key detection.
- Rate limits: `doWithRateLimitRetry` — 429 → up to 3 retries, `Retry-After` or exponential backoff
  (2s base, 60s cap).
- **Credentials:** app ID + RSA private key in the `settings` table. README states plainly:
  *"The Enable Banking private key is stored unencrypted in bankingsync.db"*. Backend credentials
  (`ACTUAL_PASSWORD`, `FIREFLY_TOKEN`, `SMTP_PASS`) are env vars.
- **Consent/session:** `StartAuth` → redirect → `CompleteAuth` → `SessionResponse`. Per-account session
  state in `bank_accounts`: `session_id`, `account_uid`, `session_expiry`, `session_ended_at`,
  `session_ended_reason`, `identification_hash`. Consent validity defaults to 180 days, clamped to the
  bank's advertised maximum. **No token refresh** — renewed by re-authorisation. Expiry checked each sync;
  warnings at <7 days, email + skip when expired. `ErrSessionEnded` classifies `EXPIRED_SESSION` /
  `CLOSED_SESSION` **by error code, not HTTP status**. Renewal matches accounts by
  **`identification_hash` (stable across sessions)**, then IBAN+currency.

---

## Self-hosting footprint

Minimal. Multi-stage Dockerfile, alpine, single static binary, non-root (uid 11011), CycloneDX SBOM via
syft. **Two services** in compose — the budget backend + `bridge-bank`. **No Postgres** — SQLite at
`/data/bankingsync.db`. Only hard external dependency is Enable Banking (+ the budget backend).
Published for `linux/amd64` and `linux/arm64`.

> **The web UI has no authentication** — the listening socket is the only access control.

---

## Licensing analysis

**AGPL-3.0**, confirmed in `LICENSE` and the GitHub API. Copyright: Roman Spies.

| Use | Consequence |
|---|---|
| Personal/private use | No obligation. Run, fork, modify freely — source disclosure triggers only on *conveyance*. |
| Fork for a private Life OS | Fine, as long as it stays private. |
| **Embed code into Life OS** | Derivative work → **Life OS must be AGPL-3.0**. The killer. |
| Use as a dependency | Same problem — linking/incorporating triggers copyleft. |
| **Study/reference** | **Fully fine.** Ideas aren't copyrightable; code is. |
| Commercial fork | AGPL permits charging, but source must be available to users. |

---

## Verdict: study, don't embed

**(d) Use purely as a reference implementation.**

1. **AGPL-3.0** rules out embedding, and makes forking contingent on licensing all of Life OS AGPL-3.0.
2. **Separate-DB architecture fights the stated goal.** Dedup state lives in its own SQLite, not in "our
   canonical ledger." Sidecar → two databases + a sync (explicitly unwanted). Custom `budget.Store`
   adapter for Life OS's DB → that *is* forking.
3. **The matching engine is the crown jewel and it is AGPL.** Fellegi–Sunter + Hungarian + review queue +
   calibration gate is genuinely best-in-class — and the most license-entangled part. **Study and
   re-implement the approach**; take the design, not the code.
4. **The Enable Banking client is the most extractable part** — and small enough that re-implementing
   from the official API docs is a weekend's work, avoiding the license entirely.
5. **6 months old, 13★, one author.** Building a personal Life OS on this fork is a maintenance gamble.

**Conditional alternative:** if you're willing to license all of Life OS under AGPL-3.0 (or keep it
private forever), then **fork-and-adapt becomes viable** — the `budget.Store` port is clean enough to
swap the Actual/Firefly adapters for a Life OS adapter. But that is a significant rewrite of persistence
and UI, not a drop-in.

## Fitness for Life OS embedding — blunt: **poor**

1. **Two databases.** Dedup state in `/data/bankingsync.db`, separate from the backend. Your canonical
   ledger would either *be* that SQLite (wrong — health/food/tasks must share it) or require a sync
   (what you want to avoid).
2. **Coupled UI.** `web/server.go` + templates is a complete standalone UI. There is **no clean REST API
   for a third-party UI** — the HTTP surface is the internal UI's own endpoints.
3. **AGPL-3.0** contaminates the whole project.
4. **Single-purpose.** Ingestion + matching + dedup. No concept of a broader Life OS.
5. **The one genuinely transferable asset is the *design*** of the matcher: Fellegi–Sunter, Hungarian
   assignment, review queue, calibration gate.

## Other projects worth knowing

| project | license | note |
|---|---|---|
| **actualbudget/actual** | MIT | Local-first, 29k★, has a bank-sync plugin (Enable Banking in Europe). Far more permissive, but still a full app, not a library. **Most permissive option** if you ever wanted an external backend. |
| maybe-finance/maybe | AGPL-3.0 | Rails + Postgres; GoCardless/Plaid sync. Archived, last push 2025-07. |
| ghostfolio | AGPL-3.0 | Investment/wealth tracker. Not transaction ingestion. Wrong niche. |
| Firefly III | GPL-3.0 | Full app, PHP. BankingSync's alternative backend. |
| fava / Beancount | MIT | Plain-text accounting, no bank sync. |
| SimpleFin / Plaid | proprietary | Not self-hosted. |

> **No permissively-licensed project does "Enable Banking → own DB → own REST API" as an embeddable
> library.** The pragmatic path is a thin ingestion layer of our own, with the matcher design re-implemented.

---

## Fact confidence

**CONFIRMED (file path / URL):** architecture, package layout, `budget.Store` port, Enable Banking client
(JWT, base URL, pagination, rate-limit retry), OAuth/consent flow, session/expiry handling, `ErrSessionEnded`
codes, **the pending→booked update-in-place path** (`main.go` + `budget/reconcile.go`), dedup constraints
(`store/store.go`), transfer detection (`firefly/store.go` `splitPayload`/`ownAssetByIBAN`), rules delegation
(`actual/rules.go`, `firefly/store.go`), SQLite + migrations, scheduling, Docker footprint, AGPL-3.0, repo
metadata, alternative-project metadata.

**CONFIRMED (README, consistent with code):** "private key stored unencrypted in bankingsync.db"; "web UI
has no authentication"; "single Go binary, single Docker container, SQLite".

**LIKELY:** that the review-queue and calibration subsystems work as described. `linkage.go` and
`reconcile.go` were read in full and match the README; `calibration.go`, `refit.go`, `promotion.go` and
`web/server.go` were **not** read in full, and nothing was executed.

**UNCERTAIN:** real-world correctness of the Fellegi–Sunter parameters against a specific bank's feed. The
README itself stresses these are "stated priors, not estimates" and that behavior varies by institution.

**NEEDS USER TESTING:** whether the bank supplies `entryReference` on pending transactions (the whole
pending→booked fallback depends on it); whether it reports `PDNG` at all; whether consent validity is
180 days or less; whether the thresholds suit the user's transaction volume.

---

# Reconciliation & challenges (orchestrator)

## 1. This **validates the user's Option A**, but on different grounds than expected

The user expected the argument to be "the core domain is manageable and an AI agent can implement it."
The actual decisive grounds are stronger and concrete:

- **AGPL-3.0** — embedding contaminates all of Life OS.
- **It's Go**, and we want Python. Even ignoring the licence, lifting code is a port, not a reuse.
- **Two-database architecture** — structurally incompatible with the stated goal.
- **Six months old, 13★, one author** — an unproven dependency for a system meant to last.

**Honest caveat I owe the user:** AGPL is *not* disqualifying for a purely private personal app. Copyleft
triggers only on conveyance. If the user is certain Life OS stays private, forking BankingSync under AGPL
is legally fine and would save real work. My recommendation is still "build our own" — because the
separate-DB architecture and the Go/Python mismatch survive the licence question anyway — but the user
should know the licence is a *weaker* obstacle than it looks for their specific case.

## 2. ⚠️ Do **not** copy the `transaction_id` fallback — it is a latent bug

`getEntryRef` falls back to `transaction_id` when `entry_reference` is missing. Research 01 established
from Enable Banking's own docs that **`transaction_id` is explicitly unstable and may change between list
retrievals**. Using it as a dedup key means occasional duplicate transactions and phantom
"amount corrections" on already-booked rows. If we adopt this design, we must instead fall back to
the **content fingerprint** (Tier-3) and leave unmatched records for review.

## 3. The "occurrence index" in the pending key **confirms** the multiset answer

`date|amount|payee|occurrence-index` is exactly the right solution to the "two identical €3.20 coffees"
problem I posed to the data-model lane. It independently validates the occurrence-counting approach and
should be treated as a settled design decision, not an open question.

## 4. Push back: Fellegi–Sunter + Hungarian may be over-engineering here

BankingSync is built for many accounts, many institutions, high volume. This user has **~3 accounts** and
maybe a few hundred transactions a month. The Hungarian assignment solves *batched one-to-one matching
under ambiguity* — a problem that essentially does not exist at this scale, and which costs real
comprehension budget in a system whose stated goal is "understandable by one person."

**Recommendation:** keep the **confidence-scoring model** and the **review queue** (both essential and
cheap). **Drop the Hungarian assignment** for v1; add one-to-one disambiguation only if the review queue
demonstrably shows ambiguous clusters. This is a deliberate "boring beats clever" call and it is the
main place I am departing from BankingSync's design.

## 5. Actual Budget's **lack of transfer detection** is a significant finding

Even if we used Actual as a backend, the sync path imports everything as a payment to a payee — no
transfer linking. Since "transfers must never count as spending" is the user's #1 correctness requirement,
**transfer matching is non-negotiable work that no existing backend would do for us.** It must be ours
regardless of which ingestion path we choose. Note this needs cross-checking against Research 05, which is
investigating Actual's own `transferId` mechanism directly.

## 6. Licence discrepancy to resolve

This lane reports **actualbudget/actual as MIT**. That conflicts with the widely-reported claim that
Actual relicensed to AGPL. Research 05 (Actual + Wealthfolio) is investigating this directly and should
settle it. **Do not rely on Actual's licence until Research 05 confirms** — it is the only licence in
this project where a wrong answer would actually matter, since MIT is the one permissive option on the table.

## 7. Security findings worth carrying into the security lane

- Storing the Enable Banking **RSA private key unencrypted in the database** is a real anti-pattern.
  Confirms the need for file-based secrets with restrictive permissions, not DB-stored keys.
- **No authentication on the web UI** — reinforces the "do not expose this; bind localhost or put it
  behind a reverse proxy / Tailscale" posture.
- Useful operational pattern to borrow: `session_ended_reason` as an explicit column, and classifying
  consent errors **by provider error code rather than HTTP status**.
