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
# pyproject.toml and uv.lock disagree, rather than re-resolving. The
# dependency gate in CI is only meaningful if every command here shares that
# property.
UV ?= uv
UV_RUN := $(UV) run --frozen

# --- frontend ----------------------------------------------------------------
FRONTEND_DIR := frontend
NPM ?= npm

# --- host ports --------------------------------------------------------------
API_PORT ?= 8000
FRONTEND_PORT ?= 8080

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
	cd $(FRONTEND_DIR) && VITE_API_BASE_URL=$${VITE_API_BASE_URL:-http://localhost:$(API_PORT)/api} $(NPM) run dev

# The database is `expose`-only, never published, on purpose: host port 5432 is
# already taken by an unrelated container on this machine, and a published
# Postgres on the LAN is a liability anyway. So the API runs in the container
# rather than on the host, where it can actually reach `db`.
#
# Backend edits need `make up --build` (or `make dev-backend`); the venv on the
# host is for lint/typecheck/test, not for serving.
.PHONY: dev-backend
dev-backend: ## Run api and worker in the foreground (rebuilds first)
	$(COMPOSE) up --build api worker

.PHONY: dev-frontend
dev-frontend: ## Run the Vite dev server (:5173) against the compose API
	cd $(FRONTEND_DIR) && VITE_API_BASE_URL=$${VITE_API_BASE_URL:-http://localhost:$(API_PORT)/api} $(NPM) run dev

# ===========================================================================
# Docker stack
# ===========================================================================

.PHONY: up
up: up-db ## Start the whole stack: db, api, worker, frontend
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

.PHONY: db-shell
db-shell: ## Open a psql shell
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-lifeos} -d $${POSTGRES_DB:-lifeos}

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
	$(UV_RUN) ruff check backend
	$(UV_RUN) ruff format --check backend

.PHONY: format
format: ## Apply ruff formatting
	$(UV_RUN) ruff format backend
	$(UV_RUN) ruff check --fix backend

.PHONY: typecheck
typecheck: ## mypy --strict
	$(UV_RUN) mypy --strict backend

.PHONY: import-linter
import-linter: ## Enforce the layering contracts in .importlinter
	$(UV_RUN) lint-imports

.PHONY: test
test: ## Run the whole pytest suite
	$(UV_RUN) pytest

.PHONY: test-backend
test-backend: ## Run the backend tests only
	$(UV_RUN) pytest backend/tests -q

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
# Invariants
# ===========================================================================

.PHONY: check-invariants
check-invariants: ## Run the four machine-checked invariants (exit 0 pass / 1 violation / 2 error)
	$(UV_RUN) python scripts/check_invariants.py

.PHONY: check-invariants-update
check-invariants-update: ## Refresh the hash pins. Show and commit the diff deliberately.
	$(UV_RUN) python scripts/check_invariants.py --update --yes
	@echo
	@echo "--- diff ---"
	@git --no-pager diff -- invariants.yaml scripts/migrations.lock.json

.PHONY: test-invariants
test-invariants: ## Run the invariant checker's own tests
	$(UV_RUN) pytest tests/invariants -q

# A checker that always exits 0 looks exactly like a working checker in CI.
# This target feeds it deliberately broken COPIES of the repository — built in
# temporary directories, never in the working tree — and asserts it rejects
# them, in BOTH directions: it must flag the violation and leave the legal
# neighbouring statement alone.
.PHONY: invariant-negative
invariant-negative: ## Prove the invariant checker actually rejects what it forbids
	PYTHON="$(CURDIR)/.venv/bin/python" bash .github/scripts/invariant_negative.sh

.PHONY: egress-test
egress-test: ## Offline file import + dedup with zero connect() calls (ARCHITECTURE.md §8)
	$(UV_RUN) pytest tests/egress -v -s

# ===========================================================================
# Types and migrations
# ===========================================================================

.PHONY: generate-types
generate-types: ## Regenerate frontend/src/api/generated from the OpenAPI spec
	@echo "Reads the spec from a running API on :$(API_PORT). Run 'make up' first;"
	@echo "the script falls back to a skeleton when the API is unreachable."
	$(UV_RUN) python scripts/generate_types.py

.PHONY: migrate
migrate: ## Apply Alembic migrations for core and finance, inside the api container
	@echo "Runs in the container so the database host is the compose service name 'db'."
	$(COMPOSE) run --rm -T --workdir /app/backend api /bin/sh -c 'set -eu; \
	  for schema in core finance; do \
	    echo "==> migrating schema: $$schema"; \
	    ini=$$(mktemp); \
	    { \
	      printf "[loggers]\nkeys = root\n\n[handlers]\nkeys = console\n\n[formatters]\nkeys = generic\n\n"; \
	      sed "s|^sqlalchemy.url[[:space:]]*=.*|sqlalchemy.url = $$LIFEOS_DATABASE_URL|" \
	        "$$schema/alembic.ini"; \
	    } > "$$ini"; \
	    alembic -c "$$ini" upgrade head; \
	  done'

# WORKAROUND NOTE, and a real defect to fix in M1.
#
# backend/core/alembic.ini and backend/finance/alembic.ini declare
# `formatter = generic` under [handler_console] and `handlers = console` under
# [logger_root], but they omit the [loggers], [handlers] and [formatters]
# sections that logging.config.fileConfig() indexes by those literal names.
# Running `alembic upgrade head` against them as committed dies with
# `KeyError: 'formatters'` before it ever opens a connection.
#
# M0 prepends those three sections to a temporary copy of the ini (in /tmp,
# never in the repo) so that `make migrate` is a real, working command. The
# committed .ini files are left untouched: they belong to P1, which is verified
# green, and rewriting them here would invalidate that. Fix them at the source.
#
# The same temporary copy carries the substituted database URL, because env.py
# reads sqlalchemy.url from the ini and does not consult LIFEOS_DATABASE_URL.
.PHONY: migrate-status
migrate-status: ## Show current Alembic revision for both schemas
	$(COMPOSE) run --rm -T --workdir /app/backend api /bin/sh -c 'set -eu; \
	  for schema in core finance; do \
	    ini=$$(mktemp); \
	    { \
	      printf "[loggers]\nkeys = root\n\n[handlers]\nkeys = console\n\n[formatters]\nkeys = generic\n\n"; \
	      sed "s|^sqlalchemy.url[[:space:]]*=.*|sqlalchemy.url = $$LIFEOS_DATABASE_URL|" \
	        "$$schema/alembic.ini"; \
	    } > "$$ini"; \
	    echo "==> $$schema"; \
	    alembic -c "$$ini" current; \
	  done'

# ===========================================================================
# Dependency gate
# ===========================================================================

.PHONY: dependency-gate
dependency-gate: ## Fail if uv.lock or frontend/package-lock.json changed without APPROVED:
	$(UV_RUN) python .github/scripts/dependency_gate.py

.PHONY: dependency-gate-selftest
dependency-gate-selftest: ## Prove the dependency gate blocks an unapproved lockfile change
	$(UV_RUN) python .github/scripts/dependency_gate.py --self-test

# ===========================================================================
# Everything CI runs, in one command
# ===========================================================================

.PHONY: ci
ci: lint typecheck import-linter test check-invariants test-invariants invariant-negative egress-test frontend dependency-gate-selftest ## Run every CI gate locally
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