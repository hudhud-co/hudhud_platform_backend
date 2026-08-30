# Legacy Capture-Source Completeness Audit (ADR-0007)

- **Workstream:** HUDHUD W3-A
- **Platform repository:** `/Users/mohammadakbari/Development/Projects/Python/hudhud_platform_backend_worktrees/w3-legacy-capture-audit`
- **Platform branch:** `cursor/w3-legacy-capture-audit`
- **Platform starting SHA:** `1f9ffcae65fbc5bc42e1e5a68dbea35605512335`
- **Legacy evidence SHA (pinned, read-only):** `2e375057fdf9b9ce8416408a4436303be5301def`
- **Legacy dirty file (not inspected):** `scripts/dev_pickup_driver_simulator.py`
- **Audit date:** 2026-08-30
- **Related ADR:** [ADR-0007](../adr/0007-legacy-event-bridge-strategy.md) (Proposed — not modified in this workstream)
- **Label key:** **[evidence]** pinned legacy inspection; **[decision]** binding platform policy; **[proposal]** bridge design pending acceptance; **[unresolved]** requires runtime/DB proof or policy

---

## Executive conclusion

**[evidence]** No single existing legacy table provides a **proven complete, globally ordered, replayable, gap-free** integration feed without legacy schema change.

**[evidence]** The strongest lifecycle capture surface is **`shipment_events`**, which all 13 verified status-writer paths append in the same request-scoped transaction as `shipments.current_status` mutation. That surface is **conditionally sufficient** for Tracking, Control Tower, and derived Notification projections **only** when:

1. Cursor uses **`(occurred_at ASC, id ASC)`** total order (not `id` or timestamp alone).
2. Bridge accepts **post-commit poll lag** and proves zero-gap under ADR-0006 stage 7.
3. Non-lifecycle facts (COD, wallet, pickup scheduling, merchant-application notifications, media pointers) use **additional cursors** or remain explicitly out of scope.

**[decision boundary]** Financial capture (`delivery_cod_collections`, `wallet_ledger_entries`) may be **read for bridge staging** but Finance/Wallet **consumers remain blocked** until ADR-0005 policy resolves (atomicity and authority not aligned with target platform model).

---

## Preflight evidence

| Check | Result |
|-------|--------|
| Platform HEAD matches starting SHA | **PASS** — `1f9ffcae65fbc5bc42e1e5a68dbea35605512335` |
| Platform branch | **PASS** — `cursor/w3-legacy-capture-audit` |
| Platform working tree | **PASS** — clean before audit write |
| Legacy HEAD | **PASS** — `2e375057fdf9b9ce8416408a4436303be5301def` |
| Legacy dirty status | **Expected only** — ` M scripts/dev_pickup_driver_simulator.py` (not read) |
| Legacy inspection method | `git show ${SHA}:path` at pinned SHA only |
| Legacy mutation | **None** |

---

## Audit method

1. Read platform ADRs-0002, 0003, 0005, 0006, 0007; architecture invariants; service boundaries; ownership matrix; `docs/audit/*`.
2. Pinned read-only inspection of legacy models, migrations, repositories, and the ADR-0003 thirteen-path writer matrix at SHA `2e375057…`.
3. Classify each candidate source: **proven sufficient**, **conditionally sufficient**, **insufficient**, **unknown pending runtime/database evidence**.
4. Do **not** infer completeness from table names or append-only intent alone.

---

## Source completeness matrix

