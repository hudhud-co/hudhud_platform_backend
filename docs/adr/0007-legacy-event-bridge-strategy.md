# ADR-0007: Legacy Event Bridge Strategy

- **Status:** Proposed
- **Date:** 2026-08-30
- **Deciders:** (pending — platform architecture review)
- **Workstream:** W2-D
- **Implementation allowed:** no

Label key: **[evidence]** verified from repository or legacy audit; **[proposal]** recommended design not yet accepted; **[decision]** binding only after acceptance; **[assumption]** engineering default pending validation; **[unresolved policy]** requires named deciders.

---

## Context

### Problem statement

**[evidence]** ADR-0001 (Accepted) sequences **Wave 1 low-risk consumer extraction** (Audit, Notification, Tracking, Control Tower, Media/Proof projections) before authoritative write-owner cutover, and lists an unresolved **bridge strategy** as a Wave 1 prerequisite (`docs/adr/0001-transitional-deployables-and-extraction-order.md` §Unresolved questions).

**[evidence]** ADR-0002 (Accepted) defines platform integration semantics — versioned JSON envelope, per-service transactional `integration_outbox`, durable `integration_inbox`, NATS JetStream — but legacy has **no NATS** and **no cross-service event bus** (`docs/audit/legacy-runtime-inventory.md` §Messaging & Async).

**[evidence]** ADR-0006 (Accepted) requires **zero-gap post-HWM capture**, forbids **`updated_at` alone** as a universal HWM, and permits forward replication via outbox relay, CDC, or proven polling — selected per table cluster after capacity proof.

The decision question is:

> **How does the platform capture legacy-originated facts and publish ADR-0002 integration events to JetStream during Waves 1–2, while legacy remains the authoritative writer, without bidirectional dual-write or cross-service database access?**

This ADR defines the **initial legacy event bridge** (transitional capture + relay). It is **not** the final per-service native outbox pattern (ADR-0002). Bridge retirement is mandatory when each bounded context owns its writer and native outbox.

### Verified platform constraints (evidence)

| Constraint | Source |
|------------|--------|
| Legacy repo read-only for platform work | `AGENTS.md`, `.cursor/rules/01-legacy-read-only.mdc` |
| At-least-once JetStream; idempotent consumers | `architecture/invariants.md`, ADR-0002 |
| One-writer cutover; no bidirectional dual-write | `architecture/invariants.md`, ADR-0006 |
| No cross-service DB credentials or FKs | `architecture/invariants.md`, `AGENTS.md` |
| Shipment sole lifecycle writer (target) | ADR-0003, `ownership-matrix.yaml` |
| Physical delivery irreversible; finance ≠ delivery rollback | ADR-0003, ADR-0005 |
| Finance/settlement policy-blocked | ADR-0005, `service-boundaries.yaml` |

### Verified legacy baseline (evidence)

| Item | Evidence |
|------|----------|
| Repository | `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` |
| Pinned SHA | `2e375057fdf9b9ce8416408a4436303be5301def` |
| Database | Single PostgreSQL 16; 78 Alembic revisions; one `DATABASE_URL` for app + workers |
| Message broker | None (in-process calls only) |
| Push outbox | `notification_push_outbox` — FCM push dispatch only |
| Domain timeline | `shipment_events` append-only table |
| Multi-writer shipment status | pickup, hub, linehaul, delivery_task, shipment (13 paths per ADR-0003) |
| Notification emission | Synchronous in-process `emit_shipment_status_notification` |
| Audit | Append-only `audit_logs`; cross-module writers |
| Tracking / Control Tower | Read-only repository queries on shared DB |
| Dirty file (not inspected) | `scripts/dev_pickup_driver_simulator.py` |

**[evidence]** Legacy dirty file `scripts/dev_pickup_driver_simulator.py` was not read, edited, or modified during this ADR preparation.

---

## Verified legacy write and capture surfaces

Audit method: `git show` at pinned SHA; cross-check `docs/audit/legacy-data-ownership-inventory.md`, ADR-0003 writer matrix.

