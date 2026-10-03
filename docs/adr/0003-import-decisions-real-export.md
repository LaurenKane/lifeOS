# ADR 0003: Import decisions forced by the real exports

**Date:** 2026-10-02
**Status:** Accepted
**Decision Drivers:** the parsers were written against the real statements and reconcile to the
printed totals with delta 0 (doc 11 §1) — every behaviour they rely on is now measured, and each
one below was a place where the design docs and the files disagree.

## Context

Nine real statement PDFs — 4 Amex, 4 Rabobank, 1 Revolut — were parsed and reconciled against the
totals each document prints on its own front page. 578 rows, all three parsers PASS with delta 0
(`docs/research/11-real-export-verification.md` §1). The raw data is gitignored
(`data/extracted/`); the findings document is the record.

Five things the design docs had no answer for. Four of them are decisions, not notes, because the
obvious implementation of each is wrong in a way that silently corrupts the ledger.

---

## Decision 1 — the Amex source→ledger sign flip is explicit

**Two conventions, and they are inverses.**

| Where | Convention |
|---|---|
| Amex source, CSV (`docs/research/02-amex-nl.md:59`) | charges positive, payments/credits negative |
| Amex source, PDF (doc 11 §3.2) | **no sign at all** — see below |
| The ledger (`docs/ARCHITECTURE-PROPOSAL.md §E, 'Sign carries direction'`) | **Debits negative, credits positive** (signed/ISO, no `direction` column) |

The inversion was written down nowhere. `exp-2` flagged it under `F-SCHEMA-NO-DIRECTION`; it is
real (doc 11 §3.3).

**The PDF encodes direction in a separate marker line, not in a signed amount.** The amount column
carries no sign for charges *or* credits; a credit is flagged by a line containing only `CR`
printed *beneath* the amount. So "is this a credit?" is not a property of the number at all — it
is a property of the following line.

**Therefore an adapter must flip explicitly:**

```
CR marker present  →  raw_amount is POSITIVE  (credit)
CR marker absent   →  raw_amount is NEGATIVE  (debit)
```

Both are emitted as already-signed minor units with `AmountSignConvention.SIGNED`, so the
normalizer does not flip them a second time. That is what the parser does today:
`signed_minor = amount_minor if is_credit else -amount_minor`, then
`normalize_record(..., sign_convention=AmountSignConvention.SIGNED)`. The docs match that code.

**A second inversion hides behind the first: `CR` separates credits from charges, not card
payments from refunds.** Each statement's credit section mixes the monthly card payment with real
refunds:

| Statement | Card payment | Refunds | `Crediteringen` |
|---|---|---|---|
| 2026-06-23 | 721,35 | 1 (70,00 PayPal) | 791,35 |
| 2026-07-23 | 765,67 | 2 (10,00 Amazon, 144,30 asos) | 919,97 |
| 2026-08-23 | 332,43 | 3 (2,99 / 6,99 Prime, 39,99 asos) | 382,40 |
| 2026-09-23 | 272,48 | 0 | 272,48 |

(doc 11 §3.2.) So payment-vs-refund is separated by **description only**. The research parser keys
on `HARTELIJK BEDANKT VOOR UW BETALING`, which held for all four statements — but that is one
literal Dutch string, and `ARCHITECTURE-PROPOSAL.md §H` already concedes the equivalent Rabobank
rule is "brittle — so it learns". This is a **second string to learn**, via the same
`CategoryRule` / merchant-alias promotion mechanism.

### Rejected

- **Treat the unsigned amount as if it were already ledger-signed.** Rejected: a €50 purchase
  lands `+50` on the liability and reads as a payment. The balance identity in Decision 2 catches
  it immediately, which is the only reason this is a documented inversion and not a silent bug.
- **Add a `direction` column** and leave the amount unsigned. Rejected by `docs/research/09-canonical-data-model.md`
  correction 2: `direction` is derivable from the sign, and two encodings of one fact that can
  disagree are a permanent bug source. The PDF needs an adapter-side flip, not a schema change.
- **Infer direction from the description.** Rejected: descriptions are free text and the section
  heading is the only reliable signal. `CR` is printed; use it.

---

## Decision 2 — the balance identity is per-account-type, and is a required adapter test

Two opposite identities, and no column stores either.

| Account type | Identity | Meaning |
|---|---|---|
| Current / asset (`checking`, `savings`) | `prev + credits − debits = closing` | money in raises the balance |
| Liability (`credit_card`) | `Vorig saldo + Debiteringen − Crediteringen = Nieuw saldo` | **a charge *increases* the amount owed** |