### 1. `shipments`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | Model: `app/modules/shipment/infrastructure/models.py`; migration: `alembic/versions/e5f3b2a81d04_add_shipments_and_shipment_events.py` |
| **Primary key** | UUID v4 (`default=uuid.uuid4`) |
| **DB sequence** | **`shipment_code_seq`** exists for human-readable `shipment_code` generation only — **not** for row ordering or PK |
| **Timestamps** | `created_at`, `updated_at` via `TimestampMixin` (`server_default=func.now()`, `onupdate=func.now()`) — application assigns on ORM update |
| **Append/update/delete** | **Update-heavy** mutable aggregate; `SqlAlchemyShipmentRepository.update()` mutates status, custody, SLA fields |
| **Hard-delete/tombstone** | No `deleted_at`; no application delete path evidenced for shipments |
| **Transaction boundaries** | Status mutation co-located with `shipment_events` append in use cases; single API `AsyncSession` commit |
| **Writers** | shipment, pickup, hub, linehaul, delivery_task (13 status paths per ADR-0003) |
| **Lifecycle write ↔ source record atomic** | **N/A** — table is mutable state, not an event log |
| **Every writer produces source record** | All status writers update this row; not all operational facts touch non-status columns |
| **Ordering guarantees** | **`updated_at` alone insufficient** (ADR-0006 H2); clock skew; non-lifecycle updates possible |
| **Replay identity** | PK UUID stable; row content mutable — **unsuitable as integration cursor** |
| **PII / classification** | **confidential** — receiver name, phone, address, geo |
| **Retention/cleanup** | No documented purge |
| **Polling suitability** | **Weak** as universal HWM — forbidden as default per ADR-0006/0007 |
| **CDC suitability** | Captures all column changes post-commit; needs enrichment for semantics |
| **Semantic event quality** | **Low** — row state, not typed facts |
| **Gaps / unknowns** | Cannot distinguish lifecycle transition from unrelated column touch without diff mapping |
| **Classification** | **Insufficient** as primary capture feed; **conditionally sufficient** for read-model backfill snapshot only |

---

### 2. `shipment_events`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | Model: `ShipmentEventORM` in `shipment/infrastructure/models.py`; migration: `e5f3b2a81d04_add_shipments_and_shipment_events.py` |
| **Primary key** | UUID v4 |
| **DB sequence** | **Absent** — no `SERIAL`, no monotonic sequence on event PK |
| **Timestamps** | `occurred_at` — **application-assigned** (`datetime.now(UTC)` in writers); `created_at` — **DB server default** `func.now()` on insert |
| **Append/update/delete** | **Append-only** in code — `SqlAlchemyShipmentEventRepository.append()` only; **no update/delete** paths found in `app/modules` |
| **Hard-delete/tombstone** | No soft-delete column; no app delete evidenced; DB cascade on shipment delete **unknown pending runtime evidence** |
| **Transaction boundaries** | Event `append` + status `update` in same request session before route-level commit |
| **Writers (status paths)** | All 13 ADR-0003 status writers append (see §Mandatory Q1) |
| **Writers (timeline-only)** | pickup, hub, linehaul, delivery_task also append without status change (e.g. `PICKUP_TASK_CREATED`, `ORIGIN_HUB_CONDITION_CHECKED`, `LINEHAUL_SHIPMENT_ASSIGNED`, `DELIVERY_TASK_ASSIGNED`) |
| **Lifecycle write ↔ event atomic** | **Yes (evidence)** for all 13 status paths — same session, flush before commit |
| **Every relevant writer produces event** | **No** — COD collection and wallet credit do not append shipment_events; pickup scheduling confirmations use separate notification paths |
| **Ordering guarantees** | Repository lists `order_by(occurred_at.asc(), created_at.asc())` — **not global monotonic PK**; equal `occurred_at` requires `id` tie-break; UUID v4 **not time-ordered** |
| **Replay identity** | Row PK + `(occurred_at, id)` cursor position; deterministic bridge `event_id` derivable from source coordinates **[proposal]** |
| **PII / classification** | **internal** — may embed location, reason codes; generally no raw phone in event row |
| **Retention/cleanup** | No documented purge |
| **Polling suitability** | **Conditionally suitable** on `(occurred_at, id) > cursor` with tie-break |
| **CDC suitability** | **Suitable** for insert capture; updates/deletes unlikely |
| **Semantic event quality** | **Med–High** for lifecycle — typed `event_type`, `old_status`, `new_status`, `metadata_jsonb`, `actor_*` |
| **Gaps / unknowns** | Non-status operational facts absent; notification catalog parity incomplete; zero-gap not proven without ADR-0006 drill |
| **Classification** | **Conditionally sufficient** for lifecycle/timeline integration (Tracking, Control Tower, derived Notification) |