### Shipment lifecycle writers (status + events)

| Module | Mutates `shipments.current_status` | Appends `shipment_events` | In-process notification |
|--------|-----------------------------------|---------------------------|-------------------------|
| shipment | yes (create, ops completion) | yes | yes (`delivery_completion.py`) |
| pickup | yes (`acceptance_scan_pickup_task.py` ~272–275) | yes | yes |
| hub | yes (`origin_hub_inbound_scan.py`) | yes | yes |
| linehaul | yes (`dispatch_linehaul_trip.py`, `arrive_linehaul_trip.py`) | yes | yes (`isolate_failures=True` on dispatch) |
| delivery_task | yes (start/complete/fail) | yes | yes |

**[evidence]** `ShipmentEventORM` (`shipment/infrastructure/models.py`): UUID PK, `event_type`, `occurred_at`, `old_status`/`new_status`, `metadata_jsonb`; **no** `updated_at` on event rows; `created_at` server default.

**[evidence]** `ShipmentORM` uses `TimestampMixin` (`updated_at` on status mutation) — **insufficient alone** for completeness (ADR-0006 H2 warnings: non-touching updates, hard deletes, clock skew).

### COD and wallet (same-transaction coupling)

**[evidence]** `CompleteDeliveryTaskUseCase` (`delivery_task/application/complete_delivery_task.py`): resolves COD, calls `_persist_cod_collection_and_wallet_credit`, sets `shipment.current_status = DELIVERED`, appends events, audit, and `emit_shipment_status_notification` in one request-scoped session.

**[evidence]** `CreditCodCollectedToMerchantWalletUseCase` idempotency key `cod_collected:{shipment_id}` — legacy wallet fact, **not** the target cross-service finance model (ADR-0005).

### Notification — not a general domain outbox

**[evidence]** `NotificationPushOutboxORM` (`notification/infrastructure/push_outbox_models.py`): FK to `in_app_notifications`, FCM provider fields, `dedupe_key`, lease/retry columns — **downstream of in-app notification creation**, not co-located with domain mutations.

**[evidence]** `PushOutboxWorker` (`notification/application/push_outbox_worker.py`): poll loop, recover stale leases, claim batch, per-row commit — **push delivery relay only**.

**[evidence]** `NotificationEventKey` catalog (`notification/domain/event_catalog.py`): stable string keys (`shipment.created`, `shipment.delivered`, …) for **in-app/push preference routing** — not JetStream integration contracts.

**[decision boundary]** The legacy push-notification outbox MUST NOT be treated as a general domain-event outbox or as proof that legacy already implements ADR-0002 transactional integration outbox.

### Audit capture surface

**[evidence]** `AuditLogORM` (`audit/infrastructure/models.py`): append-only; `action`, `entity_type`, `entity_id`, `metadata_jsonb`, `created_at`; no `updated_at`.

**[evidence]** Writers include auth, wallet, delivery_task, merchant (`docs/audit/legacy-data-ownership-inventory.md` §Audit Log Writers).

### Read projections (no dedicated event store)

**[evidence]** Tracking: `tracking/api/routes.py` — use cases query shipment repositories/events (no tracking-owned mutable tables).

**[evidence]** Control Tower: `control_tower/application/search_shipments.py`, `get_shipment_detail.py`, `get_proof_detail.py` — read aggregation.

### Background mutators

**[evidence]** `scripts/run_push_outbox_worker.py` — notification push only.

**[evidence]** `delivery_evidence_attachment_cleanup_worker.py` — shipment evidence cleanup (not lifecycle events).

**[evidence]** No CDC, logical replication slot, or Debezium configuration in legacy repository.

---

## Bridge versus native outbox

| Aspect | Initial legacy bridge (this ADR) | Final service-native outbox (ADR-0002) |
|--------|----------------------------------|---------------------------------------|
| Writer | Legacy monolith DB (authoritative until cutover) | Extracted service DB |
| Capture | Read-only relay from legacy or legacy-side outbox (if ever authorized) | Same-transaction `integration_outbox` insert |
| `producer` envelope field | `legacy_bridge` with `metadata.source_module` **[proposal]** | Deployable service id (`pickup`, `shipment`, …) |
| Retirement | Required when context passes ADR-0006 stage 13 for that cluster | Permanent |
| Cutover | Forwards post-HWM changes until credential revocation | Sole writer after stage 12 |

