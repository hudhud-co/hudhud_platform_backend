# Runtime capacity and port budget

Workstream: **HUDHUD W2-E**  
Registry: [`architecture/runtime-port-registry.yaml`](../../architecture/runtime-port-registry.yaml)  
Verifier: `uv run python scripts/quality/verify_port_allocations.py`

Statement classes used throughout: **evidence**, **proposal**, **decision**, **assumption**,
**unresolved policy**. A suggested deployable count is **not** an architectural fact.

---

## Purpose

Establish an evidence-based baseline for:

1. **Host-port governance** — verified legacy bindings, explicit reservations, configurable and
   unresolved platform allocations.
2. **Provisional capacity scenarios** — formulas and validation procedures where production
   numbers are not yet evidenced.
3. **HA and production gates** — what single-node Compose can and cannot claim today.

This document does **not** authorize Compose changes, service bootstrap, or production sizing
sign-off.

---

## Verified runtime evidence

| Item | Source | Implication |
|------|--------|-------------|
| Foundation F0 — no platform compose yet | `infra/README.md` | Platform host binds are reservations/unresolved |
| 16 GB single host (planning input) | `infra/README.md`, ADR-0001 | Cited as planning scenario — not verified production host evidence in this repository |
| Legacy prod stack | `docs/audit/legacy-runtime-inventory.md` | app loopback `:8001`, Postgres, Redis, MinIO |
| Legacy dev host publishes | `hudhud-backend/deploy/docker-compose.local.yml` | `8001`, `5433`, `6380`, `9000`, `9001` |
| Legacy prod MinIO loopback | `hudhud-backend/deploy/docker-compose.prod.yml` | `127.0.0.1:9010→9000` |
| NATS absent in legacy | `docs/audit/legacy-runtime-inventory.md` | JetStream is greenfield (ADR-0002) |
| Single-node JetStream `replicas: 1` | ADR-0002 **[decision]** | Durability on disk; no quorum HA |
| 3–5 transitional deployables (proposal) | ADR-0001 **[proposal]** | Initial plateau capacity model |
| Postgres `max_connections` default ~100 | ADR-0001 **[assumption]** | Connection budget driver |
| Push outbox worker (legacy) | `legacy-runtime-inventory.md` | Worker budget precedent exists |
| No backup automation documented | `legacy-runtime-inventory.md` | Backup headroom unresolved |

---

## Port inventory summary

Full machine-readable registry: [`architecture/runtime-port-registry.yaml`](../../architecture/runtime-port-registry.yaml).

### Verified legacy host bindings

| Role | Dev/local host | Staging host | Production host | Container | Public |
|------|----------------|--------------|-----------------|-----------|--------|
| FastAPI HTTP | `8001` | `127.0.0.1:8001` | `127.0.0.1:8001` | `8001` | No |
| PostgreSQL | `5433→5432` | unpublished | unpublished | `5432` | No |
| Redis | `6380→6379` | unpublished | unpublished | `6379` | No |
| MinIO API | `9000` | unpublished | `127.0.0.1:9010→9000` | `9000` | No |
| MinIO console | `9001` | unpublished | unpublished | `9001` | No |

### Platform reservations and unresolved allocations

| Role | Local reservation | Staging | Production | Notes |
|------|-------------------|---------|------------|-------|
| NATS client | internal only by default | unresolved | unresolved | Eventing Compose publishes no fixed host ports unless local override |
| NATS monitoring | internal only by default | unresolved | unresolved | See `infra/compose/eventing-foundation.compose.yaml` |
| Transitional HTTP block | `8100–8149` (proposed reservation, inactive) | unresolved | unresolved | Not an accepted allocation — pending capacity proof |
| Gateway HTTP | ADR-0004 dependent (proposed) | unresolved | unresolved | **Not in Wave 2 / S1** — deferred until ADR-0004 accepted |
| Observability ingress | unresolved | unresolved | unresolved | Prometheus/Grafana/Loki not scaffolded |
| Per-service Postgres | internal `5432` only | unpublished | unpublished | Repeatable per isolated network |

**Collision policy:** active host bindings are checked per environment and protocol by
`scripts/quality/verify_port_allocations.py`. Configurable (`env_var`) and `unresolved` entries
do not participate in fixed-port collision detection.

---

## Provisional capacity scenarios

These scenarios model ADR-0001 plateaus **P1–P5** as **3–5 transitional runtimes** plus shared
infrastructure. They are planning aids, not production facts.

### Scenario S0 — Legacy coexistence (today)