Both hold on all four Amex statements, and the Amex one runs the *opposite* way to a current
account (doc 11 §3.3). **A single generic "balance identity" self-check cannot be written for both;
the test has to be per-account-type.**

**No `source_record` or `journal_line` column stores a statement balance** (`P-NO-HISTORIC-BALANCE`),
so this identity is asserted nowhere today — yet it is what caught every parsing bug during
verification (doc 11 §4). It is therefore **a required acceptance test for every new adapter**:
an adapter is not done until its output reconciles, at delta 0, against the totals the source
document prints on its own front page (`Debiteringen`/`Crediteringen` for Amex, `Total amount
debited`/`credited` for Rabobank, the summary table for Revolut). That is the whole of the doc 11
§1 result.

### Why this lives in a test and not in `invariants.yaml`

The checker in `scripts/check_invariants.py` supports exactly three kinds — `forbid_regex`, `hash`,
`manifest`. None can express a runtime arithmetic identity over parsed rows: they scan source text,
hash a file, or diff a revision manifest. They never execute a parse and never see a number.

So the options were: invent a fourth kind (new checker machinery, in the file whose whole purpose
is to be checkable, for one invariant); add an entry that no checker evaluates (an entry that
always passes is worse than no entry — it reads as coverage and is not); or put the identity where
it can actually run. **It goes in a test.**

There is a second reason, and it is the decisive one: the right-hand side of the identity lives in
the *uploaded file*, not in the repo. The assertion needs the statement's own printed totals and
the parsed rows together. `invariants.yaml` entries are declared against the repository; this one
is declared against an artefact the user supplies. Even a full arithmetic checker in the invariant
file would be the wrong tool for a per-artefact reconciliation.

`invariants.yaml` carries a comment recording the two identities and pointing at the test. That is
the honest maximum: a comment says the rule exists; a fake entry would say it is enforced.

---

## Decision 3 — the Rabobank type-code enum, sourced from the printed legend

No enum exists today. **The source of truth is the legend printed on the last page of every
statement** — self-documenting, and doc 11 §4 records that every code appearing in these statements
is defined by it, with no undefined codes.

**Observed in the four statements — 11 codes:** `ba bg bv cb db ei ga id sb tb we`.

**Transcribed from the legend** — all 24 entries, matching the count doc 11 §4 records. Source:
`data/extracted/rabo_selftest.json::type_code_legend`, produced by `tools/bankparse/rabo.py` and
reproducible from the PDFs.

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| `ba` | POS terminal | `ei` | Euro direct debit |
| `bc` | POS terminal contactless | `ga` | ATM Euro |
| `bg` | payment order | `gb` | ATM other currency |
| `bv` | payment request | `id` | iDEAL |
| `cb` | creditor payment | `kh` | cashier transaction |
| `cc` | creditcard | `ok` | OmniKassa |
| `cp` | Cash Pooling | `sb` | salary payment |
| `db` | various remittances | `sp` | urgent payment |
| `eb` | business Euro direct debit | `st` | refund Direct Debit |
| `ec` | E-commerce | `tb` | internal account |
| | | `te` | returned Europayment |
| | | `wb` | world payment |
| | | `we` | `Wero (co branded` ← **truncated in the source** |
| | | `wr` | Wero |

**`we` cannot be resolved further.** The printed entry is itself cut off mid-phrase. Keep the
printed text verbatim; do not complete it from the `wr` entry or from any external list.

**One gap, recorded rather than assumed away:** `parse_legend` runs per file and `legend.setdefault`
keeps the first meaning seen for a code, so whether any code's legend text **differs between
statements** is untested on these four. The enum is defined from the printed legend; it is not
hardcoded from a bank product page, so a changed meaning surfaces as a parse-time difference rather
than as a silent semantic drift.

### The rule: an unknown code must warn, never guess

A code not in the legend is stored **raw and unclassified**, surfaced in the parse report and the
import review queue, and the import **succeeds**. It is never mapped to the nearest known code,
never defaulted, never dropped. A wrong guess is silent and unrecoverable once the row reaches the
ledger; a warning costs the user one click.

The reason to be strict here: doc 11 §3.5 records that the *first* pass over Rabobank suggested the
debit/credit split was indeterminate. It is not — classifying each amount against the header of
the page it appears on reconciles to delta 0 on all four statements. The source is more
determinate than it looks. "Ambiguous in the source" is usually "the parser guessed".

### Rejected

