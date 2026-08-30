---
name: legacy-evidence-audit
description: >-
  Read-only discovery of hudhud-backend behavior, routes, ownership, migrations,
  contracts, tests, and runtime evidence for HUDHUD platform extraction. Use when
  auditing a bounded context against the legacy repository. Never modify legacy.
disable-model-invocation: true
---

# Legacy evidence audit

## Purpose

Produce an evidence pack for one HUDHUD bounded context (or a named legacy
module) from the read-only `hudhud-backend` repository, without mutating it.

## When to use

- Inventorying routes, tables, writers, tests, or runtime topology in legacy
- Preparing extraction, ADR context, or provenance-backed ports
- The human instruction names this skill or asks for a legacy evidence audit

## When not to use

- Implementing or scaffolding a platform service (`bootstrap-service`)
- Writing or accepting an ADR (`prepare-adr`)
- Editing files in `hudhud_platform_backend` except optional notes the current
  task explicitly requested under `docs/audit/`
- Any request to "clean up" or format the legacy dirty simulator file

## Required inputs

- Bounded context id from `architecture/service-boundaries.yaml` **or** an
  explicit legacy module path
- Confirmation that the legacy absolute path is the documented one

## Preconditions

1. Read [AGENTS.md](../../../AGENTS.md) and
   `.cursor/rules/01-legacy-read-only.mdc`.
2. Record legacy `git rev-parse HEAD` and `git status --porcelain` (read-only).
3. Confirm the pre-existing dirty file `scripts/dev_pickup_driver_simulator.py`
   is present or still unstaged as recorded in `docs/audit/legacy-baseline.md`.
   Do not change it.

## Procedure

1. Open `architecture/service-boundaries.yaml` and
   `architecture/ownership-matrix.yaml` for the context.
2. Read existing inventories under `docs/audit/` before scanning legacy.
3. In the legacy repo, **read** only: module tree, routers, domain/application
   layers, Alembic revisions touching owned tables, tests, compose/runtime files.
4. Classify each finding as evidence (with path + SHA) or gap/unresolved.
5. Note legacy violations that platform invariants forbid (for example direct
   `shipment.status` mutation).
6. If the current task asked to update `docs/audit/*` in **this** repository,
   add evidence-only notes. Do not copy code without provenance.

## Allowed files or ownership scope

- Read: `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` (all
  paths, read-only)
- Write (only if the current task explicitly asks): `docs/audit/**` in
  `hudhud_platform_backend`
- No other writes

## Required validation

- Re-read legacy `git status --porcelain` and confirm it is unchanged from
  preflight (except the already-dirty simulator file, which must still be the
  only user dirty path unless it was already dirty).
- Platform worktree: no accidental writes outside allowed scope.
- Secret values from legacy `.env*` were not printed (names only).

## Stop conditions

- Any need to modify legacy to "see" behavior
- Ambiguous writer/table ownership that would require inventing policy
- Request to copy modules without provenance

## Prohibited actions

- Any mutation of the legacy repository
- Blind copying of legacy code into the platform repository
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Treating the dirty simulator file as in-scope

## Output contract

```text
Context:
Legacy path:
Legacy HEAD:
Legacy status (unchanged?):
Evidence (paths):
Legacy violations vs platform invariants:
Unresolved policy:
Docs updated (platform):
```

## Completion marker

`HUDHUD_LEGACY_EVIDENCE_AUDIT_COMPLETE`
