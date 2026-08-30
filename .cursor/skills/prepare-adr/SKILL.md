---
name: prepare-adr
description: >-
  Gather evidence and draft a HUDHUD Architecture Decision Record without treating
  unresolved policy as accepted. Use when a bounded-context, cutover, messaging,
  or deployable decision is required before implementation.
disable-model-invocation: true
---

# Prepare ADR

## Purpose

Produce a complete ADR draft (or an evidence-backed update) using
`docs/adr/0000-template.md`, keeping `proposed` vs `accepted` honest.

## When to use

- Ownership, database strategy, deployable grouping, or cutover policy is
  undecided and implementation depends on it
- The human instruction names this skill or asks to prepare/update an ADR

## When not to use

- Implementing the decision (`bootstrap-service`, migrations, contracts)
- Silently flipping status to `accepted` without named deciders
- Using this skill to bypass `architecture/invariants.md`

## Required inputs

- Decision question (one concern)
- Related bounded context ids
- Evidence sources (`docs/audit/`, legacy SHA, existing ADRs)

## Preconditions

1. Read [AGENTS.md](../../../AGENTS.md) and
   `.cursor/rules/09-adr-and-documentation.mdc`.
2. Inventory existing `docs/adr/*.md` for conflicts.
3. Confirm invariants that already bind the decision (do not re-decide them).

## Procedure

1. Classify known statements: evidence / proposal / decision / assumption /
   unresolved policy.
2. Copy the template to `docs/adr/NNNN-short-title.md` (next free number).
3. Fill **all** required sections: context, options, decision drivers,
   decision, consequences, migration impact, observability, security,
   rollback, unresolved questions.
4. Status is `proposed` unless the current human instruction names deciders
   and explicitly accepts the ADR.
5. If accepted, update
   `architecture/service-boundaries.yaml` and
   `architecture/ownership-matrix.yaml` only for fields the ADR actually
   decided. Leave `undecided` / `pending_adr` intact otherwise.
6. Do not invent finance, settlement, or other policy-blocked rules.

## Allowed files or ownership scope

- `docs/adr/**`
- `architecture/service-boundaries.yaml` and `architecture/ownership-matrix.yaml`
  only when the ADR is explicitly accepted and those fields change
- `architecture/invariants.md` only when the ADR updates a binding invariant
  (rare; must be stated in the ADR)

## Required validation

- Template sections all present (non-empty).
- Status is not `accepted` if unresolved questions block implementation.
- `uv run python scripts/quality/verify_boundaries.py` if architecture YAML
  changed.
- No conflicting duplicate paragraph added outside canonical docs.

## Stop conditions

- Required business policy is missing and would have to be invented
- Acceptance is requested without deciders
- The ADR would contradict `architecture/invariants.md` without an explicit
  invariant-change section and human approval

## Prohibited actions

- Marking unresolved policy as an accepted decision
- Implementing services, schemas, or NATS topology in the same change
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository

## Output contract

```text
ADR path:
Status: proposed | accepted
Deciders:
Canonical docs updated:
Unresolved questions:
Implementation allowed: yes | no
```

## Completion marker

`HUDHUD_PREPARE_ADR_COMPLETE`
