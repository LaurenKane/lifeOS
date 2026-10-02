# Research 02 — American Express Netherlands

**Lane:** lib-1 (librarian) · **Completed:** 2026-09-30 · **Status:** reconciled
**Confidence labels: CONFIRMED / LIKELY / UNCERTAIN / NEEDS USER TESTING**

---

## Executive summary

- **Amex NL is not reachable via PSD2/AIS through ANY major aggregator.** CONFIRMED. Amex itself has stated that EU card accounts outside **UK / France / Sweden / Finland** do not qualify as "payment accounts" under PSD2.
- Enable Banking **decommissioned Amex in Sweden and Finland (March 2025)**; only France remains in the EU.
- Amex's own **Account and Transaction API** exists but requires PSD2-certified AISP status, eIDAS certificates, and manual Amex registration — **not available to individuals** — and is restricted to UK/FR/SE/FI anyway.
- **File import is the only viable path.** Confirmed.
- **CSV export: YES.** ~6 billing periods of history. **Pending transactions are excluded.** **No stable transaction IDs** (Amex IDs are documented to change).
- **PDF statements are better than CSV** for this use case: itemized per transaction, up to **7 years**, and they carry **both transaction date and process date** plus FX detail. CSV has one date and a ~6-month window.
- Consumer portal also offers **OFX/QFX** and **QBO** exports.
- **Google Wallet Takeout does contain transaction-level JSON** for Amex cards (date, amount, currency, merchant, card last4, status), and includes non-Wallet transactions unless the user opts out of "Non-Device Transactions". Useful supplement, **not** a substitute.
- **Deduplicating Google Wallet against the Amex export requires fuzzy matching** (date ±1, amount exact, last4). Merchant strings differ between sources. Deterministic matching is not feasible.
- **Scraping: do not.** Prohibited by Amex ToU, legally risky, no legitimate alternative exists.

---

## 1. API / AIS availability