- **Free text, no enum.** Rejected: there is no surface to review, no way to report unknowns, and
  the codes are printed and stable.
- **Hardcoding an assumed complete table.** Rejected: the table above is *transcribed from the
  printed legend*, and an invented entry would be indistinguishable from a real one. The legend is
  read from the file, not guessed.
- **Rejecting the import on an unknown code.** Rejected: an unfamiliar code would then block a
  statement that is otherwise complete and balanced. Warn, import, review.

### Status

**This is a recorded definition, not live code.** No backend enum module exists because M1's
schema has not been written (bead `LifeOS-6`). The table above is carried into M1 as the enum
definition and its fixture set. **No Python was written for this decision.**

---

## Decision 4 — Revolut Current and Deposit are separate accounts; ambiguous ownership is HELD

One PDF, two products, and no statement of which IBAN belongs to which (doc 11 §3.6):

- `Account (Current Account)` — 340 rows; `Deposit` — 11 rows. Each has its own
  opening/money-out/money-in/closing row in the summary.
- Two own IBANs are listed: `NL69REVO…1997` and `LT5032500…9880` (Lithuanian branch) — plus 12
  further IBANs belonging to counterparties. **The file never says which of the 14 is which
  product's.**

**Decision (user-confirmed):** Revolut Current and Deposit are **separate accounts**. When the
statement does not state which IBAN the Deposit belongs to, the Deposit section is **HELD for
explicit user confirmation. It is never inferred.**

### Consequences for `import_batch.account_id`

`import_batch.account_id` is a **single nullable** column (`ARCHITECTURE-PROPOSAL.md §E `import_batch`), and
this file needs two accounts. Therefore:

1. **`import_batch.account_id` is not the row's account.** It stays nullable and is a *default or
   hint* for the file. **`source_record.account_id` is authoritative, per row** — it is already
   `NOT NULL` (`ARCHITECTURE-PROPOSAL.md §E `source_record`).
2. **One file may produce several batches.** Either one `import_batch` per resolved account with
   `account_id` set (same `source_checksum`, so the file is not re-uploaded and not duplicated), or
   one batch with `account_id IS NULL` and every row carrying its own. The first is preferred: it
   keeps `import_batch` a unit of work and matches how the API path already batches per account.
3. **Held rows are not written.** No `source_record` is created under a guessed `account_id`, even
   provisionally. The batch reports them as unattributed and asks the user which account the
   Deposit belongs to. A guess that is later corrected requires rewriting rows under
   `raw_data_immutable`, the highest-priority invariant.

### Rejected

- **Infer it.** `tools/bankparse/revolut.py` attributes Deposit rows to the secondary (LT) IBAN
  and flags `account_ref_inferred: true` in the manifest. That is **correct for a throwaway
  research parser and wrong for the ledger**: the flag is honest, but nothing downstream is
  obliged to read it, and a silently-baked inference is exactly what this repo does not do.
