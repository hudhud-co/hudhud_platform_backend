# Legacy CDC Lab — Operational Analysis

Isolated PostgreSQL 16 logical-decoding feasibility lab for ADR-0007 option O2 (WAL/CDC).
Single-node proof only — **not** HA or failover proof.

## Lab topology

| Component | Value |
|-----------|-------|
| Compose project | `hudhud-legacy-cdc-lab` |
| Network | `hudhud_cdc_lab` (bridge, no host ports) |
| Volume | `hudhud_cdc_lab_pgdata` |
| Plugin | `test_decoding` (built-in, verified) |
| Capture schema | `lab.capture_probe` |
| Slot prefix | `hudhud_cdc_lab_*` |

Row-level WAL output is **transport completeness** evidence. It is **not** canonical ADR-0002
domain integration events without an enrichment/mapping layer.

## Privileges

| Role | Purpose | Grants |
|------|---------|--------|
| `cdc_lab_owner` | Lab superuser (container bootstrap) | Full DDL/DML in lab DB |
| `cdc_replicator` | Logical decoding consumer | `REPLICATION`, `CONNECT`, `SELECT` on `lab.*` |
| `cdc_app_writer` | Simulated legacy application writer | `INSERT`/`UPDATE`/`DELETE` on `lab.capture_probe` |

Production bridge would use a **read-only** legacy role with table allowlist — never write
credentials. Lab `cdc_app_writer` simulates legacy mutations only inside the isolated DB.

Required replication prerequisites (verified in scenario 1):

- `wal_level = logical`
- `max_replication_slots >= 1`
- `max_wal_senders >= 1`
- Consumer connects with `REPLICATION` privilege or superuser

## Replication-slot lifecycle

1. **Create** — `pg_create_logical_replication_slot(name, 'test_decoding')` fixes
   `restart_lsn` at creation WAL position.
2. **Stream** — `pg_logical_slot_peek_changes` reads without advancing; `pg_logical_slot_get_changes`
   **consumes** and advances the slot position. Production Bridge MUST NOT call `get_changes` (or
   equivalent feedback) until decoded changes are durably landed in Bridge-owned storage.
3. **Lag** — `pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)` bytes retained for the slot.
4. **Inactive consumer** — slot prevents WAL recycling past `restart_lsn`; disk grows.
5. **Drop** — `pg_drop_replication_slot` releases retention; WAL may be recycled; **changes
   between last ack and drop are lost** for that consumer identity.

Slot name is **stable identity** for a bridge stream. Renaming requires new slot + coordinated
HWM/backfill (ADR-0006 stage 3).

## WAL disk risk

- Inactive or slow consumers hold WAL via replication slots.
- `max_slot_wal_keep_size` (lab: 64MB) can invalidate slots when exceeded (`wal_status`).
- Monitor: `pg_replication_slots` (`restart_lsn`, `confirmed_flush_lsn`, `wal_status`,
  `safe_wal_size`), database size, disk free.
- Mitigation: consumer lag SLO, slot alerts, drop unused slots, scale consumer, avoid long outages.

## Failover limitations (single-node lab scope)

This lab runs one PostgreSQL container. It does **not** prove:

- Automatic slot transfer after primary failover
- Consistent LSN across promote/replica
- Debezium/operator-managed slot migration

Failover typically requires slot recreation, logical snapshot re-sync, or physical replication
follower promotion runbooks — treat as **unresolved ops policy** for legacy bridge.

## Monitoring signals

| Signal | Source | Meaning |
|--------|--------|---------|
| `cdc_slot_lag_bytes` | `pg_wal_lsn_diff(current, restart_lsn)` | Consumer backlog |
| `cdc_confirmed_flush_lsn` | `pg_replication_slots` | Last acked progress |
| `cdc_wal_status` | `pg_replication_slots.wal_status` | `reserved` / `extended` / `lost` |
| `cdc_plugin` | slot metadata | Decoding plugin identity |
| Mapping errors | bridge metric (future) | Row → envelope failures |