**[decision boundary]** Bridge consumers MUST use ADR-0002 inbox deduplication on `(consumer_name, event_id)`. Bridge-generated `event_id` MUST be deterministic from `(source_table, source_pk, source_position)` **[proposal]** so replays do not double-apply side effects.

---

## Options

### O1 — Transactional domain outbox inside legacy monolith

Introduce `legacy_integration_outbox` (or per-module outbox tables) written in the **same DB transaction** as domain mutations across pickup, hub, linehaul, delivery_task, shipment, wallet, audit emitters.

| Criterion | Assessment |
|-----------|------------|
| Atomicity with legacy writes | **High** — same transaction as mutation |
| Semantic event quality | **High** — mapper at write site knows business context |
| Multi-writer coverage | **Med** — requires touching all 13+ writer paths; easy to miss timeline-only writers |
| Zero-gap / cutover (ADR-0006) | **High** — durable sequence; satisfies R2 forward capture |
| Replay / idempotency | **High** — monotonic outbox sequence + deterministic `event_id` |
| Operational complexity | **Med** — legacy worker + platform relay; **violates platform legacy read-only policy for implementation** |
| Failure behavior | Relay backlog; domain commit succeeds if outbox insert succeeds |
| Reconciliation evidence | Outbox lag, row counts vs JetStream publish ACK |
| Physical delivery / COD facts | Can emit distinct facts if mappers split completion steps (legacy refactor) |
| One-writer extraction | Strong post-HWM buffer until target service owns outbox |
| Rollback / forward-fix | Disable relay; outbox accumulates; no dual-write |
| Consumer-first extraction | **Blocked on legacy mutation authorization** |

### O2 — PostgreSQL WAL / logical CDC (Debezium or native logical replication)

Stream changes from legacy Postgres after a bookmarked LSN to platform bridge consumers.

| Criterion | Assessment |
|-----------|------------|
| Atomicity with legacy writes | **High** (WAL order) — capture is post-commit |
| Semantic event quality | **Low–Med** — row-level; needs enrichment layer for ADR-0002 payloads |
| Multi-writer coverage | **High** — captures all tables in publication |
| Zero-gap / cutover | **High** — LSN bookmark aligns with ADR-0006 H3 |
| Replay / idempotency | **Med** — replay from slot; bridge must map LSN+tuple to stable `event_id` |
| Operational complexity | **High** — CDC ops, schema drift, slot disk, single-host 16 GB budget |
| Failure behavior | Slot lag; WAL retention pressure; bridge down → growing lag |
| Reconciliation evidence | LSN lag metrics, row-level diff vs source |
| Physical delivery / COD facts | Captures `delivery_cod_collections`, wallet ledger inserts — **finance semantics still ADR-0005 blocked** |
| One-writer extraction | Compatible — forward replication until revocation |
| Rollback / forward-fix | Stop consumer; no legacy write impact |
| Consumer-first extraction | **Med** — best completeness; **blocked on CDC infrastructure proof** |

### O3 — Proven monotonic polling on append-only cursors

Platform **Legacy Event Bridge** (read-only DB role) polls:

- `shipment_events` on `(occurred_at, id)` cursor (H4)
- `audit_logs` on `(created_at, id)` cursor
- Optional: `delivery_cod_collections` inserts by PK/time for finance-adjacent facts (**policy-blocked consumers**)

