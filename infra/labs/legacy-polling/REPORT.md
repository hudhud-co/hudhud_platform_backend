# HUDHUD W3-B Legacy Polling Completeness Lab

**Marker:** `HUDHUD_W3_B_POLLING_LAB`  
**ADR:** [0007 Legacy Event Bridge Strategy](../../docs/adr/0007-legacy-event-bridge-strategy.md)  
**Workstream:** W3-B  
**Scope:** Synthetic PostgreSQL 16 only — no legacy database connection.

## Purpose

Deterministic lab proving which cursor polling strategies can or cannot achieve
gap-free capture on PostgreSQL 16, informing ADR-0007 **without** accepting polling
as the authoritative general capture mechanism.

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

## Outcome terminology

| Outcome | Meaning |
|---------|---------|
| `complete` | Expected rows captured in this narrow synthetic scenario only — **not** production gap-free proof |
| `duplicate_safe` | Overlap/dedupe tolerates duplicates — **does not** imply gap-free completeness |
| `gap` | At least one expected row permanently missed |
| `unprovable` | Completeness cannot be established (e.g. hard deletes) |

## Scenario-Result Matrix

| # | Scenario | timestamp_only | uuid_only | (ts,id) | updated_at | overlap+dedupe | monotonic_seq |
|---|----------|----------------|-----------|---------|------------|----------------|---------------|
| 1 | UUID non-monotonic | complete | **gap** | complete | — | duplicate_safe | — |
| 2 | Same timestamp | **gap** | **gap** | complete | — | duplicate_safe | complete* |
| 3 | Late commit, earlier ts | **gap** | **gap** | **gap** | — | duplicate_safe | **gap** |
| 4 | Long txn cross boundary | **gap** | **gap** | **gap** | — | duplicate_safe | **gap** |
| 5 | Update after HWM | — | — | **gap** | duplicate_safe | **gap** | — |
| 6 | Hard delete, no tombstone | unprovable | unprovable | unprovable | unprovable | unprovable | unprovable |
| 7 | Persisted cursor restart | complete | **gap** | complete | — | duplicate_safe | complete* |
| 8 | Overlap window dedupe | **gap** | **gap** | **gap** | — | duplicate_safe | complete* |
| 9 | (timestamp, UUID) composite | **gap** | **gap** | complete | — | duplicate_safe | complete* |
| 10 | Monotonic sequence (committed) | complete | **gap** | complete | — | duplicate_safe | complete* |
| 11 | **Seq alloc ≠ commit order** | gap | gap | gap | — | duplicate_safe | **gap** |
| 12 | Snapshot + post-HWM (illustrative) | duplicate_safe | gap | duplicate_safe | — | duplicate_safe | duplicate_safe |
| 13 | Concurrent writers | complete | **gap** | complete | — | duplicate_safe | complete* |

\* `monotonic_sequence` **complete** applies only when all rows are committed before poll and
allocation order matches visibility. Scenario 11 proves the counterexample when it does not.

Full machine-checkable matrix: [`strategies/matrix.yaml`](strategies/matrix.yaml)

## PostgreSQL sequence counterexample (scenario 11)

**[verified PostgreSQL behavior]** `SEQUENCE`, `SERIAL`, and `BIGSERIAL` allocate on `INSERT`
before transaction `COMMIT`. Allocation order is **not** commit/WAL order.

Counterexample (lab uses `PREPARE TRANSACTION` / `COMMIT PREPARED`):

1. Transaction T1 allocates `capture_seq=1` but remains uncommitted (prepared).
2. Transaction T2 allocates `capture_seq=2` and commits — visible to poller.
3. Poller observes sequence 2 and advances HWM to 2.
4. T1 commits (prepared transaction) — row with sequence 1 becomes visible.
5. Strict `WHERE capture_seq > HWM` poll **permanently misses** sequence 1.

**Do not** describe a database-generated sequence alone as proving gap-free polling.

## Strategies proven unsafe (PostgreSQL lab)

1. **`uuid_only`** — UUID v4 PK is not time-monotonic; phased inserts lose rows permanently.
2. **`timestamp_only`** — equal `occurred_at` values lose rows when cursor advances by timestamp alone.
3. **`(occurred_at, id)` composite** — cannot capture rows that commit after HWM with earlier application timestamps.
4. **`updated_at_only`** — ADR-0006 H2; misses non-touching semantics; cannot prove deletes.
5. **`monotonic_sequence` without commit-order proof** — sequence allocation precedes commit; see scenario 11.
6. **Finite overlap lookback** — `duplicate_safe` only when maximum transaction duration and late-arrival time are **bounded and operationally enforced**; unbounded lateness cannot prove completeness.
7. **Any poll without tombstones** — hard deletes make completeness **unprovable**.

## Snapshot + HWM polling (scenario 12)

Synthetic snapshot scenarios **illustrate** post-HWM row capture when row counts match. They
**do not** prove a production zero-gap protocol. Production requires:

- Capture activation before or atomically with snapshot position
- No post-HWM write loss
- Deterministic backfill/live deduplication
- Restartable position store
- Semantic reconciliation (ADR-0006)

Lab outcomes for scenario 12 are classified `duplicate_safe` — not production gap-free proof.

## Conditions required for bounded polling (not authoritative CDC replacement)

| Condition | Requirement |
|-----------|-------------|
| Append-only streams | `(occurred_at, id)` cursor + inbox dedupe on stable `event_id` |
| Late commits | Overlap lookback (**duplicate_safe**, bounded lateness) or CDC/outbox |
| Mutable entities | CDC or tombstones; not timestamp-only poll |
| Sequence columns | Never treat allocation order as commit order |
| Hard deletes | Tombstone strategy or CDC — poll alone insufficient |

## Evidence classification

| Class | What this lab proves |
|-------|---------------------|
| **PostgreSQL behavior** | Scenarios 1–13 with deterministic assertions |
| **Legacy suitability inference** | Patterns analogous to `shipment_events` / `audit_logs` per ADR-0007 |
| **Synthetic-lab observation** | Overlap dedupe, illustrative snapshot — not production zero-gap |
| **Unresolved production evidence** | Real write-path coverage, lag SLO, zero-gap drill (ADR-0007 gates) |

**Do not** declare polling accepted as authoritative capture because synthetic lab scenarios pass.
Legacy has no `capture_seq` column today; ADR-0007 selects CDC as transitional transport direction.

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
