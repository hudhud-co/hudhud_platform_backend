# CDC Staging Zero-Gap Drill Kit (ADR-0007)

**Status:** Kit artifact — **not** executed staging evidence.

Reusable, guarded procedures for a future **staging** drill validating logical replication
prerequisites, coordinated snapshot/slot correctness, restart/resume behavior, zero-gap
reconciliation, and stop/rollback conditions per ADR-0007 implementation gates G1–G10 and
ADR-0006 stage 7.

## Scope

| In scope | Out of scope |
|----------|--------------|
| Read-only preflight SQL and checklists | Live Bridge implementation |
| Evidence templates and manifest validator | Legacy or production mutation |
| Guarded command templates (dry-run default) | Automatic slot creation/drop |
| Staging drill runbook and procedures | ADR or architecture edits |

## Layout

```text
infra/cdc/staging-drill/
  RUNBOOK.md                         # Phases, gates, snapshot/slot protocol
  config.example.env                 # Operator config (names only — no secrets)
  sql/preflight-readonly.sql         # Read-only server/table checks
  templates/
    allowlist-inventory.yaml         # Table allowlist inventory template
    evidence-manifest.yaml           # Post-drill evidence package template
  checklists/
    reconciliation.md                # Zero-gap reconciliation checklist
    stop-conditions.md               # WAL/stop/emergency conditions
  procedures/
    cleanup-slot-retirement.md       # Slot retirement and cleanup
  commands/
    README.md                        # Command tier separation
    readonly.sh                      # Safe read-only (default)
    privileged-manual.sh             # Manual replication-protocol steps
    destructive-cleanup.sh           # Slot drop — explicit confirmation only
  validate.py                        # Manifest/config static validator
```

## Quick start (kit validation only)

```bash
# Validate example config and empty manifest template (no DB connection)
uv run python infra/cdc/staging-drill/validate.py \
  --config infra/cdc/staging-drill/config.example.env \
  --manifest infra/cdc/staging-drill/templates/evidence-manifest.yaml

# Static kit tests
uv run pytest tests/cdc_staging_drill -q
```

## Related documentation

- [RUNBOOK.md](RUNBOOK.md) — operator procedure
- [ADR-0007](../../../docs/adr/0007-legacy-event-bridge-strategy.md) — Bridge strategy and gates
- [ADR-0006](../../../docs/adr/0006-one-writer-data-cutover-and-reconciliation.md) — zero-gap stage 7
- [Legacy CDC lab operations](../../labs/legacy-cdc/OPERATIONS.md) — isolated lab (illustrative only)

## Safety defaults

- All kit scripts default to **dry-run** (`CDC_DRILL_DRY_RUN=1`, the default).
- Preflight SQL is **SELECT-only**; preflight failure **must stop** the drill.
- Destructive cleanup requires `CDC_DRILL_CONFIRM_DESTRUCTIVE=1` **and** an exact
  `CDC_DRILL_SLOT_NAME` match — no broad or recursive cleanup.