Amex **does** publish an Account and Transaction API ([developer.americanexpress.com](https://developer.americanexpress.com/products/account-and-transaction-api-public/overview)) that is PSD2-compliant. But:

- It requires **PSD2-certified AISP** status, eIDAS-qualified certificates, manual registration and Amex approval. Not available to an individual or a small personal app.
- Coverage is **UK, France, Sweden, Finland only — not the Netherlands**, even though the marketing copy mentions "Personal, Small Business and Corporate Cards".

**The decisive primary evidence** — Amex's own statement (via Emma Community, 2021-08-17), still operative given the 2025 decommissioning:

> "access to American Express' PSD2 Account Financials suite of APIs can only be granted to EU-authorised or registered TPPs accessing account information pertaining to American Express customers in the UK, France, Sweden, and Finland. Markets outside of the scope of those listed have been in consultation with American Express, and they have communicated that we are not currently legislated in the TPP OpenBanking of PSD2 according to local definition of a payment account."
> — [Emma Community](https://community.emma-app.com/t/cant-connect-amex-icc-euro-basic-card/4554)

### Provider-by-provider

| Provider | Amex NL? | Detail |
|---|---|---|
| Enable Banking | **NO** | Amex not in the NL institution list; SE/FI decommissioned 2025-03, France only remains in EU |
| Yapily | **NO** | Has `amex-ob_uk` and `amex-ob_eu` (NO/FR/SE/FI). No NL |
| TrueLayer | **NO** | UK/FR/SE/FI only. NL coverage is banks, not Amex |
| GoCardless | **NO** | Amex NL absent from institution list |
| Plaid | **NO** | Amex in US/CA/UK. NL = ING, Rabobank, ABN AMRO |
| Salt Edge | **NO** | Claims generic Amex support but absent from its NL coverage page |
| Tink | **NO** | The 2021 Amex–Tink partnership is **income verification / onboarding** (reading the user's *bank* account), NOT reading Amex card transactions |
| Emma | **NO** | UK/US/CA only |
| Rocket Money | **NO** | US only |
| Spendee | **NO** | Norway only |
| Saldo / Knack / InstaCash / Dezeen | Unlikely | No evidence of Amex NL support |

**Conclusion: the architecture must not assume Amex API access will ever become available.** The user's prior assumption is **CONFIRMED as correct**.

---

## 2. File export reality

| Format | Available | Contents | History depth | Notes |
|---|---|---|---|---|
| **CSV** | **YES** | Date, Description, Card Member, Account # (last4), Amount. With "Include all additional transaction details": + Reference, Category, Address, City/State, Zip, Country | **~6 billing periods** (US CONFIRMED, NL LIKELY) | Charges positive, payments/credits negative — **CSV only.** The **PDF carries no sign at all**; see §3a. **Pending excluded.** NL date format likely `DD/MM/YYYY` |
| **XLSX** | **UNCERTAIN** for consumer | Same as CSV | — | Merchant/business portal has XLS. Consumer portal verified as CSV/OFX/QBO; XLSX unverified |
| **PDF** | **YES** | Account Summary, New Credits, New Charges (transaction date, process date, description, amount, **FX detail**), Fees, Interest | **up to 7 years** | Parseable text layer. **Itemized per transaction, not just a payments ledger** |
| OFX/QFX | **YES** | Standard OFX | ~CSV | For Quicken/QuickBooks |
| QBO | **YES** | QuickBooks format | ~CSV | For QuickBooks |

> ### ⚠️ Design-relevant inversion
> The intuition "CSV is the easy path, PDF is the fallback" is **backwards for Amex**. PDF has a **7-year** window and richer fields (two dates, FX amounts, rates); CSV has a **~6-month** window and one date. PDF parsing is likely the *more* valuable importer, not the emergency option. The user's brief listed `AmexPDFImporter` last — it should be treated as a first-class importer, not a stretch goal.

**CSV gotcha:** the **"Include all additional transaction details" checkbox is OFF by default**. Without it the CSV drops Reference, Category, Address, City, Zip, Country. The importer must detect whether the detail columns are present, and the onboarding UI should tell the user to tick it.

---

## 3. Data characteristics for import/dedup

| Field | Present | Stable | Notes |
|---|---|---|---|
| Transaction ID / Reference | Yes (with details checkbox) | **NO** | Amex: "transaction IDs are subject to change" (TrueLayer help, 2024-07) |
| Transaction date | Yes | Yes | Primary date in CSV |
| Posting / process date | **PDF only** | Yes | CSV has a single date |
| Amount | Yes | Yes | **CSV:** single signed column; charges +, payments −. **PDF: no sign in the amount** — direction is a separate `CR` marker line (§3a) |
| Currency | No separate column | — | Converted to card currency (EUR); original amount in PDF |
| Merchant name | Yes (Description) | Yes | Amex-enriched string |
| Category | Yes (with details) | Yes | e.g. "Fees & Adjustments", "Travel" |
| Card Member | Yes | Yes | Distinguishes supplementary cards |
| Account # (last4) | Yes | Yes | |
| MCC | **No** (consumer CSV) | — | Available in Amex @ Work for business cards |
| **Pending** | **NO** | — | Only posted transactions in export |
| Refunds/credits | Yes | Yes | **CSV:** negative amount + description. **PDF:** the amount is positive like a charge; a `CR` line *beneath* it carries the direction (§3a), and the card payment must be separated by description on top of that |
| FX amount/rate | **PDF yes**, CSV partial | — | PDF: original amount + rate + non-sterling fee |
| Purchase vs statement payment | Yes | Yes | Sign convention + description text |

### Consequence for the dedup design — CRITICAL

> Amex is the **only** account in the system with **no provider ID and no pending state**. Therefore:
>
> 1. **Tier-1 (provider ID) dedup is unavailable for Amex.** The *only* mechanism is **Tier-3: a synthesized content fingerprint** plus **occurrence counting** for genuinely identical transactions (two €3.20 coffees the same day).
> 2. Because the CSV window is **~6 months**, the user must **archive exports regularly** or history is permanently lost. This is a real product requirement, not a nicety: a "reminder to export Amex" affordance, and permanent storage of every uploaded export file so the ledger can be **rebuilt from raw** at any time.
> 3. **No pending→booked problem for Amex** (pending is excluded). The pending→booked machinery exists only for Rabobank/Revolut via Enable Banking. Good — this narrows what must be correct.

---

## 4. Google Wallet as an Amex-adjacent source

When an Amex card is in Google Wallet, Amex shares recent transaction data with Google — including transactions made **outside** Wallet (tap-to-pay, physical card) unless the user opts out of **"Non-Device Transactions"**.

**Google Takeout** can export Google Pay/Wallet data as JSON with: `transactionTime`, `amount`, `currency`, `merchant`/`counterparty`, `paymentInstrument` (network + last4), `status`, sometimes `location`/`category`.

**What it gives:** genuine transaction-level detail for Amex transactions shared with Google.
**What it does NOT give:** full history (only recent/shared), no posting date, no FX detail, no category, no reference ID.

**Dedup feasibility:** Google Wallet JSON and the Amex CSV share `date`, `amount`, `last4` — but **merchant strings differ** and dates may differ (transaction date vs posting date). **Deterministic matching is not feasible; fuzzy matching on (date ±1 day, amount exact, last4) is required.**

> **Product caution:** enabling Google Wallet as a source is a *deduplication liability* as much as a data source. It should be ranked **strictly below** the Amex export and treated as optional/last, or excluded from v1.
>
> **Disposition: excluded from v1.** `google_wallet` is not in the provider enum — see `docs/adr/0002-import-provider-enum.md`.

---

## 5. Scraping: verdict

**Are there any legitimate automated ways to retrieve Amex NL transaction data without browser scraping?** — **No.**

1. Amex's own PSD2 API → AISP-only, and UK/FR/SE/FI only.
2. Aggregator APIs → none support Amex NL.
3. Reverse-engineered private APIs / screen scraping → technically possible, **prohibited by Amex's Terms of Use**, legally risky (GDPR, computer-misuse).

**Recommendation: do not scrape.** File import is the only path — and for Amex, as of 2026-10-01,
that means **PDF only**: the Amex app offers no CSV, so `amex_csv` is retired and
`revolut_csv`-style fallbacks do not apply here. See `docs/adr/0002-import-provider-enum.md`.
Google Wallet Takeout remains optional supplementary material, excluded from the v1 provider enum.

---

## Fact confidence

| Claim | Confidence | Source |
|---|---|---|
| Amex NL unavailable via PSD2 | **CONFIRMED** | Enable Banking SE docs (2025-03) + Amex statement (2021) |
| Amex PSD2 only UK/FR/SE/FI | **CONFIRMED** | Amex statement, Emma Community 2021-08-17 |
| Amex decommissioned SE/FI | **CONFIRMED** | enablebanking.com/docs/markets/se/ (2025-03) |
| CSV export exists on americanexpress.nl | **CONFIRMED** | BUNNI 2024-09; Yuki 2025-06; StatementBridge |
| CSV columns (Date, Description, Card Member, Amount + details) | **LIKELY** | US confirmed; NL format may differ |
| CSV limited to ~6 billing periods | **LIKELY** | US confirmed; **NL unverified** |
| Pending excluded from CSV | **LIKELY** | US confirmed |
| **No stable transaction IDs** | **CONFIRMED** | TrueLayer help 2024-07 |
| PDF itemized per transaction | **CONFIRMED** | FlowParse; QuickBankConvert 2026-02 |
| PDF available ~7 years | **LIKELY** | QuickBankConvert 2026-02 |
| Google Takeout has Amex transactions | **LIKELY** | Google ToS; Amex Google Pay ToS; BankXLSX 2025-12 |
| XLSX for consumer cards | **UNCERTAIN** | Consumer portal offers CSV/OFX/QBO |
| NL CSV date format DD/MM/YYYY | **LIKELY** | StatementBridge |
| Dutch budget apps don't support Amex NL | **LIKELY** | Synci feature request 2025-10 |

## Version & freshness

| Item | Value |
|---|---|
| Core Amex/PSD2 evidence | Amex statement 2021-08-17, still operative per 2025-03 Enable Banking decommissioning |
| Newest sources | QuickBankConvert 2026-02; BankXLSX 2026-08; Subgrove 2026-07 |
| Amex developer portal | developer.americanexpress.com (current) |
| Enable Banking SE/FI deprecation | 2025-03-17 |
| Tink–Amex partnership | 2021 (income verification, not card txns) |

---

## What the user must test themselves (NEEDS USER TESTING)

1. **Download the current CSV** — americanexpress.nl → Rekeningoverzicht → Transactiehistorie → Downloaden → CSV. Record exact column headers, date format, sign convention, and whether the "Include all additional transaction details" checkbox exists and what it adds.
2. **Download a PDF statement** — verify itemization, presence of *both* transaction date and process date, FX original amount + rate, and that the text layer is selectable (not a scan).
3. **Test the date-range limit** — request CSV for 6 / 12 / 24 months back; note where truncation occurs.
4. **Pending check** — make a purchase, wait 24h, download CSV. Expect it absent.
5. **Google Takeout** — export Google Pay data only; inspect JSON for Amex; check fields and whether merchant strings resemble Amex descriptions.
6. **XLSX availability** on the consumer portal.
7. **Multi-card** — if supplementary cards exist, confirm a Card Member column and per-card distinguishability.
8. **Reference field** — is it populated? Stable-looking IDs or sequential numbers?
9. **FX** — make a foreign-currency purchase; check CSV (EUR only?) vs PDF (original + rate), and whether the FX fee is broken out.
10. **PDF depth** — try 1, 2, and 5 years back; note the earliest available.

---

## RESOLVED 2026-10-01 by real exports (bead LifeOS-2)

Four real Amex NL statements (dated 23.06, 23.07, 23.08, 23.09.2026, covering periods
24.05–23.06 through 24.08–23.09) were inspected. Full evidence and method:
**`docs/research/11-real-export-verification.md`**. Answers to the numbered user tests above:

| # | Item | Result |
|---|---|---|
| 2 | **PDF text layer selectable?** | **YES — selectable, not a scan.** No OCR needed. **M7 is a GO**; `RECO:148` risk 3 retired. |
| 2 | Itemized per transaction? | Yes, 28–33 rows per statement. Not a payments ledger. |
| 2 | Both transaction and process date? | Yes, and **they differ on 42 of 121 rows (35%)**. See correction below. |
| 2 | FX original amount + rate? | **Untestable — FX column empty in all 4 statements**, no non-EUR code anywhere. Still open. |
| 1, 3, 4, 6, 7, 8, 9, 10 | all CSV-specific tests | **Not answerable — the Amex app offers PDF export only.** See decision below. |

### Decision: Amex is PDF-only (bead LifeOS-18, 2026-10-01)

The user will download Amex statements as **PDF from the mobile app**. The web app offers
CSV/OFX/QBO but is not part of the workflow. Consequences:

- **M2 (Amex CSV, `LifeOS-7`) is retired** — there is no CSV source to import.
- **M7 (Amex PDF, `LifeOS-12`) is the only Amex path** and is raised to P0.
- The PDF→CSV fingerprint test at `ARCH:779`, and the question of whether the CSV's single
  date equals the PDF's transaction date or its processing date, are **moot**. There is no
  CSV to reconcile against.
- `ARCH:122` ("PDF is the better source, not the fallback") is now understated: for Amex,
  PDF is the *only* source.
- The `ARCH:127` rationale strengthens — archived PDFs are the only durable copy, so
  retention is load-bearing rather than merely prudent.

### Correction: `07-dedup-contingencies.md:41` is wrong

That file states, for Amex, `raw_posting_date vs raw_date | same (posted only) | Nullable,
no issue`. Measured against the real statements, the two columns differ on **42 of 121 rows
(35%)** — 10/32, 10/33, 11/28, 11/28. Example: transaction date `16.06.26`, processed
`17.06.26`; `10.06.26` processed `12.06.26`. `raw_posting_date` is load-bearing for Amex.
Tracked as bead `LifeOS-hwv`.

### §3a — the sign flip is explicit, and the PDF has no sign at all

This is a **decision**, recorded in full at
**`docs/adr/0003-import-decisions-real-export.md` Decision 1**. The short form:

| Where | Convention |
|---|---|
| This file, `:59` — Amex **CSV** | charges positive, payments/credits negative |
| This file, `:59` — Amex **PDF** | **no sign at all.** The amount column is positive for charges *and* credits; a credit is marked by a line containing only `CR` printed **beneath** the amount (doc 11 §3.2) |
| `docs/ARCHITECTURE-PROPOSAL.md` §E — the ledger | **Debits negative, credits positive** (signed/ISO; no `direction` column) |

**The source→ledger flip was written down nowhere** and `exp-2` flagged it under
`F-SCHEMA-NO-DIRECTION`. It is real (doc 11 §3.3). **An adapter must flip it explicitly:**
`CR` present → positive, `CR` absent → negative, emitted as already-signed minor units declared
`AmountSignConvention.SIGNED` so the normalizer does not flip a second time. The parser does exactly
that; the docs now match the code.

**And a second inversion hides behind it: `CR` separates credits from *charges*, not card payments
from *refunds*.** Each statement's credit section mixes the monthly card payment with real refunds,
so the payment must be separated by description on top of the sign:

| Statement | Card payment | Refunds | `Crediteringen` |
|---|---|---|---|
| 2026-06-23 | 721,35 | 1 (70,00 PayPal) | 791,35 |
| 2026-07-23 | 765,67 | 2 (10,00 Amazon, 144,30 asos) | 919,97 |
| 2026-08-23 | 332,43 | 3 (2,99 / 6,99 Prime, 39,99 asos) | 382,40 |
| 2026-09-23 | 272,48 | 0 | 272,48 |

(doc 11 §3.2.) The payment was identified by the literal string
`HARTELIJK BEDANKT VOOR UW BETALING`, which held for all four statements — one Dutch string, in the
same position as the brittle Rabobank rule at `docs/ARCHITECTURE-PROPOSAL.md` §H that "learns". **This
is a second string to learn.**

### Two other measured facts about the Amex PDF

- **The balance identity runs the opposite way to a current account:**
  `Vorig saldo + Debiteringen − Crediteringen = Nieuw saldo` (a charge *increases* the amount
  owed), true of all four statements. A generic balance self-check cannot serve both account
  types, so the identity is **per-account-type** and is a required adapter acceptance test — see
  `docs/adr/0003-import-decisions-real-export.md` Decision 2.
- **The printed `Periode` is advisory, not a filter.** Real statements contain transactions dated
  the day *before* the period opens. Filtering on period bounds would drop genuine spend; the
  period is metadata and is never an import predicate.
