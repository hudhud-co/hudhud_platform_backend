# ADR-0006: One-Writer Data Cutover, Reconciliation, and Credential Revocation

- **Status:** proposed
- **Date:** 2026-08-30
- **Deciders:** (pending — platform architecture review)
- **Workstream:** W1-F
- **Implementation allowed:** no (pending acceptance and dependent ADRs)

Label key: **[evidence]** verified from repository or legacy audit; **[proposal]** recommended design not yet accepted; **[decision]** binding only after acceptance; **[assumption]** engineering default pending validation; **[unresolved policy]** requires named deciders.

---

## Context

**[evidence]** Platform invariant (`architecture/invariants.md` §Database Extraction): one-writer cutover per extracted datastore; bidirectional dual-write is forbidden; credential revocation is a mandatory cutover gate.

**[evidence]** Legacy (`hudhud-backend` @ `2e375057fdf9b9ce8416408a4436303be5301def`) operates as a clean modular monolith with a **single PostgreSQL 16 instance**, **single Alembic chain** (78 revisions, head `b8c9d0e1f2a3`), and **no per-module migration isolation** (`docs/audit/legacy-baseline.md`, `docs/audit/legacy-data-ownership-inventory.md`). All bounded contexts share one schema namespace and one `DATABASE_URL` credential consumed by the app and optional workers.

**[evidence]** Legacy exhibits **multi-writer ambiguity** on critical tables: at least five modules mutate `shipments.current_status` (pickup, hub, linehaul, delivery_task, shipment) and extensive **cross-domain foreign keys** (e.g. `shipments.order_id → orders.id`, `shipments.merchant_id → merchants.id`, `orders.receiver_contact_id → receiver_contacts.id`) (`docs/audit/legacy-data-ownership-inventory.md`).

**[evidence]** Primary keys are **UUID v4** (`uuid.uuid4`) across operational tables; timestamps use **`TimestampMixin`** (`created_at`, `updated_at` with `func.now()`) on mutable entities; append-only event tables carry domain **`occurred_at`** (e.g. `shipment_events.occurred_at`) distinct from row `created_at` (`app/shared/db/mixins.py`, `app/modules/shipment/infrastructure/models.py`).

**[evidence]** Background mutation paths beyond HTTP request handlers include: **push outbox worker** (`scripts/run_push_outbox_worker.py`, `notification.push_outbox` table), **delivery evidence cleanup worker** (`shipment/application/delivery_evidence_attachment_cleanup_worker.py`), and **in-process synchronous cross-module calls** within the same DB transaction (e.g. `complete_delivery_task.py` → COD + wallet + shipment status). No automated backup/restore scripts are documented in legacy (`docs/audit/legacy-runtime-inventory.md` §Backup & Recovery).

**[evidence]** Deployment uses Docker Compose with a **single app credential** to Postgres; optional worker profiles reuse the same app image and `DATABASE_URL` (`deploy/docker-compose.prod.yml`). No per-module database roles, no network segmentation for DB access, and no credential rotation evidence in repository.

**[proposal]** This ADR defines a **reusable one-writer extraction and data-cutover protocol** applicable to any bounded context migrating from the legacy shared database to a platform service-owned database. It specifies evidence gates, reconciliation layers, credential revocation requirements, and rollback boundaries. It does **not** implement migrations, schemas, replication jobs, Compose files, or live cutover.

**[evidence]** Legacy dirty file `scripts/dev_pickup_driver_simulator.py` was not inspected beyond baseline inventory and was not modified during this ADR preparation.

### Dependencies on prior ADRs

| ADR | Topic (expected) | Relevance to cutover |
|-----|------------------|----------------------|
| ADR-0001 | Transitional deployable grouping | Determines which table clusters cut over together; does not change one-writer rule |
| ADR-0002 | Event envelope, outbox/inbox, JetStream | Forward replication of post-HWM changes; command/fact path during compatibility |
| ADR-0003 | Shipment lifecycle authority | Resolves multi-writer `shipments` ambiguity; defines canonical writer after cutover |
| ADR-0004 | Identity and service trust | Service credentials for cutover tooling; break-glass authorization |
| ADR-0005 | Finance / settlement | COD/wallet table clusters; policy-blocked contexts excluded from first wave |

