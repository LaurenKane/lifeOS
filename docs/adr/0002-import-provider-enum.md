# ADR 0002: The v1 `provider` enum

**Date:** 2026-10-02
**Status:** Accepted
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
| `amex_csv` | **RETIRED** | No CSV exists in the workflow — the Amex app exports PDF only (`LifeOS-18`). Not deferred; there is no file to defer *to*. |
| `revolut_csv` | **DEFERRED** | No Revolut CSV was ever supplied, so its stable `id` claim is unverified (doc 11 §7). Reconsider only if a CSV is supplied *and* its `id` is confirmed stable across re-exports. |
| `google_wallet` | **EXCLUDED from v1** | Recent/shared data only, no posting date, no FX, no reference ID, and merchant strings differ from Amex's — dedup is fuzzy-only. A dedup liability as much as a source (`R02` §4). |

**`rabobank_pdf` and `revolut_pdf` are v1 import paths — a user-confirmed decision.** Rabobank was
planned as Enable-Banking-API-only (`ARCHITECTURE-PROPOSAL.md` §C; LifeOS-10), so the schema
would have had no way to represent four real statements. `P-BANK-CSV-FIRST-CLASS` contemplates
bank files as a historical source but never says whether Rabobank offers one; a PDF does.

**Neither has an implemented `FileAdapter` yet.** They are *listed but not uploadable*:
`/imports/providers` returns them with `accepts_upload=false`, and the upload endpoint's
adapter map omits them. That is the intended state — the enum must describe the target, the
adapter set describes what is built, and a provider is not uploadable until it is implemented.

**The modules for retired paths stay in the tree.** `amex_csv.py` and `revolut_csv.py` remain
importable so historical `raw_payload` can still be replayed, but neither is reachable through
`V1_PROVIDERS`. Their removal is a separate decision, not a side effect of this one.

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

## Consequences

**Positive:**
- The schema represents every file the user actually holds.
- `/imports/providers` and the frontend enum cannot drift from the DB `CHECK` — one tuple, three
  consumers.
- `accepts_upload=false` makes "declared but not built" an explicit, visible state rather than a
  404 at upload time.

**Negative:**
- `rabobank_pdf` and `revolut_pdf` are import paths with no implementation. Each needs its own
  bead; the enum alone does not deliver them.
- Retirements still have live modules. A reviewer reading `adapters/__init__.py` sees two exports
  (`AmexCsvAdapter`, `RevolutCsvAdapter`) that the provider list cannot reach.
- The M2/M7 roadmap rows and the "PDF → CSV overlap" tests describe a CSV that will not exist.
  Those are marked MOOT where they appear, not silently deleted — the reasoning is worth keeping.

## Acceptance Criteria

- [x] Five-value v1 list recorded in one place and cited by DB, API and frontend
- [x] `amex_csv` retired, `revolut_csv` deferred, `google_wallet` excluded — each with a reason
- [x] `rabobank_pdf` / `revolut_pdf` recorded as v1 import paths with no `FileAdapter` yet
- [x] Retired-path modules left in the tree, documented as unreachable via `V1_PROVIDERS`
- [x] `docs/research/07-dedup-contingencies.md` provider matrix and `ARCHITECTURE-PROPOSAL.md` §E
      `CHECK` both match this list

## Related

- `docs/research/11-real-export-verification.md` §5 (what the exports proved about the enum),
  §7 (Revolut CSV `id` still unverified)
- `docs/research/02-amex-nl.md` — the Amex-is-PDF-only decision and §4 on Google Wallet
- `docs/adr/0003-import-decisions-real-export.md` — the decisions the real exports forced *within*
  the import path
- Beads `LifeOS-3pe` (this decision), `LifeOS-18` (Amex PDF-only), `LifeOS-gfi` / `LifeOS-lcy` /
  `LifeOS-2rg` (superseded into it)