#### Verified status-writer → event mapping (13 paths)

| # | Path | Status transition | Appends `shipment_event` | Event type(s) |
|---|------|-------------------|--------------------------|---------------|
| 1 | `create_shipment.py` | → CREATED | **Yes** | `SHIPMENT_CREATED` |
| 2 | `confirm_send_parcel.py` | → CREATED via #1 | **Yes** (delegated) | via create |
| 3 | `acceptance_scan_pickup_task.py` | → IN_CUSTODY | **Yes** | `PICKUP_ACCEPTANCE_SCAN` |
| 4 | `origin_hub_inbound_scan.py` | → AT_ORIGIN_HUB | **Yes** | `ORIGIN_HUB_INBOUND_SCAN` |
| 5 | `dispatch_linehaul_trip.py` | → IN_LINEHAUL | **Yes** | `LINEHAUL_TRIP_DISPATCHED` |
| 6 | `arrive_linehaul_trip.py` | → AT_DESTINATION_HUB | **Yes** | `LINEHAUL_TRIP_ARRIVED` |
| 7 | `start_delivery_task.py` | → OUT_FOR_DELIVERY | **Yes** | `SHIPMENT_OUT_FOR_DELIVERY`, `DELIVERY_TASK_STARTED` |
| 8 | `complete_delivery_task.py` | → DELIVERED | **Yes** | `SHIPMENT_DELIVERED`, `DELIVERY_TASK_COMPLETED` |
| 9 | `fail_delivery_task.py` | → DELIVERY_FAILED | **Yes** | `SHIPMENT_DELIVERY_FAILED`, `DELIVERY_TASK_FAILED` |
| 10 | `MarkShipmentOutForDeliveryUseCase` | → OUT_FOR_DELIVERY | **Yes** | via `delivery_completion._DeliveryCompletionService.apply` |
| 11 | `MarkShipmentDeliveredUseCase` | → DELIVERED | **Yes** | same |
| 12 | `MarkShipmentDeliveryFailedUseCase` | → DELIVERY_FAILED | **Yes** | same |
| 13 | `CancelShipmentDeliveryUseCase` | → DELIVERY_CANCELLED | **Yes** | same |

---

### 3. `audit_logs`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | Model: `audit/infrastructure/models.py`; repository: `audit/infrastructure/repositories.py` |
| **Primary key** | UUID v4 |
| **DB sequence** | **Absent** |
| **Timestamps** | `created_at` — DB `server_default=func.now()` only; **no `updated_at`** |
| **Append/update/delete** | **Append-only** — `append()` only |
| **Hard-delete/tombstone** | No soft-delete; no app delete evidenced |
| **Transaction boundaries** | Usually same session as triggering use case; **not guaranteed** for all modules |
| **Writers** | Decentralized: auth, wallet, delivery_task, merchant, pickup, hub, linehaul, shipment (grep at pinned SHA) |
| **Lifecycle write ↔ audit atomic** | **Partial** — major transitions audited; not universal for every `shipment_event` |
| **Every writer produces audit** | **No** — timeline-only `shipment_events` may lack matching audit row |
| **Ordering guarantees** | `(created_at, id)` list order in repository; UUID tie-break same caveats as events |
| **Replay identity** | UUID PK + created_at cursor |
| **PII / classification** | **confidential** — IP, user_agent; metadata redacted via `redact_audit_metadata` |
| **Retention/cleanup** | No documented purge |
| **Polling suitability** | **Conditionally suitable** on `(created_at, id)` |
| **CDC suitability** | Insert-only surface |
| **Semantic event quality** | **Med** — `action`, `entity_type`, `entity_id`; heterogeneous schema in `metadata_jsonb` |
| **Gaps / unknowns** | Incomplete coverage of operational transitions; not a substitute for lifecycle stream |
| **Classification** | **Conditionally sufficient** for Audit consumer transport; **insufficient** as sole domain feed |

