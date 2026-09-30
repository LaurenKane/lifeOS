#!/usr/bin/env bash
# Creates the Life OS roadmap as Beads issues with dependency wiring.
set -euo pipefail
cd /home/lauren/Documents/NewCode/LifeOS

C() { bd create --id "$1" -t "$2" -p "$3" -l "$4" -t "$5" \
      --title "$6" --description "$7" --acceptance "$8" --design "$9" --json >/dev/null; echo "created $1"; }

# ─── GATING: research tasks that must happen before build work ───────────────

bd create --id LifeOS-1 -t task -p 0 -l gating,research,banking --title "Probe Enable Banking: is Revolut NL supported?" \
  -d "GATING. Answers the single biggest roadmap unknown.

Run GET /aspsps?country=NL on the Enable Banking production API with a valid RS256 JWT and record:
  - Is Rabobank present in the production ASPSP list? (sandbox is CONFIRMED, production is LIKELY)
  - Is Revolut present, and under what exact name?
  - maximum_consent_validity for each (seconds)
  - auth_methods for each - does any NL bank use a non-redirect (decoupled/credential) flow?
  - required_psu_headers for each

Decision rule: if Revolut is absent, M8 becomes a Revolut CSV importer instead of an API adapter.
No architectural change either way - the schema is provider-agnostic." \
  -a "Results recorded in docs/research/01-enable-banking-psd2.md as a dated addendum, and docs/RECOMMENDATION.md risk #1 updated." \
  --design "Script: tools/probe_aspsps.py (see that file for the exact procedure). Credentials live in the OS keyring, never in the repo." \
  -e 30 --json >/dev/null && echo "created LifeOS-1"

bd create --id LifeOS-2 -t task -p 0 -l gating,research,amex --title "Inspect real Amex NL CSV and PDF exports" \
  -d "GATING. Only the user can do this - it needs their own Amex account.

Download from americanexpress.nl (Rekeningoverzicht > Transactiehistorie > Downloaden):
  1. CSV, with AND without 'Include all additional transaction details' ticked
  2. One PDF statement

Record exactly:
  - CSV column headers, in order
  - date format (NL likely DD/MM/YYYY) and the sign convention
  - is the Reference column populated? does it look like a stable ID or a sequential number?
  - what the details checkbox adds or removes
  - actual history window: request 6 / 12 / 24 months and note where truncation begins
  - pending: make a purchase, wait 24h, re-export - is it there? (expect NO)
  - PDF: is it itemized per transaction? does it show BOTH transaction date and process date?
  - PDF CRITICAL: is the text layer selectable/copyable, or is it a scanned image?
  - PDF: are foreign-currency charges shown with original amount + exchange rate?
  - is XLSX offered on the consumer portal (unconfirmed)?" \
  -a "Findings recorded in docs/research/02-amex-nl.md. M2 CSV column mapping confirmed. M7 go/no-go decided on the PDF text layer (if scanned, M7 becomes an OCR project and must be rescoped)." \
  --design "Do NOT commit the exported files - they contain real financial data. Store outside the repo, e.g. ~/lifeos-private/." \
  -e 45 --json >/dev/null && echo "created LifeOS-2"

bd create --id LifeOS-3 -t task -p 0 -l gating,decision,infra --title "Decide deployment topology (VPS / home server / hosted Postgres)" \
  -d "GATING. Blocks M0.

The app needs a host that is always on, plus secure access from laptop and phone.

Requirements:
  - always available (laptop may be off)
  - Docker Compose: db + api + worker
  - a LONG-RUNNING worker process (Enable Banking has no webhooks, so sync is polled every few hours)
  - PostgreSQL with pg_trgm and DEFERRABLE INITIALLY DEFERRED constraint triggers
  - automated backups with point-in-time recovery (losing a financial ledger is unacceptable)
  - minimal lock-in, minimal third parties, EU data residency, low cost

Evaluate: small EU VPS (Hetzner/Netcup/etc), home server/NAS, Supabase or other managed Postgres,
PaaS container + managed DB. See docs/research/10-hosting.md for the full comparison.

