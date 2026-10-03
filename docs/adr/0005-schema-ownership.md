# ADR 0005: every table lives in the `finance` Postgres schema

**Date:** 2026-10-03
**Status:** Accepted
**Decision Drivers:** the `no_cross_schema_fk` invariant; one Postgres schema per module;
keeping the choice written down so it is not re-derived.

## Context

`docs/ARCHITECTURE-PROPOSAL.md` §E defines ~15 tables in one flat list with no schema
qualification. Two things make the placement question real rather than cosmetic.

**One: the `no_cross_schema_fk` invariant forbids qualified references.** `invariants.yaml`
declares it as a `forbid_regex` over `backend/`:

```
(?i)\bREFERENCES\s+(?!\s*"?public"?\s*\.\s*)"?([a-z_][a-z0-9_]*)"?\s*\.
```

So `REFERENCES core.currency(code)` fails CI while unqualified `REFERENCES currency(code)` is
allowed. The regex inspects only the `REFERENCES` keyword, so `CREATE TABLE finance.x` is
invisible to it — the invariant constrains how tables may point at each other, not where they
may be declared.

**Two: §E is a single FK-connected graph, not a set of independent tables.** `account` →
`institution` and `currency`; `exchange_rate` → `currency`; `source_record` → `import_batch`,
`account`, `journal_entry`; `journal_line` → `journal_entry`, `account`, `currency`, `category`,
`merchant`, `transfer_match`.

There is also a naming collision worth stating plainly, because it is what makes this question
feel unanswerable: the **`core/` package** (shared Python primitives — money, datetime, blob,
preference) and the **`core` Postgres schema** (created by the `db_bootstrap` step in
`docker-compose.yml`) are unrelated things that happen to share a name.

## Decision

**Every §E table is created in the `finance` schema. The `core` schema holds only its
`alembic_version` table.**

### Why not put shared reference tables in `core`

This is not a tradeoff. Splitting §E is **unimplementable** under the project's own invariant:
moving `currency`, `institution`, `account` or `import_batch` into `core` forces every table
that references them to write `REFERENCES core.<table>`, which `no_cross_schema_fk` rejects.
There is no version of "reference tables in `core`" that passes CI.

### Why not use `public`

`public` is writable by every role by default, `DROP SCHEMA public CASCADE` is a one-liner any
person or bot can run, and it would leave the `finance` module — the thing that owns the entire
domain model — mapped to the empty schema. The module-per-schema mapping stops describing
anything.

### Why `core` is not "empty"

`alembic upgrade head` against a script directory containing zero revisions still creates
`core.alembic_version`. After `make migrate`, `core` contains exactly that one table. A module
that owns no table still owns a schema, because migration history is per-schema
(`version_table_schema`).

## Consequences

- **`core` is reserved.** It is where the first table genuinely shared by two modules goes.
  None exists at M1. The next module (`health`, `tasks`) gets its own schema, and if two of them
  ever need the same table, that table is what `core` is for.
- **`search_path` becomes load-bearing, and it is set per connection.**
  `backend/config.py:pg_connect_args()` returns
  `{"options": "-csearch_path=<module>,public"}` and is called by the application engine **and by
  both `alembic/env.py` files**. One helper, deliberately: a migration that resolves names
  differently from the application is the failure mode this prevents. `ALTER ROLE`/`ALTER
  DATABASE` were rejected because neither can apply to a database that does not exist yet — the
  test database is created by the fixture at run time.
- **plpgsql function bodies are schema-qualified regardless.** `finance.journal_line`, never
  bare `journal_line`. A trigger body resolves names against the *executing* session at COMMIT
  time, so an unqualified reference would raise `relation does not exist` at COMMIT inside the
  running application. This is not covered by `search_path` being set correctly, so it does not
  depend on it being correct.
- **A hand-typed `psql` session fails loudly.** With no `search_path` set, unqualified names
  resolve to `public` and error out rather than silently hitting a table in the wrong schema.
  `make db-shell` sets `PGOPTIONS` so humans get the same resolution as the application.
- **The `GRANT ALL ON SCHEMA core|finance TO lifeos` in the bootstrap is not an enforcement of
  `no_cross_schema_fk`.** A role owning both schemas can create a cross-schema foreign key
  freely. The regex is the enforcement; the grants are convenience. Tightening this to a role
  that does not hold `USAGE` on the other schema is deferred, and is tracked as its own bead
  rather than folded into M1.