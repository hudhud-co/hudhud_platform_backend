---
name: plan-extraction-cutover
description: >-
  Prepare a one-writer HUDHUD extraction plan covering ownership, high-water
  mark, backfill, replication, shadow reads, reconciliation, read/write transfer,
  credential revocation, and rollback. Use before extracting a datastore from
  legacy. Forbids bidirectional dual-write.
disable-model-invocation: true
---

# Plan extraction cutover

## Purpose

Write an executable one-writer cutover plan for one table cluster / bounded
context. This skill plans; it does not perform live cutover.

## When to use

- Extracting data ownership from the legacy monolith toward a platform service
- The human instruction names this skill

## When not to use

- Bidirectional dual-write designs
- Live production cutover in this skill
- Finance/settlement work that is `policy_blocked` without a new accepted ADR

## Required inputs

- Bounded context and canonical writer
- Legacy table list (from boundaries + audit evidence)
- Target service and database strategy
- Legacy HEAD used as evidence baseline

## Preconditions

1. Writer is identified in `architecture/ownership-matrix.yaml`.
2. Target service exists **or** bootstrap is a separate explicit task.
3. Read `architecture/invariants.md` (Database Extraction).
4. Legacy remains read-only.

## Procedure

Document these stages, each with owner, evidence, and rollback boundary:

1. Ownership freeze (who writes what today vs target)
2. High-water mark
3. Backfill
4. Replication (one direction only)
5. Shadow reads
6. Semantic reconciliation
7. Read transfer
8. Write transfer (single writer)
9. Old credential revocation (mandatory gate)
10. Rollback boundaries (what can revert; what cannot — e.g. physical delivery)

State explicitly: **no bidirectional dual-write**.

## Allowed files or ownership scope

- `docs/adr/**` and `docs/audit/**` if the current task asks to store the plan
- Architecture YAML only for extraction_status fields the ADR already allows
- No production systems, no legacy writes, no platform schema unless separately
  tasked

## Required validation

- Every stage has a rollback note or an explicit "irreversible" justification
- Credential revocation is a named gate, not optional
- No stage requires two active writers
- Shipment lifecycle writer remains `shipment` if that state is in scope

## Stop conditions

- Plan needs two writers "for safety"
- Unresolved policy presented as decided
- Live mutation requested as part of planning

## Prohibited actions

- Bidirectional dual-write
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Revoking credentials as a live action in this skill

## Output contract

```text
Context:
Writer:
High-water mark:
Backfill:
Replication direction:
Shadow reads:
Reconciliation:
Read transfer:
Write transfer:
Credential revocation gate:
Rollback boundaries:
Dual-write: forbidden (confirmed)
Live mutation: no
```

## Completion marker

`HUDHUD_EXTRACTION_CUTOVER_PLAN_READY`