**[assumption]** ADR-0001 through ADR-0005 may remain `proposed` when this ADR is accepted; cutover execution for a given context remains blocked until that context's writer, deployable boundary, and messaging contracts are accepted.

---

## Options

### Cutover orchestration model

| Option | Summary | Trade-offs |
|--------|---------|------------|
| A. Phased state machine (recommended) | Sixteen named stages with explicit one-writer at each point; forward replication only | Operational rigor; longer calendar time; requires runbooks |
| B. Big-bang snapshot + flip | Stop legacy writes, dump, restore, resume on target | Simple narrative; unacceptable downtime; no incremental validation |
| C. Bidirectional dual-write | Legacy and target both accept writes with conflict resolution | **Forbidden** by platform invariant; silent divergence risk |
| D. Permanent shared-database access | Target service reads/writes legacy DB indefinitely | **Forbidden**; erases service independence; credential sprawl |

### High-water mark (HWM) capture

| Option | Summary | Trade-offs |
|--------|---------|------------|
| H1. Transaction-scoped snapshot timestamp | `pg_snapshot_xmin` / repeatable-read snapshot time at backfill start | Simple; requires quiesce or careful ordering for hot tables |
| H2. Per-table `updated_at` ceiling | Max `updated_at` ≤ HWM at snapshot boundary | Works where `TimestampMixin` present; misses hard deletes; clock skew risk |
| H3. Logical sequence / LSN bookmark | PostgreSQL WAL LSN or change-data-capture offset | Precise; needs CDC tooling (**unresolved infrastructure**) |
| H4. Domain event cursor | Last `shipment_events.id` / `occurred_at` for event-sourced clusters | Semantically meaningful; not all tables have event logs |
| H5. Hybrid (recommended per cluster) | H1/H2 for bulk tables + H4 for lifecycle timelines | Best accuracy; more documentation per extraction |

### Forward replication mechanism

| Option | Summary | Trade-offs |
|--------|---------|------------|
| R1. Change-data capture (Debezium / logical replication) | Stream WAL changes after HWM to target | Near-real-time; ops complexity; schema drift must be managed |
| R2. Transactional outbox relay (legacy side) | Legacy emits changes to outbox table; relay applies to target | Aligns with ADR-0002; requires legacy code change (read-only repo — platform implements on extracted path only) |
| R3. Polling delta on `updated_at` / event cursor | Periodic idempotent upserts for rows changed since HWM | Simple; lag; misses deletes unless tombstone strategy |
| R4. Command/fact replay via JetStream | Post-cutover writes become messages; target is sole DB writer | Clean one-writer; requires API availability during transition |

**[proposal]** Recommend **Option A** orchestration with **H5 hybrid HWM** and replication strategy **selected per table cluster** (R1/R3/R4) based on change rate, delete semantics, and ADR-0002 acceptance — not a single global mechanism.

---

## Decision drivers

1. **One-writer invariant** — at every lifecycle point exactly one authoritative writer; bidirectional dual-write forbidden (`architecture/invariants.md`).
2. **Credential revocation gate** — extraction is incomplete until legacy write credentials for the cluster are revoked and proven (`AGENTS.md`, `plan-extraction-cutover` skill).
3. **Silent divergence prevention** — row-count equality alone is insufficient; multi-layer reconciliation required.
4. **Irreversible operational facts** — physical delivery, COD collection, and append-only audit/event rows cannot be "rolled back" via database restore without business harm (`architecture/invariants.md` §Physical Delivery and Finance).
5. **Service independence** — no permanent cross-service DB credentials or FKs (`architecture/invariants.md` §Service Independence).
6. **Legacy evidence** — multi-writer and cross-FK legacy state must be explicitly inventoried and remediated in cutover plan, not assumed away.
7. **Least privilege** — per-service DB roles, network allowlists, and auditable break-glass only.

---

## Decision

**[proposal] (not accepted):** Adopt **Option A — phased sixteen-stage state machine** as the mandatory cutover protocol for every datastore extraction from legacy to platform. Each extraction wave produces a **reusable evidence package** (template below) signed off before credential revocation.

**One-writer rule:** At any instant, for any mutable row cluster, exactly one system holds **write authority**. Temporary compatibility MUST route writes as **commands to the current owner** (HTTP or message), never as direct dual writes to legacy and target databases.

**Dual-write status:** bidirectional dual-write is **forbidden (confirmed)**.

