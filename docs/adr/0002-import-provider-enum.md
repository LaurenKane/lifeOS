# ADR 0002: The v1 `provider` enum

**Date:** 2026-10-02
**Status:** Accepted
**Amended:** 2026-10-03 — `rabobank_pdf` and `revolut_pdf` are now built. The original five-value
list is unchanged; what moved is their disposition, from "listed but not uploadable" to
implemented and verified against the real statements. See the Amendment section.
**Decision Drivers:** the enum is the single source of truth shared by the DB `CHECK`, the
`/imports/providers` response and the frontend TS union; the schema must be able to represent the
files the user actually has; a value that is neither implemented nor decided must not ship as a
promise.

## Context

`docs/ARCHITECTURE-PROPOSAL.md` §E fixed `import_batch.provider` as

```
'enable_banking','amex_csv','amex_pdf','revolut_csv','manual','google_wallet'
```

Nine real statement PDFs were then parsed and reconciled against their own printed totals
(`docs/research/11-real-export-verification.md` §1: 578 rows, all three parsers delta 0). Two
consequences, both from doc 11 §5:

- **The enum had no value for the files that were supplied.** `revolut_pdf` and `rabobank_pdf`
  did not exist. The Revolut PDF path was mentioned *nowhere* — no adapter, no enum value, no
  assumption, no open question — yet it is the only format supplied and the only full-year source
  (2026-01-01 → 10-01) against Rabobank's 2026-06 start (doc 11 §5.4).
- **Three of the six values had no source behind them.** `amex_csv` was retired by bead
  `LifeOS-18`: the user downloads Amex statements as PDF from the app, which offers no CSV, so
  there is nothing to import (`docs/research/02-amex-nl.md`, "Decision: Amex is PDF-only").
  `revolut_csv` rests on a stable `id` column that no supplied file confirms (doc 11 §7).
  `google_wallet` was already recommended for exclusion from v1 (`docs/research/02-amex-nl.md` §4:
  it is a dedup liability as much as a data source).

`SourceAdapter.provider` (`ARCHITECTURE-PROPOSAL.md` §C) and the resolver's per-provider branches
are written against this enum, so the list is a contract, not a label.

## Decision

**The canonical v1 provider list is five values, in this order:**

```
enable_banking | amex_pdf | rabobank_pdf | revolut_pdf | manual
```

It is declared once in `backend/finance/api/routes/imports.py::V1_PROVIDERS` and read by the DB
`CHECK`, the `/imports/providers` response and `frontend/src/features/finance/imports/types.ts`.

**Status of the three values being dropped:**

| Value | Disposition | Reason |
|---|---|---|
| `amex_csv` | **RETIRED** | No CSV exists in the workflow — the Amex app exports PDF only (`LifeOS-18`). Not deferred; there is no file to defer *to*. The adapter module has since been deleted. |
| `revolut_csv` | **DEFERRED** | No Revolut CSV was ever supplied, so its stable `id` claim is unverified (doc 11 §7). Reconsider only if a CSV is supplied *and* its `id` is confirmed stable across re-exports. The unverifiable adapter module has since been deleted; the deferral is unaffected and a rebuild would start from a supplied file. |
| `google_wallet` | **EXCLUDED from v1** | Recent/shared data only, no posting date, no FX, no reference ID, and merchant strings differ from Amex's — dedup is fuzzy-only. A dedup liability as much as a source (`R02` §4). |

**`rabobank_pdf` and `revolut_pdf` are v1 import paths — a user-confirmed decision.** Rabobank was
planned as Enable-Banking-API-only (`ARCHITECTURE-PROPOSAL.md` §C; LifeOS-10), so the schema
would have had no way to represent four real statements. `P-BANK-CSV-FIRST-CLASS` contemplates
bank files as a historical source but never says whether Rabobank offers one; a PDF does.

**Both are now built, and uploadable.** *(Amended 2026-10-03; see "Amendment" below.)*

