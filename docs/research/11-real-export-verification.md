# 11 — Real export verification: what the PDFs actually say

**Date:** 2026-10-01
**Status:** findings only. No production code was written; the parsers described below are
throwaway research tooling under `tools/bankparse/` and their output lives in the
gitignored `data/extracted/`.
**Answers:** the GATING question behind LifeOS-2, and the PDF/CSV assumptions behind
`docs/ARCHITECTURE-PROPOSAL.md` §C/§D/§E.

Citation shorthand: `ARCH` = `docs/ARCHITECTURE-PROPOSAL.md`,
`R02`/`R06`/`R07`/`R09` = `docs/research/0{2,6,7,9}-*.md`, `RECO` = `docs/RECOMMENDATION.md`.

---

## 1. Scope and method

Nine PDF exports were supplied: 4 Amex monthly card statements, 4 Rabobank monthly account
statements, 1 Revolut annual statement. All are text-layer PDFs; no OCR is required.

To decide whether a parser is *complete* rather than merely plausible, each parser
reconciles its own output against the totals the source document prints on its own front
page. A parser that silently drops or mis-signs a row cannot balance.

| Provider | Files | Rows | Self-test |
|---|---|---|---|
| Amex | 4 | 121 | **PASS** — charges/credits match `Debiteringen`/`Crediteringen` with delta 0 on all 4; balance identity holds on all 4 |
| Rabobank | 4 | 106 | **PASS** — matches stated `Total amount debited`/`credited` with delta 0 on all 4; all 3 cross-month balance joins delta 0 |
| Revolut | 1 | 351 | **PASS** — matches the summary table with delta 0; running-balance continuity holds on all 349 rows that have a predecessor (the first row of each product has none) |
| **Total** | **9** | **578** | |

Reproduce with:

```
python3 tools/bankparse/amex.py    --src ~/Documents/Banking/Amex --out data/extracted
python3 tools/bankparse/rabo.py    --src ~/Documents/Banking/Rabo --out data/extracted
python3 tools/bankparse/revolut.py --src ~/Documents/Banking/Rev  --out data/extracted
```

`data/extracted/*_selftest.json` holds every check with its exact delta;
`data/extracted/*_parse_report.json` holds every line the parser could not classify.
All IBANs and card numbers are masked to the last 4 before being written.

---

## 2. LifeOS-2 answered: Amex PDF is go

`ARCH:861-862` asks whether the Amex PDF text layer is selectable or scanned, noting that
if it is scanned, M7 becomes an OCR project. `RECO:148` makes M7 contingent on it.

**The text layer is selectable.** All four statements extract as clean, correctly ordered
text with no OCR. **M7 is a go**, and the `Bedrag in vreemde valuta` column and
`Nieuwe transacties voor:` sub-headers are real, structurally parseable fields.

Answering the other LifeOS-2 items that these files can settle:

- Itemized per transaction: yes, 28–33 rows per statement, not a payments ledger.
- Both transaction date and process date: yes, and see §3.1 — they differ.
- Foreign-currency charges: **unverifiable from this data.** The FX column is empty in all
  four statements and no non-EUR currency code appears anywhere. The `foreign_amount` /
  `foreign_currency` columns at `ARCH:353-354` cannot be populated from these exports.
  `F-AMX-PDF-FX` stays open pending an actual foreign purchase.
- CSV columns, date format, sign convention, `Reference` stability, XLSX availability,
  multi-card `Card Member`, and the 6-month CSV window: **all still untested.** No CSV was
  supplied, so `ARCH:858-859` and the whole M2 column mapping remain open.

---

## 3. Assumptions the exports refute

### 3.1 `R07:41` is wrong — the two Amex dates are not interchangeable

`R07:41` states, for Amex, "`raw_posting_date` vs `raw_date` | **same (posted only)** |
Nullable, no issue." In these statements the two columns differ on **42 of 121 rows
(35%)**:

| Statement | Rows | Dates differ |
|---|---|---|
| 2026-06-23 | 32 | 10 |
| 2026-07-23 | 33 | 10 |
| 2026-08-23 | 28 | 11 |
| 2026-09-23 | 28 | 11 |

Example: transaction date `16.06.26` / processed `17.06.26`; `10.06.26` / `12.06.26`.

This is load-bearing, not cosmetic, because of the cross-format dedup test. `ARCH:559`
keys the Tier-3 fingerprint on `raw_date` only, and the headline property at `ARCH:779` is
"import a 7-year Amex PDF, then a 6-month CSV → 0 new entries". That test only passes if
the PDF's `raw_date` and the CSV's single date are **the same date**. `R02:78-79` says the
CSV has one date and the PDF has two — but never says *which* of the two the CSV's single
date corresponds to. If the CSV carries the processing date, PDF-then-CSV will not
fingerprint-match and will silently double-count. **This must be settled with a real Amex
NL CSV before M2/M7 dedup can be trusted.**