---

### 4. `notification_push_outbox`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | Model: `notification/infrastructure/push_outbox_models.py`; worker: `notification/application/push_outbox_worker.py` |
| **Primary key** | UUID v4 |
| **DB sequence** | **Absent** |
| **Timestamps** | `TimestampMixin` + delivery lifecycle columns (`next_attempt_at`, `sent_at`, `failed_at`, …) |
| **Append/update/delete** | **Insert + update** — status, attempts, lease fields mutated by worker |
| **Hard-delete/tombstone** | No soft-delete evidenced |
| **Transaction boundaries** | Enqueued **after** `in_app_notifications` insert in same business transaction; **downstream of domain mutation**, not co-located with shipment status write |
| **Writers** | `InAppNotificationService` from lifecycle emitters and catalog-driven flows |
| **Lifecycle write ↔ outbox atomic** | **No** for domain mutation — atomic with in-app row only |
| **Every lifecycle writer produces outbox** | **No** — push gated by preferences/device tokens; in-app always persisted |
| **Ordering guarantees** | Worker poll on `(status, next_attempt_at)` — **push delivery order**, not domain order |
| **Replay identity** | Outbox PK; `dedupe_key` for FCM dedupe |
| **PII / classification** | **restricted** — push title/body; device token FK |
| **Retention/cleanup** | Worker marks sent/failed; no archive policy documented |
| **Polling suitability** | Suitable for **push relay** only |
| **CDC suitability** | Captures push state machine, not domain facts |
| **Semantic event quality** | **Low** for integration — FCM dispatch envelope |
| **Gaps / unknowns** | Not co-transactional with shipment lifecycle writers |
| **Classification** | **Insufficient** as domain-event outbox (ADR-0007 decision boundary) |

---

### 5. Delivery task / evidence tables

#### 5a. `delivery_tasks`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | `delivery_task/infrastructure/models.py`; migration lineage from `m9n0o1p2q3r4` |
| **Primary key** | UUID v4 |
| **DB sequence** | Absent |
| **Timestamps** | `TimestampMixin` + domain timestamps (`assigned_at`, `started_at`, `completed_at`, …) |
| **Append/update/delete** | **Mutable** — status transitions update row |
| **Hard-delete/tombstone** | No `deleted_at` |
| **Atomic with shipment_event** | Task + shipment status updated together on start/complete/fail |
| **Ordering** | Per-table `updated_at`/`created_at` — no cross-table order |
| **Classification** | **Insufficient** alone; **conditionally sufficient** as enrichment cursor alongside `shipment_events` |

#### 5b. `delivery_cod_collections`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | `DeliveryCodCollectionORM`; migration `o1p2q3r4s5t6_add_delivery_completion_integrity.py` |
| **Primary key** | UUID v4 |
| **DB sequence** | Absent |
| **Timestamps** | `collected_at` (application); `created_at` (DB default) |
| **Append/update/delete** | **Insert-only** evidenced; unique per `shipment_id` |
| **Atomic with delivery complete** | **Yes** — same transaction as status + wallet credit in `complete_delivery_task.py` |
| **Ordering** | `(created_at, id)` or `collected_at` poll |
| **Classification** | **Conditionally sufficient** for COD fact capture; **Finance consumer blocked** (ADR-0005) |

#### 5c. `delivery_evidence`, `delivery_evidence_attachments`