| Criterion | Assessment |
|-----------|------------|
| Atomicity with legacy writes | **Med** — post-commit visibility; poll interval lag |
| Semantic event quality | **Med–High** for `shipment_events` (rich domain fields); **Med** for audit |
| Multi-writer coverage | **Med** — lifecycle transitions that append events are covered; **gaps** if status changes without events (none evidenced for status writers); timeline-only events included |
| Zero-gap / cutover | **Med** — requires HWM cursor captured **before** backfill + continuous poll; gap if bridge down > poll SLO |
| Replay / idempotency | **High** with deterministic `event_id` from source row |
| Operational complexity | **Low–Med** — one bridge deployable, read-only credential |
| Failure behavior | Bridge lag; legacy unaffected; consumers at-least-once |
| Reconciliation evidence | Cursor position vs `max(shipment_events.id)`; inbox duplicate rate |
| Physical delivery / COD facts | `SHIPMENT_DELIVERED` events captured; COD row requires separate poll/table |
| One-writer extraction | Bridge replays until stage 13; then retired |
| Rollback / forward-fix | Pause bridge; reset cursor with evidence |
| Consumer-first extraction | **High fit** — no legacy code change |

**[evidence]** `shipment_events.id` is UUID v4 — **not strictly monotonic by time**; cursor MUST use `(occurred_at, id)` total order with tie-break, not `id` alone.

### O4 — Application-level after-commit publishing (no durable legacy outbox)

Hook legacy use cases to HTTP/NATS publish after `COMMIT` without durable local queue.

| Criterion | Assessment |
|-----------|------------|
| Atomicity | **Low** — commit succeeds, publish may fail → gap |
| Semantic quality | **High** if at write site | 
| Multi-writer coverage | **Low** — same touch-all-modules problem as O1 |
| Zero-gap / cutover | **Low** — violates ADR-0006 spirit |
| Replay / idempotency | **Low** without durable capture |
| Operational complexity | **Low** initially |
| Failure behavior | Silent event loss |
| Consumer-first extraction | **Rejected as primary** |

### O5 — Narrowly scoped hybrid transition (recommended proposal)

Combine mechanisms by **fact class** and **migration wave**:

| Fact class | Phase 1 capture (proposal) | Phase 2+ (proposal) |
|------------|---------------------------|------------------------|
| Shipment lifecycle timeline | O3 cursor on `shipment_events` | Native `shipment` outbox after cutover |
| Audit entries | O3 cursor on `audit_logs` | Per-service audit emission |
| COD / wallet | O3 poll `delivery_cod_collections` + ledger (**consumers policy-blocked**) | ADR-0005 finance facts only |
| Hot mutable entities pre-cutover | O2 CDC when ops proof exists | Retire bridge segment |
| Optional completeness backstop | O1 legacy outbox **only if** legacy mutation explicitly authorized | N/A |

**[proposal]** Platform-side bridge with **read-only** legacy credentials; **no** bidirectional dual-write; legacy remains write authority until ADR-0006 stage 13.

---

## Comparative option matrix

Scores: **Low** / **Med** / **High** (qualitative — **proposal**, not measured).

| Criterion | O1 Legacy outbox | O2 WAL/CDC | O3 Monotonic poll | O4 After-commit | O5 Hybrid |
|-----------|------------------|------------|-------------------|-----------------|-----------|
| Atomicity with legacy writes | High | Med (post-commit) | Med | Low | Med–High |
| Semantic event quality | High | Med | Med–High | High | Med–High |
| Multi-writer coverage | Med | High | Med | Low | High |
| Zero-gap / cutover compatibility | High | High | Med | Low | Med–High |
| Replay / idempotency | High | Med | High | Low | High |
| Operational complexity | Med | High | Low–Med | Low | Med |
| Failure behavior (bridge down) | Outbox backlog | WAL lag | Poll lag | Event loss | Segmented |
| Reconciliation evidence | Outbox vs publish | LSN lag | Cursor vs max(row) | Weak | Combined |
| Delivery / COD fact fidelity | High (if refactored) | High (raw rows) | Med (multi-cursor) | Low | Med–High |
| One-writer extraction fit | High | High | Med | Low | High |
| Rollback / forward-fix | Strong | Strong | Strong | Weak | Strong |
| Consumer-first (no legacy edit) | Low | Med | **High** | Med | **High** |
| Legacy read-only policy fit | **Fails** (needs legacy edits) | **Passes** | **Passes** | **Fails** | **Passes** (Phase 1) |