Lab tests assert lag visibility and slot metadata; production bridge would export Prometheus
metrics per ADR-0007 observability proposal.

## Snapshot coordination (illustrative lab only)

ADR-0006 stage 3 requires post-HWM capture **before or atomically with** backfill snapshot.

**[synthetic-lab observation]** Lab scenario 10 calls `lab.capture_hwm_snapshot()` which returns
`pg_export_snapshot()`, `pg_current_wal_lsn()`, and a row count in one SQL function. This is
**illustrative only** — it is **not** the PostgreSQL replication protocol
`CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT` that atomically binds a consistent snapshot to a
logical slot/WAL start position.

Production Bridge requires a **staging exported-snapshot drill** proving:

1. Coordinated slot creation + exported snapshot identity
2. Backfill from snapshot repeatable-read view
3. Live CDC from slot `restart_lsn` with deterministic dedupe against backfill
4. Durable Bridge landing before slot advancement/feedback

Do **not** claim the zero-gap backfill protocol is fully proven from lab scenario 10 alone.

Bridge must persist **both** snapshot id / backfill cursor **and** slot `restart_lsn` at HWM.

## Schema evolution

`test_decoding` emits row images for DML; DDL appears as decoding messages depending on plugin.
Lab scenario 12 adds a column — decoding continues but consumers must tolerate:

- New columns in row images
- Type changes (may break downstream parsers)
- Table rewrites / `REPLICA IDENTITY` changes affecting UPDATE/DELETE keys

**Enrichment layer** must version mappers; raw CDC ≠ stable `event_type` contracts.

## PII exposure

Logical decoding exposes **full row contents** for published tables. Legacy bridge must:

- Allowlist tables/minimize columns where possible
- Classify payloads per ADR-0002 `data_classification`
- Avoid logging decoded rows at INFO
- Treat CDC stream as **confidential** transport

Lab table uses synthetic `payload` text only — no real customer data.

## Delete / tombstone behavior

`test_decoding` emits `DELETE` with key/old tuple depending on `REPLICA IDENTITY`.
Hard deletes without `deleted_at` require CDC or PK-set reconciliation (ADR-0006).
Bridge must map deletes to tombstone integration facts — **unresolved policy** for envelope shape.

## Bridge persistence before acknowledging progress

A Legacy Event Bridge (ADR-0007) MUST durably persist **before** advancing slot/cursor or
acknowledging replication feedback:

1. **Receive/peek** decoded changes (prefer peek until durable landing is ready)
2. **Source position** — `(slot_name, lsn, xid)` coordinates
3. **Normalized observation + outbox record** — stable `event_id`, transitional envelope payload
4. **Commit** Bridge-owned storage transaction
5. **Only then** advance slot / send replication feedback / mark checkpoint
6. **Publish** via retryable outbox (at-least-once); consumer inbox deduplicates

**Unsafe for production:** calling `pg_logical_slot_get_changes` (or Debezium auto-ack) before
step 4 commits — changes can be lost if Bridge crashes after slot advance.

Ack order: **durable landing commit → publish → slot advance/feedback**. Never advance slot past
unpersisted changes.

## Cleanup

```bash
sh infra/labs/legacy-cdc/scripts/cleanup.sh
```

Removes containers, `hudhud_cdc_lab` network, and `hudhud_cdc_lab_pgdata` volume.

## What this lab proves vs does not prove

| Proves | Does not prove |
|--------|------------------|
| Native PG16 logical decoding mechanics | HA / failover slot continuity |
| Post-commit visibility, rollback exclusion | Coordinated EXPORT_SNAPSHOT + slot drill |
| Illustrative snapshot count + LSN helper (scenario 10) | Semantic ADR-0002 event quality |
| Slot lag / WAL retention visibility | Zero-gap under legacy multi-writer without table allowlist |
| `get_changes` slot advancement mechanics (lab) | Production durable landing before feedback |
| Container restart slot durability | Cross-region DR |

Transport completeness ≠ domain event quality. CDC row changes are **not** canonical domain
events automatically.
