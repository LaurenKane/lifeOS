# ADR 0010: the module boundary contract

**Date:** 2026-10-06
**Status:** Accepted
**Owner:** LaurenKane
**Decision Drivers:** one schema per module (ADR 0005); the `no_cross_schema_fk` event trigger that
refuses cross-schema FKs at DDL time; one host for every module (ADR 0009); `backend/finance/`
as the shape a second module copies.

## Context

The finance module is the only module, so every boundary it has is currently a boundary with
nothing on the other side. That will not last: ADR 0009 already decided the habit/life-dashboard
module shares the same Postgres, the same Tailscale network, and the same deploy path. The
question this record answers is what "a module" means when the second one arrives — concretely
enough that adding it is copying a shape, not re-deriving one.

The shape exists. It is `backend/finance/`, and it has four faces:

- a backend package, `backend/finance/`;
- a Postgres schema, `finance`, with its own Alembic env (`backend/finance/alembic/`);
- a router, mounted in `backend/main.py` (`finance.api.routes.ROUTERS` under the API prefix);
- a frontend directory, `frontend/src/features/finance/`, with routes under `/finance/*`
  (`frontend/src/routes/index.tsx`).

## Decision

**A LifeOS module is one vertical slice with those four faces.** A new module `<module>` is:

1. a backend package `backend/<module>/`;
2. a Postgres schema `<module>` with its own Alembic env;
3. a router mounted in `backend/main.py`;
4. a frontend directory `frontend/src/features/<module>/` with a route prefix `/<module>/*`.

### The backend package mirrors `backend/finance/`

```
backend/<module>/
├── alembic/        # migrations for the <module> schema
├── domain/         # PRIVATE
├── api/routes/     # HTTP handlers
├── api/schemas/    # Pydantic = the public contract
├── public.py       # the only export surface
└── tests/          # {unit,integration,fixtures}/
```

`domain/` is private; only `public.py` is imported by anyone else (ARCHITECTURE.md §3).
`public.py` exports only protocols and read-only schemas — `backend/finance/public.py` states
the rule in its own docstring, and the new module follows it.

Shared primitives live in `backend/core/` (money, datetime) and are never extended for one
module's needs. A type that genuinely belongs to two modules is what the `core` schema is
reserved for (ADR 0005) — and at the time of writing none exists, so a new module does not
create one by default.

### The database: composition at the app layer, never in the schema

One Postgres schema per module (ADR 0005). The `no_cross_schema_fk` event trigger
(`trg_no_cross_schema_fk` on `ddl_command_end`), installed by `backend/db_bootstrap.sql`,
aborts any DDL that creates a foreign key crossing schemas — in the DDL's own transaction,
even for the superuser role the app runs as.

Therefore modules **must not** foreign-key into each other. Cross-module composition happens
in the application layer — via each module's `public.py` read-only schemas or its HTTP API —
never in the database.

The concrete consequence: a future life-dashboard that wants finance plus habit data composes
them at the app layer. It reads `AccountSummary` / `TransactionSummary` from finance's
`public.py` and its own module's rows, and joins them in Python or in the frontend. It cannot
write `REFERENCES finance.account`, because the trigger refuses the DDL and the migration
fails.

### Naming: pick once

Module name and schema name are chosen together and are the same string. Renaming a schema
later means rewriting every migration, every `search_path`, and every route prefix — so the
name is picked once. ADR 0005 already notes the standing warning: the `core/` Python package
(shared primitives) and the `core` Postgres schema are unrelated things sharing a name. Do
not repeat that collision with a new module.

### Deployment: modules share the API service by default

Modules share one host (ADR 0009). A new module arrives as a new schema and a router mounted
in the existing API process by default — not a new machine or a separate Compose service.
Add a separate service only when the module has an independent runtime need, such as its own
long-running worker. All modules use the same Postgres, Tailscale network, and restic plus age
backup story. Nothing about the topology changes.

## Consequences

**Positive:**

- The second module is a copy of a documented shape, not a design discussion.
- The trigger makes the hardest boundary self-enforcing: a cross-module FK fails at
  migration time with the database refusing it, not at review time with a human noticing it.
- One host, one bill, one backup story keep holding no matter how many modules arrive.

**Negative:**

- Cross-module queries the database could answer in one join become two reads plus app-layer
  composition. That cost is accepted deliberately — it is what keeps modules independently
  migratable.
- The `public.py` discipline is a convention, not a gate (ARCHITECTURE.md §3): nothing in
  CI stops a module importing another module's `domain` directly. Review enforces it.

## Adding a module

1. Create `backend/<module>/` mirroring `backend/finance/`: `alembic/`, `domain/`,
   `api/routes/`, `api/schemas/`, `public.py` (protocols plus read-only schemas only),
   `tests/`.
2. Create schema `<module>` in `backend/db_bootstrap.sql` (alongside `core` and `finance`)
   so the schema exists before Alembic's `version_table_schema` bookkeeping needs it.
3. Write the module's migrations under its own `alembic/` env; never reference another
   module's schema in a FK — the event trigger will refuse it.
4. Mount the module's router in `backend/main.py`.
5. Add `frontend/src/features/<module>/` with routes under `/<module>/*`.
6. Extend `backend/core/` only if the new primitive is genuinely shared; otherwise it lives
   in the module's `domain/`. Add a separate Compose service only for an independent runtime
   need; ordinary routes join the shared API service.

## Related

- ADR 0005 (one schema per module; the event trigger; the `core` name collision)
- ADR 0006 §5 (why the trigger is an event trigger and not grants)
- ADR 0009 (one host for every module; modules share the API service by default)
- `backend/db_bootstrap.sql` (the trigger itself)
- `backend/main.py` (where routers mount)
- `backend/finance/public.py` (the export-surface rule, stated in its own docstring)
