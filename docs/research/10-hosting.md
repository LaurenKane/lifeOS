# Research 10 — Deployment / Hosting

**Lane:** lib-6 (librarian) · **Completed:** 2026-09-30 · **Status:** reconciled
**Question:** *"What's the issue with hosting online on something like Supabase? If I host locally I
can't access it when my laptop is off."*
Answers bead **LifeOS-3**.

---

## Short answer

Your instinct about the laptop is right and it is the crux. And Supabase is the wrong tool for this
app — but **not primarily for privacy reasons**, which surprised me and is worth correcting up front.

**Supabase fails on three concrete engineering grounds, in order of severity:**

1. **The free tier has no automated backups at all.** Not weak backups — none. Supabase's own docs tell
   you to run `db dump` yourself. For a personal financial ledger, where "never lose raw data" is
   requirement #1, that is disqualifying on its own.
2. **Proper backups (PITR) costs ~$140/month.** PITR is a **$100/mo add-on**, on top of Pro ($25), on
   top of mandatory compute (~$15). For a single-user budgeting app, that is absurd — it costs more than
   twenty years of a €6/month VPS.
3. **Supabase cannot run your background worker.** It hosts no arbitrary long-running containers, and
   Enable Banking has **no webhooks**, so sync must be *polled every few hours* by a process that stays
   alive. The workarounds are all bad: `pg_cron` + `pg_net` + an Edge Function needs Pro tier and hits
   a **2-second CPU limit**; a `while(true)` loop inside an Edge Function gets killed at 150s (free) /
   400s (Pro). You end up running the worker on a VPS anyway — so you have a VPS *and* Supabase, paying
   twice for the thing you were trying to avoid.

**Recommendation: a ~€6/month EU VPS running Docker Compose, with Tailscale for access.**
`db` + `api` + `worker` on one small box. No platform to fight, full backups under your control, and
Tailscale means nothing is exposed to the internet.

---

## 1. Can Supabase run this app? (honest assessment)

| Requirement | Verdict |
|---|---|
| EU data residency | ✅ Frankfurt (`eu-central-1`), Ireland, Paris, Stockholm. **No Amsterdam** |
| Backups | ❌ **Free tier: none.** Pro: daily, 7d. PITR: **+$100/mo** |
| PITR | ⚠️ Pro + $100/mo add-on + ~$15 compute = **~$140/mo** |
| `pg_trgm` | ✅ `create extension pg_trgm with schema extensions;` — no restriction |
| Deferred constraint triggers | ✅ Vanilla Postgres, works. ⚠️ Not on `storage`/`auth` schemas (owned by other roles) |
| Connection pooling | ⚠️ Transaction mode (port 6543) is the serverless default and **breaks prepared statements, `SET`, `LISTEN/NOTIFY`, advisory locks**. Use direct (5432) — for one user you don't need a pooler anyway |
| RLS | ✅ Not auto-enabled on tables created via SQL migrations. **Don't use it** — pure overhead for a single user who authenticates by session cookie |
| **Background worker** | ❌ **The real blocker.** No long-running containers. See above |
| Secrets | ⚠️ Vault or env vars only. **No OS keyring** — your long-lived RSA key would live in Supabase's store |
| Auth | ⚠️ Overkill. Supabase Auth adds `auth.users`, JWT validation, RLS policies you don't need |
| Storage | ✅ Works, 1 GB free. ⚠️ **Storage objects are not in database backups** — restoring an old DB dump does not restore files. That breaks the "raw exports are the only durable copy" guarantee |
| Cost | $0 (no backups) → $35 (Pro + compute, daily backups) → **$140** (with PITR) |

### Cost, realistically

| Setup | Monthly |
|---|---|
| Free tier (no backups, 500 MB) | **$0** |
| Pro + micro compute (7-day daily backups) | $25 + $10 = **$35** |
| Pro + small compute + PITR | $25 + $15 + $100 = **$140** |
| **Netcup VPS doing everything** | **€5.91** |

---

## 2. Realistic alternatives

### Small EU VPS — the boring answer

