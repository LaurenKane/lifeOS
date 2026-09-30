# Enable Banking — Setup State & Resume Guide

**Purpose:** everything needed to resume Enable Banking work from cold, without re-reading the
research trail. Last updated **2026-09-30**.

**Read this before touching Enable Banking.** Then read `SAFETY.md` — a previous attempt at this
work destroyed a live private key.

---

## TL;DR

| | Status |
|---|---|
| **Sandbox app** | ✅ **Active and working.** Probe executed, Rabobank CONFIRMED |
| **Production app** | ⏸ **Not created.** Blocked on a Control Panel form asking for 4 KYB fields |
| **Revolut support** | ❓ **Unknown.** Sandbox list is uninformative; needs production |
| **Blocking the build?** | **No.** Rabobank alone is sufficient for M1→M10 |
| **Legal pages** | ✅ Written, not published. One placeholder to replace first |

---

## Current state

### Sandbox app — WORKING

| Field | Value |
|---|---|
| `app_id` | `528ee4b1-cbbe-4116-b552-7e9dd6555fd2` |
| Environment | SANDBOX |
| Services | Account Information, Payment Initiation |
| Redirect URL | `http://localhost:8000/auth/callback` |
| Status | **Active** |
| Credentials | `~/.config/lifeos/eb/` (outside the repo, by design) |

`app_id` is recorded in **three** places, per SAFETY.md Rule 6:
`~/.config/lifeos/eb/app_id` · this file · the git log of the commit that created the sandbox app.

The private key and certificate were generated **once** and are shared. Do not generate a second
keypair — the sandbox and production apps can both use this one.

### Production app — NOT CREATED

Enable Banking's production signup asks for four fields that the ToS describes as being required
only for the *unrestricted* (commercial) path:

1. Application Description
2. Data-protection email
3. Privacy URL
4. Terms URL

Only a single form was offered; no restricted/unrestricted toggle was visible. Their documentation
may be out of date. **Unresolved whether restricted mode is still available as a separate path.**

Prepared values for all four are in `docs/legal/` and below. Nothing has been submitted.

---

## Prepared answers for the production form

**Application Description** — ready to paste:
> Personal financial ledger for single-user, self-hosted use. Imports the account holder's own
> bank and credit-card transactions for personal budgeting, categorisation and net-worth tracking.
> Not a service; no third-party data.

**Data-protection email** — an address that is actually monitored. This is where a GDPR breach
notification (72h) would go. Do not use a throwaway.

**Privacy URL** / **Terms URL** — must **resolve**. The bar is "loads a real page", not "looks
professional". See publish instructions below.

### Publishing the legal pages

Files: `docs/legal/{index,privacy,terms}.html` — written, accurate, committed.

**Before publishing:** replace `YOUR-EMAIL-HERE` in `privacy.html` and `terms.html`.

```bash
cd docs/legal
sed -i 's/YOUR-EMAIL-HERE/you@example.com/g' privacy.html terms.html
git init -q && git add -A && git commit -qm "legal: privacy notice and terms"
git branch -M main
git remote add origin https://github.com/YOURUSERNAME/lifeos-legal.git
git push -u origin main
```

Then **Settings → Pages → Source: Deploy from a branch → `main` / `(root)`**. Yields:
`https://YOURUSERNAME.github.io/lifeos-legal/privacy.html` (and `/terms.html`).
**Confirm both load before pasting them into the form.**

Any static host works. This is just the cheapest option that gives a real, resolvable URL.

---

## Resume procedure

```bash
# 1. Confirm credentials survived
python3 tools/probe_aspsps.py verify

# 2. Re-run the sandbox probe at any time (free, instant)
python3 tools/probe_aspsps.py probe

# 3. Once a production app exists, add its id and re-run against production
python3 tools/probe_aspsps.py --app-id <prod-uuid> probe
```

The probe prints a direct FOUND/ABSENT verdict for Rabobank and Revolut, plus `auth_methods`,
`required_psu_headers` and `maximum_consent_validity`. Raw output is written to `aspsps-<CC>.json`
at the repo root (gitignored — curated evidence goes in `docs/research/`).

