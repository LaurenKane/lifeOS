-- SQL that must run before ANY migration, for every database that will be
-- migrated: the production container and a test fixture's fresh database alike.
--
-- This file is the single source of truth for that SQL. It was inline in
-- docker-compose.yml, which works but means a test fixture cannot run the same
-- statements without either duplicating them or shelling out to compose - and a
-- test database missing its schema fails as "InvalidSchemaName" rather than as
-- "the fixture forgot something".
--
-- Mounted by docker-compose.yml as /docker-entrypoint-initdb.d/10-pg_trgm.sql.
-- The postgres entrypoint runs everything in that directory, alphabetically,
-- ONCE, on first initialisation of an empty data directory. Re-running it
-- against an already-initialised volume does nothing: the entrypoint does not
-- look at the directory again. That is why every statement below is
-- IF NOT EXISTS.

-- ARCHITECTURE.md §E: trigram indexes over merchant/payee names.
-- Run once by the postgres entrypoint, before any application start.
-- Installed into `public`, which is why `public` is last on every search_path
-- this project pins: `gin_trgm_ops` has to stay reachable by name.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- One database, one schema per module. These two are containers for a module's
-- tables; nothing here decides what goes inside them — M1 (bead LifeOS-6) owns
-- the tables, the deferrable balance trigger and the raw_data_immutable
-- trigger.
--
-- Created HERE rather than by an Alembic migration, for one concrete reason:
-- Alembic cannot create its own bookkeeping table inside a schema that does not
-- exist. Both env.py files set `version_table_schema`, so `alembic upgrade
-- head` emits `CREATE TABLE core.alembic_version` and fails with
-- InvalidSchemaName before a single revision runs. Left to a migration, its
-- first migration would be unable to execute at all.
--
-- IF NOT EXISTS throughout, so this is safe to re-run.
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS finance;

-- The application role owns its schemas, so M1's migrations can create tables in
-- them. These grants are convenience, NOT the enforcement of
-- `no_cross_schema_fk`: the role the app runs as is a superuser, and a
-- superuser bypasses USAGE/GRANT checks, so a USAGE revoke would bind nobody.
-- The enforcement is the event trigger appended below.
--
-- The role name is literal, matching POSTGRES_USER's default in
-- docker-compose.yml. Overriding POSTGRES_USER means overriding it here too.
GRANT ALL ON SCHEMA core TO lifeos;
GRANT ALL ON SCHEMA finance TO lifeos;

-- `no_cross_schema_fk`: no foreign key may reference a table in another
-- Postgres schema (ARCHITECTURE.md §4, ADR 0005). WHY AN EVENT TRIGGER:
--
-- The regex that used to enforce this (`invariants.yaml`, a static
-- `forbid_regex` over `backend/`) was deleted in commit 8bdfd57, so the
-- invariant was enforced by nothing. Role separation would not work here:
-- the app/migration/test role `lifeos` is a PostgreSQL SUPERUSER, and a
-- superuser bypasses USAGE and GRANT checks entirely.
--
-- A `ddl_command_end` event trigger runs INSIDE the DDL's own transaction,
-- before it commits, and RAISEs for the same session — even for a
-- superuser. The offending CREATE/ALTER is aborted with the transaction
-- that carried it, so a cross-schema FK can never land. Verified live
-- against Postgres 17: refused for both inline
-- `CREATE TABLE ... REFERENCES core.x` and
-- `ALTER TABLE ... ADD CONSTRAINT ... REFERENCES core.x`, while same-schema
-- FKs and TRUNCATE pass through undisturbed.
--
-- RESIDUAL RISKS, stated rather than hidden: a superuser can
-- `DROP EVENT TRIGGER`, so this binds cooperating writers, not an attacker
-- holding the role; and `SET session_replication_role = replica` skips
-- event triggers (the same Postgres-wide limitation the balance trigger
-- carries, ARCHITECTURE.md §5). Either one is a deliberate act visible in
-- the session, not a migration anyone writes by accident.
--
-- Idempotent by drop-then-create: `CREATE EVENT TRIGGER` has no
-- IF NOT EXISTS, and this file is re-run against fresh databases by both
-- the postgres entrypoint and the test fixture. The function lives in
-- `public` (which always exists) so this survives later schema or
-- ownership changes; the trigger call site schema-qualifies it for the
-- same reason.
CREATE OR REPLACE FUNCTION public.fn_no_cross_schema_fk()
RETURNS event_trigger LANGUAGE plpgsql AS $fn$
DECLARE
  cmd record;
  bad_count int;
BEGIN
  FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    SELECT count(*) INTO bad_count
    FROM pg_constraint c
    JOIN pg_class cl    ON cl.oid = c.conrelid
    JOIN pg_namespace n ON n.oid  = cl.relnamespace
    WHERE cl.oid = cmd.objid
      AND c.contype = 'f'
      AND EXISTS (
        SELECT 1 FROM pg_class ref
        JOIN pg_namespace rn ON rn.oid = ref.relnamespace
        WHERE ref.oid = c.confrelid AND rn.oid <> n.oid
      );
    IF bad_count > 0 THEN
      -- Concatenation under USING MESSAGE, NOT format placeholders: this
      -- file is executed whole through a parameterized driver
      -- (`exec_driver_sql` in the test fixture), where a bare percent sign
      -- reads as a query placeholder and aborts the bootstrap itself —
      -- while the postgres entrypoint runs the same file via psql, where
      -- percent is literal, so doubling it would corrupt that path
      -- instead. (`RAISE` also requires its format to be a string literal,
      -- so an expression has to arrive through USING MESSAGE.) The raised
      -- code is still 42501 (`insufficient_privilege`) and the text is
      -- identical either way.
      RAISE insufficient_privilege
        USING MESSAGE = 'no_cross_schema_fk: '
          || cmd.object_identity
          || ' has '
          || bad_count::text
          || ' cross-schema FK(s); refusing',
        HINT = 'Foreign keys may only reference tables in the same schema.';
    END IF;
  END LOOP;
END
$fn$;

DROP EVENT TRIGGER IF EXISTS trg_no_cross_schema_fk;
CREATE EVENT TRIGGER trg_no_cross_schema_fk
  ON ddl_command_end
  EXECUTE FUNCTION public.fn_no_cross_schema_fk();