# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: Lauren, EU resident, banking in the Netherlands. EUR is the base currency and the only
currency the ledger currently supports without special handling.

Not a household tool. There is no second user, no shared access, no permissions. This is a
deliberate refusal, not an unfinished feature — see Product Principles.

The user is technically competent and does not want to be walked through the product. He reads
documentation and code rather than being told what a button does.

## Product Purpose

**Answer any question about my own money.**

LifeOS exists so that a question like "where did the money go last month" has one true answer, and
so that the answer spans every account — including the ones a bank's own app treats as unrelated
worlds. A checking account at Rabobank, a credit card at Amex, and a Revolut account are one
position, not three.

Success looks like: a number you can state without hedging, produced from statements you can point
at. It does not look like a beautiful dashboard, and it is not reached by adding more screens.

V1 scope is spending. Savings, investments and net-worth-over-time are explicitly wanted later, and
the M9/M10 milestones point at them — but they are not what V1 is for.

## Positioning

Most personal-finance tools are a bank or an aggregator's own view: they will show you what their
ingestion pipeline captured, they cannot reconcile their own accounts against each other, and they
hold your financial data on someone else's servers.

LifeOS's differentiator is **the double-entry ledger as the single source of truth**, with the bank
as a source of *raw records* rather than as an authority. Every provider is parsed into raw rows,
fingerprinted, and only then posted as balanced journal entries. That is what lets the product say
things a bank app cannot:

- a card payment is recognised as a transfer, so paying the Amex bill is not counted as spending
  twice — once at the shop, once at the payment
- moving money between your own accounts is not income
- totals are computed from a rule the user can inspect and disagree with

The second differentiator is **ownership of the raw record**. Once a statement line is saved, the
database refuses to let anything change it (a Postgres trigger, `raw_data_immutable`). The history
is append-only and reconstructible; a bug in today's parser can be fixed by replaying tomorrow.

A neighbouring product could copy the UI. It could not truthfully copy the claim, because the claim
is an accounting property, not a feature.

## Operating Context

The rhythm is **monthly, with month-in-month review**:

- Statements are downloaded from each provider — Rabobank and Revolut as PDF, Amex as PDF — and
  imported as files.
- After an import there is a **review** step: the categoriser has made its best guess for each
  transaction and the user corrects what it got wrong. Corrections teach future runs.
- *During* the month, the product is read-only in practice: "check my spending from the previous
  month" is the query that gets asked.
- Long term, Enable Banking would remove the download step entirely and ingestion becomes automatic.

The user works on an old MacBook with Linux for development, against a Netcup VPS in Amsterdam
(ADR 0001), reaching both over Tailscale. Statement files are real, sensitive documents and stay on
the user's own hardware.

## Capabilities and Constraints

### What it does

- Imports statements from Rabobank (PDF), Amex (PDF), Revolut (PDF), and Enable Banking (API),
  plus manual entry.
- Deduplicates: the same statement imported twice creates zero new transactions.
- Categorises transactions, and lets the user correct any categorisation and create their own
  categories.
- Recognises transfers and card payments so they are not double-counted as spending.
- Answers net-worth, spend-by-category and cashflow questions over the ledger.

### Durable constraints

- **Money is integer minor units.** Signed BIGINT, never a float, never `NUMERIC(18,2)`. The
  frontend `Amount` takes `minor: number`. This is not negotiable and is enforced on both sides.
- **No telemetry.** The local file-import path must run with `--network=none` and make zero
  `connect()` calls. This is proven by a test, not asserted by convention. Live HTTPS to a provider
  API is expected on the Enable Banking path only.
- **Offline-capable.** The file import and dedup path does not need the network.
- **Raw records are immutable.** Saved statement data is permanent.
- **EUR-base only.** The ledger sums to zero in one currency. A foreign-currency transaction
  carries an FX residual rather than being split into two currencies.

### Terminology

- *Ledger* — the double-entry record. Balanced entries, every entry sums to zero.
- *Raw record* — the unparsed line as it appeared in the statement, frozen.
- *Entry* — one balanced transaction: a set of legs that sum to zero.
- *Account nature* — asset, liability, equity or income/expense. Determines sign.
- *Fingerprint* — a digest of a normalised record, used for deduplication. Frozen once shipped.

### Explicitly undecided

- **Cloud sync.** The user wants it eventually and does not consider its absence a virtue. Not
  planned, not refused. ADR 0001 (self-hosted VPS, Tailscale) is the V1 answer and a sync product
  would change it.
- **Investments and savings tracking.** Wanted after V1. `LifeOS-l3j` (populating `exchange_rate`)
  and M9/M10 are the groundwork.
- **Automatic transaction creation.** Wanted, and conditional on Rabobank and Revolut both being
  linkable through Enable Banking. M5/M8. The V1 answer is that nothing enters the ledger without a
  statement the user can point at — but that is a consequence of not having live ingestion, not a
  principled refusal.
- **Foreign currency beyond EUR** — needed before investments are meaningful.
- **M9/M10 versus the on-demand analytics endpoints.** `LifeOS-14` (budgets + recurring detection)
  and `LifeOS-15` (net worth snapshots) describe nightly snapshot jobs. The read-side analytics
  endpoints compute the same figures on demand. These are not yet reconciled, and one of the two is
  redundant.

## Brand Commitments

None. No name styling beyond the repository name, no logo, no tagline, no external brand.

The product's voice in its own UI is plain and specific: it names what is missing rather than
filling an empty view with invented numbers. This is a correctness commitment, not a tone decision —
a finance tool that shows a plausible wrong number is worse than one that shows nothing.

## Evidence on Hand

- **Real statements.** Three genuine Amex PDF extractions under `data/extracted/`
  (`amex-2026-06-23.jsonl`, `amex-2026-07-23.jsonl`, plus manifests and parse reports). Card-only,
  two months. These are the only real data in the repo.
- **A real Revolut and Rabobank statement** exist outside the repo, used to verify the Revolut
  description-furniture defect (`LifeOS-qvv`).
- **Seven ADRs** in `docs/adr/`, each recording a consequential decision and what it cost.
- **No seed script and no demo dataset.**

### Absences that future work must not fabricate

- No testimonials, users, reviews or benchmarks. There is exactly one user.
- No performance claims. None have been measured.
- No uptime, deployment or cost claims beyond what ADR 0001 records as a decision.
- **The database is empty by default.** Chart work has three card statements and nothing else —
  no checking, no savings, no multi-month history. Do not design against an assumed populated
  ledger.

## Product Principles

1. **The ledger is the truth; the bank is a source.** A provider is parsed into raw records and
   posted into a balanced ledger. Where the two disagree, the ledger is right.
2. **No number without a statement behind it.** Every figure traces to raw data the user can open.
   This is what separates the product from an aggregator's summary.
3. **Errors are loud; empty views are honest.** A failed import says so. An empty category says
   there are no transactions. Neither invents a plausible value.
4. **Correctness beats coverage.** One provider parsed exactly right is worth more than four parsed
   approximately. This is why the roadmap is sequential.
5. **The user owns the data and can override the machine.** Corrections are expected, not an
   admission of failure. Anything the product decides about a transaction must be changeable.

## Accessibility & Inclusion

No product-specific accessibility requirement has been established.

Standing constraints that follow from the constraints above: colour is never the sole carrier of
meaning anywhere in the interface, and the UI keeps the system font stack rather than downloading a
webfont, because a downloaded font would break the offline guarantee.