### 3.2 Amex credits are not distinguishable by sign

`R02:59` describes the CSV convention as "charges positive, payments/credits negative",
and `R02:89` says purchase vs statement payment is separated by "sign convention +
description text". In the **PDF** the amount column carries no sign at all: charges and
credits are both positive, and a credit is flagged by a line containing only `CR` printed
*beneath* the amount.

The consequence is that the credit section mixes two different things:

| Statement | Card payment | Refunds | `Crediteringen` |
|---|---|---|---|
| 2026-06-23 | 721,35 | 1 (70,00 PayPal) | 791,35 |
| 2026-07-23 | 765,67 | 2 (10,00 Amazon, 144,30 asos) | 919,97 |
| 2026-08-23 | 332,43 | 3 (2,99 / 6,99 Prime, 39,99 asos) | 382,40 |
| 2026-09-23 | 272,48 | 0 | 272,48 |

So the card payment must be separated from refunds by **description only**. The parser
here keys on `HARTELIJK BEDANKT VOOR UW BETALING`, which held for all 4 statements — but
that is one literal Dutch string, and `ARCH:626` already concedes the equivalent Rabobank
rule is "brittle — so it learns". This is a second string to learn.

### 3.3 The Amex balance identity runs the opposite way to a current account

Amex satisfies `Vorig saldo + Debiteringen − Crediteringen = Nieuw saldo` on all four
statements, i.e. a charge **increases** the amount owed. A current account satisfies
`previous + credits − debits = closing`. A single generic "balance identity" self-check
cannot be written for both; the test has to be per-account-type.

`ARCH:366` fixes the ledger convention as "Debits negative, credits positive", while
`R02:59` fixes the Amex source convention as "charges positive". **The sign flip between
them is not written down anywhere.** `exp-2` flagged this as an implicit gap under
`F-SCHEMA-NO-DIRECTION`; it is real, and an adapter must flip it explicitly.

### 3.4 Rabobank `End-to-End ID` cannot serve as a dedup key

`D-T1-SOURCES` (`ARCH:523-524`, `R09:60`) makes Tier-1 dedup available via "Enable Banking
`entry_reference`; Revolut `id`". The Rabobank **PDF** exposes an `End-to-End ID` field on
SEPA records, which looks like a Tier-1 candidate. It is not usable. Observed values in
these four statements:

- `261912` — a bare sequence number
- `01-09-2026 17:52 7670884404417442` — a value with an embedded timestamp
- `AI0001398/112` — an opaque prefixed code

60 of 106 rows carry the field, in mutually inconsistent shapes. It must be stored raw and
never used as a key. **The Rabobank PDF path has no Tier-1 dedup at all** and, like Amex,
depends entirely on the Tier-3 fingerprint.

### 3.5 Rabobank debit/credit columns move between pages

The column header repeats at the top of every page, but the character offsets of the
`Debit amount` and `Credit amount` columns differ per page — in the September statement,
`Debit@91/Credit@113` on page 1, `@86/@102` on pages 2–4, `@107/@131` on page 5. A parser
using one global column offset mislabels amounts while still appearing to work.

The rule that is correct — classify each amount against the header of the page it appears
on — is what the self-test validates, and it reconciles to delta 0 on all four statements
plus all three cross-month joins (620,59 → 620,59; 912,98 → 912,98; 3.627,09 → 3.627,09).

Worth recording: the **statement's own debit/credit split is fully recoverable**, so the
ambiguity a first pass suggests is not inherent to the source. (One delegated attempt
reported the column position as "indeterminate" with 44 of 96 rows flagged; that turned out
to be both a fabricated report and a misdiagnosis.)

### 3.6 Revolut: one file, two products, two IBANs — and 12 counterparty IBANs besides

`A-BATCH-SINGLE-ACCOUNT` (`ARCH:287`, `ARCH:304`) is a single nullable `account_id` on
`import_batch` and a non-null one on `source_record`. The Revolut statement violates that
premise outright:

- One file covers **two products**: `Account (Current Account)` (340 rows) and `Deposit`
  (11 rows), each with its own opening/money-out/money-in/closing row in the summary.
- It lists **two own IBANs** — `NL69REVO…1997` and `LT5032500…9880` (Lithuanian branch) —
  and 12 further IBANs belonging to counterparties.
