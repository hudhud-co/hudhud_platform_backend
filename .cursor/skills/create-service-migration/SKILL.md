---
name: create-service-migration
description: >-
  Create and prove a HUDHUD service-owned schema migration with a single Alembic
  head, disposable upgrade, rollback or forward-recovery notes, and no
  cross-service foreign keys. Use when changing one service's persistence schema.
disable-model-invocation: true
---

# Create service migration

## Purpose

Add one service-owned Alembic revision and prove it on a disposable database.

## When to use

- Schema change inside a service that owns that database
- The human instruction names this skill

## When not to use

- Root-level or shared Alembic
- Cross-service foreign keys or writing another service's tables
- Destructive drops without explicit approval and a recovery plan
- Migrating the legacy monolith chain in `hudhud-backend`

## Required inputs

- Owning service path `services/<name>/`
- Change description (expand/contract intent)
- Approval note if the change is destructive

## Preconditions

1. Service already bootstrapped with its own `alembic/` (or this task
   includes creating that owned history — still one service).
2. Read `.cursor/rules/05-database-migrations.mdc`.
3. Current Alembic heads for **this** service: zero or one; never merge
   heads silently.

## Procedure

1. Generate a revision only under `services/<name>/alembic/versions/`.
2. Keep a single head. Additive first (expand); contract later.
3. Store foreign context identifiers as opaque IDs — no cross-service FK.
4. Upgrade a disposable database (local container or ephemeral URL from
   the task). Record exact commands.
5. Document rollback (`downgrade`) **or** why rollback is unsafe and what
   forward recovery + backup/restore looks like.
6. Do not point `script_location` at another service.

## Allowed files or ownership scope

- `services/<name>/alembic/**`
- That service's persistence/ORM modules
- Tests under `services/<name>/tests/` proving upgrade

## Required validation

- `alembic heads` (service-local) shows a single head
- Disposable `upgrade` succeeds; command + result captured
- `verify_boundaries.py` passes (no root alembic, no cross-service refs)
- No FK spanning services
- Backup/restore or rollback evidence noted when data-lossy

## Stop conditions

- Multiple heads
- Destructive change without explicit human approval
- Migration files outside the owning service

## Prohibited actions

- Root-level shared Alembic
- Cross-service foreign keys
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Running migrations against production

## Output contract

```text
Service:
Revision id:
Heads: 1
Disposable upgrade command:
Disposable upgrade result:
Rollback or forward-recovery:
Cross-service FK: none
```

## Completion marker

`HUDHUD_SERVICE_MIGRATION_PROVEN`