---

## Phased cutover state machine

Each stage lists: **authoritative writer**, **evidence required**, **gate to proceed**, **rollback boundary**.

| Stage | Name | Authoritative writer | Evidence / gate | Rollback boundary |
|-------|------|---------------------|-----------------|-------------------|
| 1 | Establish source and target ownership | Legacy DB (source); target schema owner TBD per service | Signed ownership matrix row; ADR acceptance for writer; table→service map | N/A — planning only |
| 2 | Inventory tables, columns, keys, constraints, jobs, writers | Legacy | Inventory doc: PKs, FKs, indexes, triggers, workers, write paths (grep + migration refs); ambiguity register | Discard inventory — no data moved |
| 3 | Define source high-water mark | Legacy | HWM record: mechanism (H1–H5), capture timestamp/LSN/cursor, scope per table; clock skew note | Re-capture HWM — no target writes yet |
| 4 | Create target schema under target ownership | Target (DDL only) | Alembic upgrade proof on disposable DB; **no legacy write credential** on target | Drop target schema — legacy unaffected |
| 5 | Backfill deterministic snapshots | Legacy (read); target (load) | Row counts per table; PK set hash; snapshot checksum manifest; idempotent load keys | Truncate target — re-run backfill |
| 6 | Capture changes after HWM | Legacy | Delta manifest: rows/events with `(updated_at > HWM)` or CDC offset; tombstone policy for deletes | Extend HWM window — re-backfill delta |
| 7 | Apply forward replication idempotently | Legacy (source truth); target (apply) | Replication lag metric; idempotency key coverage; zero unapplied backlog at gate | Stop relay — target still read-only to consumers |
| 8 | Run shadow reads | Legacy (read authority for prod); target (shadow) | Shadow read diff rate below threshold; sample semantic compares | Disable shadow — no user impact |
| 9 | Structural and semantic reconciliation | Legacy (prod read); target (compare) | Reconciliation matrix pass (see below); exception queue empty or waived with sign-off | Fix target — legacy still authoritative |
| 10 | Transfer read ownership | Target (read prod); legacy (read deprecated) | Traffic switch evidence; error rate SLO; read credential scope reduced on legacy | Revert read routes to legacy |
| 11 | Quiesce or fence old writers | Legacy (write fenced) | Write fence: feature flags, connection pool drain, worker stop proof | Unfence legacy — pre-write-cutover |
| 12 | Transfer write ownership | Target (sole writer) | Single successful write path; legacy writes rejected or no-op with alert | **Critical boundary** — see rollback matrix |
| 13 | Revoke legacy write credentials | Target | Credential revocation proof (see Security); audit log entries; connection failure tests | **Irreversible without re-provisioning** |
| 14 | Observe new writer | Target | SLO dashboard green window; reconciliation spot-checks; incident log empty | Forward-fix only |
| 15 | Retire obsolete replication | Target | Relay stopped; CDC slot dropped; no legacy read except break-glass | N/A — replication is disposable |
| 16 | Archive evidence and decommission timing | Target | Evidence package archived; legacy table cluster decommission date; read credential retirement plan | Decommission is operational — not DB rollback |

### One-writer timeline (conceptual)

```text
Time ──────────────────────────────────────────────────────────────────────►

Legacy DB:  [════════ write ════════][fence][── read-only ──][credentials revoked]
Target DB:  [DDL][backfill][replicate][shadow][read prod][════ write ═══════════►]
Replication:[        forward only ──────────────►][stop]
Consumers:  [legacy reads][shadow][target reads][target writes via API/events only]
```

**[proposal]** Compatibility period (stages 6–11): legacy remains write authority; target receives forward replication. Operational modules that will eventually publish facts (ADR-0003) MUST NOT write target lifecycle tables directly — they send commands to Shipment (or current owner) which writes one store.

---

## High-water mark strategy

**[proposal]** Per table cluster, document in the evidence package:

| Cluster type | Recommended HWM | Forward capture |
|--------------|-----------------|-----------------|
| Mutable entity (`TimestampMixin`) | H2 + H1 confirmation | R3 poll or R1 CDC |
| Append-only events (`shipment_events`) | H4 cursor on `(occurred_at, id)` | R4 fact replay or R1 |
| Ledger / wallet entries | H4 + monotonic entry sequence | R1 CDC strongly preferred |
| Reference data (hubs, merchants) | H1 snapshot | R3 infrequent poll |
| Soft-delete absent | Tombstone strategy required | Deletes must appear in delta |

