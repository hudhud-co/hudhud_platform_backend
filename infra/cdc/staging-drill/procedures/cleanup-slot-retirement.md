# Slot retirement and cleanup procedure

**Tier:** Destructive — manual confirmation required.  
**Default:** Blocked by kit scripts until `CDC_DRILL_CONFIRM_DESTRUCTIVE=1` and exact
`CDC_DRILL_SLOT_NAME` are set.

## When to retire a drill slot

- Drill complete and evidence archived
- Drill aborted and ops accepts WAL retention cost until alternative plan exists
- Slot polluted by failed experiment — **after** evidence capture

**Do not** drop production or shared consumer slots. Drill slots must use configured prefix
(e.g. `hudhud_bridge_staging_*`) and appear in allowlist inventory.

## Pre-drop checklist

- [ ] Bridge consumer stopped (no active pid on slot)
- [ ] Final lag and `restart_lsn` / `confirmed_flush_lsn` captured
- [ ] Evidence manifest updated with cleanup section
- [ ] Slot name double-checked against inventory (exact match)
- [ ] Operator and approver recorded
- [ ] No other replication consumer shares this slot name

## Procedure

1. Run read-only slot verification:

```bash
sh infra/cdc/staging-drill/commands/readonly.sh slot-inspect
```

2. Execute destructive template **only** when authorized:

```bash
export CDC_DRILL_CONFIRM_DESTRUCTIVE=1
export CDC_DRILL_SLOT_NAME='hudhud_bridge_staging_drill_001'  # exact name
export CDC_DRILL_DRY_RUN=0  # explicit execution opt-in
sh infra/cdc/staging-drill/commands/destructive-cleanup.sh drop-slot
```

3. Verify slot absent:

```bash
sh infra/cdc/staging-drill/commands/readonly.sh slot-inspect
```

4. Record in evidence manifest:

- `cleanup.slot_retired: true`
- `cleanup.slot_drop_timestamp`
- `cleanup.slot_drop_operator`
- Optional: WAL disk trend after drop

## What dropping a slot destroys

Changes between last acknowledged consumer position and drop are **lost** for that slot
identity. Renaming requires new slot + coordinated HWM/backfill (ADR-0006 stage 3).

## Forbidden cleanup patterns

- Broad `pg_drop_replication_slot` without exact name match
- Recursive drop of all slots matching a prefix
- Automated cleanup in CI or default kit execution
- Dropping slots from this kit without staging authorization

## Publication cleanup (if created for drill)

If a drill-specific publication was created, drop it in a separate privileged step with the
same confirmation discipline. Record publication name in cleanup evidence.

**This kit does not automate publication drop.**

## Credential notes (G9)

Document whether drill replication credentials were revoked or rotated after cleanup. Credential
values never belong in evidence artifacts — names and timestamps only.
