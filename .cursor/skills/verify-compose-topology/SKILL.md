---
name: verify-compose-topology
description: >-
  Audit or validate HUDHUD Docker Compose topology, bounded build contexts,
  network isolation, database reachability, health/readiness, resource budgets,
  and absence of production source mounts. Use when adding or changing
  infra/compose files.
disable-model-invocation: true
---

# Verify Compose topology

## Purpose

Prove that Compose files (when present) preserve service isolation and do not
smuggle monolith-style coupling or production bind-mounts.

## When to use

- Adding or changing `infra/compose/**`
- The human instruction names this skill or asks to validate Compose

## When not to use

- Inventing a full NATS/production HA topology without an ADR
- Using Compose validation as a substitute for architecture tests
- Mounting the legacy repository into platform services

## Required inputs

- Compose file paths (default `infra/compose/`)
- Service names expected in this change
- Whether the environment is local-dev vs a deploy profile

## Preconditions

1. Read [infra/README.md](../../../infra/README.md) and
   `architecture/invariants.md` (Deployment).
2. Each service Dockerfile that copies parent context has
   `BUILD_CONTEXT_ALLOWLIST` (see `verify_boundaries.py`).
3. Do not treat missing Compose in Foundation F0 as a failure unless the
   current task required those files to exist.

## Procedure

1. `docker compose ... config` (exact file set) and capture output/errors.
2. Check build contexts are bounded (not the entire monorepo unless
   allowlisted).
3. Check networks: a service must not receive another service's database
   credentials or extra-hosts that punch isolation without ADR.
4. Confirm health and readiness mappings for app services.
5. Confirm **no production source mounts** (`.:/app` and similar on deploy
   profiles). Local-dev mounts must be named as local-only.
6. Note resource budgets if declared; do not invent host-capacity facts.
7. Database reachability: only the owning service should list that DB in
   its runtime env.

## Allowed files or ownership scope

- `infra/compose/**` and referenced Dockerfiles
- Read-only inspection of `services/*/Dockerfile`
- Test fixtures under `tests/` if the current task adds compose policy tests

## Required validation

- `docker compose config` succeeds when compose files exist
- `uv run python scripts/quality/verify_boundaries.py` for Docker allowlists
- Production/deploy compose: no app source bind mounts
- No legacy path mounted

## Stop conditions

- Production mounts found
- Shared DB credentials across services
- Compose requires the whole monorepo copy without allowlist

## Prohibited actions

- Applying compose to production in this skill
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Starting long-lived cluster mutation outside the stated local validation

## Output contract

```text
Compose files:
config command:
config result:
Build contexts:
Network isolation:
DB reachability:
Health/readiness:
Resource budgets:
Production source mounts: none | FAIL
```

## Completion marker

`HUDHUD_COMPOSE_TOPOLOGY_VERIFIED`