---

## Decision drivers

1. **[evidence]** Legacy read-only policy — platform must not depend on unapproved legacy code changes for Wave 1.
2. **[evidence]** ADR-0006 zero-gap capture — bridge must record HWM/source position; `updated_at`-only polling forbidden as default.
3. **[evidence]** ADR-0002 envelope — stable `event_id`, `source_position`, versioned `event_type`/`event_version`.
4. **[evidence]** Multi-writer legacy — capture must not assume single module emits all lifecycle facts.
5. **[proposal]** 16 GB single-host — prefer low moving parts until CDC capacity proof.
6. **[evidence]** Irreversible delivery and COD decoupling (ADR-0003/0005) — bridge must not merge wallet credit into delivery lifecycle messages as canonical finance fact.
7. **[proposal]** Consumer-first Wave 1 — minimize cutover risk; bridge is explicitly temporary.

---

## Decision

**[proposal] Recommended strategy (status remains Proposed):**

Adopt **O5 — narrowly scoped hybrid**, with **Phase 1 default capture = O3 (proven monotonic polling)** via a platform **Legacy Event Bridge** deployable:

1. **Read-only** legacy PostgreSQL role scoped to `SELECT` on approved capture tables only.
2. **Durable bridge cursor store** in platform-owned DB (not legacy) recording per-stream `(table, occurred_at|created_at, id)` high-water mark.
3. **Publish** ADR-0002 envelopes to JetStream with:
   - `producer`: `legacy_bridge` **[proposal]**
   - `metadata.source_module`, `metadata.source_table`, `metadata.source_pk`, `metadata.source_position` **[proposal]**
   - `metadata.replay` / `metadata.replay_source` when applicable
   - Deterministic `event_id` = UUIDv5(namespace, `{table}:{pk}:{position}`) **[proposal]**
4. **Pre-HWM / post-HWM:** Align with ADR-0006 stage 3 — record bridge cursor at HWM **before or atomically with** consumer backfill snapshot; continuous polling guarantees post-HWM rows reach JetStream before consumer cutover.
5. **Retire** bridge stream when ADR-0006 stage 13 completes for that fact class; native service outbox becomes sole publisher.

**Phase 2 escalation (unresolved):** Add **O2 CDC** for table clusters where polling lag or completeness proof fails (hot mutable tables, hard deletes, non-event status paths).

**Explicitly not selected as Phase 1 default:**

- **O1** — requires legacy mutation (forbidden without separate authorization).
- **O4** — insufficient durability for ADR-0006.
- **`updated_at`-only polling** on `shipments` — forbidden unless completeness proven (not evidenced).

**[decision boundary] Forbidden regardless of option:**

- Bidirectional dual-write between legacy and platform databases.
- Platform services holding legacy **write** credentials (read-only bridge role except break-glass).
- Treating `notification_push_outbox` as domain integration outbox.
- Publishing accepted `contracts/events/*` payloads without contract registration and consumer sign-off.

**Status: Proposed.** Production proof (bridge lag SLO, zero-gap drill, first-consumer inbox tests) and CDC capacity decision remain blockers before acceptance.

---

## Pre-HWM, backfill, and post-HWM capture

**[proposal]** For each bridge stream:

```text
Stage A — HWM record
  bridge_cursor_0 := max captured key at T0 (transaction-scoped snapshot preferred)

Stage B — Consumer backfill (if any)
  Historical rows ≤ HWM loaded into consumer projection store via deterministic replay
  event_id derived from source row (same formula as live bridge)

Stage C — Live capture (starts at or before Stage B snapshot)
  Poll legacy WHERE (occurred_at, id) > cursor OR (created_at, id) > cursor
  Publish to JetStream; advance cursor only after broker ACK

Stage D — Zero-gap gate (ADR-0006 stage 7)
  Prove: ∀ row R written after T0, R appears in JetStream or quarantine DLQ
  Bridge cursor ≥ max(source keys) at verification instant
```

