# Research 01 — Enable Banking, Dutch PSD2, and Alternatives

**Lane:** lib-3 (librarian) · **Completed:** 2026-09-30 · **Status:** reconciled
**Confidence labels used throughout: CONFIRMED / LIKELY / UNCERTAIN / NEEDS USER TESTING**

---

## Executive summary

- **Enable Banking is viable for a personal self-hosted app.** ToS explicitly permits "personal use of private individuals" in the Production Environment, free of charge, no contract required, when linking your own accounts (**restricted mode**). CONFIRMED.
- **Auth is RS256 JWT**, not HS256. Self-signed cert + RSA private key. Sandbox auto-activates; production restricted mode activates by linking your own accounts (no KYB).
- **Rabobank: CONFIRMED** present in the NL sandbox ASPSP list. **Revolut NL: UNCERTAIN** — not in the sandbox credential list; must verify against `GET /aspsps?country=NL`.
- **Transaction identity — the single most important finding:** `entry_reference` is the dedup key (unique per account, immutable). `transaction_id` is explicitly **not** stable and is only for detail-fetch.
- **`entry_reference` is usually only present on `BOOK` (booked) transactions.** This means pending→booked correlation **cannot** rely on a provider-supplied ID. It must be content/heuristic-based.
- **Status enum:** `BOOK, CNCL, HOLD, OTHR, PDNG, RJCT, SCHD`. `PDNG` = pending (ISO 20022 Expected).
- **Pagination** uses `continuation_key`, **not** offset/cursor. All other query params must stay identical across pages. The key is **session-bound only**.
- **History depth:** most ASPSPs give ~1 hour of full history in the first hour post-auth, then clamp to ~90 days. Some give 1–3 years. Use `strategy=longest` for initial backfill.
- **Consent validity** typically **180 days** (`maximum_consent_validity`, e.g. 15552000s). **No refresh token** — the *consent* expires and must be re-authorized. Error: `EXPIRED_SESSION` (401).
- **Rate limits are ASPSP-driven, not Enable-Banking-driven.** 429 = `ASPSP_RATE_LIMIT_EXCEEDED`. Many ASPSPs allow only **~4 background fetches/day** when PSU headers are absent.
- **Fallback if Enable Banking fails:** GoCardless Bank Account Data (ex-Nordigen) — free tier, self-serve, NL coverage.

---

## Enable Banking: capabilities

