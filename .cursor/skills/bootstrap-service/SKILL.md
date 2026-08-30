---
name: bootstrap-service
description: >-
  Scaffold one independently deployable HUDHUD FastAPI service after ADR and
  ownership are approved. Use when adding a single services/<name> tree with its
  own uv lock, Docker context, health endpoints, and tests. Do not mass-generate
  placeholder services.
disable-model-invocation: true
---

# Bootstrap service

## Purpose

Create **one** service directory that is a real runtime boundary, not a stub farm.

## When to use

- An accepted ADR (or binding invariant) names the service owner
- Database strategy, API/event boundary, and dependency policy are decided
- The human instruction names this skill and a single service id

## When not to use

- Ownership or `data_ownership.strategy` is `undecided`
- Generating multiple services in one run
- Extracting schema from legacy without `create-service-migration` /
  `plan-extraction-cutover` when data movement is in scope
- Gateway growing domain tables

## Required inputs

- Service directory name (e.g. `shipment`) matching
  `proposed_platform_owner` / bounded context
- Database strategy (`dedicated_database` | `read_projection` | `none`)
- Allowed shared packages (allowlist)
- Public API owner and published/consumed events (may be empty)

## Preconditions

1. `architecture/service-boundaries.yaml` lists the context with concrete
   ownership (not `undecided` where this skill needs a writer).
2. Worktree is the task branch from a clean baseline (or a wave worktree).
3. Read [services/README.md](../../../services/README.md) and
   `.cursor/rules/04-python-service-quality.mdc`.

## Procedure

1. Scaffold only `services/<name>/` with: `pyproject.toml`, `uv.lock` (run
   `uv lock` inside that directory), `Dockerfile` with bounded context /
   `BUILD_CONTEXT_ALLOWLIST` if copying parents, `src/<pkg>/main.py`
   composition root, health and readiness routes, empty/owned `alembic/`
   **if** the strategy is `dedicated_database`, and `tests/`.
2. Domain package independent of FastAPI/SQLAlchemy/NATS; API has no
   business logic.
3. Register allowed package imports in the service manifest / boundaries
   as required by `verify_boundaries.py`.
4. Do not add Compose runtime topology unless the current task explicitly
   includes it (then follow `verify-compose-topology`).
5. Run quality commands for the new tree.

## Allowed files or ownership scope

- `services/<name>/**` (the single named service)
- `architecture/service-boundaries.yaml` only for that context's
  extraction/path fields if the ADR already accepted them
- `tests/` only for that service or architecture hooks required by the verifier
- Do not create sibling services

## Required validation

- Independent `pyproject.toml` and `uv.lock` exist
- Health and readiness exist
- `uv run python scripts/quality/verify_boundaries.py`
- `uv run ruff check .`
- Focused tests for health/composition import
- Migration ownership directory exists iff dedicated database

## Stop conditions

- Second service starts to appear in the diff
- Shared ORM/domain package is requested
- Cross-service import or root Alembic appears
- ADR missing for required ownership

## Prohibited actions

- Generating many placeholder services
- Shared ORM or shared domain models
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Path dependency on `hudhud-backend`

## Output contract

```text
Service:
Path:
Database strategy:
uv.lock: present
Dockerfile allowlist:
Health/readiness:
Boundaries verifier:
Tests:
```

## Completion marker

`HUDHUD_BOOTSTRAP_SERVICE_COMPLETE`
