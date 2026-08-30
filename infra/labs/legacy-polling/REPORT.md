# HUDHUD W3-B Legacy Polling Completeness Lab

**Marker:** `HUDHUD_W3_B_POLLING_LAB`  
**ADR:** [0007 Legacy Event Bridge Strategy](../../docs/adr/0007-legacy-event-bridge-strategy.md)  
**Workstream:** W3-B  
**Scope:** Synthetic PostgreSQL 16 only — no legacy database connection.

## Purpose

Deterministic lab proving which cursor polling strategies can or cannot achieve
gap-free capture on PostgreSQL 16, informing ADR-0007 O3 (monotonic polling)
**without** accepting polling as production-ready.

## Topology

```text
Compose project: hudhud-legacy-polling-lab
Network:         hudhud_polling_lab (bridge, dedicated)
Volume:          hudhud_polling_lab_pgdata
Service:         postgres:16-alpine (profile: polling-lab)
Host ports:      none
Credentials:     polling_lab / polling_lab_dev_only (synthetic lab only)
Init schema:     infra/labs/legacy-polling/schema/01_init.sql
```

Tables (synthetic, not legacy Alembic):

| Table | Role |
|-------|------|
| `lab_events` | Append-only UUID PK + `occurred_at` (shipment_events analog) |
| `lab_entities` | Mutable rows with `updated_at` (TimestampMixin analog) |
| `lab_events_sequenced` | Hypothetical `capture_seq BIGSERIAL` (schema change candidate) |
| `lab_bridge_cursor` | Platform-owned persisted cursor store |

## Scenario-Result Matrix

| # | Scenario | timestamp_only | uuid_only | (ts,id) | updated_at | overlap+dedupe | monotonic_seq |
|---|----------|----------------|-----------|---------|------------|----------------|---------------|
| 1 | UUID non-monotonic | complete | **gap** | complete | — | complete | — |
| 2 | Same timestamp | **gap** | **gap** | complete | — | complete | — |
| 3 | Late commit, earlier ts | **gap** | **gap** | **gap** | — | duplicate_safe | complete |
| 4 | Long txn cross boundary | **gap** | **gap** | **gap** | — | duplicate_safe | complete |
| 5 | Update after HWM | — | — | **gap** | duplicate_safe | **gap** | — |
| 6 | Hard delete, no tombstone | unprovable | unprovable | unprovable | unprovable | unprovable | unprovable |
| 7 | Persisted cursor restart | complete | **gap** | complete | — | duplicate_safe | complete |
| 8 | Overlap window dedupe | **gap** | **gap** | **gap** | — | duplicate_safe | complete |
| 9 | (timestamp, UUID) composite | **gap** | **gap** | complete | — | complete | — |
| 10 | Monotonic sequence | complete | **gap** | complete | — | complete | complete |
| 11 | Snapshot + post-HWM | complete | **gap** | complete | — | complete | — |
| 12 | Concurrent writers | complete | **gap** | complete | — | complete | — |

Full machine-checkable matrix: [`strategies/matrix.yaml`](strategies/matrix.yaml)

## Strategies Proven Unsafe (PostgreSQL lab)

1. **`uuid_only`** — UUID v4 PK is not time-monotonic; phased inserts lose rows permanently.
2. **`timestamp_only`** — equal `occurred_at` values lose rows when cursor advances by timestamp alone.
3. **`(occurred_at, id)` composite** — cannot capture rows that commit after HWM with earlier application timestamps.
4. **`updated_at_only`** — ADR-0006 H2; misses non-touching semantics; cannot prove deletes.
5. **Any poll without tombstones** — hard deletes make completeness **unprovable**.

## Conditions Required for Safe Polling

| Condition | Requirement |
|-----------|-------------|
| Append-only streams | `(occurred_at, id)` or `(created_at, id)` cursor + inbox dedupe |
| Late commits | Overlap lookback (duplicate_safe) or CDC/outbox |
| Mutable entities | CDC or tombstones; not timestamp-only poll |
| Completeness proof | DB-generated monotonic sequence on capture table (schema change) |
| Hard deletes | Tombstone strategy or CDC — poll alone insufficient |

## Evidence Classification

| Class | What this lab proves |
|-------|---------------------|
| **PostgreSQL behavior** | Scenarios 1–12 with deterministic assertions |
| **Legacy suitability inference** | Patterns analogous to `shipment_events` / `audit_logs` per ADR-0007 |
| **Unresolved production evidence** | Real write-path coverage, lag SLO, zero-gap drill (ADR-0007 E3) |

**Do not** declare polling accepted because synthetic `capture_seq` works — legacy has no such column today.

## Run

```bash
# Static tests (no Docker)
uv run pytest tests/polling_lab/test_compose_topology.py tests/polling_lab/test_strategies_matrix.py -v

# Integration (Docker required)
uv run pytest tests/polling_lab/test_scenarios_integration.py -v -m integration

# Cleanup
infra/labs/legacy-polling/scripts/cleanup.sh
```

## Cleanup

`scripts/cleanup.sh` runs `docker compose down -v` for project `hudhud-legacy-polling-lab`
and verifies `hudhud_polling_lab` network and `hudhud_polling_lab_pgdata` volume are absent.
