# Life OS — developer entry points.
#
# `make help` lists every target.
#
# DOCKER SCOPE. Every target that talks to Docker goes through $(COMPOSE),
# which pins the project name to `lifeos`. This machine also runs unrelated
# stacks — a Supabase set for LifeManager and a compose project named
# `financeapp` — and a bare `docker compose down` or `docker volume prune` from
# this directory would destroy someone else's database. There is deliberately
# no target here that runs an unscoped Docker command.

.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# --- project identity -------------------------------------------------------
COMPOSE_PROJECT_NAME := lifeos
COMPOSE := COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT_NAME) docker compose -p $(COMPOSE_PROJECT_NAME)

# --- python ------------------------------------------------------------------
# --frozen everywhere: uv must install exactly what uv.lock pins and fail if
# pyproject.toml and uv.lock disagree, rather than re-resolving.
UV ?= uv
UV_RUN := $(UV) run --frozen

# --- frontend ----------------------------------------------------------------
FRONTEND_DIR := frontend
NPM ?= npm

# --- host ports --------------------------------------------------------------
API_PORT ?= 8000
FRONTEND_PORT ?= 8080
# Loopback-published port for the compose `db` service, so the DB-backed tests
# can run in the host venv. NOT 5432, which is taken by an unrelated container on
# this machine; see docker-compose.yml for the full reasoning.
LIFEOS_TEST_DB_PORT ?= 55432

.PHONY: help
help: ## List available targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ===========================================================================
# Setup
# ===========================================================================

.PHONY: setup
setup: ## Install backend (uv, frozen) and frontend (npm ci) dependencies
	$(UV) sync --frozen --extra dev
	cd $(FRONTEND_DIR) && $(NPM) ci

.PHONY: setup-db
setup-db: up-db ## Start just the database and wait for it to be healthy

# ===========================================================================
# Local development
# ===========================================================================

.PHONY: dev
dev: up ## Start the full stack, then run the Vite dev server (:5173) in the foreground
	@echo
	@echo "stack: http://localhost:$(FRONTEND_PORT)   vite: http://localhost:5173"
	cd $(FRONTEND_DIR) && LIFEOS_DEV_API=http://127.0.0.1:$(API_PORT) VITE_API_BASE_URL=$${VITE_API_BASE_URL:-/api} $(NPM) run dev

# The database is `expose`-only, never published, on purpose: host port 5432 is
# already taken by an unrelated container on this machine, and a published
# Postgres on the LAN is a liability anyway. So the API runs in the container
# rather than on the host, where it can actually reach `db`.
#
# Backend edits need `make up --build` (or `make dev-backend`); the venv on the
# host is for lint/typecheck/test, not for serving.
.PHONY: dev-backend
dev-backend: ## Run api in the foreground (rebuilds first)
	$(COMPOSE) up --build api

# `/api`, not `http://localhost:$(API_PORT)/api`: the dev server proxies /api to
# the API (see frontend/vite.config.ts). backend/main.py registers no CORS
# middleware, so a cross-origin base URL is blocked by the browser whatever the
# server replies — the proxy is what makes the dev server able to talk to the API
# at all, and it is the same same-origin path nginx takes in the compose stack.
.PHONY: dev-frontend
dev-frontend: ## Run the Vite dev server (:5173) against the compose API
	cd $(FRONTEND_DIR) && LIFEOS_DEV_API=http://127.0.0.1:$(API_PORT) VITE_API_BASE_URL=$${VITE_API_BASE_URL:-/api} $(NPM) run dev

# ===========================================================================
# Docker stack
# ===========================================================================

.PHONY: up
up: up-db ## Start the whole stack: db, api, frontend
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory ps

.PHONY: up-db
up-db: ## Start only the database
	$(COMPOSE) up -d db
	@$(COMPOSE) ps db

.PHONY: down
down: ## Stop the stack (databases and volumes are kept)
	$(COMPOSE) down

.PHONY: ps
ps: ## Show container status for the lifeos project
	$(COMPOSE) ps

.PHONY: logs
logs: ## Tail logs for the whole stack
	$(COMPOSE) logs -f --tail=100

.PHONY: shell
shell: ## Open a shell in the api container
	$(COMPOSE) exec api /bin/bash

