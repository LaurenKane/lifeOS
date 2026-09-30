# Research 07 — Design Contingencies (dedup under real provider constraints)

**Lane:** ora-1 (oracle, Specialist C) · **Received:** 2026-09-30 · **Status:** partially received
**Note:** this lane's *main* deliverable (entity model, DDL, transfer-matching algorithm) is being
re-issued. What follows is the contingency analysis it produced in response to confirmed Amex/Enable
Banking facts — retained because it usefully pins down which parts of the design are provider-dependent.

---

## Confirmed provider constraint matrix

| Provider | Stable ID (`provider_txn_id`) | Pending status | Import window |
|---|---|---|---|
| **Enable Banking** (Rabobank, Revolut) | `entry_reference` — stable pending→booked | **Yes** (`PDNG`) | Unbounded via API (clamped to ~90d post-consent) |
| **Revolut CSV** | `id` column | No (posted only) | ~6 months |
| **Amex NL (CSV/PDF)** | **None** | **No — excludes pending** | CSV ~6mo, **PDF 7yr** |
| **Google Wallet** | None | No | Unknown |

---

## Which design components depend on which assumption

### 🔴 Depends on `provider_txn_id` being present (Tier 1)

| Component | Amex impact | Mitigation |
|---|---|---|
| `source_record.UNIQUE (import_batch_id, provider_txn_id)` | Unusable for Amex | Partial index `WHERE provider_txn_id IS NOT NULL` |
| `IdentityResolver` Tier 1 | Skipped for Amex → falls to Tier 3 | Explicit `if sr.provider_txn_id:` guard |
| PENDING→BOOKED via `provider_txn_id` | Rabobank/Revolut only | Provider-specific |
| Dedup counting by `provider_txn_id` | Amex counted via fingerprint | Separate code path |

**Verdict: no schema change. Tier 1 is opt-in per provider; code branches on `provider_txn_id IS NOT NULL`.**

### 🔴 Depends on pending status existing

| Component | Amex impact | Mitigation |
|---|---|---|
| `source_record.status` ∈ {`pending`,`posted`} | Amex never writes `pending` | Enum allows it; value unused |
| Tier 2 candidate generation | Enable Banking only | Guard `sr.status == 'pending'` |
| Nightly pending→posted job | Enable Banking only | Filter `import_batch.provider = 'enable_banking'` |
| `raw_posting_date` vs `raw_date` | Amex: same (posted only) | Nullable, no issue |

**Verdict: no schema change. Pending machinery is Enable-Banking-only. Add a provider filter to the job.**

### 🟢 Works with neither — the Amex path (Tier 3 only)

| Component | Status |
|---|---|
| `fingerprint` = SHA256(canonicalised fields + `occurrence_index`) | **Primary key for Amex.** Must be computed at parse time |
| `UNIQUE (import_batch_id, fingerprint)` | Prevents intra-batch duplicates — critical for re-importing the same CSV |
| Cross-batch fingerprint lookup: `EXISTS (… WHERE fingerprint = $1 AND account_id = $2)` | Handles **PDF (7yr) → CSV (6mo) overlap** |
| `MerchantAlias` learning from `raw_description` | Unchanged |
| `CategoryRule` matching on normalized description | Unchanged |

**Critical Amex CSV implementation detail** — `occurrence_index` must be the running count within a
`(date, normalized_description, amount, currency)` group:

```python
df['occurrence_index'] = df.groupby(
    ['date', 'normalized_description', 'amount', 'currency']
).cumcount() + 1
```

For PDF, where there is no reliable line number, use `hash(raw_line) % 10000` as a tiebreaker —
**documented as lower confidence**.

---

## Adjustments required

| Change | Reason |
|---|---|
| Use `import_batch.provider` to drive per-provider logic in the normalizer | Avoids a join |
| Add `import_batch.import_method` ∈ {`api`,`csv`,`pdf`} | PDF fingerprints are less stable → lower auto-match threshold |
| Tier 3 confidence must account for import method (`pdf` −0.15, `csv` 0.0, `api` +0.1) | PDF text extraction varies (column alignment, OCR) |
| `import_batch.chunk_size` + resumable checkpoint | A 7-year Amex PDF import is large; avoid memory blow-up, allow resume |
| Explicit allowlist to disable Tier 2 for `amex_csv`, `amex_pdf`, `revolut_csv`, `google_wallet` | Clearer than a status check |