| Capability | Detail |
|---|---|
| Base URL | `https://api.enablebanking.com` (deprecated: `https://api.tilisy.com`) |
| Environments | `SANDBOX` (auto-activated), `PRODUCTION` (restricted or unrestricted) |
| Regulatory | Enable Banking Oy (Espoo, FI), FIN-FSA authorized AISP, EBA register ID `FI_FIN_FSA!29884997` |
| Storage | Does not store or cache account data |
| Certification | ISO 27001 |
| SDKs | **No official SDKs.** Community examples: `EnableBankingPythonExamples`, `enablebanking-api-samples` (MIT, varies) |
| UI | `<enablebanking-consent>` / `<enablebanking-auth-flow>` web components |
| Webhooks | **Payment status only — no AIS/transaction webhooks.** So sync must be polled. |

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/aspsps?country=NL` | List available banks (+ `maximum_consent_validity`, `auth_methods`, `required_psu_headers`) |
| POST | `/auth` | Start authorization → returns redirect URL |
| POST | `/sessions` | Exchange `code` → `session_id` + accounts |
| GET | `/sessions/{session_id}` | Session status |
| DELETE | `/sessions/{session_id}` | Close session **and** the bank consent |
| GET | `/accounts/{uid}/details` | Account metadata |
| GET | `/accounts/{uid}/balances` | Balances (types `CLBD`, `ITAV`, `XPCD`, `CLAV`) |
| GET | `/accounts/{uid}/transactions` | Transactions (paginated) |
| GET | `/accounts/{uid}/transactions/{transaction_id}` | Single transaction detail |
| GET | `/application` | App info from JWT |

**Query params on `/transactions`:** `date_from`, `date_to` (date-only, UTC, inclusive), `continuation_key`, `transaction_status`, `strategy` (`default` | `longest`).

---

## Auth & consent lifecycle

1. Register app in Control Panel → choose environment. Sandbox auto-activates; Production starts *pending*.
2. Generate RSA key (`openssl genrsa -out private.key 4096`; browser SubtleCrypto also works). Self-signed cert: `openssl req -new -x509 -days 365 -key private.key -out public.crt`.
3. Upload cert → receive `app_id` (UUID) = JWT `kid`.
4. **Every request** = RS256 JWT:
   - header `{"typ":"JWT","alg":"RS256","kid":"<app_id>"}`
   - payload `{"iss":"enablebanking.com","aud":"api.enablebanking.com","iat":now,"exp":now+3600}` (max TTL 86400s)
5. `POST /auth`:
   ```json
   {"access":{"valid_until":"2026-12-01T00:00:00Z"},
    "aspsp":{"name":"Rabobank","country":"NL"},
    "state":"<uuid>","redirect_url":"https://yourapp/callback",
    "psu_type":"personal"}
   ```
   → `{"url":"https://auth.enablebanking.com/ais/start?sessionid=..."}`
6. Redirect user. Enable Banking ToS page → bank's SCA flow.
7. User authenticates at the bank.
8. Bank redirects to `redirect_url?code=...&state=...` (or `?error=access_denied`).
9. `POST /sessions` with `{"code":"..."}` → `session_id` + `accounts[]` (each with `uid`, **`identification_hash`**, IBAN).
10. Fetch using `account.uid`.

### Consent lifecycle notes (design-critical)

- `access.valid_until` ≤ `maximum_consent_validity` (typically 180 days).
- **New session ⇒ new `session_id` and new account `uid`s.** Link accounts across sessions by **`identification_hash`**, not `uid`. This is essential — a naive design keyed on `uid` will orphan every account at the first re-auth.
- On expiry: `EXPIRED_SESSION` 401 → must re-run the whole auth flow.
- Premature expiry can occur (ASPSP KYC, cert migration, single-session-per-PSU limits).
- `DELETE /sessions/{id}` revokes.

---

## Transaction identity & pending→booked (feeds the dedup algorithm)

| Field | Stability | Use for dedup? |
|---|---|---|
| `entry_reference` | Unique + immutable per account (per `identification_hash`). **Usually only present on `BOOK`.** | **YES — primary key** |
| `transaction_id` | **May change between list retrievals.** Detail-fetch only. | **NO** |
| `identification_hash` (account) | Stable across sessions for the same account | Use to match accounts across sessions |
| `merchant_category_code` | ISO 18245, present on some | Enrichment only |

### What changes on pending→booked

| Field | PDNG → BOOK |
|---|---|
| `status` | `PDNG` → `BOOK` |
| `booking_date` | null → populated |
| `value_date` | may change |
| `entry_reference` | **may appear only after booking** |
| `balance_after_transaction` | may be null when pending |
| `creditor`/`debtor` name | may be enriched after booking |

### Consequence for our design — CRITICAL

> Provider IDs **cannot** be relied on for pending→booked correlation, because the ID typically only materialises at booking time.
>
> Therefore the pending→booked match must be **content/heuristic based**: `(account, amount, currency, direction, booking date within ±N days, counterparty name similarity)`. Store pending rows in a *separate staging concept* (or a `status` column with a partial unique index) and promote/merge on booking. Anything below a confidence threshold goes to a **user review queue**, never an automatic guess.
>
> Some ASPSPs *do* supply `entry_reference` on `PDNG` and keep it stable — detect this at runtime rather than assuming either way. If `entry_reference` is present on both the pending row and the booked row and they are equal, that is a Tier-1 exact match.

### Also note

- `entry_reference` is **not globally unique** — always scope by account.
- No `entry_reference` at all: some ASPSPs omit it. Enable Banking may synthesise references on the fly, but **synthesised refs are not stable across retrievals** — never dedup on a synthetic value.

---

## Limits, pricing, history depth

| Item | Value |
|---|---|
| Sandbox | Free, auto-activated |
| Production **restricted** | **Free.** Link your own accounts; only those accounts accessible; no contract, no KYB, no privacy-policy URL required |
| Production **unrestricted** | Contract + KYB + privacy policy URL + terms URL + DP email. Volume-based, "minimum invoicing per month" |
| Rate limits | No documented per-second quota. 429 = `ASPSP_RATE_LIMIT_EXCEEDED` (the *bank* is limiting, not Enable Banking) |
| Background fetch limit | **~4/day per ASPSP** when PSU headers absent. Supply **all** `Psu-*` headers or **none** |
| History, first hour | Full (varies by ASPSP) |
| History, after ~1h | Commonly clamped to **90 days**; some ASPSPs 1–3 years |
| `strategy=longest` | Fetches from earliest available; `date_to` ignored; suppresses `WRONG_TRANSACTIONS_PERIOD` |
| `strategy=default` | Returns `WRONG_TRANSACTIONS_PERIOD` if range unavailable |
| Pagination | `continuation_key`; keep all other params identical; **session-bound** |
| Balances | No historic balances — only `balance_after_transaction` per transaction |

> **Backfill warning:** because history clamps to ~90 days shortly after authorization, the *initial* pull should happen immediately at consent time using `strategy=longest`, and be re-run right after each re-auth. This is a product-visible behaviour, not a nicety.

---

## Personal-use / licensing verdict

ToS last updated **2026-01-09**.

| Question | Answer |
|---|---|
| Individual, private, self-hosted app? | **YES** — "personal use of private individuals" expressly permitted |
| Contract needed? | **NO** in restricted mode |
| Store PSD2 data? | **YES** — no prohibition on storing it in our own app |
| Derive / combine data? | **YES** — no restriction |
| Access other people's accounts? | **NO** — only the Control Panel user's own accounts |
| Business use? | **PROHIBITED** without separate agreement |
| Redistribution / sublicensing? | **PROHIBITED** |
| Liability cap | EUR 100 aggregate |
| Governing law | Finnish law, arbitration Helsinki |

**Activation path for us (restricted mode):** register app → "Activate by linking accounts" → complete auth flow with our own Rabobank account → done. No contract, no KYB.

**Gotchas:** no categorization; no historic balances; **no AIS webhooks** (must poll); must handle `EXPIRED_SESSION`; ASPSP flakiness in both environments.

---

## Dutch institution support

| Institution | Sandbox | Production |
|---|---|---|
| **Rabobank** | ✅ CONFIRMED | **LIKELY** — verify via `/aspsps?country=NL` |
| **Revolut NL** | ❌ not in sandbox credential list | **UNCERTAIN** — verify via `/aspsps?country=NL` |

**How to verify (do this early — it is a go/no-go for the Revolut path):**
```bash
curl -H "Authorization: Bearer <RS256 JWT>" \
  "https://api.enablebanking.com/aspsps?country=NL&psu_type=personal"