The specific question to answer: is Supabase viable given it cannot run an arbitrary
long-running worker container?" \
  -a "Topology chosen and written up in docs/adr/0001-deployment-topology.md. Docker Compose service list fixed. Backup + restore-test procedure documented. Secrets approach fixed (OS keyring vs platform env vars)." \
  --design "Tailscale is the likely answer for remote access: no public exposure, no reverse proxy to maintain, works for laptop and phone. Confirm free tier covers the device count." \
  -e 60 --json >/dev/null && echo "created LifeOS-3"

bd create --id LifeOS-4 -t task -p 1 -l decision,legal --title "Record licence policy: MIT, no AGPL code" \
  -d "DECISION MADE BY USER 2026-09-30, needs writing down so it is not re-litigated.

- Life OS will be permissively licensed (MIT).
- We will NOT fork or embed BankingSync (AGPL-3.0, also Go, also carries its own SQLite DB).
- We copy no code from BankingSync, Firefly III, or Wealthfolio (all AGPL-3.0).
- Actual Budget is MIT, so its patterns may be used directly with no attribution requirement.
- Rationale for keeping the AGPL door open: the user may later publish or make Life OS public.
  Owning the domain model with no copyleft is what preserves that option." \
  -a "LICENCE file (MIT) added. A short LICENSING.md records each project consulted, its SPDX id, and whether anything was copied. An invariant / CI check greps for AGPL-3.0 headers in our source." \
  -e 20 --json >/dev/null && echo "created LifeOS-4"

# ─── Milestones ─────────────────────────────────────────────────────────────

bd create --id LifeOS-5 -t feature -p 0 -l m0,infra,blocked --deps LifeOS-3 --title "M0: repo skeleton, Docker Compose, invariants, CI" \
  -d "First implementation block. Nothing stores real data yet.

Deliverables per docs/ARCHITECTURE-PROPOSAL.md section D:
  - backend/ (core/, finance/ with domain, ingestion, api, background)
  - frontend/ (React + TS + Vite + Tailwind + shadcn/ui)
  - docker-compose.yml: db, api, worker
  - Makefile, pyproject.toml (uv), lint config
  - AGENTS.md, ARCHITECTURE.md (<=200 lines), invariants.yaml
  - CI: ruff, mypy --strict, pytest, importlinter, invariant check, zero-egress test

The four non-negotiable invariants, machine-checked:
  1. raw_data_immutable - source_record.raw_data and raw_description are never updated or deleted
  2. no_cross_schema_fk  - no foreign keys across Postgres schemas
  3. migrations_immutable - applied migrations are never edited
  4. fingerprint_frozen  - finance/ingestion/fingerprint.py is hash-pinned

The zero-egress test is the distinctive one: run the app with --network=none and strace -e network,
assert zero connect() calls, while file import still works. Proves no telemetry AND offline operation." \
  -a "docker compose up brings up a healthy db/api/worker. CI green on a fresh clone. A deliberate attempt to violate each invariant fails CI (a negative test per invariant, not just a positive check)." \
  --design "Guardrails BEFORE data. An AI-vibe-coded project breaks invariants silently without these. This is the highest-leverage non-feature work in the whole roadmap." \
  -e 480 --json >/dev/null && echo "created LifeOS-5"

bd create --id LifeOS-6 -t feature -p 0 -l m1,schema --deps LifeOS-5 --title "M1: double-entry schema + manual transactions" \
  -d "Schema from ARCHITECTURE-PROPOSAL.md section E, with the corrections already applied:
  - DEFERRABLE INITIALLY DEFERRED balance trigger (NOT a row-level FOR EACH ROW trigger, which would
    reject every valid transaction because it checks the sum before the second line exists)
  - no 'direction' column - the sign is the direction, and a direction column contradicts a signed amount
  - no user_id - single user, and it is noise on every row
  - no account.provider_account_id - duplicated by provider_account_link, and its unique constraint
    would break when one card is visible to two providers (exactly the Amex + Google Wallet case)
  - no journal_entry.source_record_id - circular FK with source_record.journal_entry_id
  - no empty security/holding/cost_basis_lot tables - the seam is account_type='investment' plus
    nullable security_id/units/price_per_unit on journal_line, using the same unconstrained-BIGINT
    pattern as the future attachment_id/goal_id
  - pg_trgm extension enabled

