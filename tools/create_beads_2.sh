#!/usr/bin/env bash
# Creates the remaining Life OS roadmap beads.
# NOTE: -a is --assignee. Acceptance criteria MUST use --acceptance.
set -uo pipefail
cd /home/lauren/Documents/NewCode/LifeOS

# Fix LifeOS-7: -a was wrongly used for acceptance, so it landed in assignee.
bd update LifeOS-7 --assignee "" --acceptance "Import September.csv twice -> second import creates 0 new journal_entry rows; all source_record rows marked status='duplicate'. Two identical EUR 3.20 coffees on the same day -> 2 entries with occurrence_index 1 and 2. Re-import after row reordering -> no duplicates. raw_description is byte-identical after any user edit." >/dev/null 2>&1 && echo "fixed LifeOS-7"

mk() { # mk <id> <type> <prio> <labels> <title> <description> <acceptance> <design> <est> <deps>
  bd create --id "$1" -t "$2" -p "$3" -l "$4" --title "$5" \
    --description "$6" --acceptance "$7" --design "$8" -e "$9" --deps "$10" --json >/dev/null 2>&1 \
    && echo "created $1 (deps: $10)" || echo "FAILED $1"
}

mk LifeOS-8 feature 2 m3,categorization \
  "M3: merchant normalization + 7-layer categorization" \
  "Layer 1 exact user rules / 2 merchant_alias normalization / 3 known NL merchant map /
4 trigram fuzzy / 5 LEARNED FROM MANUAL CORRECTIONS / 6 LLM fallback (opt-in, deferred) /
7 uncategorized review queue.

Layer 5 is the differentiator: NEITHER reference project implements it. Firefly has no evidence of
learnability; Actual has no auto-suggest. Every manual correction raises merchant_alias.confidence to
1.0 or creates an is_learned category_rule, so the queue drains itself.

Rules must stay hand-editable: description_pattern + priority + is_learned. No generic rules DSL,
no YAML logic engine, no expression evaluator. The whole rule set should fit on one screen." \
  "Correcting 'PayPal XYZ' to Entertainment > Music means the NEXT identical transaction categorizes automatically with no hand-authored rule. The uncategorized queue is served by the idx_jl_uncat partial index. Rules are editable in the UI and readable as plain text." \
  "Reuse the merchant_alias learning mechanism for card-payment description patterns later rather than inventing a second learning store." \
  480 --deps LifeOS-7

mk LifeOS-9 feature 1 m4,transfer,correctness \
  "M4: transfer matching + the Amex card payment" \
  "The correctness requirement. Once this works, spending totals are trustworthy.

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
  "THE HEADLINE TEST: Amex 50 purchase then Rabobank -50 -> total spending is 50, not 100. Both journal entries balance. Two EUR 50 transfers the same day produce 2 distinct transfer_match rows, not 1 merged pair. A SEPA DD to a third party is an expense with no transfer_match. An ambiguous DD-vs-transfer lands in the review queue rather than being guessed." \
  "Card-payment detection is ILIKE '%american express%' at confidence 0.95 with user rejection allowed, and it LEARNS: after confirmation the observed description is promoted to a rule. Brittle on day one, self-correcting by month three." \
  600 --deps LifeOS-6

mk LifeOS-10 feature 2 m5,banking,enable-banking \
  "M5: Enable Banking ingestion for Rabobank" \
  "  - RS256 JWT client (NOT HS256 - correction to the original assumption), hand-rolled, no SDK
  - Consent flow: POST /auth -> bank redirect -> POST /sessions
  - Account linking on identification_hash, NEVER on account.uid (uid rotates at every re-auth)
  - Aggressive backfill at consent time with strategy=longest (history clamps to ~90 days shortly
    after consent, so waiting loses data permanently)
  - Polled sync - Enable Banking has NO AIS webhooks. Respect ~4 background fetches/day/ASPSP
    unless PSU headers are supplied.
  - Tier 1 dedup (entry_reference, scoped by account) + Tier 2 pending->booked
  - EXPIRED_SESSION (401) handled as a NORMAL expected re-auth flow, not an error state
  - session_ended_reason recorded as an explicit column

  entry_reference usually only materialises on BOOK, so Tier 2 cannot lean on provider IDs and must
  be confidence-scored with a review queue. Tier 3 fingerprint is computed for API rows too as a
  backstop if entry_reference ever changes on a historical re-fetch." \
  "Rabobank consent completes end to end. A re-sync of the same window creates 0 duplicates. A PDNG->BOOK transition reconciles into ONE journal_entry, updated in place, preserving any user-set category. EXPIRED_SESSION produces a clear re-auth prompt, not a stack trace. A 90-day backfill lands in full on first consent." \
  "The RSA private key lives in the OS keyring, never the database. Enable Banking issues us no access or refresh tokens - the credential surface is one long-lived key." \
  600 --deps LifeOS-8,LifeOS-9,LifeOS-1