| Attribute | Finding |
|-----------|---------|
| **Evidence** | `shipment/infrastructure/delivery_evidence_*.py`; migrations `h4c5d6e7f8a9`, `i5d6e7f8a9b0` |
| **Primary key** | UUID v4 |
| **Timestamps** | Mixed; attachments use `TimestampMixin` + `deleted_at` soft-delete |
| **Append/update/delete** | Evidence rows created on completion; attachments **updated** (`upload_status`) and **soft-deleted** (`deleted_at`); cleanup worker may delete abandoned MinIO objects |
| **Atomic with lifecycle** | Evidence persisted in complete/fail/ops paths — same session |
| **Classification** | **Insufficient** as ordered event feed; **conditionally sufficient** for Media/Proof **reference** emission |

#### 5d. Ancillary delivery tables

| Table | Role | Classification |
|-------|------|----------------|
| `delivery_otp_verifications` | OTP consume audit | **Insufficient** as stream; enrichment only |
| `delivery_action_idempotency_keys` | HTTP idempotency | **Insufficient** — not domain events |
| `delivery_driver_notifications` | Driver inbox | **Insufficient** for customer integration |

---

### 6. Pickup task / evidence tables

| Table | Evidence | PK | Sequence | Behavior | Classification |
|-------|----------|-----|----------|----------|----------------|
| `pickup_batches` | `pickup/infrastructure/models.py` | UUID | No | Mutable status | **Insufficient** alone |
| `pickup_tasks` | same | UUID | No | Mutable; many writers | **Insufficient** alone |
| `pickup_evidence_files` | same | UUID | No | Insert + status update; **hard delete** via `delete_pickup_evidence_file.py` | **Insufficient** as gap-free stream |
| `pickup_handover_manifests` | migration `z3b4c5d6e7f8` | UUID | No | Mutable handover state | **Insufficient** alone |
| `pickup_task_exceptions` | models | UUID | No | Append/update | Enrichment only |

**[evidence]** Pickup lifecycle transitions that change shipment status append `shipment_events` (acceptance scan). Other pickup steps append timeline events without status change — captured in `shipment_events`, not pickup tables.

**Note:** Platform `service-boundaries.yaml` lists `hub_operations` — **[evidence]** no `hub_operations` table exists at pinned SHA; hub state is `hubs` + `shipment_events` (`ORIGIN_HUB_*`).

---

### 7. Hub and linehaul transition tables

| Table | Evidence | PK | Sequence | Mutable | Status-changing | Classification |
|-------|----------|-----|----------|---------|-----------------|----------------|
| `hubs` | `hub/infrastructure/models.py` | UUID | No | Yes (`TimestampMixin`) | Reference data | **Insufficient** |
| `linehaul_trips` | `linehaul/infrastructure/models.py` | UUID | No | Yes | Trip-level | **Insufficient** alone |
| `linehaul_trip_shipments` | same | UUID | No | Yes | Per-shipment leg status | **Insufficient** alone |

**[evidence]** Hub inbound and linehaul dispatch/arrive mutate `shipments.current_status` and append `shipment_events`. Linehaul assign appends `LINEHAUL_SHIPMENT_ASSIGNED` without status change.

---

### 8. `delivery_cod_collections`

See §5b. **Conditionally sufficient** for operational COD fact; not authoritative finance posting.

---

### 9. Wallet / ledger tables

| Table | Evidence | PK | Sequence | Behavior | Classification |
|-------|----------|-----|----------|----------|----------------|
| `wallet_accounts` | `wallet/infrastructure/models.py` | UUID | No | Mutable (`status`, metadata) | **Insufficient** |
| `wallet_ledger_entries` | same | UUID | No | **Append-only** (documented); idempotency `(wallet_account_id, idempotency_key)` | **Conditionally sufficient** for legacy wallet replay; **Finance blocked** |
| `payout_requests` | same | UUID | No | Mutable status | **Insufficient** as event stream |

**[evidence]** `CreditCodCollectedToMerchantWalletUseCase` runs in **same transaction** as delivery complete — wallet credit failure rolls back delivery (ADR-0005 gap vs platform invariant).

---

### 10. Media / proof tables

