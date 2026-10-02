# ADR 0004: psycopg[binary] for the developer dependency

**Date:** 2026-10-03
**Status:** Accepted
**Decision Drivers:** a fresh clone must be usable for database work on a bare host; the Docker
path must keep working; no build toolchain on the developer machine.

## Context

With neither the C extension nor a bundled libpq, every database operation fails at first use:

    no pq wrapper available

M0 (`bd` bead `LifeOS-pl7`) worked around this by installing `libpq5` in
`backend/Dockerfile`, so the container is fine. But `uv sync` on a bare developer host still hits
this, which means a fresh clone is not immediately usable for DB work outside Docker. Bead
`LifeOS-mkj` recorded this as a deliberate undecided tradeoff and asked to settle it before M1
(`LifeOS-6`) needs real migrations.

Options considered:

- **a) `psycopg[binary]`** — bundles its own libpq. Easiest for developers, but the wheels carry
  their own copy of the client library.
- **b) bare `psycopg`, require system libpq** — matches the Netcup VPS target
  (ADR 0001), but every new machine needs `libpq-dev`/`libpq5` installed first.
- **c) `psycopg[c]`** — build against the system library, for distros that prefer it.

## Decision

**(a) `psycopg[binary]`**, in `pyproject.toml`.

Rationale: the documented developer path is Docker (ADR 0001 records "Same Docker Compose stack"
for development), but the bare-host path must not be a trap for anyone running `uv sync` directly
to do migrations or tests. `psycopg[binary]` removes the system-libpq prerequisite for both paths
at the cost of the wheel carrying its own client library.

## Consequences

- `backend/Dockerfile` keeps installing `libpq5`. It is harmless with `[binary]` and keeps the
  container working if the pin is ever reverted; it can be removed if the image size matters.
- The VPS (ADR 0001) uses the same wheel. libpq version drift between the app and the installed
  `libpq5` is no longer a concern for correctness, since psycopg ships a known-good libpq.
- To revert to (b) if a distro ever refuses the manylinux wheel: drop `[binary]` from
  `pyproject.toml` and install `libpq5` on the host.