| Provider | Entry price | EU region | Notes |
|---|---|---|---|
| **Netcup** | **€5.91** (VPS 500 G12, 2 vCPU / 4 GB, incl. VAT) | **Amsterdam** ✅ | Best latency from NL. German company, GDPR-clean. DDR5 ECC, NVMe, flatrate |
| Hetzner | €5.49 + €0.50 IPv4 (CX23) | Falkenstein, Nuremberg, Helsinki | Cheapest EU shared vCPU. **Prices rose 30–40% in June 2026.** 20 TB traffic |
| Contabo | €3.60 (4 vCPU / 8 GB) | Multiple EU | Cheapest, but support quality varies |
| DigitalOcean | ~$24 | Amsterdam, Frankfurt | US-centric, pricier |
| Vultr | ~$24 | Amsterdam, Frankfurt | Similar to DO |

**Both Netcup and Hetzner are far cheaper than Supabase Pro and include backups you control.**

### Home server / mini PC

| | |
|---|---|
| Raspberry Pi 5 (4 GB) | ~€80 one-time, ~5 W (≈€5/yr at Dutch rates) |
| Used mini PC (ThinkCentre M720q, i5, 16 GB) | ~€150–200 one-time, ~10–15 W |
| **Real cost** | Not power — it's **reliability**. Home internet goes down, power trips, the box gets unplugged by a houseguest. Your ledger is unavailable exactly when you want to check it |
| **Verdict** | Viable if you already own hardware and accept the availability risk. Power cost is genuinely negligible |

### PaaS container + managed Postgres (two vendors)

| Combo | Monthly | Verdict |
|---|---|---|
| Fly.io (app) + Supabase (DB) | ~$7 + $25 = **~$32** | Fly has Amsterdam/Frankfurt. Two vendors, two bills |
| Railway (app + DB) | ~$15–30 | Simplest DX, but Railway's managed Postgres is **$87.50/mo** |
| Render (app + DB) | ~$14 | Free tier sleeps; cold starts |

All work. All cost 3–6× the VPS. All add a second vendor relationship to maintain.

### Managed Postgres only, app self-hosted on a VPS

Neon (free 0.5 GB / $19 Launch, Frankfurt), Aiven (~€9/mo, Frankfurt/Helsinki), Supabase.
**Recommendation: don't.** If you're paying for a VPS anyway, run Postgres in Docker on it. Managed
Postgres doubles the bill for zero benefit at single-user scale.

### Oracle Cloud Always Free

Still exists, but **quietly halved in June 2026** — Always Free Ampere A1 went from 4 OCPUs/24 GB to
2 OCPUs/12 GB with no announcement; users found out when instances were shut down. Combined with
Oracle's history of aggressive account reviews: **do not put a financial ledger here.**

---

## 3. The privacy question, honestly

I expected to write "hosted = bad for privacy." That would have been oversold.

**What a VPS actually means:** your data is on a single VM you control. Your provider has hypervisor
access but no application-level access. No sub-processors, no CDN, no third-party auth.

**What Supabase actually means:** your data is on AWS infrastructure in Frankfurt (SOC 2, ISO 27001
certified). Supabase the company runs the platform and has technical access. Sub-processors include
AWS and Cloudflare.

**But the practical risk difference for one person's ledger is small.** Both are, in the end, "a company
hosts your data." The threat is misconfiguration or a legal request, not a targeted attack. Where the
control difference genuinely matters is **knowing exactly where your data lives and who can touch it** —
and that is a real, legitimate reason to prefer the VPS, but it is a *control* preference more than a
*security* one.

**The bigger risks are neither privacy nor cost — they are data loss and losing access.**
- Data loss → the free-tier-has-no-backups problem, which is why the VPS wins decisively.
- Losing access → account/billing problems locking you out of a hosted platform.

---

## 4. Lock-in: the escape hatch

If you use Supabase as **just a Postgres database** — no Supabase Auth, no Storage, no Edge Functions,
no Realtime — migration away is genuinely trivial:

```bash
pg_dump lifeos > lifeos.sql
# on the VPS:
docker compose up -d db
pg_restore -d lifeos lifeos.sql
# change one connection string
```

Your schema, data, and application code are **100% portable**. The only things lost are the dashboard
UI, the auto-generated REST API (which FastAPI replaces anyway), and Vault (replace with env vars or
Docker secrets).

**So lock-in is a manageable concern — but it's an argument for using it as a plain Postgres host, not a
reason to prefer it.** And you still need the VPS for the worker.

---

## 5. Secure access: Tailscale wins

