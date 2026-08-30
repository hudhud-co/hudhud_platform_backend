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
2. **Stream** — `pg_logical_slot_get_changes` / `pg_recvlogical` advances consumer cursor;
   unconfirmed data remains available until `pg_replication_slot_advance` or implicit flush.
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

## Snapshot coordination (zero-gap)

ADR-0006 stage 3 requires post-HWM capture **before or atomically with** backfill snapshot.

Lab procedure (scenario 10):

1. Create logical slot (capture starts at `restart_lsn`).
2. `pg_export_snapshot()` + record `pg_current_wal_lsn()` as HWM.
3. Backfill rows visible in snapshot (repeatable read).
4. Concurrent inserts after snapshot appear only in WAL stream.
5. Union(backfill, WAL from slot) = no gap.

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

A Legacy Event Bridge (ADR-0007 proposal) MUST durably persist **before** advancing slot/cursor:

1. **Source position** — `(slot_name, confirmed_flush_lsn)` or equivalent LSN coordinate
2. **Decoded transport record** — raw change + source xid/LSN/timestamp (for deterministic
   `event_id` = UUIDv5 over `{table}:{pk}:{position}`)
3. **Mapped envelope** (optional stage) — ADR-0002 JSON pending contract registration
4. **Publish ack** — JetStream ACK for relay path, or outbox `published_at` if buffered

Ack order: **persist checkpoint → publish → advance slot**. Never advance slot past unpublished
or un-persisted changes.

## Cleanup

```bash
sh infra/labs/legacy-cdc/scripts/cleanup.sh
```

Removes containers, `hudhud_cdc_lab` network, and `hudhud_cdc_lab_pgdata` volume.

## What this lab proves vs does not prove

| Proves | Does not prove |
|--------|------------------|
| Native PG16 logical decoding mechanics | HA / failover slot continuity |
| Slot identity, LSN resume, ordering | Semantic ADR-0002 event quality |
| Post-commit visibility, rollback exclusion | Legacy production topology |
| Snapshot + WAL gap-free coordination pattern | Debezium/Kafka ops model |
| Slot lag / WAL retention visibility | Zero-gap under legacy multi-writer without table allowlist |
| Container restart slot durability | Cross-region DR |

Transport completeness ≠ domain event quality. CDC row changes are **not** canonical domain
events automatically.