| Table | Evidence | PK | Behavior | Classification |
|-------|----------|-----|----------|----------------|
| `proofs` | `proof/infrastructure/models.py` | UUID | Mutable metadata | **Insufficient** — stub API |
| `pickup_evidence_files` | pickup models | UUID | Upload lifecycle + delete | **Conditionally sufficient** for references |
| `delivery_evidence` | shipment models | UUID | Insert on completion | Enrichment |
| `delivery_evidence_attachments` | shipment models | UUID | Soft-delete + cleanup worker | Enrichment |

**[evidence]** Object bytes live in MinIO; DB holds bucket/key references suitable for ADR-0002 `media_refs` **[proposal]**.

---

### 11. Related notification tables (not in required list but gap-relevant)

| Table | Role | Classification |
|-------|------|----------------|
| `in_app_notifications` | Durable customer feed | **Conditionally sufficient** for Notification read-model; **not** co-located with all domain writers |
| `notification_preferences` | User prefs | **Insufficient** |
| `notification_device_tokens` | Push targets | **Insufficient** |

**[evidence]** `InAppNotificationService` docstring: inbox + optional push outbox commit with business transaction for **notification creation**, not shipment status mutation site.

---

## Mandatory questions (pinned evidence)

### Q1. Do all 13 verified Shipment status-writer paths append a `shipment_event`?

**Yes — [evidence].** All thirteen paths in ADR-0003 writer matrix call `ShipmentEventRepository.append()` in the same use case that sets `shipment.current_status`. Path #2 (`confirm_send_parcel`) delegates to `CreateShipmentUseCase` (#1). Ops paths (#10–13) append via `_DeliveryCompletionService.apply()` in `delivery_completion.py`.

### Q2. Are status mutation and timeline append committed atomically in every path?

**Yes — [evidence]** within the legacy monolith request transaction. Writers flush status update and event insert to the same `AsyncSession`; API/route wrapper commits once. **Caveat:** bridge poll observes rows **post-commit** (ADR-0007 O3 — post-commit visibility lag).

**Exception scope:** If an API route committed shipment state without event append, that would violate code paths reviewed — **no such path found** among the 13 writers.

### Q3. Can UUID plus `created_at`/`occurred_at` form a gap-free cursor?

**No — not proven sufficient — [evidence].**

| Issue | Evidence |
|-------|----------|
| UUID v4 not monotonic | PK `default=uuid.uuid4` on `ShipmentEventORM` |
| Two timestamps | `occurred_at` (application) vs `created_at` (DB default) may diverge |
| Equal timestamps | Multiple events same `occurred_at` in single batch (e.g. start delivery appends two events with same `now`) |
| Cursor ambiguity | Must use **`(occurred_at, id)`** with lexicographic UUID tie-break — still not proof of gap-free global capture |
| No sequence | Migration creates no event sequence column |

**Classification:** **Unknown pending runtime/database evidence** for gap-free claim; **conditionally sufficient** cursor for replay if zero-gap drill passes (ADR-0006 E3 / ADR-0007 E3).

### Q4. Can equal timestamps or late commits cause skipped rows?

**Yes — [evidence].**

- **Equal `occurred_at`:** `start_delivery_task.py` appends two events with the same `now` — cursor must use `(occurred_at, id)` strict inequality consistently.
- **Late commits:** Poll/CDC sees rows after commit; bridge down during commit creates lag, not necessarily loss — **loss not proven** but **skip risk** if cursor advanced past visible rows incorrectly.
- **`created_at` vs `occurred_at` lag:** Backdated `occurred_at` with later `created_at` can reorder relative to poll using wrong column.

### Q5. Are rows updated after insertion?

| Source | Updated after insert? |
|--------|----------------------|
| `shipment_events` | **No** — append-only repository |
| `audit_logs` | **No** |
| `delivery_cod_collections` | **No** evidenced |
| `wallet_ledger_entries` | **No** — append-only per wallet API docs |
| `notification_push_outbox` | **Yes** — worker status/lease/attempt fields |
| `shipments` | **Yes** — mutable aggregate |
| `delivery_evidence_attachments` | **Yes** — upload_status, deleted_at |
| `pickup_evidence_files` | **Yes** — upload lifecycle; deletable |