```
Look for `name: "Rabobank"` and `name: "Revolut"`. The response also carries `maximum_consent_validity`, `auth_methods`, `beta`, `required_psu_headers`.

---

## Alternatives comparison

| Provider | Active 2026 | Rabobank NL | Revolut NL | Self-serve | Personal-use OK | Cost | Notes |
|---|---|---|---|---|---|---|---|
| **Enable Banking** | ✅ | ✅ LIKELY | ⚠️ UNCERTAIN | ✅ | ✅ YES (restricted) | **Free (personal)** | Best fit. ToS explicitly allows personal use |
| **GoCardless Bank Account Data** (ex-Nordigen) | ✅ | ✅ | ✅ | ✅ | ✅ | Free tier (~25 accounts), then ~€0.20/acct/mo | **Strongest fallback** |
| Tink | ✅ (Visa-owned) | ✅ | ✅ | ❌ sales-only | ❌ enterprise | Custom | No self-serve |
| Yapily | ✅ | ✅ | ✅ | ✅ sandbox | ⚠️ LIKELY | Free sandbox, paid prod | UK |
| Salt Edge | ✅ | ✅ | ✅ | ✅ | ⚠️ LIKELY | Free trial, then paid | PSD2-focused |
| TrueLayer | ✅ | ✅ | ✅ | ✅ portal | ⚠️ LIKELY | Free sandbox, paid EU | UK, good EU coverage |
| Plaid EU | ✅ | ⚠️ UNCERTAIN | ⚠️ UNCERTAIN | ✅ | ⚠️ LIKELY | Pay-per-use | Verify NL coverage |

⚠️ The alternatives' coverage/pricing rows are based on known 2025–2026 status and were **not** independently re-verified this session. Treat as **LIKELY** and re-verify directly before relying on them.

**Fallback recommendation:**
1. **Primary** — Enable Banking, restricted mode. Free, ToS-friendly, Rabobank confirmed.
2. **Fallback 1** — GoCardless Bank Account Data. Self-serve, free tier, strong NL coverage. Best answer to "if Enable Banking becomes unavailable/unsuitable."
3. **Fallback 2** — Yapily or TrueLayer (both have developer sandboxes).
4. **Avoid** — Tink (enterprise-only). Plaid EU (uncertain NL coverage).

---

## Fact confidence

| Claim | Confidence | Source |
|---|---|---|
| RS256 JWT auth, app registration, env model | CONFIRMED | docs/api/reference |
| Consent flow `POST /auth` → redirect → `POST /sessions` | CONFIRMED | docs/api/quick-start |
| Status enum `BOOK, CNCL, HOLD, OTHR, PDNG, RJCT, SCHD` | CONFIRMED | OpenAPI spec |
| `entry_reference` is the dedup key; `transaction_id` unstable | CONFIRMED | FAQ |
| `entry_reference` typically only on `BOOK` | CONFIRMED | FAQ / OpenAPI |
| Pagination via `continuation_key` | CONFIRMED | FAQ |
| History ~1h full then 90d | CONFIRMED | FAQ |
| Consent ~180d, no refresh token | CONFIRMED | FAQ |
| 429 `ASPSP_RATE_LIMIT_EXCEEDED`, ~4/day background | CONFIRMED | FAQ |
| Free for personal use, restricted mode | CONFIRMED | terms (2026-01-09) |
| Rabobank in sandbox | CONFIRMED | docs/api/sandbox |
| Revolut NL support | **UNCERTAIN** | absent from sandbox list |
| GoCardless fallback | LIKELY | needs re-verification |
| Tink enterprise-only | LIKELY | needs re-verification |
| Data residency FI, ISO 27001, no storage | CONFIRMED | terms / privacy |

## Version & freshness

| Item | Value |
|---|---|
| ToS last updated | **2026-01-09** |
| Docs accessed | 2026-09-30 |
| OpenAPI spec | `enablebanking.com/docs/api/reference/enablebanking-api.yaml` (current) |
| `enablebanking-api-samples` last activity | 2026-09-17 |
| `OpenBankingPythonExamples` last activity | 2026-06-16 |
| Sample repo licence | MIT (varies per repo) |
| Enable Banking Oy | Finnish FIN-FSA AISP, ISO 27001 |

---

## Actions this creates for later phases

1. **NEEDS USER TESTING** — `GET /aspsps?country=NL` for Rabobank + Revolut. Gate the Revolut ingestion lane on this.
2. Design dedup so `entry_reference` is **optional**, and pending→booked works heuristically when it is absent.
3. Key account links on `identification_hash`, never `uid` (uid rotates at re-auth).
4. Backfill aggressively at consent time; clamp to ~90d means history is lost if we wait.
5. Sync must be **polled** (no AIS webhooks). Respect ~4 background fetches/day per ASPSP unless PSU headers are sent.
6. Design for `EXPIRED_SESSION` re-auth as a normal, expected user flow.

---

## Addendum — Control Panel setup specifics (verified 2026-09-30, bead LifeOS-1)

Verified against Enable Banking's own docs while the user was registering an app.

### ⚠️ The sandbox ASPSP list is a FILTERED list — a negative result proves nothing

This is the single most important consequence. Enable Banking states plainly:

> *"After you register a sandbox application, you will get access to a limited number of ASPSPs'
> sandboxes… Enable Banking does not aim to provide access to a large number of ASPSPs' sandboxes"*
> — [docs/api/sandbox](https://enablebanking.com/docs/api/sandbox)

And the ASPSP list carries a **`sandbox` boolean attribute** that the Control Panel widget uses to filter
the displayed banks ([docs/api/widgets](https://enablebanking.com/docs/api/widgets)).

**Consequence:** *"Revolut is absent from the sandbox ASPSP list"* is **NOT** evidence that Revolut is
unsupported in production. It only means Revolut has no PSD2 test sandbox on Enable Banking. A false
negative is entirely possible, and acting on one would wrongly push us to a Revolut CSV importer when an
API path may exist.

| Environment | Use it for | Authoritative? |
|---|---|---|
| **Sandbox** | Learning the mechanics, confirming Rabobank, running the probe for free | **No** — filtered list |
| **Production (restricted mode)** | The actual Revolut answer | **Yes** |

Restricted mode is free per the ToS and needs no contract — it activates by linking your own accounts.
**So: if sandbox comes back negative for Revolut, the correct next step is activating production
restricted mode, not concluding Revolut is unavailable.**

### ✅ Browser-generated keys ARE exported to disk (correction)

I initially flagged that a browser-generated key might be trapped in the page and unusable. **That was
wrong.** Enable Banking's own quick-start states:

> *"Your web browser will generate a private key for the application and it will be saved into your
> downloads folder. The file name will be the ID that was assigned to the newly registered application
> (e.g., `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.pem`)"*
> — [docs/api/quick-start](https://enablebanking.com/docs/api/quick-start)

So **whichever route was used, the private key is on disk** and can sign JWTs. The filename is the
`app_id`, which is a convenient way to recover the app_id if it was not noted elsewhere.

Requirements (documented): RSA, 4096-bit recommended, self-signed X.509 certificate uploaded as PEM.
**Enable Banking never needs to see the private key** — it is used only to sign JWTs locally.

### ⚠️ Redirect URL matching is UNDOCUMENTED

Enable Banking documents only: *"Enter URLs whitelisted for redirecting of end users after they
complete authorisation"* ([docs/api/quick-start](https://enablebanking.com/docs/api/quick-start)). It
publishes **no** matching rules, no wildcard support, and no length limits.

**Documented:** you register redirect URLs in the Control Panel, and the `redirect_url` in `POST /auth`
must match one of them.

**Inference, not fact:** that matching is exact on scheme + host + port + path, that trailing slashes
are significant, and that wildcards are unsupported. Standard across Open Banking platforms and the safe
assumption, but **unverified** — the only way to know is to try a consent flow.

For the `/aspsps` probe the redirect URL is **never used** — no consent flow is involved. Register a
placeholder and revisit once **LifeOS-3** (deployment topology) is decided. A Tailscale URL
(`https://<host>/…`) differs from a localhost URL in both scheme and host, so the value **must** change
when the deployment is chosen.

