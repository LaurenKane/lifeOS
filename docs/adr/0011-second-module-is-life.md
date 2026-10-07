# ADR 0011: the second module is `life`, not habit tracking

**Date:** 2026-10-06
**Status:** Accepted
**Owner:** LaurenKane
**Decision Drivers:** ADR 0010's naming rule (a module and its schema are named
once, together, because renaming rewrites every migration, `search_path` and
route prefix); `docs/PHASE2-PLAN.md` Part 0b, which kept `core/alembic` alive
for a "habit tracking" second module; the product-discovery interviews that
concluded before any module-two code exists.

## Context

Two existing documents name the second LifeOS module. ADR 0005 mentions "the
next module (`health`, `tasks`)". `docs/PHASE2-PLAN.md` Part 0b goes further:
"habit tracking is a confirmed second LifeOS module" — the reason given for
keeping `core/alembic` alive.

Discovery for the second module has now happened, and it concluded the other
way. The module's vocabulary is **Thought / Goal / Action / Upkeep**, its first
screen is forbidden from showing missed habits or streaks, and its recurring
concept exists precisely so nothing is ever "overdue". The design intent is
anti-habit-tracker. Naming the schema `habit` would preserve that contradiction
in the one artifact that is expensive to change: a Postgres schema name.

## Decision

**The second module is named `life`.** `backend/life/`, Postgres schema
`life`, a router mounted in `backend/main.py`, and
`frontend/src/features/life/` with routes under `/life/*` — the ADR 0010
four-face contract, no deviation.

**"Habit" is deliberately not vocabulary of this module.** Recurring work is
an Upkeep: a cadence and a last-done moment, and by design no due-date column.
The first screen never renders a streak, a completion rate, a missed routine
or an overdue Upkeep. If a habit-tracker feature is ever genuinely wanted, it
is a new module, not an extension of `life`.

`tasks` was also rejected as a name: an Action is one of four kinds, not the
module's identity. The module is the whole capture-surface-act pipeline;
Actions are only its last step.

## Consequences

- ADR 0005's "next module (`health`, `tasks`)" and PHASE2-PLAN's "habit
  tracking" phrasing are superseded in **name only**. Their substance still
  holds: `core/alembic` and the `core` schema stay reserved, because a second
  module now exists. Per ADR 0005, `core` keeps its meaning — tables genuinely
  shared by two modules — and none exist: `life` composes finance data at the
  app layer (ADR 0010), never through shared tables or cross-schema FKs.
- The Upkeep data shape is a product decision, not an oversight: cadence plus
  last-done, no due date. A future reader will be tempted to "fix" this; this
  ADR is why they shouldn't.