---

## What is settled, and what is not

| Claim | Confidence | Evidence |
|---|---|---|
| Rabobank reachable via Enable Banking (NL) | **CONFIRMED** | `aspsps-NL-sandbox-2026-09-30.json` |
| Consent validity 180 days (15552000 s) | **CONFIRMED** | same |
| **No credential fallback** — `approach: REDIRECT` only | **CONFIRMED** | same; was LIKELY in the threat model |
| `psu-ip-address` PSU header is **mandatory** | **CONFIRMED** | same |
| ToS permits free personal use | **CONFIRMED** | ToS 2026-01-09 |
| Sandbox NL list = 3 entries (2 real banks + mock) | **CONFIRMED** | same |
| **Revolut NL support** | **UNCERTAIN** | not in a 2-real-bank list — uninformative |
| Whether restricted mode still exists as a separate signup | **UNCERTAIN** | only one form was offered |
| Redirect URL matching rules | **UNCERTAIN** | EB publishes none; exact-match is an inference |
| Whether any NL ASPSP uses non-REDIRECT auth | **LIKELY** (all sandbox ones are REDIRECT) | only 2 real banks observed |

### The Revolut situation, stated precisely

The sandbox list contains **two real Dutch banks out of the ~15+ with PSD2 in the Netherlands.**
Enable Banking states it "does not aim to provide access to a large number of ASPSPs' sandboxes."

**Absence from sandbox is not a verdict.** Revolut's absence is fully explained by Revolut having
no PSD2 test sandbox, and says nothing about production. **Do not read the sandbox result as
"unsupported"** — that inference is wrong and would send us to build a CSV importer unnecessarily.

Only a production query settles it. Both outcomes are cheap:
- Revolut present → API adapter (a few hours)
- Revolut absent → CSV importer (Revolut's CSV has a stable `id` column, so Tier-1 dedup applies)

**Neither outcome changes the schema.** The ledger is provider-agnostic by design; only the
IdentityResolver is provider-aware. This is the whole point of that decision.

---

## Why production is not blocking

Revolut is bead `LifeOS-13`, priority **P3**. Rabobank is CONFIRMED and is enough to build
everything in the roadmap. `M1`→`M10` all work on Rabobank alone.

**Recommended next work: `M0` (repo skeleton, invariants, CI) then `M1` (double-entry schema).**
Neither touches Enable Banking. Come back to production when `LifeOS-3` (deployment) has given a
real host — at which point the privacy URL is trivial.

---

## Cost & terms notes (for the record)

- Enable Banking production **restricted mode is free**, no contract, per the ToS — *if it still
  exists as a separate path.*
- **Fallback if Enable Banking becomes unusable:** GoCardless Bank Account Data (ex-Nordigen) —
  self-serve, free tier ~25 accounts, strong NL coverage. Enable Banking is not a single point of
  failure; the adapter is ~200 lines.
- Rebranding risk: if Enable Banking's terms for personal use changed, the whole integration would
  need re-evaluating. The GoCardless fallback is the mitigation.

---

## Related files

| File | What |
|---|---|
| `tools/probe_aspsps.py` | The probe. `keygen` / `verify` / `probe` |
| `docs/legal/` | Privacy notice + terms, awaiting publication |
| `docs/research/01-enable-banking-psd2.md` | Full research, incl. probe result addendum |
| `docs/research/aspsps-NL-sandbox-2026-09-30.json` | Raw sandbox response (evidence) |
| `docs/research/06-security-privacy.md` | Threat model, credential inventory |
| `docs/ARCHITECTURE-PROPOSAL.md` §C | Connectivity strategy across all four sources |
| `SAFETY.md` | **Read before any destructive command** |

## Beads

| Bead | What |
|---|---|
| `LifeOS-1` | ASPSP probe — **CLOSED**, result recorded |
| `LifeOS-13` | M8 Revolut — conditional on this |
| `LifeOS-16` | Safety incident — keygen moved out of repo, rm_guard shipped |
| `LifeOS-17` | **Production app activation — this document's work item** |