**The modules for retired paths were removed once the removal was decided.** `amex_csv.py` and
`revolut_csv.py` stayed importable for a while after this ADR so historical `raw_payload` could
still be replayed. Both have since been deleted: neither was reachable through `V1_PROVIDERS`, and
neither had ever been run against a supplied file, so neither parser was ever verified. A replay of
historical CSV `raw_payload` is no longer supported.

### Rejected alternatives

1. **Leave the six-value enum.** Rejected: the schema cannot represent the data the user has, and
   `google_wallet` would ship as an unbacked promise that `ARCHITECTURE-PROPOSAL.md` §C already
   argues against.
2. **Record the two PDF paths as "research-only, not import paths"** — doc 11 §6.4 offered this.
   Rejected by the user: they are real statements, and the Revolut one is the only full-history
   source in the system.
3. **Drop the `CHECK` and let `provider` be free text.** Rejected: it destroys the per-provider
   resolver branch that §G is built on and turns a typo into a silent misroute.
4. **Defer `amex_csv` rather than retire it.** Rejected: there is no CSV in the workflow at all.
   "Deferred" implies a file that might arrive; that is a different claim and would need its own
   bead when it becomes true.

## Amendment (2026-10-03): `rabobank_pdf` and `revolut_pdf` are built

**What moved.** Both providers now have a `FileAdapter` (`rabobank_pdf.py`, `revolut_pdf.py`),
both are in the upload endpoint's adapter map, and `/imports/providers` reports
`accepts_upload=true` for all five values. There is no longer a *declared-but-not-built* split:
`enable_banking` and `manual` are in the enum because they are import paths that are **not
uploads** (one arrives over its API, one is POSTed as JSON), which is a different state from a
listed provider that cannot yet accept a file.

**Why it moved.** The original decision separated "the enum describes the target" from "the
adapter set describes what is built", and listed these two as targets with no adapter. Listing a
provider is cheap and implementing one is not, and the gap was visible only because the split
was pinned. What changed is not a judgement about the enum — it was right that both belong in it
— but that the implementations landed, so the honest state record now says so. Keeping
`accepts_upload=false` for a provider that has a working adapter would be the actual error: the
flag exists to keep a promise the app cannot keep, not to preserve a historical note.

**Verified against the real statements, not against fixtures.** The claim that matters is not
"the adapters exist" but "they read the files the user actually has". Both were reconciled
against the statements themselves, at delta 0, in
`backend/finance/tests/integration/test_real_statement_reconciliation.py`:

| Provider | Statement | Printed identity | Result |
|---|---|---|---|
| `rabobank_pdf` | 4 monthly statements, 2026-06 → 2026-09 | `previous + Total amount credited − Total amount debited = closing balance` | delta 0 on all four |
| `rabobank_pdf` | the same four, chained | closing balance of month *N* = `Previous balance` of month *N+1* | delta 0 on all three joins |
| `revolut_pdf` | 1 annual statement, 2026-01-01 → 2026-10-01 | `opening − Money out + Money in = closing`, per product **and** for `Total` | delta 0 on all three rows |
| `amex_pdf` | 4 monthly statements, 24.05 → 23.09.2026 | `Vorig saldo + Debiteringen − Crediteringen = Nieuw saldo` | delta 0 on all four |

The printed figures are read out of the documents by the test's own readers rather than by the
adapters' helpers, so each identity compares a *document* against a *parser* and not one function
with another. The tests skip when the statements are absent (`LIFEOS_STATEMENTS_DIR`, default
`~/Documents/Banking`), because CI does not have them; they are never committed, since they carry
a real name, home address, IBANs and a full spending history.

**What this amendment does not claim.** It does not claim `revolut_csv` is worth building — that
value stays DEFERRED, because a CSV was still never supplied and its stable `id` is still
unverified. It does not retire any module. And it does not make the split test redundant: that
test keeps its guard, and now guards the other direction — that a provider added to the enum
with an upload-shaped `import_method` must be classified.

## Consequences