Then: manual transaction CRUD through the API + the simplest possible UI." \
  -a "A two-line entry commits successfully. An unbalanced entry is REJECTED AT COMMIT (not per-row). No user table. Manual expense CRUD works end to end. Every balance invariant test passes." \
  --design "Proving the double-entry balance invariant before any ingestion complexity sits on top of it is the entire point of this milestone." \
  -e 480 --json >/dev/null && echo "created LifeOS-6"

bd create --id LifeOS-7 -t feature -p 1 -l m2,dedup,amex --deps LifeOS-6,LifeOS-2 --title "M2: Amex CSV import + fingerprint dedup" \
  -d "The milestone that proves correctness, and the only ingestion path guaranteed to exist.

- SourceAdapter protocol + AmexCsvAdapter (column mapping from LifeOS-2 findings)
- import_batch (retains raw_payload, never deleted) + source_record (immutable raw_data)
- Normalization into journal_entry/journal_line, double-entry
- Tier 3 fingerprint: SHA256 over canonicalised description|amount|currency|date|account|occurrence_index
- occurrence_index via ROW_NUMBER() so two identical EUR 3.20 coffees produce TWO entries
- The cross-batch UNIQUE index on (fingerprint, account_id)
- Categorization deferred to M3 - everything lands uncategorized

Note: Amex has NO stable transaction IDs, so Tier 1 is unavailable here. This IS the Tier 3 path." \
  -a "Import September.csv twice -> second import creates 0 new journal_entry rows; all source_record rows marked status='duplicate'. Two identical EUR 3.20 coffees on the same day -> 2 entries with occurrence_index 1 and 2. Re-import after row reordering -> no duplicates. raw_description is byte-identical after any user edit." \
  --design "fingerprint.py is written as a pure function with no I/O so it is exhaustively unit-testable, and it is hash-frozen from the start. Changing it later invalidates every stored fingerprint - that is why it is an invariant." \
  -e 600 --json >/dev/null && echo "created LifeOS-7"

bd create --id LifeOS-8 -t feature -p 2 -l m3,categorization --deps LifeOS-7 --title "M3: merchant normalization + 7-layer categorization" \
  -d "Layer 1 exact user rules / 2 merchant_alias normalization / 3 known NL merchant map /
4 trigram fuzzy / 5 LEARNED FROM MANUAL CORRECTIONS / 6 LLM fallback (opt-in, deferred) /
7 uncategorized review queue.

Layer 5 is the differentiator: NEITHER reference project implements it. Firefly has no evidence of
learnability; Actual has no auto-suggest. Every manual correction raises merchant_alias.confidence to
1.0 or creates an is_learned category_rule, so the queue drains itself.

Rules must stay hand-editable: description_pattern + priority + is_learned. No generic rules DSL,
no YAML logic engine, no expression evaluator. The whole rule set should fit on one screen." \
  -a "Correcting 'PayPal XYZ' to Entertainment > Music means the NEXT identical transaction categorizes automatically with no rule authored by hand. The uncategorized queue is served by the idx_jl_uncat partial index. Rules are editable in the UI and readable as plain text." \
  --design "Reuse the merchant_alias learning mechanism for card-payment description patterns later rather than inventing a second learning store." \
  -e 480 --json >/dev/null && echo "created LifeOS-8"