**[evidence]** Legacy `shipment_events` provides `occurred_at` suitable for H4; `shipments` provides `updated_at` via mixin suitable for H2.

**[unresolved policy]** Whether legacy extraction uses R1 CDC infrastructure or R3 polling-only for Phase 1 waves — depends on ADR-0001 deployable timeline and ops capacity.

---

## Backfill and replication protocol

**[proposal]**

1. **Deterministic backfill:** Export from legacy using repeatable ordering (PK ASC); load to target with `ON CONFLICT` upsert on PK; record batch checksums.
2. **No FK enforcement across services on target:** Load reference IDs as bare UUIDs; validate via reconciliation, not cross-service FK (`architecture/invariants.md`).
3. **Forward replication (one direction only):** Apply changes where `source.updated_at > HWM` OR event cursor > HWM; idempotency key = PK + `updated_at` (or event `id`).
4. **Delete handling:** Explicit tombstone table or `deleted_at` column on target; hard deletes in legacy require CDC or periodic full PK-set compare.
5. **Worker coordination:** Stop or fence `push-outbox-worker` and domain workers that mutate in-scope tables before stage 11; document in inventory (stage 2).
6. **Monetary fields:** `Numeric(12,2)` columns (e.g. `cod_amount`) backfilled with decimal string normalization; checksum includes SUM aggregates, not only counts.

**Forbidden:** applying legacy writes to target while target simultaneously writes the same rows; any "merge" without a single writer.

---

## Reconciliation matrix

Row-count equality alone is **insufficient** because: equal counts can mask **wrong rows** (PK collision after UUID regeneration), **stale values** (same PK, divergent columns), **missing deletes**, **duplicate semantic events**, **FK references to rows not yet extracted**, and **state machine violations** (valid count, invalid `current_status` distribution).

**[proposal]** Multiple evidence layers with defined roles (thresholds are **placeholders** — production values require named sign-off):

| Layer | Check | Detects | Blocking default | Exception path |
|-------|-------|---------|------------------|----------------|
| L1 Row counts | `COUNT(*)` per table | Missing/extra bulk rows | >0 delta | Waive with root-cause + compensating job |
| L2 Primary-key coverage | PK set symmetric difference | Missing/duplicate keys | Any PK in symmetric diff | Manual row-level fix list |
| L3 Row checksums | `hash_agg` of canonical column subset per PK | Column drift | Any mismatch in blocking columns | Field-level diff export |
| L4 FK reference validity | Reference IDs exist in owning service or stub registry | Orphan references | Any orphan on blocking FK | Stub row or async backfill plan |
| L5 State distribution | `GROUP BY current_status` (etc.) | Lifecycle skew | Any status bucket delta | ADR-0003 transition audit |
| L6 Monetary totals | `SUM(cod_amount)`, ledger balances | Financial drift | Any non-zero monetary delta | Finance sign-off (ADR-0005) |
| L7 Lifecycle invariants | State machine rules (no illegal transitions) | Semantic corruption | Any violation | Shipment owner review |
| L8 Missing/duplicate semantics | Unique business keys (`shipment_code`, idempotency keys) | Business dupes | Any duplicate | Merge playbook |
| L9 Temporal lag | `max(updated_at)` source vs target | Replication stall | Lag > SLO window | Scale relay / pause cutover |
| L10 Business-semantic | End-to-end scenarios (create → deliver → track) | Integration drift | Failed scenario | Cross-team war room |

**Exception queue:** All L1–L10 failures create a ticket with: table, PK, layer, diff artifact, owner, waiver authority. **Blocking threshold:** any L2, L6, or L7 failure blocks stage 10+ without written waiver. **Sign-off evidence:** reconciliation report SHA, waiver log, named approvers (roles, not individuals hardcoded).

---

## Read and write cutover gates

### Read transfer gate (stage 10)

**[proposal]** Proceed only when:

- L1–L5 pass (or waived with evidence)
- Shadow read error rate below SLO for agreed window
- Target read path load-tested
- Rollback runbook validated (revert routes to legacy)

### Write transfer gate (stage 12)

**[proposal]** Proceed only when:

- L1–L10 pass for blocking layers
- Legacy write fence proven (integration test: legacy write attempt fails closed)
- Target write path end-to-end tested on staging disposable environment
- ADR-0002 outbox/inbox ready if post-cutover integration is message-driven
- On-call and observability dashboards active

### Credential revocation gate (stage 13 — mandatory completion)

**[proposal]** Extraction is **not complete** until:

1. Legacy DB role used by app/workers for the cluster **cannot** `INSERT`/`UPDATE`/`DELETE` on in-scope tables (privilege proof via `\dp` or equivalent audit output — names only in docs).
2. Network policy blocks legacy app → legacy DB write path for cluster (if physically separate) OR legacy deployment stopped for monolith extract of whole DB.
3. **Revocation proof artifact:** timestamp, operator identity, before/after privilege listing, failed write test output, ticket ID.
4. Break-glass role exists separately, time-limited, audited — not the application role.

---

## Rollback and forward-fix matrix

| Phase | State | Rollback feasible? | Mechanism | Cannot undo |
|-------|-------|-------------------|-----------|-------------|
| RB-1 | Before read cutover (≤ stage 9) | **Yes** | Drop target data; reset replication; legacy unchanged | Engineering time |
| RB-2 | After read cutover, before write cutover (stage 10–11) | **Partial** | Revert read routes to legacy; target becomes shadow again | Target-served reads may have cached stale data — cache invalidation required |
| RB-3 | After write cutover (stage 12) | **No simple DB rollback** | Forward-fix on target; replay missing events | Dual-written period if fence failed — **forbidden design** |
| RB-4 | After credential revocation (stage 13+) | **Forward-fix only** | Restore credentials only via break-glass; re-sync from target to legacy **not** automatic | Credential revocation is operational fact |
| RB-5 | Physical delivery / COD committed | **Irreversible** | Compensating business process | Delivered parcels, collected COD, append-only audit |

**[evidence]** Legacy `complete_delivery_task.py` combines delivery, COD, wallet, and shipment status in one transaction — cutover must **split writers** before write transfer (ADR-0003) to avoid RB-3 ambiguity.

**[proposal]** Do not claim "restore from backup" reverses cutover after stage 12 unless backup isolation and RPO/RTO are explicitly tested and **business accepts** data loss window.

---

## Ownership ambiguity inventory

**[evidence]** From `docs/audit/legacy-data-ownership-inventory.md` and `architecture/ownership-matrix.yaml`:

| Table cluster | Legacy writers | Target canonical writer (proposed) | Cutover note |
|---------------|----------------|-----------------------------------|--------------|
| `shipments`, `shipment_events` | shipment, pickup, hub, linehaul, delivery_task | **shipment** (ADR-0003) | Highest risk; multi-writer remediation required before stage 12 |
| `delivery_cod_collections` | delivery_task | **delivery** (fact) + finance projection | Same-transaction wallet credit must decouple |
| `wallet_*` | wallet, delivery_task (orchestration) | **wallet_cod** | Extract after delivery fact path stable |
| `orders` | order, send_parcel (create) | **order** | FK refs to merchant, address_book — extract refs as IDs |
| `proof_records` / evidence blobs | proof (stub), pickup, shipment, delivery | **undecided** (media_proof) | Metadata vs object storage split |
| Customer profile fields | auth (partial) | **undecided** (customer) | No standalone legacy module |
| `audit_logs` | auth, wallet, delivery, merchant | **audit** or per-service emission | Append-only; cross-cutting |
| `notification_*`, `push_outbox` | notification + lifecycle emitters | **notification** | Worker must fence before cutover |

**Platform invariant gaps requiring cutover remediation:**

| Invariant | Legacy state | Cutover action |
|-----------|--------------|----------------|
| Shipment sole lifecycle writer | **Violated** | Fence non-shipment writers; command/fact path |
| No cross-service FK | **Violated** | Drop FK on target; validate L4 |
| One-writer cutover | N/A | This ADR protocol |
| Finance failures ≠ delivery rollback | **Partial** | Split transactions before write cutover |

---

## Security and credentials

**[proposal]**