**[decision boundary]** Do not start consumer write cutover until Stage D evidence exists for that stream.

**[evidence gap]** Hard deletes on legacy operational tables — tombstone strategy **unresolved** (ADR-0006); polling append-only tables avoids deletes on `shipment_events`/`audit_logs`.

---

## Envelope mapping (proposal — not accepted contracts)

Bridge maps **verified legacy rows** to **provisional** integration shapes. Names below are **mapping targets for bridge implementation planning**, not registered `contracts/events/*` until Wave 1 contract gate passes.

| Legacy source | Verified fields | Provisional bridge mapping | First-consumer suitability |
|---------------|-----------------|----------------------------|----------------------------|
| `shipment_events` row | `event_type`, `occurred_at`, `old_status`, `new_status`, `actor_*`, `metadata_jsonb` | Lifecycle/timeline integration facts | Tracking, Control Tower, Notification (derived) |
| `audit_logs` row | `action`, `entity_type`, `entity_id`, `metadata_jsonb` | Audit transport events | Audit service |
| `in_app_notifications` + catalog keys | Created by `InAppNotificationService` | **Not** captured via push_outbox for domain bridge — prefer `shipment_events` + audit | Notification (partial — see gap) |
| `delivery_cod_collections` insert | `shipment_id`, amounts | COD fact candidate | **Policy-blocked** (ADR-0005) — bridge may capture but Finance consumer blocked |
| Wallet ledger insert | `idempotency_key`, `entry_type` | Wallet fact candidate | **Policy-blocked** |

**[evidence]** Notification today is triggered **synchronously** from use cases (`emit_shipment_status_notification`) — bridge can **supplement** Notification consumer by translating `shipment_events` with status transitions, but parity with all notification catalog keys (pickup scheduling, merchant application, etc.) requires **additional cursors** or **unresolved** legacy-side hooks.

---

## Candidate first-consumer facts (evidence-backed)

| Consumer | Verified legacy facts safe for low-risk projection | Gaps / blockers |
|----------|---------------------------------------------------|-----------------|
| **Audit** | Append-only `audit_logs` | Cross-module emission already decentralized; bridge cursor viable |
| **Notification** | Status transitions via `shipment_events` + catalog key mapping | Pickup-scheduling/support events need more sources; push_outbox is not domain capture |
| **Tracking** | `shipment_events` timeline + shipment read model backfill | Enriched operational detail (pickup proof pointers) requires Media/Proof ADR |
| **Control Tower** | Same as Tracking + proof metadata reads | No write path; read API may remain on legacy until read cutover |
| **Media/Proof** | MinIO object keys referenced in event `metadata_jsonb` / evidence tables | Canonical owner **undecided**; bridge must emit **references only** (ADR-0002 media_refs) |

**[decision boundary]** Do not treat `NotificationEventKey` strings as platform JetStream `event_type` contracts without `contracts/events/` registration.

---

## Minimum evidence before first consumer extraction

**[proposal]** Gate Wave 1 consumer deploy on:

| # | Evidence artifact | Pass criteria |
|---|-------------------|---------------|
| E1 | Bridge read-only credential proof | `SELECT` only on allowlisted tables; write attempt fails |
| E2 | Cursor monotonicity test | No duplicate `event_id`; `(occurred_at,id)` ordering verified on sample |
| E3 | Zero-gap drill (ADR-0006 stage 7) | Synthetic legacy writes after HWM appear on JetStream ≤ lag SLO |
| E4 | Inbox idempotency test | Duplicate delivery does not double projection side effects |
| E5 | Reconciliation sample | 100-shipment compare: legacy timeline vs consumer projection |
| E6 | Observability baseline | `bridge_poll_lag_seconds`, `bridge_publish_total`, DLQ depth |
| E7 | Rollback drill | Pause bridge; legacy API unaffected; consumer catches up on resume |
| E8 | Finance boundary test | COD/wallet events **not** consumed by Wallet/Finance until ADR-0005 unblocked |

