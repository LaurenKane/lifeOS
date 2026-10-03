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

**Option A — GitHub Pages (do this now, cheapest, no dependencies):**

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

**Option B — the VPS (later, once provisioned):** deployment is now decided
(`docs/adr/0001-deployment-topology.md`): Netcup VPS 500 G12, Amsterdam, €5.91/mo, with Tailscale.
Once it exists, serve the same two files from it — e.g. behind `tailscale funnel`, which gives a
**public HTTPS URL without opening any inbound port or managing a TLS certificate.** Zero extra cost
and it keeps the pages on infrastructure you already own.

⚠️ **Option B is not available yet** — the VPS does not exist, and the Enable Banking form wants the
URL now. Use Option A to unblock the form; you can move the pages to the VPS later. EB does not care
where a privacy policy is hosted, and moving it afterwards does not invalidate anything.

Any static host works. These are just the two cheapest options that give a real, resolvable URL.

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
at the repo root (gitignored — curated evidence goes in `docs/`).

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

Only a production query settles it. Both outcomes are workable, and neither is on the critical path:
- Revolut present → API adapter (a few hours)
- Revolut absent → **`revolut_pdf` importer** — a real annual statement the user already holds
  (2026-01-01→10-01, the only full-year source). **Not** a CSV importer: the Revolut *PDF* carries
  **no stable ID** (one file covers two products and two own IBANs), and the Revolut *CSV* `id` is
  unverified — no CSV has ever been seen (doc 11 §3.6, §7). Tier-1 dedup by provider ID is
  **Enable-Banking only**; Revolut rows dedupe by fingerprint. → `docs/adr/0003-import-decisions-real-export.md` Decision 4

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

## Credential inventory & rotation

Restored from the deleted spike `docs/research/06-security-privacy.md` §2. That file was a
research spike, but this content is operational: it is the inventory of what has to be kept
alive, and the recovery procedure if one of them is lost. Do not delete this section.

### What we must store

| Credential | Type | Lifetime | Sensitivity | Storage |
|---|---|---|---|---|
| RSA private key (4096) | **Long-lived app credential** | Years | **CRITICAL** — signs all API JWTs | OS keyring (libsecret) or age file (0600) |
| Application ID (`kid`) | Public identifier | Permanent | Low | config/env |
| `session_id` (per consent) | Consent handle | ≤180d | Medium | DB (protected by FDE) |
| Access token (to EB) | Short-lived | 1h | Low | memory only |
| Refresh token (from bank/EB) | Long-lived | ≤90d | **HIGH** | DB encrypted column (Fernet) or keyring |

Live values live in `~/.config/lifeos/`, never inside this repository. See `SAFETY.md` rule 2.

### Storage verdicts

| Approach | Verdict |
|---|---|
| `.env` (600) | **Baseline for dev only** |
| Docker secrets | Skip — Swarm-oriented, awkward in Compose |
| **OS keyring (libsecret/gnome-keyring)** | **Recommended default** — encrypted at rest, session-bound, no file to manage |
| SOPS/age file in git | Good fallback if keyring is unavailable headless |
| Vault / Doppler | **Upgrade trigger** — multi-device or team use. Overkill for one user |

### Bank credentials are never stored

PSD2 uses OAuth2 Authorization Code with a redirect to bank SCA. The app never receives
passwords, PINs, or eIDAS certificates. A compromise of the app does not yield transferable
bank authority.

**Architectural rule, regardless of provider:** if a bank ever appears that lacks a redirect
flow, **refuse to implement credential-based fallback — fail loud.** Do not add a credential
fallback path.

### Rotation & revocation

| Action | Procedure |
|---|---|
| Revoke consent | Bank UI → "connected apps" → revoke. App: `DELETE /sessions/{id}` + purge local row + token |
| Rotate EB app JWT key | New RSA keypair → upload cert → update keyring → deploy. Old key invalid immediately |
| Machine lost | Revoke all consents → rotate app key → re-encrypt backups → reprovision |

### ⚠️ Unverified claim — do not treat as settled

The original research asserted that **NL banks (Rabobank, Revolut, ABN AMRO, ING) all support
redirect/OAuth and that no credential fallback exists for the NL market.** This was marked
CONFIRMED but **was never verified against Enable Banking's ASPSP registry.**

Enable Banking does support `decoupled` and other auth methods, and whether any NL bank uses
them is exactly what `GET /aspsps?country=NL` reports via its `auth_methods` field.

**Treat as LIKELY, not confirmed. Verify during the real consent flow.** The architectural
mitigation above is unchanged and is the right response either way.

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
| `docs/research/aspsps-NL-sandbox-2026-09-30.json` | Raw sandbox response (evidence) |
| `docs/adr/0002-import-provider-enum.md` | The v1 provider list, incl. the `/aspsps?country=NL` go/no-go |
| `SAFETY.md` | **Read before any destructive command** |

## Beads

| Bead | What |
|---|---|
| `LifeOS-1` | ASPSP probe — **CLOSED**, result recorded |
| `LifeOS-13` | M8 Revolut — conditional on this |
| `LifeOS-16` | Safety incident — keygen moved out of repo, rm_guard shipped |
| `LifeOS-17` | **Production app activation — this document's work item** |
