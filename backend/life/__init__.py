"""The `life` module — Thought / Goal / Action / Upkeep.

One vertical slice per docs/adr/0010-adding-a-lifeos-module.md: this package,
the `life` Postgres schema with its own Alembic env, a router mounted in
`main.py`, and `frontend/src/features/life/`. Naming per docs/adr/
0011-second-module-is-life.md: this module is deliberately not a habit
tracker; it has no streaks, no due-date obligation on Upkeep, and the
Dashboard (ADR 0012) never renders missed-routine states.
"""