---

## PROBE RESULT — executed 2026-09-30 (bead LifeOS-1, CLOSED)

```
app_id  528ee4b1-cbbe-4116-b552-7e9dd6555fd2   (SANDBOX, Account Information enabled)
GET     https://api.enablebanking.com/aspsps?country=NL&psu_type=personal
result  HTTP 200 — 3 ASPSPs
```

### The full sandbox NL list — only three entries

| Institution | consent validity | auth approach | required PSU headers |
|---|---|---|---|
| **Rabobank** | 15,552,000 s (180 d) | `REDIRECT` | `psu-ip-address` |
| Handelsbanken | 15,552,000 s (180 d) | `REDIRECT` | `psu-ip-address` |
| Mock ASPSP | 15,552,000 s (180 d) | `REDIRECT` | — |

**That is two real Dutch banks, plus a test fixture.** ING, ABN AMRO, bunq, ASN Bank, Regiobank,
Triodos, SNS Bank, de Volksbank and **Revolut** are all absent.

### What this settles

| Claim | Status |
|---|---|
| Rabobank is reachable via Enable Banking in NL | **CONFIRMED** (was LIKELY) |
| Rabobank consent validity = 180 days | **CONFIRMED** (15552000 s) |
| **No credential fallback for Rabobank** | **CONFIRMED** — `approach: REDIRECT` only. The security question from the threat model is answered: we will never see bank credentials. (was LIKELY) |
| PSU headers are **required** for Rabobank | **CONFIRMED** — `psu-ip-address` is mandatory |
| Revolut NL support | **STILL UNCERTAIN** — see below |