# PGOPTIONS pins the same search_path the application engine and both Alembic
# environments pin (config.pg_connect_args). Without it a human in psql gets
# "relation does not exist" for tables that plainly exist, because psql
# defaults to public and every table lives in finance.
.PHONY: db-shell
db-shell: ## Open a psql shell with the application's search_path
	$(COMPOSE) exec -e PGOPTIONS='-csearch_path=finance,public' db psql -U $${POSTGRES_USER:-lifeos} -d $${POSTGRES_DB:-lifeos}

# The only target that removes data, and it is named for that. `down -v` drops
# the Postgres volume; there is no way to reach it by accident, because `down`
# above does not include -v.
.PHONY: clean
clean: ## DESTROY the stack AND its database volume
	@echo "This deletes the lifeos database volume. Unrelated stacks are not touched."
	$(COMPOSE) down --volumes --remove-orphans

# ===========================================================================
# Quality gates — the exact commands CI runs
# ===========================================================================

.PHONY: lint
lint: ## ruff check + ruff format --check
	$(UV_RUN) ruff check backend tools
	$(UV_RUN) ruff format --check backend tools

.PHONY: format
format: ## Apply ruff formatting
	$(UV_RUN) ruff format backend
	$(UV_RUN) ruff check --fix backend

.PHONY: typecheck
typecheck: ## mypy --strict
	$(UV_RUN) mypy --strict backend tools

.PHONY: test
test: ## Run the whole pytest suite (no Docker, no database)
	$(UV_RUN) pytest

.PHONY: test-backend
test-backend: ## Run the backend tests only
	$(UV_RUN) pytest backend/tests -q

# The DB-backed suite. It is NOT part of `test`, and that is the point: `test`
# stays runnable with no Docker at all, and these tests only run when asked for
# by name.
#
# `-m db` on the command line OVERRIDES the `-m 'not db'` in pytest's addopts
# (pytest's `store` action: the last `-m` wins), so this selects the database
# tests and nothing else. It is the selection mechanism - there is no plugin and
# no second config file.
#
# The URL is assembled here rather than left to the shell, from the same
# throwaway local defaults docker-compose.yml uses, and it points at the
# `postgres` maintenance database: the fixture creates and migrates its own
# `lifeos_test` and never touches the database named in this URL. The password
# is the same default compose already ships - see the credentials note at the top
# of that file.
#
# Run `make up-db` first. It is not a prerequisite on purpose: the tests also run
# against a database that is not this compose stack (the CI job's service
# container), and a target that silently started a second Postgres would hide
# which one the tests were actually talking to.
#
# `make ci` deliberately does NOT depend on this target. `ci` is the gate that
# must run anywhere; folding a database in would mean it cannot.
.PHONY: test-db
test-db: ## Run the DB-backed tests (-m db) against a real Postgres
	LIFEOS_TEST_DATABASE_URL="postgresql://$${POSTGRES_USER:-lifeos}:$${POSTGRES_PASSWORD:-lifeos}@127.0.0.1:$(LIFEOS_TEST_DB_PORT)/postgres" \
	  $(UV_RUN) pytest -m db -v

.PHONY: test-frontend
test-frontend: ## Run the frontend vitest suite
	cd $(FRONTEND_DIR) && $(NPM) test

.PHONY: frontend-lint
frontend-lint: ## oxlint
	cd $(FRONTEND_DIR) && $(NPM) run lint

.PHONY: frontend-build
frontend-build: ## tsc -b && vite build
	cd $(FRONTEND_DIR) && $(NPM) run build

# `tsc -b`, never bare `tsc --noEmit`. tsconfig.json is solution-style
# ("files": [], project references only), so `tsc --noEmit` type-checks nothing
# and exits 0 — a green gate over zero files.
.PHONY: typecheck-frontend
typecheck-frontend: ## tsc -b --force --noEmit (the only meaningful TS check)
	cd $(FRONTEND_DIR) && npx tsc -b --force --noEmit

.PHONY: frontend
frontend: frontend-lint typecheck-frontend test-frontend frontend-build ## All frontend gates

# ===========================================================================
# Types and migrations
# ===========================================================================