- The file **does not state** which IBAN the Deposit product belongs to. Attributing
  Deposit rows to the LT IBAN is an inference, recorded as such in the manifest, and it is
  an assumption this repo would otherwise silently bake in.

`A-PROV-LINK-ID` (`ARCH:278-280`) says store the account identifier "exactly as the
provider sends it" — which here means knowing *which* of 14 IBANs is the account.

Internal transfers also do not pair up as `T-OWN-ACCOUNT-TRANSFERS` (`ARCH:803-804`)
assumes: 34 `To Savings` against 9 `From Savings`. The counter-legs live in the Deposit
section, so no single-account pass can match them. Across both sections the parser counts
53 internal-transfer legs.

### 3.7 Revolut uses a different decimal separator from the Dutch statements

Amex and Rabobank print `1.260,00` (dot thousands, comma decimal). Revolut prints
`€5,378.27` (comma thousands, **period** decimal). A single shared amount parser silently
misreads one of the two by three orders of magnitude. `ARCH:310` ("minor units, signed")
is silent on the input side, which is reasonable, but the adapters cannot share a converter.

### 3.8 Identical same-day rows exist, so `occurrence_index` is load-bearing

`ARCH:582-584` and `RECO:149` treat "two genuinely identical transactions on the same day
at the same merchant" as a known-unachievable case. It is not hypothetical: the June
statement contains `SNCB-NMBS SNCB-NMBS BRUXELLES` for `-13,40` on `2026-06-12` **twice**.

That is the only such pair in these 121 Amex rows, but it is enough to confirm the risk is
real. The counter-case matters just as much: the same statement also contains
`NMBS NMBS BRUXELLES 12,80` and `SNCB-NMBS SNCB-NMBS BRUXELLES 13,40`, which a
normalisation rule aggressive enough to merge the first pair would also wrongly collapse.
`ARCH:558`'s normalisation ("strip trailing `* # REF:...`") is nowhere near aggressive
enough to reach this, so the current rule is safe — but the margin is thin, and
`D-OCCURRENCE-PDF-TIEBREAK` (`R07:64-65`, `hash(raw_line) % 10000`) is the only thing
standing between a fingerprint collision and a silently dropped transaction.

---

## 4. Confirmed

- **`T-DETECTION-ILIKE` (`ARCH:624`) works, empirically.** The string
  `AMERICAN EXPRESS EUROPE S.A.` appears in the Rabobank counterparty field in **4 of 4**
  statements, and the amounts match the Amex `Te betalen` exactly for every month:

  | month | Amex `Te betalen` | Rabobank `AMERICAN EXPRESS` debit |
  |---|---|---|
  | June | 765,67 | 765,67 (30-06, type `ei`) |
  | July | 332,43 | 332,43 (30-07, type `ei`) |
  | August | 272,48 | 272,48 (28-08, type `ei`) |
  | September | 922,19 | 922,19 (30-09, type `ei`) |

  `R09:120` lists "Rabobank 'American Express' description reliably identifying card
  payments" as a contingent assumption. On this evidence it holds, and the monthly pairing
  is exact rather than approximate. `T-AMEX-NO-SECOND-ROW` (`ARCH:606-609`) is confirmed as
  the reason a synthesized leg is mandatory — the two legs genuinely live in two files.
- **Rabo type codes are self-documenting.** The last page carries a 24-entry legend; the 11
  codes appearing in these statements (`ba bg bv cb db ei ga id sb tb we`) are all defined
  by it, with no undefined codes. No enum for them exists anywhere in the docs, so the
  legend is currently parsed rather than hardcoded. One caveat: the `we` entry is truncated
  in the source itself — `we = Wero (co branded` — so it cannot be resolved further.
- **Statement-level balance continuity is a usable, strong invariant** across all three
  providers. No `source_record` or `journal_line` column stores a statement balance
  (`P-NO-HISTORIC-BALANCE`), so this is currently asserted nowhere. It is what caught every
  parsing bug in this exercise and deserves to be an import-time invariant.

---

## 5. Gaps the docs do not have

1. **`provider` has no value for the files that were actually supplied.** The enum at
   `ARCH:288-289` is `enable_banking | amex_csv | amex_pdf | revolut_csv | manual |
   google_wallet`. There is no `revolut_pdf` and no `rabobank_pdf`. The Revolut PDF path is
   mentioned *nowhere* in any doc — no adapter, no enum value, no assumption, no open
   question — yet it is the only format supplied, and the only full-year source
   (2026-01-01→10-01) against Rabobank's 2026-06 start. (`ARCH:101` treats "PDF (7yr)" as
   Amex-only; `ARCH:118`/`EB-SETUP:152` assume Revolut arrives as CSV with a stable `id`.)
