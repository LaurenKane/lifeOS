# Research 06 — Security & Privacy Model

**Lane:** ora-2 (oracle, Specialist D) · **Completed:** 2026-09-30 · **Status:** reconciled
**Mode:** read-only advisory. Self-labelled DRAFT; several items were flagged as needing confirmation
from the open-banking research. That cross-check has now been done — see reconciliation.
> Orchestrator reconciliation notes in the final section. **Read those — they correct real errors.**

---

## 1. Threat model

| Threat | Asset | Likelihood | Impact | Mitigation | Layer |
|---|---|---|---|---|---|
| Malicious local user, same machine | DB, secrets, raw payloads, logs | Medium | High | LUKS FDE; dedicated unprivileged user; DB perms 600; secrets in OS keyring or age file; nothing world-readable | Host/App |
| Stolen laptop / stolen backup | All data at rest | Low–Med | Critical | LUKS FDE + age-encrypted restic backups; backup key stored separately; rotate app JWT key on loss | Host/Backup |
| Compromised dependency (PyPI/npm/Docker Hub) | RCE, exfiltration | **Medium — AI agents add deps** | Critical | Pinned lockfiles, digest-pinned base images, `pip-audit`/`npm audit`, Dependabot, SBOM, **agent rule: no new runtime dep without human approval** | Build/CI |
| Compromised Enable Banking | Transaction data, session_ids | Low | High | Revoke consent via bank UI; monitor EB request logs | Vendor |
| Network attacker (LAN/Tailscale) | API traffic, cookies, tokens | Medium if exposed | High | **Default localhost only**; if exposed → Tailscale + TLS, secure cookies, CSRF, rate limiting, no unauthenticated endpoints | Network |
| Future accidental public deployment | Everything | Low | Critical | AuthN required, no debug endpoints, no telemetry, secrets never in DB, egress allowlist, CSP, HSTS | App/Infra |
| XSS in frontend | Transaction PII, balances | Medium | High | CSP `default-src 'self'` / `script-src 'self'`, no `unsafe-inline`; sanitize user content; HttpOnly cookies; no `dangerouslySetInnerHTML` | Frontend |
| CSRF on ingestion | Unauthorized import | Medium | Medium | `SameSite=Strict`; CSRF token on all mutating endpoints; ingestion requires valid session | App |
| Bank-side compromise / PSD2 phishing | Bank credentials | Low | Critical | PSD2 guarantee — app never sees credentials; redirect flow only | Protocol |
| Physical disk access | DB, backups, logs | Low | Critical | LUKS FDE; **no app-level column encryption** (breaks dedup/query) | Host |
| Accidental loss / ransomware | Ledger integrity | Medium | High | Daily restic to offsite, encrypted; **monthly restore test**; 90d retention; WAL archiving for PITR | Ops |

### Key security property

> **PSD2 credentials are never available to the app, and a compromise of the app does not yield
> transferable bank authority.**

**Verdict: holds.** AIS uses OAuth2 Authorization Code with redirect to bank SCA. The app never receives
passwords, PINs, or eIDAS certificates.
**Residual risk:** long-lived session/consent authority + Enable Banking's own access to the data.

---

## 2. Credential & token handling

### What Enable Banking requires us to store (as reported by this lane)

| Credential | Type | Lifetime | Sensitivity | Storage |
|---|---|---|---|---|
| RSA private key (4096) | **Long-lived app credential** | Years | **CRITICAL** — signs all API JWTs | OS keyring (libsecret) or age file (0600) |
| Application ID (`kid`) | Public identifier | Permanent | Low | config/env |
| `session_id` (per consent) | Consent handle | ≤180d | Medium | DB (protected by FDE) |
| Access token (to EB) | Short-lived | 1h | Low | memory only |
| Refresh token (from bank/EB) | Long-lived | ≤90d | **HIGH** | DB encrypted column (Fernet) or keyring |

### Secret storage options

| Approach | Verdict |
|---|---|
| `.env` (600) | **Baseline for dev only** |
| Docker secrets | Skip — Swarm-oriented, awkward in Compose |
| **OS keyring (libsecret/gnome-keyring)** | **Recommended default** — encrypted at rest, session-bound, no file to manage |
| SOPS/age file in git | Good fallback if keyring is unavailable headless |
| Vault / Doppler | **Upgrade trigger** — multi-device or team use. Overkill for one user |

### Bank credentials — never stored

PSD2 redirect flow means the user authenticates at the bank; we receive only an authorization code.
**Claim: NL banks (Rabobank, Revolut, ABN AMRO, ING) all support redirect/OAuth, and no credential
fallback exists in Enable Banking for the NL market.**
**Recommendation:** *if a non-NL bank lacking OAuth is ever added, block credential-based fallback in
code — fail loud.*

