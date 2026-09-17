# One entry point for running and checking the HUDHUD platform.
#
# Everything here shells out to the scripts that already own the work — `make` adds the
# one thing they cannot do for themselves: make sure the fifteen virtualenvs exist before
# something that runs with UV_NO_SYNC=1 tries to use them.
#
#     make up          start the whole platform
#     make journeys    drive the real HTTP APIs
#     make check       everything CI would run without Docker
#
# Written for GNU Make 3.81, the version macOS ships. No .ONESHELL, no $(file ...).

.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
STACK := scripts/dev/stack.py
JOURNEYS := scripts/dev/journeys.py
API_DOCS := scripts/dev/api_docs.py

# Every directory under services/ that is its own uv project.
SERVICES := $(notdir $(patsubst %/,%,$(dir $(wildcard services/*/pyproject.toml))))

# uv refuses to be quiet about an activated venv that is not the one it is syncing, and
# this repo has sixteen of them. The scripts do the same thing for the same reason.
export VIRTUAL_ENV :=

SYNC_STAMP := .make/sync.stamp
SYNC_INPUTS := pyproject.toml uv.lock \
               $(wildcard services/*/pyproject.toml) $(wildcard services/*/uv.lock)

.PHONY: help up down restart status logs journeys api-docs api-docs-build token \
        docker-up docker-down docker-build docker-status docker-logs docker-restart \
        sync resync lint verify test test-services test-integration proofs \
        check check-all clean

# ----------------------------------------------------------------------- running

help: ## Show this help
	@echo "HUDHUD platform — make targets"
	@echo
	@grep -hE '^[a-z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Arguments:  make logs s=finance   make journeys only=finance"

up: $(SYNC_STAMP) ## Start the whole platform (Postgres, NATS, 14 services)
	$(UV) run python $(STACK) up

down: ## Stop everything and remove the data
	$(UV) run python $(STACK) down

restart: down up ## Stop, then start again from a clean database

status: ## What is running, and is it ready
	$(UV) run python $(STACK) status

logs: ## Tail one service's log: make logs s=finance
	@test -n "$(s)" || { echo "usage: make logs s=<service>"; exit 2; }
	$(UV) run python $(STACK) logs $(s)

journeys: ## Drive the real HTTP APIs: make journeys [only=finance]
	$(UV) run python $(JOURNEYS) $(if $(only),--only $(only))

api-docs: ## One Swagger page for all 14 services: make api-docs [PORT=8115]
	$(UV) run python $(API_DOCS) $(if $(PORT),--port $(PORT))

api-docs-build: ## Write that page and its specs as static files: make api-docs-build out=docs/api
	$(UV) run python $(API_DOCS) --build $(if $(out),$(out),docs/api)

# --------------------------------------------------------------- everything in docker

COMPOSE := docker compose -f infra/compose/platform.compose.yaml

docker-up: ## Start the whole platform in containers (builds images if needed)
	$(COMPOSE) up -d --build
	@echo
	@$(MAKE) --no-print-directory docker-status

docker-down: ## Stop the containerised platform and remove the data
	$(COMPOSE) down --volumes --remove-orphans

docker-build: ## Build all 14 service images without starting anything
	$(COMPOSE) build

docker-restart: docker-down docker-up ## Rebuild and restart the containerised platform

docker-status: ## What is running in containers
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

docker-logs: ## Tail container logs: make docker-logs s=finance (omit s for all)
	$(COMPOSE) logs -f --tail=100 $(s)

# ------------------------------------------------------------------ environments

sync: $(SYNC_STAMP) ## Sync the root and every service virtualenv, if stale

resync: ## Force a full re-sync even when nothing changed
	@rm -f $(SYNC_STAMP)
	@$(MAKE) --no-print-directory sync

# The stack starts thirteen `uv run` processes at once with UV_NO_SYNC=1, so a missing
# or stale environment surfaces as thirteen confusing service failures. Syncing first
# turns that into one clear step. The stamp keeps it near-free once it is done.
$(SYNC_STAMP): $(SYNC_INPUTS)
	@mkdir -p $(dir $@)
	$(UV) sync --dev
	@for service in $(SERVICES); do \
	  printf '  syncing %s\n' "$$service"; \
	  $(UV) sync --project services/$$service --quiet || exit 1; \
	done
	@touch $@

# --------------------------------------------------------------------- the gates

lint: ## ruff over the whole repository
	$(UV) run ruff check .

verify: ## Boundaries, governance, requirement accounting, traceability, ports
	$(UV) run python scripts/quality/verify_boundaries.py
	$(UV) run python scripts/quality/verify_agent_governance.py
	$(UV) run python scripts/quality/verify_requirement_accounting.py
	$(UV) run python scripts/quality/verify_requirement_traceability.py
	$(UV) run python scripts/quality/verify_port_allocations.py

test: ## Root test suites that need no Docker
	$(UV) run pytest tests -q -m "not integration"

test-services: $(SYNC_STAMP) ## Every service's own suite, in its own virtualenv
	@failed=""; \
	for service in $(SERVICES); do \
	  printf '\n=== %s ===\n' "$$service"; \
	  $(UV) run --project services/$$service pytest services/$$service/tests -q \
	    || failed="$$failed $$service"; \
	done; \
	if [ -n "$$failed" ]; then printf '\nFAILED:%s\n' "$$failed"; exit 1; fi; \
	printf '\nAll %s service suites passed.\n' "$(words $(SERVICES))"

test-integration: ## Every suite that needs Docker — labs and migration proofs
	$(UV) run pytest tests -q -m integration

proofs: ## Migration proofs only, against disposable PostgreSQL 16
	$(UV) run pytest tests/new_service_migration_proof -q -m integration

check: lint verify test test-services ## Everything CI runs without Docker

check-all: check test-integration ## check, plus every Docker-backed suite

clean: ## Remove make's own bookkeeping (not the stack — use `make down`)
	@rm -rf .make
