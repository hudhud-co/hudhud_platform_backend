# CDC staging drill — stop conditions and emergency thresholds

Explicit stop conditions for operators. **Preflight FAIL stops before privileged steps.**
During live drill, breach of a **stop** condition requires immediate halt per rollback notes.

## Automatic stop (do not proceed / halt consumer)

| Condition | Source | Default threshold (config key) |
|-----------|--------|------------------------------|
| Preflight SQL any `FAIL` status | `sql/preflight-readonly.sql` | — |
| `wal_level` ≠ `logical` | preflight | — |
| Allowlisted table missing or no PK | preflight | — |
| Slot name collision before create | preflight + config | expected unused name |
| WAL lag bytes ≥ stop threshold | `pg_replication_slots` / metrics | `CDC_DRILL_WAL_LAG_STOP_BYTES` |
| WAL volume free < minimum | OS monitoring | `CDC_DRILL_MIN_WAL_VOLUME_FREE_BYTES` |
| `wal_status` = `lost` on drill slot | `pg_replication_slots` | — |
| Durable landing evidence missing before feedback attempt | Bridge audit | G5 violation |
| Uncontrolled post-HWM row missing from landing/DLQ | reconciliation | zero-gap FAIL |

## Warning (escalate, continue only with ops approval)

| Condition | Threshold |
|-----------|-----------|
| WAL lag bytes ≥ warn | `CDC_DRILL_WAL_LAG_WARN_BYTES` |
| SSL not in use for replication connection | preflight `ssl_in_use` |
| `max_replication_slots` headroom low | preflight WARN |
| Replica identity WARN on allowlisted table | preflight |

## Emergency stop procedure

1. **Stop Bridge consumer** — do not send slot feedback / advance cursor.
2. **Preserve evidence** — capture slot metadata, lag, active pid, current WAL LSN.
3. **Do not drop slot** unless cleanup procedure authorized separately.
4. **Notify** database operations and platform architecture.
5. **Record** incident in evidence manifest `blockers` section.

## Rollback boundaries

| Phase | Safe rollback |
|-------|---------------|
| Before slot create (≤ phase 2) | Abort drill — no slot exists |
| After slot create, before cleanup | Stop consumer; slot retains WAL — monitor lag |
| After partial backfill | Do not claim zero-gap; re-plan HWM |
| Destructive cleanup | Irreversible for slot — changes lost past last ack |

## WAL growth monitoring cadence

During phases 5–7, record lag at least:

- Every 5 minutes during active CDC, **or**
- On each consumer batch landing

Store timeline artifact path in evidence manifest `wal_growth.timeline_artifact`.

## Bridge position rule (binding)

Replication slot / cursor position **may advance only after** durable Bridge landing evidence
for the decoded change exists (committed Bridge DB transaction). Violation = **immediate stop**.

## Kit defaults (this repository)

- `CDC_DRILL_DRY_RUN=1` — no remote execution from kit scripts by default
- `CDC_DRILL_CONFIRM_DESTRUCTIVE=0` — slot drop blocked
- No automatic PostgreSQL restart or `ALTER SYSTEM`