2. **No Rabobank file-import path exists at all.** Rabobank is planned as Enable Banking
   API only (`ARCH:99`, LifeOS-10). There is no PDF adapter, no CSV adapter, and no enum
   value. `P-BANK-CSV-FIRST-CLASS` (`ARCH:867`) contemplates bank CSVs as a historical
   source but never says whether Rabobank offers one.
3. **The printed `Periode` is advisory, not a filter.** Real statements carry transactions
   dated the day *before* the period opens: `2026-06-23` in the statement whose period is
   24.06–23.07, and `2026-07-23` in the 24.07–23.08 statement. Filtering on period bounds
   would drop genuine spend. Worth writing down, because the natural implementation is to
   trust the period.
4. **Coverage is asymmetric and unstated.** Rabobank history starts 2026-06-01; Amex
   2026-05-24; Revolut 2026-01-01. Any "how far back does history matter" question
   (`G-HISTORY-DEPTH`, `ARCH:867`) is constrained by what these providers will actually
   serve, which the docs do not record.
5. **PII in free text is load-bearing, not theoretical.** Full counterparty IBANs appear
   inside `To:` / `From:` / `Reference:` free-text fields — at least 32 distinct ones
   across the four Rabobank statements, plus 11 in the Revolut statement, and not all of
   them in Dutch layout: a German one (`DE59 … 3984`, 2 extra numeric groups) and an Irish
   one (`IE30 CITI … 5648`, 4 numeric groups) both occur, and both are third-party accounts,
   not the user's own. `R06:103-112` / `ARCH:726` say log last 4 only; that rule has to be
   applied to parsed description fields, not just to log lines.
   A redaction pattern pinned to the Dutch IBAN layout silently misses foreign ones — that
   is exactly the bug hit while building these parsers, where an Irish counterparty passed
   through unmasked until the pattern was widened.
6. **`docs/adr/0001-deployment-topology.md` contains no import decisions** — zero matches
   for `import|CSV|PDF|CAMT|OFX|adapter|statement`. Nothing in the ADR record constrains
   this design area.

---

## 6. Suggested doc changes

Ordered by how much damage they do if left unfixed.

1. ~~**Settle the Amex date question before M2/M7.**~~ **MOOT as of 2026-10-01.** The user
   will download Amex statements as PDF from the app, which offers no CSV, so there is no
   cross-format reconciliation to perform. The measurement in §3.1 still stands and still
   matters — it is why `raw_posting_date` is load-bearing for the PDF path — but the
   fingerprint no longer needs a second date component. Bead `LifeOS-18`.
2. **Correct `R07:41`.** Amex `raw_posting_date` is not equal to `raw_date`; it differs on
   ~35% of rows.
3. **Write down the source→ledger sign flip for Amex** (§3.3) as an explicit, tested
   adapter rule rather than leaving it implied in `F-SCHEMA-NO-DIRECTION`.
4. **Add `revolut_pdf` and `rabobank_pdf` to the `provider` enum**, or record an explicit
   decision that these files are research-only and not import paths. Right now the schema
   cannot represent the only data the user actually has.
5. **Record the per-statement balance identity as an import invariant**, and treat
   statement-balance continuity as an acceptance test for every new adapter.
6. **Note that the Revolut Deposit→IBAN mapping is an inference**, and decide whether an
   unattributable product should be its own `source_record` status rather than a guess.
7. **Add the type-code enum** for Rabobank, sourced from the printed legend, and record
   that `we` is truncated in the source.

## 7. What this does not cover

- **No Amex or Revolut CSV was supplied**, so every M2 column-mapping question in
  `ARCH:858-859` and the whole `F-AMX-CSV-*` / `D-REVOLET-ID` assumption set remain
  untested. In particular the claim that Revolut CSV has a stable `id` (`R07:15`,
  `EB-SETUP:152`) is still unverified.
- **Nothing here exercises the Enable Banking API path.** `D-T1-*`, `D-T2-*`,
  `A-EB-*` and `P-EB-90D-CLAMP` are all API claims and are untouched by this work.
- **`T-SEPA-DD-RULE` (`ARCH:629-633`) is only partly addressed.** Rabobank records do carry
  `Mandate Identifier / Creditor ID` (36 of 106 rows) and Dutch SEPA creditor identifiers,
  so the creditor-ID branch is reachable in principle, but the match between a Rabobank
  mandate and an inbound leg was not tested.
- Foreign-currency Amex charges, XLSX availability, and the Amex PDF's 7-year depth are all
  untestable from a 4-month sample.