---

## Provider-aware Identity Resolver (pseudocode)

```python
def resolve_source_record(self, sr: SourceRecord) -> ResolutionResult:
    provider = sr.import_batch.provider

    # TIER 1: provider stable ID (Enable Banking, Revolut API)
    if sr.provider_txn_id and provider in ('enable_banking', 'revolut_api'):
        existing = self.repo.find_by_provider_txn_id(sr.account_id, sr.provider_txn_id)
        if existing:
            return ResolutionResult.MATCH_EXISTING(existing, tier=1, confidence=1.0)

    # TIER 2: pending -> booked correlation (Enable Banking ONLY)
    if sr.status == 'pending' and provider == 'enable_banking':
        best = self.score_candidates(sr, self.repo.find_pending_candidates(sr))
        if best.score >= 0.85:
            return ResolutionResult.MATCH_EXISTING(best.record, tier=2, confidence=best.score)
        if best.score >= 0.50:
            return ResolutionResult.NEEDS_REVIEW(best, tier=2)

    # TIER 3: content fingerprint (ALL providers; PRIMARY for Amex)
    base = 0.90
    if provider in ('amex_pdf', 'google_wallet_pdf'): base -= 0.15
    elif provider in ('amex_csv', 'revolut_csv'):   base -= 0.05

    existing = self.repo.find_by_fingerprint(sr.fingerprint, sr.account_id)
    if existing:
        return ResolutionResult.MATCH_EXISTING(existing, tier=3, confidence=base)
    return ResolutionResult.NEW(tier=3, confidence=base)
```

**Code changes required (no DDL change):**
1. `IdentityResolver` branches on `provider` as above.
2. Background reconciliation job filters `WHERE provider = 'enable_banking'`.
3. Amex CSV/PDF parser computes `occurrence_index` correctly.
4. Import UI states "Amex: no pending transactions — all imported as posted."

---

# Orchestrator notes

## The one open contingency — and its resolution

The lane asked: *is `entry_reference` stable across re-authentications?*

**Research 01 answer:** `entry_reference` is "unique + immutable **per account**, scoped by the same
`identification_hash`", and `identification_hash` is the stable cross-session account identity (whereas
`account.uid` rotates at every re-auth). So the *intent* is yes. But it is **not explicitly confirmed for
a historical re-fetch months later**, and Revolut NL support is still UNCERTAIN.

**Resolution given to the lane:** compute a content `fingerprint` for **API-sourced rows too**, not just
file-sourced ones. Tier 1 stays the fast path; Tier 3 becomes a backstop. This costs almost nothing
(fingerprint computation is cheap and deterministic) and removes a whole class of long-term duplicate
risk. **This is now a closed decision.**

## Why this is the right shape overall

The tiering is **provider-agnostic in the schema, provider-aware only in the resolver.** That is exactly
what was asked for: one canonical ledger, many ingestion sources, no provider-specific columns. The
`import_batch.provider` + `import_batch.import_method` pair is the only place provider identity lives,
and the `import_method` field is a genuinely good addition — it lets the confidence model degrade
appropriately for formats whose extraction is inherently noisier.

## Note on the PDF/CSV overlap

The cross-batch fingerprint lookup keyed on `(fingerprint, account_id)` is what makes **importing a
7-year Amex PDF and then a 6-month CSV non-destructive** — the CSV rows will already be present from the
PDF and will fingerprint-match. This is a subtle but important property, and it is worth an explicit
integration test: *import PDF (7y) → import CSV (6m) → assert no new transactions, and the CSV row
count is fully accounted for.*

## Carried into the synthesis

- Money representation (`NUMERIC` vs `BIGINT` minor units) — **still open**, highest-stakes single decision.
- The Amex liability/card-payment representation — **still open**, the hardest modelling problem.
- Full entity list and DDL — **pending** redelivery from ora-1.