mk LifeOS-11 feature 1 m6,integrity \
  "M6: replay-from-raw" \
  "Turn raw retention into an actual safety net, not a promise.

  - CLI: replay --batch-id=N re-runs the entire pipeline from import_batch.raw_payload
  - import_batch records raw bytes + sha256 + parser version
  - Enables: schema changes that affect normalization, categorization rule changes, and full rebuild
    after any bug in the pipeline

  This is the mitigation for risk #9 (schema churn during vibe-coding destroying real financial
  history) and the answer to the Amex 6-month CSV window, where archived exports are the only
  durable copy of anything." \
  "Deleting every journal_entry and journal_line, then running replay, reproduces the ledger exactly. Changing a category rule and replaying re-categorizes without re-importing the file. Done BEFORE any schema churn, as promised in the roadmap." \
  "Requires import_batch.raw_payload to never be deleted. That is a P0 invariant from M0, and this milestone is what proves it has teeth." \
  300 --deps LifeOS-7

mk LifeOS-12 feature 2 m7,amex,pdf \
  "M7: Amex PDF importer (7-year history)" \
  "PDF is the BETTER Amex source, not the emergency fallback: 7 years of history vs ~6 months for
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
  "A 7-year PDF imports. Then a 6-month CSV of the same data creates ZERO new entries and every CSV row is accounted for as a fingerprint match. Original foreign amount + exchange rate are captured when present." \
  "The PDF/CSV overlap test is the single most important test in this milestone - it is the proof that archiving exports is a safe strategy rather than a source of duplicates." \
  600 --deps LifeOS-11,LifeOS-2

mk LifeOS-14 feature 3 m9,budgets \
  "M9: budgets + recurring detection" \
  "  - recurring_series: detection OUTPUT, produced by a nightly job and user-confirmed
    (not user input). Deliberately not a 'recurring transaction template' like Firefly's 4-model
    Recurrence family.
  - Simple monthly budgets
  - Budget vs actual

  Explicitly deferred: envelope budgeting, rollover. Category is already hierarchical with a 'kind'
  column (expense/income/transfer/investment), so budget queries need no hardcoded category names." \
  "Nightly detection proposes recurring series with a confidence score; the user confirms or dismisses. Monthly budget vs actual is correct and fast." \
  "Reuse the review-queue pattern from the dedup and transfer work rather than inventing a third confirmation UX." \
  480 --deps LifeOS-8

mk LifeOS-15 feature 3 m10,networth \
  "M10: net worth over time" \
  "assets - liabilities, snapshotted over time. Already correct from M1: account.account_nature
carries 'asset' | 'liability' | 'equity' as a first-class, indexed column, and account_nature is what
makes the Amex liability work. This milestone is aggregation only, not a modelling change.

  - daily or on-demand net worth snapshot
  - exclude transfers and non-spending lines
  - show the path Income -> Spending -> Savings -> Investments -> Net Worth" \
  "Net worth is correct across the Amex liability, correct after a card payment, and never double-counts a transfer. A transaction-time reconstruction of net worth on any past date matches the snapshot." \
  "This milestone is the first honest payoff of the account_nature column decided back in M1." \
  240 --deps LifeOS-9

echo "--- verifying ---"
bd list --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin); issues = d if isinstance(d,list) else d.get('issues',[])
issues.sort(key=lambda i:int(i['id'].split('-')[1]))
for i in issues:
    if i['status']=='closed': continue
    print(f\"{i['id']:12} P{i['priority']} {i['issue_type']:8} {i['title']}\")
print()
print('total open:', sum(1 for i in issues if i['status']!='closed'))
"
