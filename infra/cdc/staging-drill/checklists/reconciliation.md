# Zero-gap reconciliation checklist (ADR-0006 stage 7 / ADR-0007 G8)

Use after phases 4–7 of the staging drill. Mark each item PASS, FAIL, or N/A with evidence
artifact reference. **Row-count equality alone is insufficient.**

## Preconditions

- [ ] HWM created via replication-protocol `EXPORT_SNAPSHOT` (not SQL-only snapshot)
- [ ] `restart_lsn` and `snapshot_id` recorded in evidence manifest
- [ ] Backfill completed from exported snapshot transaction
- [ ] Live CDC started with durable landing before slot feedback (G5)

## Identity and deduplication

- [ ] Same append-only source row from backfill and CDC yields identical `event_id` (A1/A2)
- [ ] Duplicate deliveries are `duplicate_safe` only — no missing rows
- [ ] `source_position` / LSN stored as provenance, not as `event_id` input

## Completeness (zero-gap)

- [ ] ∀ committed row R on allowlisted table after HWM activation: R in Bridge landing OR DLQ
- [ ] No unaccounted gap between backfill high-water PK/cursor and first live CDC row
- [ ] Controlled synthetic writes (phase 6) all observed within lag SLO
- [ ] Restart drill (phase 7): no rows missing across stop/resume window

## Per-table checks

### `shipment_events`

- [ ] PK set symmetric difference (backfill vs source @ snapshot) is empty
- [ ] Legacy `(occurred_at, id)` cursor samples match allowlist — display/reconciliation tie-break only; not authoritative commit order
- [ ] No unexpected UPDATE/DELETE decoded (append-only surface)

### `audit_logs`

- [ ] PK set symmetric difference empty at snapshot boundary
- [ ] Legacy `(created_at, id)` cursor samples match allowlist — display/reconciliation tie-break only; not authoritative commit order
- [ ] PII fields classified; not logged at INFO in Bridge

## Semantic layers (ADR-0006 reconciliation matrix)

| Layer | Check | Pass? | Evidence ref |
|-------|-------|-------|--------------|
| L1 | Row counts per allowlisted table | | |
| L2 | PK coverage symmetric difference | | |
| L3 | Column checksum sample | | |
| L8 | Duplicate business keys / event_id | | |
| L9 | Replication lag within SLO at gate | | |

## Fail actions

- **Any FAIL on zero-gap items:** stop drill, do not advance slot, escalate per stop-conditions
- **Duplicate missing row:** treat as gap — not production-ready
- **Waivers:** require named approver, ticket id, and compensating plan

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Drill operator | | | |
| Platform architecture | | | |
| Database operations | | | |