| Component | Count | CPU (proposal) | Memory (proposal) | Notes |
|-----------|-------|----------------|-------------------|-------|
| Legacy monolith app | 1 | measure | measure | Verified running today |
| Legacy Postgres | 1 | — | — | Single shared DB |
| Legacy Redis | 1 | — | — | OTP / rate limits |
| Legacy MinIO | 1 | — | — | Evidence storage |
| Platform services | 0 | — | — | F0 |

**Validation:** legacy `docker stats` on production host; record p95 CPU/RSS — **unresolved
evidence in platform repo**.

### Scenario S1 — Wave 2 eventing foundation (current integration scope)

| Component | Count | Replicas | Notes |
|-----------|-------|----------|-------|
| NATS JetStream | 1 server | `replicas: 1` per stream (ADR-0002) | Single-node; no HA |
| Observability (optional) | 0–1 stack | 1 | Ports unresolved |
| Transitional app runtimes | 0 | — | Eventing foundation only — **Gateway deferred** (ADR-0004 Proposed) |

**Memory budget formula (proposal — Wave 2 scope only):**

```text
nats_ram_mb ≈ 256 + (jetstream_storage_gb × 64)   # tune after disk benchmark
observability_ram_mb ≈ unresolved until stack chosen
wave2_eventing_total_mb ≈ nats_ram_mb + observability_ram_mb + os_reserve_mb
os_reserve_mb ≥ 2048   # planning assumption for 16 GB host scenario
```

Gateway is **not** included in the current Wave 2 formula. Future Gateway capacity remains
ADR-0004-dependent and unresolved.

**Validation procedure:**

1. Stand disposable compose on staging host.
2. Publish sustained test stream at provisional envelope size (ADR-0002 soft limit 64 KiB).
3. Measure `jetstream_storage_bytes`, process RSS, and disk write rate.
4. Record results in cutover/capacity evidence package — do not freeze ADR defaults without this.

### Scenario S2 — Initial plateau (3 transitional runtimes)

Aligns with ADR-0001 **Option C** lower bound: e.g. P1 Platform Edge, P3 Commerce, P4 Network Ops.

| Component | Count | Notes |
|-----------|-------|-------|
| Transitional app images | 3 | Each may host multiple bounded contexts internally |
| Dedicated Postgres (per extracted context over time) | 1→N | Start shared instance only for F0 bootstrap; exit per ADR-0001 |
| NATS JetStream | 1 | Shared |
| Redis | 0–1 | Required when OTP/rate-limit contexts extract |
| MinIO | 1 | Shared object store |
| Workers (outbox/inbox relay) | ≤3 embedded + legacy worker parity | ADR-0002 R1 embedded relay v1 |

**Connection budget formula (proposal):**

```text
app_connections ≈ Σ(deployables) × (pool_size_per_deployable)
pool_size_per_deployable — default assumption: 10 (ADR-0001)
admin_and_migration_headroom ≈ 10
pgbouncer_multiplier — if used: effective_connections ≈ app_connections / avg_queries_per_tx
required_max_connections ≥ app_connections + admin_and_migration_headroom + replication_slots
```

**PgBouncer (proposal):** introduce when `required_max_connections` exceeds evidenced
`max_connections` on the host. Transaction pooling mode for OLTP APIs; session mode for migrations
only. Exact mode — **unresolved policy**.

**Validation:**

1. Load test each deployable at expected concurrent requests (TBD — **unresolved traffic**).
2. Monitor `pg_stat_activity`, pool wait time, and NATS consumer pending.
3. Scale pool down until wait time SLO violated; document per-deployable pool ceiling.

### Scenario S3 — Upper plateau (5 transitional runtimes)

Adds P2 Engagement/Proof and P5 Field Ops groupings (ADR-0001).

| Risk | Mitigation (proposal) |
|------|------------------------|
| RAM exhaustion on 16 GB host | Cap concurrent Blue/Green pairs; stagger deploys |
| Connection storm | PgBouncer + lower per-service pool |
| JetStream disk growth | Per-stream retention from ADR-0002; monitor `jetstream_storage_bytes` |
| Port collisions | Run port verifier before compose authoring |

**Scale-up trigger (proposal):** sustained host memory > 85% for 1 h **or** JetStream disk > 70%
of allocated volume **or** Postgres connection wait > 5% of requests.

**Scale-out trigger (proposal):** second host required when Scenario S3 cannot satisfy scale-up
mitigations — **unresolved timeline** (ADR-0001).

### Scenario S4 — Steady-state direction (not a near-term plan)

One deployable per bounded context (~20+). **Rejected for initial plateau** per ADR-0001 due to
16 GB and connection budget. Documented as long-term exit direction with per-context exit criteria.

---

## Event storage and replay windows

From ADR-0002 **[decision/proposal]** — numeric values are **provisional defaults**:

| Stream class | Max age (provisional) | Storage | Replicas | Capacity note |
|--------------|----------------------|---------|----------|---------------|
| Operational (`HUDHUD_*` ops) | 3–7d | File | 1 | Size ≈ `publish_rate × avg_bytes × age` |
| Wallet | 30d | File | 1 | Longer retention — higher disk |
| Audit transport (`HUDHUD_AUDIT`) | 365d (provisional) | File | 1 | **Transport window only** — not legal archive; size planning MUST account for large provisional window until capacity proof replaces default |
| DLQ (`HUDHUD_DLQ`) | 30d (provisional) | File | 1 | Poison isolation |

**Disk formula (proposal):**

```text
stream_disk_gb ≈ (messages_per_sec × avg_envelope_bytes × retention_seconds) / (1024^3) × 1.3
```

The `1.3` factor accounts for JetStream file overhead — **replace with benchmark**.

**Replay window:** bounded by stream `max_age` and consumer `DeliverPolicy`. Full aggregate replay
requires `aggregate_id` subject shard and single consumer per partition (ADR-0002).

**Validation:** load test with representative `event_type` mix; compare measured growth to formula;
adjust `max_age` or disk allocation before production freeze.

---

## Database and PgBouncer budgets

| Input | Status | Formula / procedure |
|-------|--------|---------------------|
| `max_connections` | unresolved policy | Read from host Postgres config (name only) |
| Pools per deployable | assumption 10 | ADR-0001 planning default |
| Migration connections | proposal | Dedicated role; max 2 concurrent per service |
| Read replicas | not evidenced | Count as 0 for initial plateau |
| Cross-service queries | forbidden | No shared reader pools across services |

**Alert thresholds (proposal):**

- `connections_used / max_connections > 0.8` for 15 min → warning
- `> 0.9` → page
- `pgbouncer_wait_count` increasing → investigate pool size vs. transaction length

---

## Redis and worker budgets

### Redis

**Evidence:** legacy uses Redis for OTP, rate limits, delivery OTP sessions
(`legacy-runtime-inventory.md`). Platform extraction may share one Redis for transitional plateaus
or isolate per context — **unresolved policy**.

**Memory formula (proposal):**

```text
redis_ram_mb ≈ key_count × avg_value_bytes × 1.5 + 128
```

**Validation:** sample production key cardinality from legacy — **not yet performed in platform
repo**.

### Workers

| Worker type | Evidence | Budget approach |
|-------------|----------|-----------------|
| Push outbox (legacy) | verified profile | 1 process; CPU scales with batch size |
| Delivery evidence cleanup | verified profile | 1 process; low duty cycle |
| Integration outbox relay (platform) | ADR-0002 proposal | 1 embedded per publishing service |
| Inbox consumer (platform) | ADR-0002 proposal | 1+ pull workers per durable consumer |

**Formula (proposal):**

```text
worker_cpu_millicores ≈ (batch_size / batch_interval_sec) × cost_per_message_mcpu
```

Measure `cost_per_message` during integration tests — **unresolved**.

---

## CPU and memory requests/limits

Platform services are not scaffolded. Until images exist, use **measure-then-set**:

| Phase | CPU | Memory |
|-------|-----|--------|
| Bootstrap / local | no limit | host default |
| Staging disposable | request = p50, limit = p95 × 1.5 | same |
| Production initial plateau | request = staged p95, limit = peak × 1.25 | evidence package required |

**Compose (future):** declare `deploy.resources` per changed service only (Blue/Green per
ADR-0001). Do not copy limits across unrelated deployables.

**Kubernetes (if adopted later):** out of scope for F0 — **unresolved**.

---

## Disk growth and alerts

| Volume | Driver | Alert threshold (proposal) |
|--------|--------|----------------------------|
| JetStream file store | event rate × retention | > 70% allocated |
| Postgres per service | table growth + WAL | > 75% volume; WAL > 20% of data |
| MinIO | evidence objects | > 80% bucket quota |
| Container logs (local) | stdout volume | > 2 GB per service without rotation |

**Log rotation (proposal):** json-file driver `max-size: 100m`, `max-file: 3` for local dev;
centralized aggregation for staging/prod — **unresolved stack**.

---

## Log and metric retention

| Signal | Legacy | Platform proposal | Owner |
|--------|--------|-------------------|-------|
| App logs | unstructured/basic | JSON per deployable | each service |
| Metrics | not evidenced | Prometheus-compatible | observability stack |
| Traces | not evidenced | W3C `traceparent` | each service + gateway |
| Audit | DB tables + partial | Audit service + `HUDHUD_AUDIT` transport | audit context |