| Concern | Requirement |
|---------|-------------|
| Separate database roles | Each platform service: `svc_<name>_owner` (DDL+migrations), `svc_<name>_app` (CRUD on own schema only), `svc_<name>_ro` (read replica / analytics) |
| Network access | DB reachable only from service network segment; legacy app segment removed after stage 13 |
| Credential inventory | Spreadsheet of role names, scope, rotation date, last revoker — **names only** in ADR |
| Rotation | Cutover generates fresh credentials; no reuse of legacy `DATABASE_URL` password |
| Revocation proof | Stage 13 artifact (see gates) |
| Audit logs | PostgreSQL audit extension or proxy logs; application audit for break-glass |
| Least privilege | Migration role not granted to runtime app |
| Break-glass | Emergency role with MFA, time-bound grant, mandatory post-incident review |
| Secret handling | No secrets in evidence package; store in vault; config audits list names only |

**[evidence]** Legacy uses single shared `DATABASE_URL` for app and workers (`app/core/config.py` — name only).

---

## Observability and SLO signals

**[proposal]**

| Signal | Purpose | Stage |
|--------|---------|-------|
| `cutover_replication_lag_seconds` | Forward replication health | 6–11 |
| `cutover_reconciliation_diff_total{layer}` | Open reconciliation diffs | 8–12 |
| `cutover_shadow_read_mismatch_rate` | Read path readiness | 8–10 |
| `cutover_write_fence_violation_total` | Legacy write after fence | 11–13 |
| `cutover_exception_queue_depth` | Blocking waivers | 9–12 |
| Structured logs with `correlation_id`, `traceparent` | Cross-service trace (ADR-0002) | all |
| Post-cutover error rate / latency SLO | Service health | 14 |

**[evidence]** Legacy lacks distributed tracing and metrics (`docs/audit/legacy-runtime-inventory.md` §Observability) — platform cutover observability is net-new.

---

## Failure injection tests

**[proposal]** Before production write cutover, execute on disposable environment:

| Test | Inject | Expected |
|------|--------|----------|
| FI-1 Relay crash mid-batch | Kill replication worker | Idempotent resume; no duplicate PK |
| FI-2 Legacy write after fence | Attempt API write post-stage 11 | Fail closed; alert fires |
| FI-3 Target DB unavailable during read cutover | Network partition | Fallback to legacy reads; no split-brain writes |
| FI-4 Clock skew | Backdated `updated_at` | HWM strategy catches or quarantines |
| FI-5 Duplicate event delivery | At-least-once replay (ADR-0002) | Inbox idempotency holds |
| FI-6 Break-glass usage | Elevated role write | Audited; does not bypass Shipment authority |
| FI-7 Partial backfill restart | Truncate half the target | Checksum mismatch detected at L3 |
| FI-8 Credential revocation | Revoke then attempt legacy ORM write | Connection or permission denied |

---

## Extraction completion checklist

**[proposal]** All items required before marking a context `extraction_status: complete` in `architecture/service-boundaries.yaml` (field update deferred until ADR acceptance):

- [ ] Ownership matrix row accepted for context
- [ ] Stage 1–16 evidence package archived
- [ ] HWM document with mechanism and timestamp
- [ ] Backfill checksum manifest
- [ ] Forward replication idempotency proof
- [ ] Reconciliation matrix L1–L10 report with sign-offs
- [ ] Read cutover rollback drill completed
- [ ] Write fence test passed
- [ ] **Credential revocation proof attached**
- [ ] Replication retired
- [ ] Observability SLO green for agreed window
- [ ] Decommission date for legacy table cluster scheduled
- [ ] No bidirectional dual-write confirmed

---

## Reusable evidence package template

**[proposal]** Each extraction stores (paths are illustrative — no implementation in this ADR):

```text
docs/audit/cutover/<context-id>/<cutover-id>/
  manifest.yaml              # context, HWM, stages completed, SHAs
  ownership-map.yaml         # table → service, writer, legacy paths
  inventory/
    tables.md                # columns, PKs, indexes, FKs (names only)
    writers.md               # code paths mutating each table
    workers.md               # background jobs
  hwm/
    capture.md               # mechanism, timestamp, scope
  backfill/
    checksums.json           # per-table row counts + hashes
  replication/
    lag-report.md
    idempotency-keys.md
  reconciliation/
    report.md                # L1–L10 results
    exceptions.csv           # PK, layer, status (no PII)
  gates/
    read-cutover-signoff.md
    write-cutover-signoff.md
    credential-revocation-proof.md  # names only; secrets redacted
  rollback/
    drill-results.md
  observability/
    slo-screenshot-refs.md   # links to dashboard IDs, not secrets
```