### Q6. Can hard deletes occur without tombstones?

**Yes — [evidence]** for some surfaces; **unlikely** for primary capture tables.

| Surface | Hard delete? | Tombstone? |
|---------|--------------|------------|
| `shipment_events` | No app path | N/A |
| `audit_logs` | No app path | N/A |
| `pickup_evidence_files` | **Yes** — `delete_pickup_evidence_file.py` | No |
| `delivery_evidence_attachments` | Soft `deleted_at`; MinIO object may be removed by cleanup worker | Partial |
| `shipments` | Not evidenced | No |
| FK cascade on parent delete | **Unknown pending runtime evidence** | — |

Polling append-only tables avoids delete gap for `shipment_events`/`audit_logs` **if** hard deletes never occur.

### Q7. Is `audit_logs` complete for operational state transitions?

**No — [evidence].** Audit is decentralized and **best-effort parallel** to domain actions:

- Major transitions (pickup acceptance, hub inbound, linehaul, delivery, ops completion) append audit rows.
- **Timeline-only** `shipment_events` (e.g. `DELIVERY_TASK_ASSIGNED`, `PICKUP_QR_SCANNED`) may **not** have audit counterparts.
- Notification preference/catalog events never appear in audit.
- No invariant links audit row to every `shipment_event`.

**Classification:** **Insufficient** as complete operational transition log.

### Q8. Is the push outbox usable as a domain-event outbox?

**No — [evidence].** ADR-0007 decision boundary confirmed:

- Table is `notification_push_outbox` — FCM dispatch queue.
- FK to `in_app_notifications`, not domain aggregates.
- Written by notification service **after** in-app creation; not at shipment/pickup/hub write sites.
- Worker (`PushOutboxWorker`) implements push retry/lease, not integration envelope relay.

### Q9. Which facts are safe only for read-model refresh versus canonical integration events?

| Fact category | Safe for read-model refresh (low-risk projection) | Canonical integration event candidate |
|---------------|---------------------------------------------------|----------------------------------------|
| Shipment lifecycle status transitions | **Yes** — from `shipment_events` | `shipment.fact.lifecycle_changed` **[proposal]** |
| Shipment timeline enrichment (task assigned, QR scan) | **Yes** — from `shipment_events` | Operational fact per context **[proposal]** |
| Customer tracking summary | **Yes** — derived from events + shipment read | Not raw `shipments.updated_at` poll |
| Control Tower aggregation | **Yes** — same as tracking + evidence pointers | Read projection |
| Audit/legal search | **Yes** — from `audit_logs` | `audit.transport.*` **[proposal]** |
| In-app / push notification feed | **Yes** — `in_app_notifications` | **Not** canonical domain integration |
| COD collected | Read-model / staging only until ADR-0005 | `delivery.fact.cod_collected` — **policy blocked consumer** |
| Wallet ledger credit | Read-model / migration evidence only | **Not** canonical finance — ADR-0005 |
| Media/proof | Reference refresh (`media_refs`) | Pointer events only — owner **undecided** |

### Q10. Can COD or Finance facts be captured without violating ADR-0005?

**Partial — [evidence] / [decision boundary].**

| Action | ADR-0005 alignment |
|--------|-------------------|
| Read-only poll of `delivery_cod_collections` inserts | **Allowed** for bridge staging |
| Read-only poll of `wallet_ledger_entries` | **Allowed** for migration/reconciliation evidence |
| Publish to Finance/Wallet consumers | **Blocked** — ADR-0005 Proposed — Policy Blocked |
| Treat wallet credit as canonical finance posting | **Violates** target model — finance must own journal |
| Treat synchronous delivery+wallet transaction as atomic finance proof | **Violates** platform invariant — finance failure must not roll back delivery |

**Safe capture:** emit **operational** COD facts separately from lifecycle; do **not** merge wallet credit into delivery lifecycle envelope as canonical finance.

