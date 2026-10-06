# ADR 0009: multi-module host and pinned backup retention

**Date:** 2026-10-06
**Status:** Accepted
**Owner:** LaurenKane
**Amends:** ADR 0001 (does not supersede it)
**Decision Drivers:** one host for every module; the `no_cross_schema_fk` event trigger that
managed Postgres cannot host; a polled PSD2 worker that does not fit serverless; backup
retention stated as numbers, not adjectives; deploys a human will actually run.

## Context

On 2026-10-06 the owner reconsidered the ADR 0001 decision and asked whether the project
should move to Vercel plus a managed Postgres (Supabase or Neon) instead of self-hosting on
the Netcup VPS. The inconvenience is real: a VPS is a machine to keep updated, and `git pull`
over `ssh` is less polished than `git push` to deploy. The answer is still no, for four
concrete reasons rather than one vague preference.

**One: the `no_cross_schema_fk` invariant lives in the database, and managed Postgres may
not run it.** The enforcement is a `ddl_command_end` event trigger
(`trg_no_cross_schema_fk`), installed by `backend/db_bootstrap.sql` (ADR 0005, ADR 0006 §5).
Neon does not support `CREATE EVENT TRIGGER` at all, so the invariant would be enforced by
nothing. Supabase runs it only when migrations connect directly rather than through the
pooler, because the pooler session cannot carry the trigger installation reliably — a
constraint on *how every future migration connects*, imposed silently by the platform
choice. On the VPS the trigger installs with plain `psql` against plain Postgres and there
is nothing to remember.

**Two: the PSD2 ingestion path needs a long-running polled worker, which does not fit
serverless functions.** Enable Banking has no webhooks, so sync is a poll every few hours
(ADR 0001). A Vercel Hobby function caps at 300 seconds and Hobby cron runs once per day;
anything approaching the needed frequency is a Pro feature with its own billing. The worker
is a container that sleeps and wakes, not a function that must finish — the VPS runs it
natively, the way ADR 0001 already documented.

**Three: managed point-in-time recovery costs an order of magnitude more than the VPS.**
Supabase PITR is a ~$100/mo add-on on top of Pro plus compute. The Netcup 500-tier host is
~€5.91/mo with restic plus age for offsite backups. This is not a rounding difference; it
is the entire hosting budget times twenty, every month, for a single-user ledger.

**Four: the managed alternative is three vendors, not one.** Vercel for the frontend and
API, Supabase or Neon for Postgres, plus somewhere else for the worker that neither of the
first two will run. Against that: one VM, one Compose file, one Tailscale network, one
backup story. Every additional vendor is a second status page, a second bill, and a second
place where an incident can strand the ledger.

## Decision

Three decisions, all accepted 2026-10-06.

### 1. The host is multi-module

The finance module and the coming habit/life-dashboard module share one Postgres, one
Tailscale network, one backup story, one deploy path. Separation between modules is by
Postgres schema (ADR 0005: one schema per module, `no_cross_schema_fk` enforced by the
event trigger), not by host. ADR 0001's boundary was implicitly single-app — one ledger on
one VM — and this corrects it: the VM is LifeOS infrastructure, and the finance ledger is
its first tenant, not its only one.

Nothing about ADR 0001's topology changes. Same VPS, same Compose services (db, api,
worker, tailscale), same Tailscale access, same restic plus age backups. The correction is
scoping, not architecture: future modules arrive as new schemas and new Compose services,
not as new machines.

### 2. Backup retention is pinned, and the restore test is a gate

Retention was previously "daily offsite with monthly restore test" — a direction, not a
schedule. It is now numbers:

- **Daily snapshots, kept for 30 days.**
- **Weekly snapshots, kept for 12 months.**
- **A monthly restore test must pass.** The test is `tools/backup_test.sh`, run via
  `make backup-test`. It restores the latest snapshot into a throwaway Postgres and
  asserts the ledger is non-empty and the raw-immutability trigger exists. A month whose
  test does not pass is a month without proven backups, full stop.

### 3. Deploy ergonomics are part of the decision

A deploy path nobody enjoys is a deploy path nobody runs, and an un-deployed backup story
is fiction. So the ergonomics are decided here, not left as shell history:

- `make deploy` — pull and rebuild on the host over `ssh`.
- `make status` — Compose state plus the API health check on the host.
- `make backup-test` — the monthly restore test above.

## Consequences

**Positive:**

- One bill (~€5.91/mo), one machine to update, one backup story covering every module.
- The event trigger, the polled worker, and PITR-equivalent restic snapshots all keep
  working, because none of them is asked to fit a platform that was not built for it.
- Retention as numbers means a future argument about "are we keeping enough" starts from
  something written down.

**Negative:**

- Self-managed Postgres stays self-managed (single-user, low complexity — ADR 0001's
  caveat stands).
- VPS OS updates remain the owner's job (`unattended-upgrades` does the routine part).
- Backup monitoring between monthly tests is still manual: a broken cron job can sit
  unnoticed for up to a month.

## What would change this decision

Any one of these, verified rather than assumed:

- Vercel Pro cron plus `maxDuration` reaching a genuine long-running story (>= 15 minutes
  per invocation, sub-daily scheduling on the plan actually held).
- Neon adding event-trigger support, so `no_cross_schema_fk` enforces there.
- Supabase bundling PITR into Pro instead of a ~$100/mo add-on.
- The Netcup 500-tier price creeping above ~€15/mo, at which point the cost gap stops
  deciding the question by itself.
- A home server actually coming online with the availability to take over (ADR 0001's
  standing second option).

## Unchanged

ADR 0001's data-transfer-to-home-server procedure (`pg_dump`/`pg_restore`, or Docker
volume copy) remains unchanged and is not repeated here. Read it there.

## Related

- ADR 0001 (amended by this record; topology, worker, and transfer procedure)
- ADR 0005 (one schema per module; the event trigger this decision depends on)
- ADR 0006 §5 (why the trigger is an event trigger and not grants)
- `backend/db_bootstrap.sql` (the trigger itself)
- `tools/backup_test.sh` (the monthly restore test)