**[evidence gap]** Numeric lag SLO — **unresolved policy** (placeholder: p95 < 10s poll interval under normal load).

---

## Policy blockers (do not block unrelated migration)

| Blocker | Affected extraction | Unrelated work allowed |
|---------|---------------------|------------------------|
| ADR-0005 finance policy | Finance/Wallet consumers on COD/ledger bridge stream | Audit, Tracking, Control Tower on lifecycle/audit streams |
| ADR-0004 identity trust | Service credential issuance for bridge → NATS | Bridge proof in staging with test credentials |
| Media/Proof ownership ADR | Evidence-rich notification payloads | Status-only timeline projections |
| CDC infrastructure choice | Hot-table completeness escalation | Poll-only Phase 1 bridge |
| Legacy mutation authorization | O1 legacy domain outbox | O3/O5 platform bridge |

---

## Migration impact

- **Legacy:** No mutation required for Phase 1 (O3/O5). Optional future O1 requires separate program — out of platform worktree scope.
- **Platform:** New transitional **Legacy Event Bridge** deployable (not a bounded-context owner of business tables); bridge DB for cursors; NATS publish credentials per ADR-0002.
- **Cutover:** For each context, bridge stream retires at ADR-0006 stage 13; native outbox replaces bridge for that `producer`.
- **Compatibility:** Consumers MUST tolerate `producer=legacy_bridge` during transition; after cutover, dual subscribe during `event_version` migration only.
- **Bidirectional dual-write:** **Forbidden.**

---

## Observability

**[proposal]**

| Signal | Purpose |
|--------|---------|
| `legacy_bridge_poll_lag_seconds{stream}` | Cursor vs source `max` |
| `legacy_bridge_publish_total{status}` | Relay health |
| `legacy_bridge_cursor_position{stream}` | Audit trail |
| `legacy_bridge_mapping_error_total` | Row → envelope failures |
| `legacy_bridge_dlq_depth` | Poison mapping |
| Logs | `event_id`, `source_table`, `source_pk`, `correlation_id` — no confidential payload at INFO |

Propagate `X-Request-ID` from legacy HTTP into envelope `correlation_id` when present in `metadata_jsonb` or audit row — **assumption:** not always available on async poll path.

---

## Security

| Concern | Requirement |
|---------|-------------|
| Legacy credentials | **Read-only** role; table allowlist; network segment restricted |
| Secret scope | Bridge holds legacy RO URL + NATS creds only — not other service DBs |
| Service identity | Bridge publishes as `legacy_bridge`; consumers verify via ADR-0004 when accepted |
| Data classification | Map legacy rows to `data_classification` per field policy; PII minimization |
| Break-glass | Elevated legacy access audited separately — not bridge default |
| Finance | Do not expose settlement projections from bridge until ADR-0005 |

---

## Rollback

| Scenario | Action | Irreversible |
|----------|--------|--------------|
| Bridge mis-publishing | Pause bridge; fix mapper; replay from cursor with `metadata.replay=true` | — |
| Consumer poison | Quarantine inbox; ACK stop; legacy unaffected | — |
| False lifecycle projection | Forward-fix consumer; legacy DB unchanged | — |
| Bridge cursor reset error | Re-run idempotent replay; inbox dedupe prevents dupes | — |
| Post-cutover | Retire bridge; native outbox only | Physical delivery facts |

**[evidence]** Physical delivery and COD collection rows cannot be rolled back by bridge pause (ADR-0003).

---

## Consequences

### Positive

- Enables ADR-0001 Wave 1 without legacy code changes.
- Honest at-least-once semantics with ADR-0002 inbox.
- Clear retirement path to per-service native outboxes.
- Separates push notification delivery from domain integration capture.

### Negative

- Poll lag vs synchronous legacy notifications.
- Semantic gap between raw `shipment_events.event_type` and target `event_type` dot notation — mapping maintenance.
- UUID event keys require careful cursor ordering.
- CDC escalation adds ops burden when required.

### Neutral

- Bridge is transitional infrastructure, not a bounded context.
- Finance streams may be captured but not consumed until policy resolves.