bd create --id LifeOS-9 -t feature -p 1 -l m4,transfer,correctness --deps LifeOS-6 --title "M4: transfer matching + the Amex card payment" \
  -d "The correctness requirement. Once this works, spending totals are trustworthy.

  - transfer_match table (stored, never recomputed, with match_method + confidence + confirmed_at)
  - Own-account transfers: Rabobank <-> Revolut, excluded from spending
  - THE AMEX CASE: a EUR 50 Amex purchase is an expense on a liability account; a later Rabobank
    -50 'American Express' has NO matching row to link, because the liability balance simply falls.
    Synthesize the liability leg (is_synthesized, synthesized_reason='card_payment') so the entry
    balances and spending is 50 once, never 100.
  - SEPA direct debit vs own-account transfer: deterministic rule, else review queue
  - Nightly sweep for unmatched outbound legs whose other leg has not been imported yet
  - Review queue UX: CONFIRM / REJECT / IGNORE

  Deliberately NOT implemented: Hungarian one-to-one assignment (borrowed from BankingSync). It solves
  batched ambiguous matching, which does not exist at 3 accounts. Reconsider only if the review queue
  shows real ambiguous clusters." \
  -a "THE HEADLINE TEST: Amex 50 purchase then Rabobank -50 -> total spending is 50, not 100. Both journal entries balance. Two EUR 50 transfers the same day produce 2 distinct transfer_match rows, not 1 merged pair. A SEPA DD to a third party is an expense with no transfer_match. An ambiguous DD-vs-transfer lands in the review queue rather than being guessed." \
  --design "Card-payment detection is ILIKE '%american express%' at confidence 0.95 with user rejection allowed, and it LEARNS: after confirmation the observed description is promoted to a rule. Brittle on day one, self-correcting by month three." \
  -e 600 --json >/dev/null && echo "created LifeOS-9"

bd create --id LifeOS-10 -t feature -p 2 -l m5,banking,enable-banking --deps LifeOS-8,LifeOS-9,LifeOS-1 --title "M5: Enable Banking ingestion for Rabobank" \
  -d "  - RS256 JWT client (NOT HS256 - correction to the original assumption), hand-rolled, no SDK
  - RS256 consent flow: POST /auth -> bank redirect -> POST /sessions
  - Account linking on identification_hash, NEVER on account.uid (uid rotates at every re-auth)
  - Aggressive backfill at consent time with strategy=longest (history clamps to ~90 days shortly after
    consent, so waiting loses data permanently)
  - Polled sync - Enable Banking has NO AIS webhooks. Respect ~4 background fetches/day/ASPSP
    unless PSU headers are supplied.
  - Tier 1 dedup (entry_reference, scoped by account) + Tier 2 pending->booked
  - EXPIRED_SESSION (401) handled as a NORMAL expected re-auth flow, not an error state
  - session_ended_reason recorded as an explicit column

  Note: entry_reference usually only materialises on BOOK, so Tier 2 cannot lean on provider IDs and
  must be confidence-scored with a review queue. Tier 3 fingerprint is computed for API rows too as a
  backstop if entry_reference ever changes on a historical re-fetch." \
  -a "Rabobank consent completes end to end. A re-sync of the same window creates 0 duplicates. A PDNG->BOOK transition reconciles into ONE journal_entry, updated in place, preserving any user-set category. EXPIRED_SESSION produces a clear re-auth prompt, not a stack trace. A 90-day backfill lands in full on first consent." \
  --design "The RSA private key lives in the OS keyring, never the database. Enable Banking issues us no access or refresh tokens - the credential surface is one long-lived key." \
  -e 600 --json >/dev/null && echo "created LifeOS-10"

bd create --id LifeOS-11 -t feature -p 1 -l m6,integrity --deps LifeOS-7 --title "M6: replay-from-raw" \
  -d "Turn raw retention into an actual safety net, not a promise.

  - CLI: replay --batch-id=N re-runs the entire pipeline from import_batch.raw_payload
  - import_batch records raw bytes + sha256 + parser version
  - Enables: schema changes that affect normalization, categorization rule changes, and full rebuild
    after any bug in the pipeline

  This is the mitigation for risk #9 (schema churn during vibe-coding destroying real financial
  history) and the answer to the Amex 6-month CSV window, where archived exports are the only
  durable copy of anything." \
  -a "Deleting every journal_entry and journal_line, then running replay, reproduces the ledger byte-identically. Changing a category rule and replaying re-categorizes without re-importing the file. This is done BEFORE any schema churn, as promised in the roadmap." \
  --design "Requires import_batch.raw_payload to never be deleted. That is a P0 invariant from M0, and this milestone is what proves it has teeth." \
  -e 300 --json >/dev/null && echo "created LifeOS-11"