### ⚠️ Revolut is *not* answered by this probe, and the reason is the point

The sandbox list contains **two real Dutch banks out of the ~15+ that offer PSD2 in the Netherlands.**
Enable Banking states it "does not aim to provide access to a large number of ASPSPs' sandboxes." A
list this narrow carries essentially no information about production coverage.

**Absent from sandbox is not a verdict.** Revolut's absence is fully explained by Revolut having no
PSD2 test sandbox on Enable Banking, and tells us nothing about production.

**To settle it, the next step is production restricted mode** — free per the ToS, no contract, no KYB,
activated by linking your own accounts. Query the same endpoint there and compare. That is the only
authoritative answer, and it is a few minutes of work with no cost.

### Design consequences now settled

1. **Account type check:** PSU headers are mandatory for Rabobank, so the "supply all `Psu-*` headers
   or none" rule is not optional — omitting them is worse than useless.
2. **Security model is simpler than assumed:** `REDIRECT`-only means no credential path exists to
   accidentally take. Keep the "refuse credential auth in code" guard anyway, as defence in depth.
3. **No architecture change.** The provider-agnostic schema absorbs any answer, including "Revolut
   needs CSV import". Bead **LifeOS-13** (M8) is written to branch on this and remains conditional.

### Evidence

`docs/research/aspsps-NL-sandbox-2026-09-30.json` — the raw response, committed.