---

## Unresolved questions

1. **[unresolved policy]** Acceptable bridge poll lag SLO (numeric)?
2. **[unresolved policy]** CDC adoption timeline vs poll-only Phase 1 — ops capacity?
3. **[unresolved policy]** Whether legacy RO replica vs primary read for bridge?
4. **[unresolved policy]** Notification parity for non-shipment catalog events without legacy hooks?
5. **[unresolved policy]** `producer=legacy_bridge` vs semantic `producer` with `metadata.bridge=true`?
6. **[unresolved policy]** Tombstone handling if legacy hard-deletes operational rows?
7. **[unresolved policy]** Who approves legacy mutation if O1 ever required for completeness?
8. **[assumption]** UUIDv5 deterministic `event_id` namespace UUID — assign at implementation?
9. **[unresolved policy]** Bridge deployable grouping — standalone vs Platform Edge plateau (ADR-0001 P1)?

---

## Alternatives considered

| Alternative | Why rejected or deferred |
|-------------|-------------------------|
| O1 as Phase 1 default | Legacy read-only policy; multi-module touch surface |
| O4 after-commit only | ADR-0006 zero-gap violation; event loss on publish failure |
| `updated_at` poll on `shipments` | ADR-0006 forbids as universal HWM; events table preferred |
| Reuse `notification_push_outbox` | Push delivery only; not co-transactional with domain writes |
| Bidirectional dual-write | Platform invariant forbidden |
| Permanent bridge | Violates native outbox target; must retire per context |
| Direct consumer DB read of legacy | Cross-service DB access forbidden |

---

## Dependencies on other ADRs

| ADR | Relationship |
|-----|--------------|
| ADR-0001 | Wave 1 sequencing; bridge prerequisite |
| ADR-0002 | Envelope, outbox/inbox, JetStream — target consumer semantics |
| ADR-0003 | Lifecycle fact semantics; irreversible delivery |
| ADR-0005 | Finance consumer block; COD fact separation |
| ADR-0006 | HWM, zero-gap, forward replication, bridge retirement at stage 13 |

---

## Explicit non-goals

- Implementing bridge code, NATS config, or legacy migrations
- Accepting this ADR as production-ready without E1–E8 evidence
- Defining final `contracts/events/*` schemas
- Modifying legacy repository
- Resolving finance settlement policy (ADR-0005)

---

## References

- ADR-0001 @ `docs/adr/0001-transitional-deployables-and-extraction-order.md`
- ADR-0002 @ `docs/adr/0002-event-envelope-outbox-inbox-and-jetstream.md`
- ADR-0003 @ `docs/adr/0003-shipment-lifecycle-authority-and-delivery-facts.md`
- ADR-0005 @ `docs/adr/0005-cod-wallet-ledger-and-settlement.md`
- ADR-0006 @ `docs/adr/0006-one-writer-data-cutover-and-reconciliation.md`
- Platform: `architecture/invariants.md`, `architecture/ownership-matrix.yaml`, `architecture/service-boundaries.yaml`
- Audits: `docs/audit/legacy-baseline.md`, `legacy-data-ownership-inventory.md`, `legacy-runtime-inventory.md`, `legacy-domain-inventory.md`
- Legacy evidence SHA: `2e375057fdf9b9ce8416408a4436303be5301def`
- Legacy files (read-only): `shipment/infrastructure/models.py`, `notification/infrastructure/push_outbox_models.py`, `notification/application/push_outbox_worker.py`, `notification/domain/event_catalog.py`, `notification/application/emit_shipment_notifications.py`, `pickup/application/acceptance_scan_pickup_task.py`, `delivery_task/application/complete_delivery_task.py`, `audit/infrastructure/models.py`, `shared/db/mixins.py`

---

```text
ADR path: docs/adr/0007-legacy-event-bridge-strategy.md
Status: Proposed
Deciders: (pending)
Canonical docs updated: none (proposed only)
Unresolved questions: 9 (see section above)
Implementation allowed: no
```