**Positive:**
- The schema represents every file the user actually holds.
- `/imports/providers` and the frontend enum cannot drift from the DB `CHECK` — one tuple, three
  consumers.
- All five v1 values are reachable: three by upload, one by API, one by JSON. `accepts_upload` is
  no longer carrying a "not built" state for anything, which removes the one way a listed provider
  could be advertised and then refuse a file.
- The two adapters are verified against the nine real statements at delta 0, so the promise is
  measured rather than asserted.

**Negative:**
- `rabobank_pdf` and `revolut_pdf` are both PDF parsers over `pdftotext -layout`, which is a
  *system* binary. Without `poppler-utils` installed, an upload of any of the three fails with an
  explicit reason — see `backend/Dockerfile` for where it is installed.
- The reconciliation tests are the only coverage that reads the real statements, and they skip off
  this machine. A parser change that only CI can catch is a parser change CI cannot catch; the
  skip reason names `LIFEOS_STATEMENTS_DIR` so the gap is legible rather than silent.
- One known Amex defect is recorded rather than fixed, as a strict `xfail`: page furniture leaks
  into 14 of 121 Amex row descriptions, four of which carry the cardholder's address and Amex's
  own IBAN. The reconciliation identities are unaffected — dates, amounts and row counts match
  `tools/bankparse/amex.py` exactly — but `raw_data` is immutable forever, so it is a write-once
  privacy defect that widens `_STATEMENT_FURNITURE` has to fix.
- Retired-path modules were live for a while after this ADR and are now deleted. That was resolved
  separately rather than left as a standing cost; the two exports (`AmexCsvAdapter`,
  `RevolutCsvAdapter`) that `adapters/__init__.py` used to carry are gone, so the provider list and
  the adapter set no longer disagree on their face.
- The M2/M7 roadmap rows and the "PDF → CSV overlap" tests describe a CSV that will not exist.
  Those are marked MOOT where they appear, not silently deleted — the reasoning is worth keeping.

## Acceptance Criteria

- [x] Five-value v1 list recorded in one place and cited by DB, API and frontend
- [x] `amex_csv` retired, `revolut_csv` deferred, `google_wallet` excluded — each with a reason
- [x] `rabobank_pdf` / `revolut_pdf` recorded as v1 import paths *(original decision; superseded
      by the 2026-10-03 Amendment below, which records both as built)*
- [x] Retired-path modules left in the tree, documented as unreachable via `V1_PROVIDERS` *(as
      decided; superseded — both modules have since been deleted, so CSV `raw_payload` can no longer
      be replayed)*
- [x] `docs/research/07-dedup-contingencies.md` provider matrix and `ARCHITECTURE-PROPOSAL.md` §E
      `CHECK` both match this list
- [x] *(Amendment)* `rabobank_pdf` and `revolut_pdf` have a `FileAdapter` and report
      `accepts_upload=true`, with the split they were declared-not-built under now empty
- [x] *(Amendment)* Both verified against the real statements at delta 0 — printed balance
      identities and, for Rabobank, the month-to-month balance chain — by
      `backend/finance/tests/integration/test_real_statement_reconciliation.py`
- [x] *(Amendment)* The statements are not committed, are not committed in redacted form, and
      `.gitignore` is enforced by a test that runs whether or not they are present

## Related

- `docs/research/11-real-export-verification.md` §5 (what the exports proved about the enum),
  §7 (Revolut CSV `id` still unverified)
- `docs/research/02-amex-nl.md` — the Amex-is-PDF-only decision and §4 on Google Wallet
- `docs/adr/0003-import-decisions-real-export.md` — the decisions the real exports forced *within*
  the import path
- `backend/finance/tests/integration/test_real_statement_reconciliation.py` — the reconciliation
  the 2026-10-03 Amendment rests on, including the sabotage cases that prove each identity can
  fail
- Beads `LifeOS-3pe` (this decision), `LifeOS-18` (Amex PDF-only), `LifeOS-gfi` / `LifeOS-lcy` /
  `LifeOS-2rg` (superseded into it)