### Rotation & revocation

| Action | Procedure |
|---|---|
| Revoke consent | Bank UI → "connected apps" → revoke. App: `DELETE /sessions/{id}` + purge local row + token |
| Rotate EB app JWT key | New RSA keypair → upload cert → update keyring → deploy. Old key invalid immediately |
| Machine lost | Revoke all consents → rotate app key → re-encrypt backups → reprovision |

---

## 3. Data at rest & retention

### PostgreSQL encryption: **LUKS FDE only, no column encryption**

> Dedup must query `description`, `merchant_name`, `amount`. Column encryption breaks indexes, LIKE
> queries, and deterministic dedup. FDE covers stolen disk. Threat model assumes an attacker with DB
> access already has app memory, where the decryption key lives.

**Single exception:** the refresh-token column (a bearer credential).

### Backups

restic + age (or restic's built-in AES-256). 3-2-1 (local + offsite + offline), daily incremental,
weekly full, 90-day retention. **Monthly restore test** — automated restore to a temp DB, verify row
counts and a checksum of the last 100 rows. Backup encryption key stored separately from app secrets.

### Raw payload storage

| Payload | Location | Retention |
|---|---|---|
| EB raw JSON (accounts, transactions) | `raw_payloads` (JSONB) | 7 years |
| Imported CSV/XLSX/PDF | `imports/` mounted volume | 7 years |
| Google Wallet export | `imports/google_wallet/` | 1 year |

All inside the Docker volume → covered by host LUKS.

### Logging policy — NEVER log

- Full IBAN / account numbers (**log last 4 only**: `NL**1234`)
- Any token (JWT, refresh token, session_id, auth code)
- RSA private key or keyring contents
- Full raw payloads (log payload hash + size only)
- Merchant name + amount together in structured logs
- User PII (name, address, BSN)

**Allowed:** request ID, timestamp, endpoint, status, latency, error class (no detail), payload SHA-256.

---

## 4. PII / sensitive data

**GDPR (pragmatic):** the household/personal exemption likely applies to purely personal, non-commercial
processing. Obligations that remain: data minimisation, storage limitation, Art. 32 security, 72h breach
notification. Document lawful basis and a retention schedule.

**Redaction:** last-4 IBAN in logs; never return full IBAN from list endpoints; masked in the dedup UI.

**Future LLM categorization — HARD CONSTRAINT:**
1. Explicit **opt-in**, per session, in the UI.
2. **Strip** IBAN, account numbers, merchant names before sending.
3. **Local model by default** (Ollama/llama.cpp); cloud API only behind a second opt-in with a warning.
4. **Never send raw transaction text** to an external LLM.

**Export / deletion:** `/api/export` → JSONL + CSV. `/api/admin/purge-all` → hard delete rows, wipe
`imports/`; document honestly that encrypted backups retain data per the retention schedule.

---

## 5. Network & exposure

| Posture | When | Config |
|---|---|---|
| **localhost only** | **Default, always** | `uvicorn --host 127.0.0.1` |
| Tailscale | Remote/mobile access wanted | `tailscale serve --https=443`, ACL tag `tag:lifeos` |
| LAN + reverse proxy | No Tailscale, trusted LAN | Caddy/Traefik + ACME |
| Public with auth | **Never recommended** | If forced: Authelia/Keycloak + rate limit + WAF + audit |

**Escalation only if** remote access is explicitly wanted *and* Tailscale is accepted.

### App authN/AuthZ — single user

**Simplest safe thing: session cookie + CSRF, no user table.**
- First run: generate `APP_SECRET` (32 bytes) → keyring → sign session cookies.
- Localhost: no login needed. Tailscale: rely on tailnet ACL + cookie.
- **Future health data raises the bar** → add Argon2id + TOTP at that point.

### If exposed

TLS via Caddy/Tailscale certs. Cookies `Secure; HttpOnly; SameSite=Strict; Path=/`. Double-submit CSRF
cookie + `X-CSRF-Token` header on all POST/PUT/DELETE. Rate limit 60 req/min/IP on `/auth/*`, `/import/*`,
`/api/ingest/*`. **No unauthenticated writes.**

### Egress

Allowlist `api.enablebanking.com:443`, `*.enablebanking.com`, plus bank auth domains.
**File imports must work with zero egress** — test in CI with a network namespace.

---

## 6. Supply chain

Lockfiles committed; digest-pinned base images (`FROM python:3.12-slim@sha256:...`); `pip-audit`,
`npm audit --audit-level=high`, `trivy fs .` on every PR; SBOM via `syft`; **CI gate: no lockfile change
without an `APPROVED: <reason>` line in the PR body.**
**Never mount `/var/run/docker.sock` into the app container**; if needed, use a least-privilege
socket-proxy sidecar.

---

## 7. Security baseline checklist (P0/P1/P2)

| # | Pri | Action | Done when |
|---|---|---|---|
| 1 | P0 | Generate RSA 4096 keypair; store private key in keyring; upload cert to EB | `secret-tool lookup key pem` returns it |
| 2 | P0 | Run app as non-root (UID 1000); DB volume owned by same UID | `docker compose exec app id` → uid=1000 |
| 3 | P0 | LUKS FDE on host | `cryptsetup status` active |
| 4 | P0 | restic + age offsite backups; test restore | snapshots exist; restore script passes |
| 5 | P0 | Logging filter redacts IBANs/tokens/payloads | `grep -rE "NL[0-9]{2}[A-Z]{4}" logs/` clean |
| 6 | P0 | CSP + HSTS + secure cookies + CSRF on mutating routes | headers present; CSRF fails without token |
| 7 | P0 | Hash-pinned lockfiles, `npm ci`, Dependabot | only lockfiles change on dep update |
| 8 | P0 | Digest-pin base images; `trivy` in CI | `FROM python@sha256:...`; trivy passes |
| 9 | P1 | Encrypt refresh-token column (Fernet, key in keyring) | DB dump shows ciphertext — **see reconciliation** |
| 10 | P1 | Consent revocation: `DELETE /sessions/{id}` + local purge | integration test passes |
| 11 | P1 | EB app key rotation script | script runs; old key rejected |
| 12 | P1 | Tailscale ACL for `tag:lifeos` if remote access needed | access works from phone |
| 13 | P1 | Egress allowlist (nftables/ufw or container network policy) | only allowed hosts permitted |
| 14 | P1 | Monthly restore-test cron + failure alert | cron log shows success |
| 15 | P1 | Write `SECURITY.md` secrets policy | file exists, reviewed |
| 16 | P2 | `pip-audit`/`npm audit` in CI; fail on HIGH/CRITICAL | CI fails on vuln |
| 17 | P2 | SBOM per build | `syft` runs |
| 18 | P2 | LLM opt-in guard (stub) | no `openai`/`anthropic` imports |
| 19 | P2 | `/api/export` | returns JSONL + CSV |
| 20 | P2 | `/api/admin/purge-all` with confirmation header | requires `X-Confirm: PURGE_ALL` |
| 21 | P2 | Verify no credential fallback for NL banks | written confirmation |
| 22 | P2 | Confirm refresh-token TTL for Rabobank/Revolut | documented |
| 23 | P2 | CSP `report-uri` | reports land in log |
| 24 | P2 | Amateur pen test (OWASP ZAP) against local instance | no HIGH findings |
| 25 | P2 | `PRIVACY.md` with lawful basis + retention | file exists |

---

## 8. Architecture requirements this imposes

1. **No unauthenticated write endpoints** — every POST/PUT/DELETE needs session + CSRF.
2. **Ingestion idempotent and auditable** — `import_id` (ULID) per import; re-import is a no-op; audit row per import.
3. **Raw payload access behind a distinct permission** — not exposed via the transaction API.
4. **Secrets never enter the database** — RSA key, backup key in keyring/age file. — **partly moot, see reconciliation**
5. **App must run with zero egress for file-only operation** — `docker run --network=none` must allow import + dedup + UI.
6. **No telemetry, verified by an egress test** — CI runs `--network=none` + `strace -e network`; zero `connect()` calls.
7. **Single-user session model** — no user table; cookie signed by `APP_SECRET` in keyring.
8. **Dedup queries plaintext** — `description`, `merchant_name`, `amount` must be indexable; no column encryption there.
9. **Refresh-token encryption at rest** — **moot, see reconciliation**
10. **Consent lifecycle tracked** — `consents` table: `session_id`, `aspsp`, `created_at`, `expires_at`, `revoked_at`, `status`; warn 30d before expiry.
11. **EB request logging** — request ID, ASPSP, latency, status; never payload or tokens.
12. **CSP without `unsafe-inline`** — Vite hashed scripts; `script-src 'self' 'sha256-...'`.
13. **Health-data readiness** — auth designed to accept Argon2id + TOTP later without a schema migration.
14. **Backup key separate from app secrets** — age key ≠ Fernet key ≠ EB RSA key.
15. **Dependency review gate** — CI fails on an unapproved lockfile change.

---

# Reconciliation & challenges (orchestrator)

## 1. ⚠️ CORRECTION — there are **no refresh tokens and no access tokens** in this integration

This lane assumed a token-based credential model. **Research 01 (Enable Banking docs, CONFIRMED) shows
otherwise:**

| This lane assumed | Reality (CONFIRMED) |
|---|---|
| "Access token (to EB), 1 hour, store in memory" | **There is no such token.** The "access token" *is the RS256 JWT we generate ourselves* from our own RSA key. Nothing is received. |
| "Refresh token (from bank/EB), ≤90d, **encrypt in DB**" | **No refresh token is ever issued to us.** Enable Banking abstracts ASPSP token refresh internally. We only ever hold a `session_id`. |
| Checklist #9 "encrypt refresh_token column" | **Void — no such column will exist.** |
| Checklist #18 LLM stub / #22 refresh-token TTL | **Void.** |
| Arch. requirement 4 "secrets never enter the DB" | Still true, but the reason changes. |
| Arch. requirement 9 "refresh-token encryption at rest" | **Void.** |

**The real credential inventory is much smaller and cleaner:**

| Secret | Where it lives |
|---|---|
| RSA private key (signs our JWTs) | **OS keyring or age file, 0600. Never in the DB.** |
| `app_id` (JWT `kid`) | config, non-secret |
| `session_id` per consent | DB — an opaque handle, revokable at the bank. Not a bearer credential with a TTL. |
| DB password, `APP_SECRET` | keyring / age file |

**Consequence:** the credential-attack surface is **one** long-lived secret (our RSA key), not three.
That makes the security posture *better* than this lane concluded, and it makes "rotate the RSA key"
the single most important rotation operation. BankingSync's decision to store the key unencrypted in
SQLite (Research 04) is therefore a much worse anti-pattern than it first appears.

## 2. ⚠️ "No credential fallback for NL banks" is over-confident

Marked CONFIRMED, but it was not verified against Enable Banking's ASPSP registry. Enable Banking does
support `decoupled` and other auth methods, and whether any NL bank uses them is exactly what
`GET /aspsps?country=NL` reports via the `auth_methods` field.
**Downgrade to LIKELY; verify during the real consent flow.** The architectural mitigation is unchanged
and is the right response either way: **refuse to implement or accept credential-based bank auth in code.**

## 3. GDPR framing is muddled — the cleaner statement

This lane says "in scope, but the household exemption likely applies." That is internally inconsistent.
The accurate position: **GDPR does not apply at all** to processing by a natural person for purely
personal purposes (Recital 7). So there is no lawful-basis analysis to do. What remains genuinely
worth doing is not a legal compliance exercise but **data minimisation and retention discipline** —
which this lane already got right (last-4 IBAN in logs, 7-year financial retention, honest backup-deletion
disclosure). Reframe it that way; it is simpler and true.

## 4. ✅ This lane **wins** the auth dispute against ora-3

| ora-3 (Research 03) | ora-2 (here) | Resolution |
|---|---|---|
| "Build `core.auth` with argon2 + JWT + TOTP. 200 lines." | "Session cookie + CSRF, no user table. Add Argon2id + TOTP only when health data lands." | **ora-2 is right.** A single-user localhost/Tailscale app does not need a user table, and a hand-rolled auth system is a *worse* security outcome than no auth system at all. Defer until there is a second user or health data. |

**Open conflict #3 from the state file is now resolved in favour of ora-2.**

## 5. ✅ The FDE decision is a genuine cross-lane win

ora-2 independently arrived at "LUKS FDE, **no column encryption**" for precisely the reason the
data-model lane will care about: **dedup must query and index `description`, `merchant_name`, and
`amount` in plaintext.** Two lanes, one constraint, one answer. This is a *settled* decision — do not
reopen it.

## 6. Keep these three ideas — they are unusually good

1. **The zero-egress test as a CI check** (`--network=none` + `strace`, asserting zero `connect()`
   calls). Concrete, automatable, and it simultaneously proves "no telemetry" and "file imports work
   offline." Best single idea in this report.
2. **Pinning the dedup-relevant columns as non-encryptable** as an explicit architectural requirement —
   it stops a future well-meaning "let's encrypt everything" change from silently breaking the ledger.
3. **"No new runtime dependency without human approval," enforced in CI.** Given the user vibe-codes
   with AI agents, an agent adding a random PyPI package is a genuine and under-appreciated risk channel.

## 7. Minor: verify the egress allowlist domains

`oauth.rabobank.nl` and `oba-auth.revolut.com` are plausible but unverified. Do not hardcode a domain
list before the real redirect flow has been observed. Prefer an allowlist built from observed traffic.

## 8. Over-engineered for a single-user personal app (flagged, not rejected)

Items 23–25 (CSP `report-uri`, OWASP ZAP pen test, `PRIVACY.md`) are P2 and arguably not worth the
attention budget. Items 1–15 are proportionate. The report is not bloated; it is correctly ordered.