---

## Candidate first-consumer classification

| Consumer | Safe candidates | Unsafe / blocked | Classification |
|----------|-----------------|------------------|----------------|
| **Audit** | `audit_logs` cursor | Sole reliance without cross-check | **Conditionally sufficient** |
| **Tracking** | `shipment_events` + shipment backfill | `shipments.updated_at` alone | **Conditionally sufficient** |
| **Notification** | Derived from `shipment_events` status mapping | `notification_push_outbox` as domain source | **Conditionally sufficient** (partial catalog parity gap) |
| **Control Tower** | Same as Tracking + evidence metadata reads | Writable operational tables | **Conditionally sufficient** |
| **Media/Proof** | Evidence table refs + MinIO keys | Inline binary in JetStream | **Conditionally sufficient** (references only) |
| **Finance/Wallet** | **Blocked** | `wallet_ledger_entries` as authority | **Insufficient / blocked** until ADR-0005 |

---

## Can one source feed everything?

| Requirement | Single-source possible? | Evidence |
|-------------|-------------------------|----------|
| Complete lifecycle coverage | **Partial** — `shipment_events` misses COD/wallet/scheduling notifications | Multi-writer paths append events, not all facts |
| Global total order | **No** | UUID PK; multi-table writers |
| Gap-free without schema change | **Not proven** | ADR-0007 leaves polling unproven; no legacy integration outbox |
| Replayable idempotent identity | **Per-table yes** with deterministic mapping | **[proposal]** UUIDv5 from `(table, pk, position)` |
| No legacy schema change | **No new columns** — but **multi-cursor hybrid** required | ADR-0007 O5 |

**Overall:** **No single existing source is proven sufficient.** Closest single surface: **`shipment_events`** for lifecycle-only consumers.

---

## Verified evidence gaps

1. **Zero-gap drill not executed** — ADR-0006 stage 7 / ADR-0007 E3 absent.
2. **Runtime delete behavior** — FK ON DELETE for `shipment_events`, hard-delete frequency unknown.
3. **Production cursor monotonicity sample** — equal `occurred_at` density unknown.
4. **Notification catalog parity** — pickup scheduling, merchant application events need additional sources (`in_app_notifications` or hooks).
5. **CDC infrastructure** — none in legacy repo; capacity proof absent.
6. **Bridge poll lag SLO** — unresolved policy.
7. **Tombstone strategy** for hard deletes on evidence tables — unresolved (ADR-0006).
8. **Finance authority** — ADR-0005 policy register blocks consumer activation.
9. **`hub_operations` table** referenced in platform boundaries — **does not exist** in legacy schema at pinned SHA.
10. **Post-commit capture lag** — inherent to poll/CDC; not measured.

---

## Deferred / non-actions

- ADR-0007 not modified (per workstream scope).
- No legacy repository mutation.
- No bridge implementation, NATS config, or contract registration.
- No push to remote.
- Finance settlement policy not resolved.

---

## References

- ADR-0002 @ `docs/adr/0002-event-envelope-outbox-inbox-and-jetstream.md`
- ADR-0003 @ `docs/adr/0003-shipment-lifecycle-authority-and-delivery-facts.md`
- ADR-0005 @ `docs/adr/0005-cod-wallet-ledger-and-settlement.md`
- ADR-0006 @ `docs/adr/0006-one-writer-data-cutover-and-reconciliation.md`
- ADR-0007 @ `docs/adr/0007-legacy-event-bridge-strategy.md`
- Platform audits @ `docs/audit/legacy-{baseline,domain-inventory,data-ownership-inventory,runtime-inventory}.md`
- Legacy pinned paths cited inline above @ `2e375057fdf9b9ce8416408a4436303be5301def`

---

```text
Evidence path: docs/evidence/legacy-capture-source-audit.md
Workstream: HUDHUD W3-A
Legacy touched: no
Implementation allowed: no (evidence only)
```