---

## Consequences

### Positive

- Reusable protocol reduces ad-hoc cutover risk across ~20 bounded contexts.
- Explicit one-writer timeline prevents bidirectional dual-write and silent divergence.
- Credential revocation as completion gate enforces service independence.
- Multi-layer reconciliation catches errors row counts miss.

### Negative

- Sixteen stages increase calendar time and documentation burden per extraction.
- Legacy multi-writer and cross-FK state requires upfront remediation (especially Shipment).
- CDC/replication infrastructure may be required for acceptable lag (**unresolved**).
- Each wave needs trained operators and sign-off authority.

### Neutral

- Does not decide deployable count (ADR-0001) or messaging schema detail (ADR-0002).
- Does not create schemas, credentials, or scripts — implementation follows acceptance.

---

## Migration impact

**[proposal]**

- Each extracted service receives dedicated database, Alembic chain, and credentials per `architecture/invariants.md`.
- Cross-domain references become ID-only; FK validation moves to reconciliation L4 and runtime checks.
- Legacy monolith remains read-only reference; no runtime dependency after cutover.
- Temporary compatibility writes route through current owner API/commands — never dual DB writes.
- Shipment extraction depends on ADR-0003 acceptance to resolve multi-writer ambiguity.
- Finance/wallet extraction waves blocked until ADR-0005 policy unblocked.

Bidirectional dual-write: **forbidden**.

---

## Unresolved questions

**[unresolved policy]**

1. CDC vs polling-only replication for Phase 1 — ops tooling and hosting?
2. Per-wave cutover order across contexts (Shipment-first vs auth-first) — ADR-0001?
3. Break-glass role provisioning owner and maximum grant duration?
4. Production RPO/RTO targets for rollback drills — business input required?
5. Whether legacy monolith receives a **read-only** DB role during stages 8–10 or full app read path switch only?
6. Tombstone strategy for hard deletes in legacy tables without `deleted_at`?
7. Minimum shadow-read duration and traffic percentage before read cutover?
8. Decommission timeline for legacy tables after stage 16 — legal/audit retention?
9. Shared `audit_logs` — central extract vs per-service outbox emission?
10. Numeric reconciliation tolerance for FX/rounding — finance policy (ADR-0005)?

---

## Alternatives Considered

| Alternative | Why rejected |
|-------------|--------------|
| Bidirectional dual-write | Forbidden by platform invariant; divergence risk |
| Permanent shared database | Violates service independence; blocks credential revocation |
| Big-bang cutover | No incremental reconciliation; unacceptable blast radius |
| Row-count-only validation | Insufficient — wrong-row and semantic drift undetected |
| Database rollback after write cutover | Misleading — physical and financial facts irreversible |
| Cross-service FK on target | Forbidden — use ID references and L4 reconciliation |

---

## References

- Platform: `AGENTS.md`, `architecture/invariants.md`, `architecture/service-boundaries.yaml`, `architecture/ownership-matrix.yaml`
- Audits: `docs/audit/legacy-baseline.md`, `docs/audit/legacy-data-ownership-inventory.md`, `docs/audit/legacy-domain-inventory.md`, `docs/audit/legacy-runtime-inventory.md`
- Legacy evidence: `hudhud-backend` @ `2e375057fdf9b9ce8416408a4436303be5301def` (read-only)
- Related ADRs: ADR-0001 (deployables), ADR-0002 (eventing), ADR-0003 (shipment authority), ADR-0004 (identity/trust), ADR-0005 (finance/settlement)
- Skills: `.cursor/skills/prepare-adr/SKILL.md`, `.cursor/skills/plan-extraction-cutover/SKILL.md`

---

## Proposed recommendation summary

**[proposal]** Accept the sixteen-stage one-writer cutover protocol with hybrid HWM, multi-layer reconciliation (L1–L10), mandatory credential revocation at stage 13, and forward-fix-only policy after write cutover. Select replication mechanism per table cluster after ADR-0002 and infrastructure decisions. Execute Shipment cluster only after ADR-0003 resolves legacy multi-writer paths. Do not begin implementation until this ADR and blocking dependencies are accepted by named deciders.

**Implementation allowed:** no