# `egress-test` is the friendly one. It runs the zero-egress gate, the
# ARCHITECTURE.md §8 property, and is deliberately non-strict: on a laptop without `strace`, or on a
# host where unprivileged user namespaces are blocked, it skips or degrades to
# strace-only observation rather than blocking a contributor who is not in a
# position to fix their kernel.
#
# `egress-test-ci` is the byte-for-byte reproduction of the `egress-test` job in
# .github/workflows/ci.yml, which is what makes a red egress gate debuggable
# without opening the Actions log. LIFEOS_REQUIRE_EGRESS=1 turns every graceful
# degradation in tests/egress/test_zero_egress.py into a hard failure, because a
# skipped egress test reads in a CI log exactly like a passed one.
#
# Neither is a prerequisite of `egress-test-ci` the way you might expect: the
# `strace` package is not optional tooling, and a target that `apt-get install`ed
# it would teach contributors to fix a missing dependency by reinstalling the
# harness instead of reporting it. Install it, then run the target:
#
#     sudo apt-get install -y strace
#     make egress-test-ci
#
# `ci` deliberately depends on the friendly `egress-test`, not this one: `ci` is
# the gate that must run anywhere, and folding in a strict kernel-level
# requirement would mean it cannot.
.PHONY: egress-test
egress-test: ## Offline file import + dedup with zero connect() calls (ARCHITECTURE.md §8)
	$(UV_RUN) pytest tests/egress -v -s

.PHONY: egress-test-ci
egress-test-ci: ## The egress gate exactly as CI runs it: a skip becomes a failure
	LIFEOS_REQUIRE_EGRESS=1 $(UV_RUN) pytest tests/egress -v -s

.PHONY: migrate
migrate: ## Apply Alembic migrations for core and finance, inside the api container
	@echo "Runs in the container so the database host is the compose service name 'db'."
	$(COMPOSE) run --rm -T --workdir /app/backend api /bin/sh -c 'set -eu; \
	  for schema in core finance; do \
	    echo "==> migrating schema: $$schema"; \
	    alembic -c "$$schema/alembic.ini" upgrade head; \
	  done'

# No temporary ini, no sed. env.py reads the URL from get_settings()
# (LIFEOS_DATABASE_URL, which compose already sets to the `db` service) and
# pins search_path through config.pg_connect_args, so the committed .ini files
# run as-is. What this target used to do — substitute a URL into a mktemp copy
# of each .ini — was an out-of-band channel that could drift from config.py, and
# it existed only because env.py read sqlalchemy.url from the .ini and nothing
# else. That is no longer true.
#
# It ALSO used to prepend the [loggers]/[handlers]/[formatters] sections to
# that copy, because the committed .ini files declared `formatter = generic`
# and `handlers = console` without the registries logging.config.fileConfig()
# indexes by those literal names — so `alembic upgrade head` died with
# `KeyError: 'formatters'` before opening a connection. All three sections are
# declared in the committed files now. Do not reintroduce the prepend.
#
# `migrate-status` below still sed-substitutes into a temporary .ini. That
# substitution is now dead: env.py overwrites sqlalchemy.url from
# get_settings() in online mode, so the substituted value is ignored. It still
# runs correctly; it is simply no longer the place the URL comes from.
.PHONY: migrate-status
migrate-status: ## Show current Alembic revision for both schemas
	$(COMPOSE) run --rm -T --workdir /app/backend api /bin/sh -c 'set -eu; \
	  for schema in core finance; do \
	    ini=$$(mktemp); \
	    sed "s|^sqlalchemy.url[[:space:]]*=.*|sqlalchemy.url = $$LIFEOS_DATABASE_URL|" \
	      "$$schema/alembic.ini" > "$$ini"; \
	    echo "==> $$schema"; \
	    alembic -c "$$ini" current; \
	  done'

# ===========================================================================
# Everything CI runs, in one command
# ===========================================================================

.PHONY: ci
ci: lint typecheck test test-db egress-test frontend ## Run every CI gate locally (needs Docker + `make up-db`)
	@echo
	@echo "All gates passed."

# ===========================================================================
# Housekeeping
# ===========================================================================

.PHONY: verify-no-secrets
verify-no-secrets: ## Guard: no credential-shaped files inside the working tree
	@! find . -path ./.git -prune -o -path ./.venv -prune -o -path ./node_modules -prune \
	    -o -type f \( -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' \) -print \
	    | grep . \
	  || { echo "Credential-shaped files found in the working tree. See SAFETY.md."; exit 1; }
	@echo "No credential-shaped files in the working tree."