- **Create a synthetic "unattributed" account.** Rejected: `A-PROV-LINK-ID`
  (`ARCHITECTURE-PROPOSAL.md §E `provider_account_link`) says store the account identifier "exactly as the provider
  sends it", and a synthetic account would carry a fabricated IBAN through that rule.
- **Attribute both sections to the first IBAN found.** Rejected: 11 rows of a savings product
  would land in the current account, and the balance identity in Decision 2 would fail loudly —
  which is the correct outcome, but only after the damage.

### Also recorded: internal transfers do not pair within one account

`T-OWN-ACCOUNT-TRANSFERS` (`ARCHITECTURE-PROPOSAL.md §H`) assumes transfer legs can be
matched. Revolut does not cooperate: **34 `To Savings` against 9 `From Savings`** — the
counter-legs live in the Deposit section (doc 11 §3.6). No single-account pass can match them.
Across both sections the parser counts **53 internal-transfer legs**. The matching algorithm is
unchanged; the claim that one account holds both legs is not.

---

## Decision 5 — the printed `Periode` is advisory, not a filter

Real statements carry transactions dated **the day before the period opens**: `2026-06-23` inside
the statement whose period is 24.06–23.07, and `2026-07-23` inside the 24.07–23.08 statement
(doc 11 §5.3).

**Filtering rows on the printed period bounds would drop genuine spend.** The period is stored as
metadata and used for labelling, continuity checks and progress reporting. It is **never** a
predicate on which rows are imported.

Worth writing down precisely because the natural implementation is to trust the period — it is
printed, it is labelled, and it looks authoritative. It is a description of the statement, not a
constraint on its contents. The parser already retains out-of-period rows; the docs now say so.

---

## Recorded constraints (measured, no decision needed)

- **Decimal separators differ by provider.** Amex and Rabobank print `1.260,00` (dot thousands,
  comma decimal). Revolut prints `€5,378.27` (comma thousands, **period** decimal). A single
  shared amount parser misreads one of them by three orders of magnitude while still appearing to
  work — the adapters cannot share a converter (doc 11 §3.7).
- **Identical same-day rows are real, so `occurrence_index` is load-bearing.** The June statement
  contains `SNCB-NMBS SNCB-NMBS BRUXELLES` for `-13,40` on `2026-06-12` **twice** (doc 11 §3.8).
  It is the only such pair in 121 Amex rows, but the same statement holds
  `NMBS NMBS BRUXELLES 12,80` and `SNCB-NMBS SNCB-NMBS BRUXELLES 13,40`, which a normalisation
  rule aggressive enough to merge the first pair would wrongly collapse.
  `D-OCCURRENCE-PDF-TIEBREAK` (`hash(raw_line) % 10000`, `docs/research/07-dedup-contingencies.md:69-70`)
  is the only thing between that collision and a silently dropped transaction.
- **PII in free text is load-bearing.** Full counterparty IBANs appear inside `To:` / `From:` /
  `Reference:` fields — at least 32 distinct across the four Rabobank statements, 11 in the
  Revolut statement — and not all in Dutch layout: a German one (`DE59 … 3984`, 2 extra numeric
  groups) and an Irish one (`IE30 CITI … 5648`, 4 groups) both occur, both third-party (doc 11
  §5.5). The last-4-only logging rule must be applied to *parsed description fields*, and a
  redaction pattern pinned to the Dutch IBAN layout silently misses foreign ones.

---

## Consequences

**Positive:**
- Every behaviour the adapters rely on is now backed by a measurement, and each inversion is named
  rather than assumed.
- The balance identity turns "the parser looks fine" into a delta-0 assertion the user can read.
- Held-until-confirmed is a real state, not a comment: no ledger row is ever written under a
  guessed account.

**Negative:**
- The balance identity must be implemented twice, once per direction, with the account type as
  the discriminator — there is no single generic form.
- The Revolut path needs a two-account batch strategy and a user confirmation prompt. That is more
  work than inferring, and it is the correct amount of work.
- The Rabobank type-code enum carries one open gap: whether a code's printed meaning is identical
  across statements is untested on these four.
- The `HARTELIJK BEDANKT VOOR UW BETALING` string is brittle by construction until it is promoted
  into a learned rule the way `ARCHITECTURE-PROPOSAL.md §H` describes for Rabobank.

## Acceptance Criteria

- [x] Source→ledger sign flip written down, with the `CR` marker line named as the carrier of
      direction, matching `AmountSignConvention.SIGNED` in the parser
- [x] Card-payment-vs-refund separation stated as description-based, with the measured per-statement
      credit split
- [x] Both balance identities recorded per account type, and the acceptance test made mandatory
- [x] `invariants.yaml` carries the identities as a **comment only**; no new invariant kind, no
      fake entry, no change to `scripts/check_invariants.py`
- [x] Rabobank type-code enum defined from the printed legend, observed codes recorded, `we`
      truncation and the 22-of-24 gap recorded, unknown-code rule stated
- [x] Revolut Current/Deposit separated; ambiguous Deposit ownership HELD, never inferred;
      consequences for `import_batch.account_id` stated
- [x] `Periode` recorded as advisory metadata, not an import predicate

## Related

- `docs/research/11-real-export-verification.md` — the measurements. §1 method and deltas,
  §3.2 `CR` marker, §3.3 both identities, §3.6 two products/two IBANs, §4 legend and continuity,
  §5.3 `Periode`, §5.5 free-text IBANs
- `docs/adr/0002-import-provider-enum.md` — which import paths exist at all
- `docs/adr/0007-imported-card-payment-is-a-transfer.md` — the sixth import decision, taken after
  this one: a flagged card payment is a transfer with an asset contra-leg, so it must be dispatched
  before the equity-only manual resolver rather than routed through it
- `docs/ARCHITECTURE-PROPOSAL.md` §E (DDL), §G (dedup), §L (test strategy)
- Beads `LifeOS-3pe` (this decision), `LifeOS-2` (the verification exercise),
  `LifeOS-hwv` / `LifeOS-nie` / `LifeOS-07m` / `LifeOS-hwv` (superseded into it)