bd create --id LifeOS-12 -t feature -p 2 -l m7,amex,pdf --deps LifeOS-11,LifeOS-2 --title "M7: Amex PDF importer (7-year history)" \
  -d "PDF is the BETTER Amex source, not the emergency fallback: 7 years of history vs ~6 months for
CSV, itemized per transaction, and it carries BOTH transaction date and process date plus FX detail.
CSV has one date and a short window. The original brief listed AmexPDFImporter last; it should be
treated as first-class.

  - pdfplumber-based extraction
  - Separate parsing step from field mapping (the Firefly III AbstractTransaction lesson)
  - Tier 3 confidence weighted DOWN for PDF (extraction is noisier: column alignment, OCR)
  - Critical property: importing a 7-year PDF and then a 6-month CSV must be NON-DESTRUCTIVE -
    the CSV rows already exist from the PDF and fingerprint-match

  CONTINGENT on LifeOS-2: if the PDF text layer is scanned images rather than selectable text, this
  becomes an OCR project and must be rescoped before starting." \
  -a "A 7-year PDF imports. Then a 6-month CSV of the same data creates ZERO new entries and every CSV row is accounted for as a fingerprint match. Original foreign amount + exchange rate are captured when present." \
  --design "The PDF/CSV overlap test is the single most important test in this milestone - it is the proof that archiving exports is a safe strategy rather than a source of duplicates." \
  -e 600 --json >/dev/null && echo "created LifeOS-12"

bd create --id LifeOS-13 -t feature -p 3 -l m8,revolut,conditional --deps LifeOS-1 --title "M8: Revolut (conditional on the /aspsps probe)" \
  -d "GATED ON LifeOS-1. Do not start before the probe result is known.

If Revolut IS in the Enable Banking production ASPSP list: add it as a second account under the
existing adapter. No new code path.

If Revolut is NOT supported: build RevolutCsvImporter instead. Revolut's CSV export has a stable 'id'
column, so Tier 1 dedup is available there - unlike Amex.

Either way the canonical schema is unchanged. The architecture is provider-agnostic by design; only
the IdentityResolver is provider-aware." \
  -a "Revolut transactions import and dedup correctly via whichever path the probe selected. No change to the ledger schema." \
  -e 360 --json >/dev/null && echo "created LifeOS-13"

bd create --id LifeOS-14 -t feature -p 3 -l m9,budgets --deps LifeOS-8 --title "M9: budgets + recurring detection" \
  -d "  - recurring_series: detection OUTPUT, produced by a nightly job and user-confirmed
    (not user input). Deliberately not a 'recurring transaction template' like Firefly's 4-model
    Recurrence family.
  - Simple monthly budgets
  - Budget vs actual

  Explicitly deferred: envelope budgeting, rollover. Category is already hierarchical with a 'kind'
  column (expense/income/transfer/investment), so budget queries need no hardcoded category names." \
  -a "Nightly detection proposes recurring series with a confidence score; the user confirms or dismisses. Monthly budget vs actual is correct and fast." \
  -e 480 --json >/dev/null && echo "created LifeOS-14"

bd create --id LifeOS-15 -t feature -p 3 -l m10,networth --deps LifeOS-9 --title "M10: net worth over time" \
  -d "assets - liabilities, snapshotted over time. Already correct from M1: account.account_nature
carries 'asset' | 'liability' | 'equity' as a first-class, indexed column, and account_nature is what
makes the Amex liability work. This milestone is aggregation only, not a modelling change.

  - daily or on-demand net worth snapshot
  - exclude transfers and non-spending lines
  - show the path Income -> Spending -> Savings -> Investments -> Net Worth" \
  -a "Net worth is correct across the Amex liability, correct after a card payment, and never double-counts a transfer. A transaction-time reconstruction of net worth on any past date matches the snapshot." \
  -e 240 --json >/dev/null && echo "created LifeOS-15"

echo "--- done ---"
bd list --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f\"{i['id']:6} P{i['priority']} {i['issue_type']:8} {i['title']}\") for i in (d if isinstance(d,list) else d.get('issues',[]))]"