**Retention (proposal — requires sign-off):**

- Metrics: 15d high-resolution, 90d downsampled
- Logs: 7d hot, 30d warm (if aggregator exists)
- Traces: 3d
- JetStream: per-stream ADR-0002 table (transport only)

---

## Backup headroom

**Evidence gap:** no automated backup scripts in legacy or platform repos.

**Proposal checklist before any production cutover (ADR-0006):**

1. Define RPO/RTO per database — **unresolved policy**
2. Allocate backup storage ≥ `2 × Σ(db_size)` for first full + incremental cycle
3. Prove restore on disposable instance (stage 4+ of ADR-0006)
4. Include JetStream snapshot strategy in Wave 0 evidence — **unresolved**

---

## Environment distinctions

| Concern | Development | Staging | Production |
|---------|-------------|---------|------------|
| Host port publish | broader (legacy dev pattern) | loopback / internal | loopback / edge TLS |
| Public exposure | false default | false default | explicit per registry entry |
| Source mounts | legacy local only | forbidden (platform) | forbidden |
| Capacity proof | optional | required before cutover | required + sign-off |
| HA | none | none | none on initial single host |
| Collision checks | run verifier on compose PR | run in CI | run in CI |

---

## Scale-up and scale-out triggers

| Signal | Scale-up (same host) | Scale-out (add host) |
|--------|----------------------|----------------------|
| Memory pressure | reduce Blue/Green overlap; lower pool sizes | Scenario S3 insufficient |
| CPU saturation | add worker replicas where safe | split deployable per exit criteria |
| JetStream lag | increase consumer `MaxAckPending`; disk | NATS cluster (ADR-0002 future HA) |
| Postgres connections | PgBouncer; pool tuning | dedicated DB host |
| Disk | retention tuning; volume expand | object store migration |

---

## HA gaps and exit criteria

| Gap | Current state | Exit criteria |
|-----|---------------|---------------|
| Single NATS node | ADR-0002 decision | 3-node cluster; streams `replicas: 3`; tested failover |
| Single Postgres per wave | legacy + initial plateau | HA replica or managed DB with tested failover |
| Single 16 GB host | evidence | Second host or vertical upgrade with capacity proof |
| No backup automation | evidence gap | Automated backup + restore drill in evidence package |
| Loopback-only legacy API | verified | Gateway TLS + platform edge public exposure documented |
| Transitional grouping | ADR-0001 plateaus | Each inner context meets exit criteria table in ADR-0001 |

---

## Validation procedures (summary)

1. **Ports:** `uv run python scripts/quality/verify_port_allocations.py`
2. **Architecture:** `uv run python scripts/quality/verify_boundaries.py`
3. **Compose (when exists):** `verify-compose-topology` skill
4. **Capacity:** disposable load test → compare to formulas → update registry reservations
5. **Cutover:** ADR-0006 evidence package before credential revocation

---

## Provisional assumptions

1. Postgres `max_connections` ≈ 100 until host config audited.
2. Per-deployable connection pool ≈ 10 until load test proves otherwise.
3. Initial transitional plateau = 3–5 runtimes (ADR-0001 proposal).
4. NATS client/monitoring ports `4222`/`8222` match pinned image defaults — verify at bootstrap.
5. Platform local HTTP block `8100–8149` avoids verified legacy dev ports — reservation only.
6. 16 GB host reserves ≥ 2 GB for OS/cache — **planning assumption**, not verified production evidence.

---

## Unresolved evidence and policy

1. Production traffic, RPS, and message publish rates — no repository evidence.
2. Exact `max_connections`, CPU, RAM on production host — names only in audits.
3. PgBouncer adoption timing and pooling mode.
4. Observability stack choice and port assignments.
5. Per-context host HTTP port assignments within transitional block.
6. Backup RPO/RTO and automation owner.
7. Second-host timeline for full per-context split.
8. Redis shared vs. per-service on platform.
9. Team size / on-call rotation affecting deployable count (ADR-0001).
10. Gateway production public bind and TLS termination topology (ADR-0004) — **deferred from Wave 2**.

---

## Related documents

- ADR-0001: transitional deployables and extraction order
- ADR-0002: JetStream topology and retention defaults
- ADR-0006: one-writer cutover and credential revocation
- [`architecture/runtime-port-registry.yaml`](../../architecture/runtime-port-registry.yaml)
- [`docs/audit/legacy-runtime-inventory.md`](../audit/legacy-runtime-inventory.md)
- [`infra/README.md`](../../infra/README.md)

---

## Explicit non-actions (this workstream)

- No Compose file creation or modification
- No ADR edits
- No service bootstrap
- No legacy repository mutation
- No production deployable count finalized