**How it works:** install Tailscale on the VPS, your laptop, and your phone. They form a private
WireGuard mesh. Reach the app at `http://<vps-tailscale-ip>:8000`. **No port forwarding, no public IP,
no reverse proxy, no TLS certificate to renew.**

**Free tier (Personal): $0 forever, 6 users, unlimited user devices, 50 tagged resources.** More than
enough for one person, permanently free.

**Alternatives:**
- Raw **WireGuard** — same protocol, but manual key management and poorer NAT traversal.
- **Reverse proxy + TLS** (Caddy/Traefik + Let's Encrypt) — gives a public `https://…` URL, but exposes
  the app to the internet, requires a domain, and adds attack surface. **For a single user this is
  strictly worse than Tailscale.**

**Verdict: Tailscale, no question.** It is the single highest-leverage choice in the whole deployment
decision — one binary on each device, zero exposed ports, and it works from a phone on 4G.

---

## 6. Recommended topology

### (a) Single EU VPS — **recommended**

```
┌──────────────────────────────────────────────────────┐
│  Netcup VPS, Amsterdam — €5.91/mo                     │
│                                                      │
│  docker-compose.yml:                                 │
│    db      PostgreSQL 16   (pg_trgm)      :5432      │
│    api     FastAPI + uvicorn            :8000      │
│    worker  APScheduler poller                       │
│                                                      │
│  /var/lib/lifeos/          Docker volume             │
│    └─ raw-imports/         CSV + PDF files, never deleted
│                                                      │
│  Tailscale agent  ─────────────┐                     │
│  restic + age  ──► offsite encrypted backups         │
└────────────────────────────────┬─────────────────────┘
                                 │  private mesh, no exposed ports
                    ┌────────────┴────────────┐
                    ▼                         ▼
              ┌───────────┐             ┌──────────┐
              │  Laptop   │             │  Phone   │
              └───────────┘             └──────────┘
```

**5 services:** `db`, `api`, `worker`, `tailscale`, plus restic as a cron job.

### (b) Home server
Identical stack on a Pi 5 or mini PC. Tailscale agent the same. **Only difference:** if your internet
or power goes out, the app is unreachable. Free after hardware.

### (c) Hybrid (redundancy, if you ever care)
VPS runs the live app. Home box runs nightly `pg_dump` + restic replication. Tailscale connects both.
Costs one extra (near-free) box.

---

## 7. Recommendation

| Rank | Option | Why |
|---|---|---|
| **1** | **Netcup VPS (Amsterdam) + Tailscale** | €5.91/mo, best NL latency, EU data residency, full root, real backups you control, runs the worker natively, zero lock-in |
| 2 | Hetzner VPS + Tailscale | Same, marginally cheaper, no Amsterdam region |
| 3 | Home server + Tailscale | €0/mo if you own hardware; availability depends on your internet |
| 4 | Supabase Pro + VPS worker | Only if you want zero DB maintenance. Still 2 vendors, 5–6× the cost |
| ✗ | Oracle Always Free | Silent tier reductions; wrong risk profile for financial data |
| ✗ | PaaS + managed Postgres | Works, costs 3–6× more, two vendors to maintain |

**#1, concretely:**
- Netcup VPS 500 G12, Amsterdam, €5.91/mo
- LUKS full-disk encryption at image creation
- Docker Compose: `db`, `api`, `worker`
- Tailscale on VPS + laptop + phone; nothing bound to a public interface
- restic + age, daily to offsite, **monthly restore test** (this is the P0 checklist item — not optional)
- Enable Banking RSA private key in the OS keyring on the VPS, never in the DB

**Total: €5.91/month**, which is less than a coffee, and every architectural decision in
`ARCHITECTURE-PROPOSAL.md` assumes exactly this and nothing else.

---

## 8. What I could not verify / open

- Netcup and Hetzner prices are as of 2026-09-30. **Hetzner raised prices 30–40% in June 2026** — prices
  on shared-vCPU EU hosting are volatile. Re-check at purchase.
- Whether your own ISP has static-IP needs or blocks common ports — **irrelevant with Tailscale**, which
  is one of its main advantages.
- **Supabase's exact 2026 tier limits** come from their own `pricing.ts` / `plans.ts` in the public repo,
  which is authoritative but is a source of truth they control. CONFIRMED as of today; the free-tier
  backup policy is a *policy*, not a technical limit, and could change.
- Whether you'd want the Home Server option instead depends entirely on whether you already own
  hardware. Not answerable from here.
