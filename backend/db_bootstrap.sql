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
-- them. This is only the ownership half; `no_cross_schema_fk` also wants grants
-- that forbid cross-schema references, and those arrive with M1 alongside the
-- tables they protect.
--
-- The role name is literal, matching POSTGRES_USER's default in
-- docker-compose.yml. Overriding POSTGRES_USER means overriding it here too.
GRANT ALL ON SCHEMA core TO lifeos;
GRANT ALL ON SCHEMA finance TO lifeos;