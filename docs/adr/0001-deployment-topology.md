# ADR 0001: Deployment Topology

**Date:** 2026-09-30
**Status:** Accepted
**Decision Drivers:** Always-on requirement, EU data residency, low cost, minimal lock-in, PITR backups, long-running worker for Enable Banking polling

## Context

The Life OS application needs:
- Always-available host (laptop may be off)
- Docker Compose: db + api + worker
- Long-running worker (Enable Banking has no webhooks, polling every few hours)
- PostgreSQL with pg_trgm and DEFERRABLE INITIALLY DEFERRED constraint triggers
- Automated backups with point-in-time recovery
- EU data residency, minimal third parties, low cost

Evaluated options:
1. **Supabase** — Rejected: free tier has no backups, PITR costs ~$140/mo, cannot run long-running worker
2. **Netcup VPS (Amsterdam)** — €5.91/mo, EU data residency, full control, runs worker natively
3. **Hetzner VPS** — Similar price, no Amsterdam region
4. **Home server** — Free if hardware owned, but availability depends on home internet/power
5. **PaaS + managed Postgres** — 3-6× cost, two vendors

## Decision

**Primary: Netcup VPS 500 G12 (Amsterdam) — €5.91/mo**
- 2 vCPU, 4 GB RAM, NVMe, 20 TB traffic
- LUKS full-disk encryption at image creation
- Docker Compose: db, api, worker, tailscale
- restic + age for daily offsite encrypted backups with monthly restore test
- Enable Banking RSA private key in OS keyring on VPS

**Development: Old MacBook with Linux (local)**
- Same Docker Compose stack
- Tailscale for access from laptop/phone
- Data transfer to VPS: single `pg_dump`/`pg_restore` or Docker volume copy

**Access: Tailscale (free personal tier)**
- Private WireGuard mesh, no public ports exposed
- Covers laptop, phone, VPS, home server
- Zero TLS cert management, no reverse proxy

## Data Transfer Procedure

**From MacBook (dev) to VPS (prod):**
```bash
# On MacBook
pg_dump -h localhost -U lifeos lifeos > lifeos.sql
# OR for full volume
docker compose exec -T db pg_dump -U lifeos lifeos > lifeos.sql

# On VPS
docker compose up -d db
pg_restore -h localhost -U lifeos -d lifeos lifeos.sql
# OR
cat lifeos.sql | docker compose exec -T db psql -U lifeos -d lifeos
```

**Transfer time:** ~seconds for schema + data (single-user ledger is small).
**Downtime:** Zero if done carefully (read-only mode on source during dump).
**Risk:** Near-zero — standard Postgres dump/restore is battle-tested.

## Consequences

**Positive:**
- €5.91/mo total (vs $140 for Supabase with PITR)
- Full backup control, monthly restore test proves PITR works
- Zero lock-in: plain Postgres, standard Docker Compose
- Tailscale gives secure access from anywhere with no public exposure
- Worker runs natively as a long-running container

**Negative:**
- Self-managed Postgres (but single-user, low complexity)
- Need to maintain VPS OS updates (unattended-upgrades handles this)
- Backup monitoring is manual (monthly restore test is the check)

## Acceptance Criteria

- [x] Topology documented in this ADR
- [x] Docker Compose service list: db, api, worker, tailscale
- [x] Backup procedure: restic + age, daily offsite, monthly restore test
- [x] Secrets: RSA key in OS keyring on VPS, env vars for app config
- [x] Data transfer procedure documented and tested

## Related

- LifeOS-3 (this issue)
- docs/research/10-hosting.md (full comparison)
- M0 (LifeOS-5) — repo skeleton with this Docker Compose