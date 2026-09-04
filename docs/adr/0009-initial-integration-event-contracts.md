# ADR-0009: Initial Versioned Integration-Event Payload Contracts

- **Status:** Accepted
- **Date:** 2026-08-30
- **Deciders:** platform architecture review (Wave 3 capture integration)
- **Workstream:** W3-E; W17-A acceptance-boundary reconciliation (C10 status only)
- **Implementation allowed:** A1/A2 — no production publishers without existing gates;
  C10 — `implementation_authorized_not_production_enabled` (contract + adapters next
  coding wave; staging/production gated)

Label key: **[evidence]** verified from repository or legacy audit; **[proposal]** recommended design not yet accepted; **[decision]** binding only after acceptance; **[assumption]** engineering default pending validation; **[unresolved policy]** requires named deciders.

---

## Context

### Problem statement

**[evidence]** ADR-0007 (Accepted) defines transitional **Legacy Event Bridge** CDC capture
mapping legacy rows to observation contracts. ADR-0002 (Accepted) defines envelope shape,
subject grammar (S2), outbox/inbox semantics, and compatibility policy, but explicitly defers
`contracts/events/{event_type}/v{N}.json` creation.

**[evidence]** The platform implements a technical envelope package (`packages/event_envelope/`) with `MessageKind`, `AggregateScope`, and validation aligned to ADR-0002, but **no versioned domain payload schemas** exist yet (`contracts/README.md` — Foundation F0).

The decision question is:

> **Which first versioned transitional observation contracts should the Legacy Event Bridge
> publish during Wave 1, without inventing finance policy, aggregate versions for legacy rows,
> or treating CDC/poll rows as canonical domain events?**

**[decision]** This ADR accepts **two transitional observation contract identities only**.
Canonical lifecycle, finance, notification projection, and operational facts remain **deferred**
(documented as future candidates). JSON Schema files and production publishers are **next Wave**.

### Verified platform constraints (evidence)

| Constraint | Source |
|------------|--------|
| At-least-once JetStream; idempotent inbox on `(consumer_name, event_id)` | ADR-0002, `architecture/invariants.md` |
| Subject grammar: aggregate S2 plus non-aggregate stream-routed form (ADR-0009 A1/A2) | ADR-0002, `infra/eventing/subject-grammar.md` |
| `message_kind` in envelope — not a subject segment | ADR-0002 S2 |
| Shipment sole canonical lifecycle writer (target) | ADR-0003, `ownership-matrix.yaml` |
| Finance/settlement policy-blocked | ADR-0005, `service-boundaries.yaml` |
| Bridge `producer=legacy_bridge` during transition | ADR-0007 |
| No generic unversioned payloads | ADR-0002 compatibility policy |
| Legacy read-only; dirty simulator untouched | `AGENTS.md`, `.cursor/rules/01-legacy-read-only.mdc` |

### Verified legacy baseline (evidence)

| Item | Evidence |
|------|----------|
| Repository | `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` |
| Pinned SHA | `2e375057fdf9b9ce8416408a4436303be5301def` |
| Shipment timeline store | `shipment_events` append-only: `event_type`, `occurred_at`, `old_status`, `new_status`, `metadata_jsonb` (`shipment/infrastructure/models.py`) |
| Shipment event enum | 24 types in `ShipmentEventType` (`shipment/domain/enums.py`) |
| Audit store | `audit_logs` append-only: `action`, `entity_type`, `entity_id`, `metadata_jsonb`, `created_at` (`audit/infrastructure/models.py`) |
| Notification catalog | `NotificationEventKey` strings (`shipment.created`, `shipment.delivered`, …) — in-app/push routing only (`notification/domain/event_catalog.py`) |
| Tracking / Control Tower | Read-only queries on shipments/events; no owned mutable tables (`docs/audit/legacy-domain-inventory.md`) |
| Media / evidence | MinIO prefixes `pickup-evidence/`, `delivery-evidence/{shipment_id}/`; ownership ambiguous (`docs/audit/legacy-data-ownership-inventory.md`) |
| COD / wallet | Same-transaction coupling in `complete_delivery_task.py`; idempotency `cod_collected:{shipment_id}` — **not** target finance model (ADR-0005) |

**[evidence]** Legacy dirty file `scripts/dev_pickup_driver_simulator.py` was not read, edited, or modified during this ADR preparation.

---

## Message taxonomy (binding distinctions)

**[decision boundary]** All messages share the ADR-0002 physical envelope. Semantic class is determined by `message_kind`, producer authority, and consumer treatment — not by capture mechanism alone.

| Class | `message_kind` | Authority | Mutability | Example |
|-------|----------------|-----------|------------|---------|
| **Immutable physical fact** | `integration` | Operational context (Pickup, Hub, Linehaul, Delivery) | Append-only; never erased | `delivery.fact.task_completed` |
| **Canonical Shipment lifecycle event** | `integration` or `domain` | **Shipment only** (post-cutover) | Append-only canonical timeline | `shipment.fact.lifecycle_changed` |
| **Transitional legacy observation** | `integration` | `legacy_bridge` (read-only capture) | Observation of legacy row; **not** canonical domain authority | `legacy_bridge.observation.shipment_timeline_entry`, `legacy_bridge.observation.audit_entry` |
| **Projection notification** | `projection` | Notification (derived) | Idempotent upsert of delivery intent | `notification.projection.delivery_requested` |
| **Command** | `command` | Authorized caller → Shipment | Intent; handler validates | `delivery.command.complete` |
| **Integration event** | `integration` | Any authorized publisher | Cross-service fact after commit | All rows above except pure projections |

**[decision boundary]** A row observed through CDC or polling **MUST NOT** automatically become a canonical domain event. Bridge observations carry `metadata.source_*` provenance and `producer=legacy_bridge`. Canonical lifecycle facts carry `producer=shipment` and Shipment-issued `aggregate_version`.

---

## Mandatory distinction evaluations

### `LegacyShipmentTimelineEntryObserved` versus `ShipmentStatusChanged`

| Aspect | `legacy_bridge.observation.shipment_timeline_entry` | `shipment.fact.lifecycle_changed` |
|--------|-----------------------------------------------------|-----------------------------------|
| **Purpose** | Transitional transport of a legacy `shipment_events` row | Canonical lifecycle transition applied by Shipment |
| **Producer** | `legacy_bridge` | `shipment` |
| **Authority** | Legacy monolith write path (multi-writer) | Shipment sole writer (ADR-0003) |
| **Status fields** | Reflects legacy `old_status`/`new_status` as observed | Reflects Shipment-validated transition |
| **Consumers (Wave 1)** | Tracking, Control Tower, Notification (interim) | Tracking, Control Tower, Notification (target) |
| **Retirement** | Required at ADR-0006 stage 13 per context | Permanent |
| **Verdict** | **[proposal] Accept** as Wave 1 bridge contract | **[proposal] Accept** as target canonical contract; **defer native publish** until Shipment cutover |

### `DeliveryCompleted` (`delivery.fact.task_completed`)

| Aspect | Assessment |
|--------|------------|
| **Kind** | Immutable operational / physical fact |
| **Producer** | `delivery` (native) or mapped from legacy `DELIVERY_TASK_COMPLETED` / `SHIPMENT_DELIVERED` rows with care |
| **Lifecycle authority** | Does **not** mutate canonical Shipment state — Shipment consumes and applies |
| **Distinct from** | `shipment.fact.delivered`, `legacy_bridge.observation.shipment_timeline_entry` |
| **Verdict** | **[proposal] Accept** contract definition; **defer native publish** until Delivery service cutover; bridge may map legacy rows with explicit `source_event_type` |

### `CodCollected` (`delivery.fact.cod_collected`)

| Aspect | Assessment |
|--------|------------|
| **Kind** | Operational finance-adjacent fact |
| **Blocker** | ADR-0005 Proposed — Policy Blocked; source atomicity and posting policy unresolved |
| **Authority** | Delivery owns physical collection row; Finance owns posting — **no Delivery→Wallet direct path** |
| **Verdict** | **[proposal] Defer** contract registration and all Finance/Wallet consumers; bridge capture allowed but consumption blocked (ADR-0007 E8) |

### `ShipmentDelivered` (`shipment.fact.delivered`)

| Aspect | Assessment |
|--------|------------|
| **Kind** | Canonical Shipment fact (terminal lifecycle) |
| **Producer** | `shipment` only |
| **Distinct from** | `delivery.fact.task_completed` (operational), legacy `SHIPMENT_DELIVERED` observation |
| **Finance coupling** | Finance may consume for merchant payable recognition — **blocked until ADR-0005** |
| **Verdict** | **[proposal] Accept** contract definition; **defer publish** until Shipment cutover; Wave 1 consumers use bridge observation or lifecycle_changed subset |

### Notification request / projection candidates

| Aspect | Assessment |
|--------|------------|
| **Kind** | `projection` — derived delivery intent, not domain authority |
| **Legacy precedent** | `NotificationEventKey` catalog (`shipment.delivered`, …) — **not** JetStream `event_type` |
| **Trigger** | Notification service derives from `shipment.fact.lifecycle_changed` or bridge observation |
| **Verdict** | **[proposal] Accept** `notification.projection.delivery_requested` as internal projection contract; **reject** reusing catalog keys as integration `event_type` |

### Media / Proof observation candidates

| Aspect | Assessment |
|--------|------------|
| **Kind** | `integration` observation — object reference only |
| **Legacy precedent** | Evidence keys in `metadata_jsonb`; MinIO prefixes per module |
| **Blocker** | `media_proof` platform owner **undecided** (`service-boundaries.yaml`) |
| **Verdict** | **[proposal] Accept** draft `media_proof.observation.evidence_registered`; **defer implementation** until ownership ADR |

### Audit observation candidates

| Aspect | Assessment |
|--------|------------|
| **Kind** | `integration` fact — append-only audit entry transport |
| **Legacy precedent** | `audit_logs` table; cross-module writers |
| **Long-term store** | Audit service owns searchable retention; JetStream is transport (ADR-0002) |
| **Verdict** | **[proposal] Accept** `audit.fact.entry_recorded` as first native-quality contract |

---

### Audit observation versus canonical Audit fact

| Aspect | `legacy_bridge.observation.audit_entry` | `audit.fact.entry_recorded` (future) |
|--------|-------------------------------------------|--------------------------------------|
| **Producer** | `legacy_bridge` | `audit` (native, post-cutover) |
| **Authority** | Legacy row observation | Audit service after persistence |
| **Verdict** | **[decision] Accept** as Wave 1 bridge contract | **Defer** — not Bridge-emitted |

**[decision boundary]** Bridge MUST NOT emit `audit.fact.entry_recorded` as if Audit-owned.
That event type is reserved for future Audit-native publication after Audit persistence.

---

## Accepted minimal first contract set

**[decision]** The first accepted publish set contains **exactly two** transitional observations:

| # | `event_type` | `event_version` | Subject (non-aggregate, stream-routed) | `message_kind` | Envelope `producer` | Aggregate scope |
|---|--------------|-----------------|-------------------------------|----------------|---------------------|-----------------|
| A1 | `legacy_bridge.observation.shipment_timeline_entry` | 1 | `hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1` → `HUDHUD_SHIPMENT` | `integration` | `legacy_bridge` | **Non-aggregate** transitional — `shipment_id` correlation in payload; **no** invented `aggregate_version` |
| A2 | `legacy_bridge.observation.audit_entry` | 1 | `hudhud.audit.legacy_bridge.observation.audit_entry.v1` → `HUDHUD_AUDIT` | `integration` | `legacy_bridge` | **Non-aggregate** — entity-scoped correlation; **no** invented `aggregate_version` |

**Stable observation identity (A1/A2 — verified append-only rows only):**

```text
event_id = UUIDv5(
  event-type-specific stable namespace,
  "{source_system}:{source_table}:{source_pk}"
)
```

- Backfill and CDC INSERT for the same append-only row MUST generate exactly the same `event_id`.
- Capture mechanism, LSN, transaction ID, timestamp, and `source_op` MUST NOT affect A1/A2 identity.
- `event_id`, source LSN/position, `correlation_id`, and `aggregate_version` are **distinct fields**.
- Source LSN/position remains provenance metadata, not event identity.
- A1/A2 apply only to verified append-only source rows. Future mutable-row observations require an
  immutable source version/change identity and remain outside this accepted minimal set.
- Deletes/updates MUST NOT reuse an A1/A2 identity ambiguously.
- Legacy timeline sources have **no proven canonical Shipment aggregate version** — do not invent one.

**Routing:** Bridge is a **producer** (transitional technical deployable), not a bounded context
and not a domain owner. Observations route through context streams (`HUDHUD_SHIPMENT`,
`HUDHUD_AUDIT`) using the non-aggregate subject grammar. Envelope:
`producer=legacy_bridge`, `aggregate_scope=non_aggregate`. `legacy_bridge` in the subject is
the producer/source — **not** an aggregate identifier. No `HUDHUD_LEGACY_BRIDGE` domain stream.

---

## Deferred and rejected contracts (future candidates)

### Deferred (documented, not accepted first-publish)

| Candidate | Reason |
|-----------|--------|
| `shipment.fact.lifecycle_changed`, `shipment.fact.delivered` | Canonical Shipment authority — post-cutover native outbox |
| `delivery.fact.task_completed`, `delivery.fact.task_failed`, `DeliveryCompleted` | Delivery service cutover |
| `delivery.fact.cod_collected`, `CodCollected` | ADR-0005 policy-blocked |
| `notification.projection.delivery_requested` | Notification-native — not Bridge first set |
| `media_proof.observation.evidence_registered` | Ownership unresolved |
| `pickup.fact.accepted` (C10) | **W17-A:** lifted to `implementation_authorized_not_production_enabled` — not Wave 1 Bridge; contract/adapters next coding wave; production gated (see C10 below) |
| Hub/Linehaul facts (C11–C13) | Not Wave 1 bridge scope; remain deferred |
| `delivery.command.complete` | ADR-0004 identity gate |
| Finance/Wallet facts (C14–C15) | ADR-0005 policy-blocked |
| Bridge wallet/COD row observations | Finance policy-blocked |

### Rejected

| Candidate | Reason |
|-----------|--------|
| `audit.fact.entry_recorded` from Bridge | Audit-owned canonical fact — not transitional observation |
| CDC row → canonical lifecycle without Shipment apply | Violates sole-writer invariant |
| `NotificationEventKey` as `event_type` | Unversioned catalog keys |
| `tracking.projection.timeline_row` | Internal projection |
| `DeliveryCompleted.cod_collected` as finance substitute | COD remains separate `CodCollected` fact (ADR-0003/0005) |
| Delivery→Wallet path | Forbidden (ADR-0005) |

---

## Candidate evaluation matrix (historical analysis)

Scores: **Accept** = recommend for minimal Wave 1 set; **Defer** = define but block publish/consume; **Reject** = do not define now.

| # | Proposed `event_type` | `event_version` | Subject (S2) | `message_kind` | Producer / authority | Aggregate scope | `aggregate_version` | Intended consumers | Source evidence | Verdict |
|---|----------------------|-----------------|--------------|----------------|---------------------|-----------------|---------------------|-------------------|-----------------|---------|
| C1 | `legacy_bridge.observation.shipment_timeline_entry` | 1 | `hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1` (accepted non-aggregate; not `hudhud.legacy_bridge.…`) | `integration` | `legacy_bridge` / legacy row | `non_aggregate` (shipment_id correlation only) | Forbidden — do not invent | Tracking, Control Tower, Notification (interim) | `shipment_events` @ legacy SHA | **Accept** (transitional) |
| C2 | `shipment.fact.lifecycle_changed` | 1 | `hudhud.shipment.shipment.fact.lifecycle_changed.v1` | `integration` | `shipment` / canonical writer | `shipment` | **Required** monotonic | Tracking, Control Tower, Notification | ADR-0003, `service-boundaries.yaml` | **Accept** (target; defer native publish) |
| C3 | `shipment.fact.delivered` | 1 | `hudhud.shipment.shipment.fact.delivered.v1` | `integration` | `shipment` | `shipment` | **Required** | Tracking, Notification; Finance (**blocked**) | ADR-0003 command/fact matrix | **Accept** (target; defer Finance consume) |
| C4 | `delivery.fact.task_completed` | 1 | `hudhud.delivery.delivery.fact.task_completed.v1` | `integration` | `delivery` / operational | `shipment` | **Required** per task outcome | Shipment (inbox), Tracking (enrichment) | `complete_delivery_task.py`, ADR-0003 | **Accept** (defer native publish) |
| C5 | `delivery.fact.task_failed` | 1 | `hudhud.delivery.delivery.fact.task_failed.v1` | `integration` | `delivery` | `shipment` | **Required** | Shipment, Tracking | `fail_delivery_task.py` | **Accept** (defer native publish) |
| C6 | `delivery.fact.cod_collected` | 1 | `hudhud.delivery.delivery.fact.cod_collected.v1` | `integration` | `delivery` | `shipment` | **Required** | Finance (**blocked**), Audit | `delivery_cod_collections`, ADR-0005 | **Defer** |
| C7 | `audit.fact.entry_recorded` | 1 | `hudhud.audit.audit.fact.entry_recorded.v1` | `integration` | `audit` (native only) | `non_aggregate` or entity-scoped | N/A (non-aggregate) | Audit service | `audit_logs` | **Defer** — use A2 observation from Bridge |
| C8 | `notification.projection.delivery_requested` | 1 | `hudhud.notification.notification.projection.delivery_requested.v1` | `projection` | `notification` | `non_aggregate` | N/A | Notification internal workers | Derived from C1/C2; legacy catalog mapping | **Accept** |
| C9 | `media_proof.observation.evidence_registered` | 1 | `hudhud.media_proof.media_proof.observation.evidence_registered.v1` | `integration` | `media_proof` or `legacy_bridge` | Context-specific (`shipment`, `pickup_task`, …) | Optional | Control Tower, Tracking, Notification | Evidence tables / MinIO keys | **Accept** (draft; defer impl.) |
| C10 | `pickup.fact.accepted` | 1 | `hudhud.pickup.pickup.fact.accepted.v1` | `integration` | `pickup` | `pickup_task` | **Required** — PickupTask-owned monotonic (not Shipment) | Shipment | `acceptance_scan_pickup_task.py`, boundaries YAML, ADR-0003 W17-A | **`implementation_authorized_not_production_enabled`** |
| C11 | `hub.fact.inbound` | 1 | `hudhud.hub.hub.fact.inbound.v1` | `integration` | `hub` | `shipment` | **Required** | Shipment | `origin_hub_inbound_scan.py` | **Defer** |
| C12 | `linehaul.fact.dispatched` | 1 | `hudhud.linehaul.linehaul.fact.dispatched.v1` | `integration` | `linehaul` | `shipment` | **Required** | Shipment | `dispatch_linehaul_trip.py` | **Defer** |
| C13 | `linehaul.fact.arrived` | 1 | `hudhud.linehaul.linehaul.fact.arrived.v1` | `integration` | `linehaul` | `shipment` | **Required** | Shipment | `arrive_linehaul_trip.py` | **Defer** |
| C14 | `finance.fact.posting_completed` | 1 | `hudhud.finance.finance.fact.posting_completed.v1` | `integration` | `finance` | `shipment` or journal entry | **Required** | `wallet_cod` | ADR-0005 | **Reject** (policy-blocked) |
| C15 | `wallet.fact.balance_updated` | 1 | `hudhud.wallet.wallet.fact.balance_updated.v1` | `integration` | `wallet_cod` | `merchant_wallet` | Projection seq. | Merchant read models | ADR-0005 | **Reject** (policy-blocked) |
| C16 | `tracking.projection.timeline_row` | 1 | `hudhud.tracking.tracking.projection.timeline_row.v1` | `projection` | `tracking` | `shipment` | Monotonic per projection | Customer API cache | Envelope test fixture | **Reject** — internal projection; not cross-service contract |
| C17 | `delivery.command.complete` | 1 | `hudhud.delivery.delivery.command.complete.v1` | `command` | `delivery` / Gateway | `shipment` | **Required** expected version | Shipment | Envelope test fixture, ADR-0003 | **Defer** — Wave 2+ write path |
| C18 | Bridge wallet ledger observation | — | — | — | — | — | — | — | `wallet_ledger_entries` | **Reject** — finance policy-blocked |
| C19 | Reuse `NotificationEventKey` as `event_type` | — | — | — | — | — | — | — | `event_catalog.py` | **Reject** — catalog keys are not versioned integration contracts |

---

## Decision

**[decision]** Accept **A1** and **A2** only as the minimal Wave 1 Bridge publish set
(see Accepted minimal first contract set). **A1/A2 migration-observation authority
is unchanged by W17-A.**

**[decision]** Defer all canonical lifecycle, finance, notification projection, media/proof,
and operational facts (except C10 status below) until respective service authority and
ADR gates clear.

**[decision — W17-A]** Lift **C10** (`pickup.fact.accepted`) only to
**`implementation_authorized_not_production_enabled`**:

| Meaning | Bound |
|---------|-------|
| Allowed next coding wave | Contract registration under `contracts/events/` and Pickup/Shipment adapter implementation (outbox publish / inbox consume) |
| Still gated for staging/production | Outbox/inbox tests, topology, credentials, and runtime evidence |
| Unchanged | A1/A2 observation authority; C11–C19 and other deferred/rejected rows |

**[decision]** Reject Bridge emission of canonical Audit/Shipment facts and finance paths.

**Status: Accepted** — minimal observation set only (A1/A2). C10 is not an accepted
Bridge first-publish contract; it is implementation-authorized for the next coding
wave without production enablement. JSON Schemas and production publishers for A1/A2
remain implementation work for their own gates. Accepted ≠ implementation-complete /
production-enabled.

---

## Draft payload field definitions (accepted observations)

### A1 — `legacy_bridge.observation.shipment_timeline_entry` v1

**Envelope:** `message_kind=integration`, `producer=legacy_bridge`, `aggregate_scope=non_aggregate`,
`aggregate_type=shipment`, `aggregate_id={shipment_id}` (correlation only — **no** `aggregate_version`),
`data_classification=internal`, `pii_present=false` (default; set true if metadata contains address/phone).

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `source_table` | string | **yes** | internal | e.g. `shipment_events` |
| `source_pk` | UUID string | **yes** | internal | Legacy row id |
| `source_position` | string | **yes** | internal | LSN or `{occurred_at}|{source_pk}` — distinct from `event_id` |
| `source_module` | string | **yes** | internal | Legacy module that appended row |
| `legacy_event_type` | string | **yes** | internal | e.g. `SHIPMENT_DELIVERED`, `PICKUP_ACCEPTANCE_SCAN` |
| `occurred_at` | RFC 3339 | **yes** | internal | From legacy row |
| `old_status` | string \| null | no | internal | Legacy enum string |
| `new_status` | string \| null | no | internal | Legacy enum string |
| `shipment_id` | UUID string | **yes** | internal | Aggregate id |
| `actor_type` | string | no | confidential | From legacy metadata |
| `actor_id` | UUID string | no | confidential | |
| `metadata` | object | no | internal | Sanitized subset of `metadata_jsonb` — no secrets |
| `bridge_mapper_version` | string | **yes** | internal | Bridge mapping code version |

**Media references:** Optional `media_refs` at envelope level when `metadata` contains evidence pointers — URIs only.

**Idempotency:** `event_id = UUIDv5(event-type-specific stable namespace, "{source_system}:{source_table}:{source_pk}")`.
`source_op`, LSN, timestamp, and capture mechanism are not part of identity.

**Ordering:** Per-`shipment_id` by `occurred_at` + `source_pk`; consumers MUST tolerate out-of-order delivery. CDC WAL order ≠ canonical aggregate versioning.

---

### A2 — `legacy_bridge.observation.audit_entry` v1

**Envelope:** `message_kind=integration`, `producer=legacy_bridge`, `aggregate_scope=non_aggregate`,
`data_classification=internal`.

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `source_table` | string | **yes** | internal | e.g. `audit_logs` |
| `source_pk` | UUID string | **yes** | internal | Legacy row id |
| `source_position` | string | **yes** | internal | LSN or `{created_at}|{source_pk}` |
| `source_module` | string | **yes** | internal | Legacy module that appended row |
| `audit_entry_id` | UUID string | **yes** | internal | Same as `source_pk` |
| `action` | string | **yes** | internal | e.g. `SHIPMENT_DELIVERED` |
| `entity_type` | string | **yes** | internal | |
| `entity_id` | UUID string | **yes** | internal | |
| `actor_type` | string | **yes** | internal | |
| `actor_id` | UUID string | no | confidential | |
| `source` | string | **yes** | internal | Legacy `source` column |
| `occurred_at` | RFC 3339 | **yes** | internal | `created_at` from legacy row |
| `metadata` | object | no | confidential | Sanitized — no secrets |
| `bridge_mapper_version` | string | **yes** | internal | Bridge mapping code version |

**Idempotency:** same A1 UUIDv5 formula (`"{source_system}:{source_table}:{source_pk}"` only).

**[decision boundary]** This is **not** `audit.fact.entry_recorded` — native Audit fact deferred.

---

### Deferred draft payloads (not accepted first-publish)

#### C2 — `shipment.fact.lifecycle_changed` v1 (deferred)

**Envelope:** `message_kind=integration`, `producer=shipment`, `aggregate_version` **required**.

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `shipment_id` | UUID string | **yes** | internal | Same as `aggregate_id` |
| `previous_status` | string | **yes** | internal | Canonical enum |
| `new_status` | string | **yes** | internal | Canonical enum |
| `transition_source` | string | **yes** | internal | `pickup_fact`, `delivery_fact`, `ops_command`, … |
| `source_event_id` | UUID string | no | internal | Causation link to operational fact |
| `custody_type` | string | no | internal | Canonical custody pointer |
| `custody_id` | UUID string | no | internal | |
| `terminal` | boolean | **yes** | internal | True for DELIVERED / FAILED / CANCELLED |

**Idempotency:** Inbox on `(consumer_name, event_id)`; Shipment outbox generates fresh `event_id` per applied transition.

**Ordering:** Strict per-shipment via `aggregate_version`.

---

### C3 — `shipment.fact.delivered` v1

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `shipment_id` | UUID string | **yes** | internal | |
| `delivered_at` | RFC 3339 | **yes** | internal | Canonical timestamp |
| `delivery_mode` | string | **yes** | internal | `driver_task`, `ops_override` |
| `delivery_task_id` | UUID string | no | internal | When driver path |
| `ops_override_reason` | string | no | confidential | Ops path only |
| `payment_required` | boolean | **yes** | internal | |
| `cod_amount` | string (decimal) | no | confidential | Amount only — no card data |
| `cod_currency` | string | no | internal | e.g. `IQD` |

**Finance consumption:** Blocked until ADR-0005 acceptance.

---

### C4 — `delivery.fact.task_completed` v1 (`DeliveryCompleted`)

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `shipment_id` | UUID string | **yes** | internal | |
| `delivery_task_id` | UUID string | **yes** | internal | |
| `completed_at` | RFC 3339 | **yes** | internal | |
| `receiver_verification_method` | string | **yes** | internal | e.g. `otp`, `signature` |
| `cod_collected` | boolean | **yes** | internal | Physical flag — not finance posting |
| `expected_cod_amount` | string | no | confidential | |
| `collected_cod_amount` | string | no | confidential | |
| `cod_currency` | string | no | internal | |
| `evidence_ref_ids` | array of UUID | no | internal | Pointers to C9 observations |

**Distinct from C3:** Operational immutable fact; Shipment applies lifecycle.

---

### C6 — `delivery.fact.cod_collected` v1 (`CodCollected`) — DEFERRED

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `shipment_id` | UUID string | **yes** | confidential | |
| `collection_id` | UUID string | **yes** | internal | Delivery-owned row id |
| `collected_amount` | string | **yes** | confidential | |
| `expected_amount` | string | **yes** | confidential | |
| `currency` | string | **yes** | internal | |
| `collected_at` | RFC 3339 | **yes** | internal | |
| `driver_id` | UUID string | no | confidential | |

**Status:** Draft only — **do not register or publish** until ADR-0005 permits.

---

### C7 — `audit.fact.entry_recorded` v1

**Envelope:** `aggregate_scope=non_aggregate` **or** entity-scoped with `aggregate_type=entity_type`, `aggregate_id=entity_id`.

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `audit_entry_id` | UUID string | **yes** | internal | |
| `action` | string | **yes** | internal | e.g. `SHIPMENT_DELIVERED` |
| `entity_type` | string | **yes** | internal | |
| `entity_id` | UUID string | **yes** | internal | |
| `actor_type` | string | **yes** | internal | |
| `actor_id` | UUID string | no | confidential | |
| `source` | string | **yes** | internal | Legacy `source` column |
| `occurred_at` | RFC 3339 | **yes** | internal | |
| `metadata` | object | no | confidential | Redact tokens/secrets at bridge |
| `source_table` | string | no | internal | When `producer=legacy_bridge` |
| `source_pk` | UUID string | no | internal | Bridge provenance |

**PII:** `ip_address`, `user_agent` — if included, set `pii_present=true`, `data_classification=confidential`.

---

### C8 — `notification.projection.delivery_requested` v1

**Envelope:** `message_kind=projection`, `aggregate_scope=non_aggregate`.

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `notification_key` | string | **yes** | internal | Maps from legacy catalog e.g. `shipment.delivered` |
| `entity_type` | string | **yes** | internal | e.g. `shipment` |
| `entity_id` | UUID string | **yes** | internal | |
| `recipient_scope` | string | **yes** | internal | `customer`, `merchant`, `driver` |
| `recipient_id` | UUID string | **yes** | confidential | |
| `dedupe_key` | string | **yes** | internal | Stable idempotency — legacy template pattern |
| `locale` | string | no | internal | BCP 47 |
| `trigger_event_id` | UUID string | **yes** | internal | Upstream fact/observation |
| `trigger_event_type` | string | **yes** | internal | e.g. C1 or C2 |
| `payload_hints` | object | no | confidential | Title/body template variables — no secrets |

---

### C9 — `media_proof.observation.evidence_registered` v1

| Field | Type | Required | Classification | Notes |
|-------|------|----------|----------------|-------|
| `evidence_id` | UUID string | **yes** | internal | |
| `context_type` | string | **yes** | internal | `shipment`, `pickup_task`, `delivery_task` |
| `context_id` | UUID string | **yes** | internal | |
| `evidence_type` | string | **yes** | internal | e.g. `photo`, `signature` |
| `storage_ref` | object | **yes** | internal | `{ "ref_type": "s3", "bucket": "...", "key": "..." }` |
| `content_type` | string | no | internal | |
| `captured_at` | RFC 3339 | **yes** | internal | |
| `source_module` | string | **yes** | internal | Legacy origin |
| `source_table` | string | no | internal | Bridge provenance |
| `source_pk` | UUID string | no | internal | |

**Envelope `media_refs`:** SHOULD mirror `storage_ref` for ADR-0002 compliance.

---

### C10 — `pickup.fact.accepted` v1 — W17-A reconciliation

**Status:** `implementation_authorized_not_production_enabled` (not Bridge first-publish;
not production-enabled). Schema is registered under
`contracts/events/pickup.fact.accepted/`. Publication remains gated.

**Envelope:** `message_kind=integration`, `producer=pickup`,
`aggregate_type=pickup_task`, `aggregate_id={pickup_task_id}`,
`aggregate_version` = PickupTask-owned monotonic version (**required**).

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `pickup_task_id` | UUID string | **yes** | Same as envelope `aggregate_id` |
| `shipment_id` | UUID string | **yes** | Payload correlation only — not Shipment aggregate authority |
| `outcome` | string | **yes** | `ACCEPTED` or `ACCEPTED_WITH_EXCEPTION` only |
| `accepted_at` | RFC 3339 | **yes** | Operational acceptance timestamp from Pickup; Shipment sets `accepted_at` and `sla_started_at` |
| `assigned_driver_user_id` | string | **yes** | PickupTask assigned driver → Shipment `current_custody_id`. Never producer, `event_id`, or a placeholder |
| `acting_driver_user_id` | string | **yes** | Pickup acceptance actor → audit `actor_id` and `AcceptanceDecisionRecord`. MUST equal `assigned_driver_user_id` |
| `scanned_identifier` | string | **yes** | Identifier actually scanned; Shipment verifies against its `waybill_identity` and persists on the decision record |

**[decision — W17 contract applicability]** The four identity/outcome/timestamp fields
alone are not sufficient for Shipment apply. The three additional payload fields above
are the minimum already required by committed Shipment acceptance persistence and
available from the Pickup acceptance operation. Envelope `media_refs` is required
when `outcome=ACCEPTED_WITH_EXCEPTION`. Evidence stays out of the payload. Custody
type is `PICKUP_DRIVER` from this event's semantics, not a payload field. Shipment
MUST NOT read Pickup storage or treat compatibility `PickupTaskSnapshot` as
production authority.

**Producer / consumer:** Pickup publishes; Shipment consumes and applies canonical
`CREATED` → `IN_CUSTODY` with custody terminology `PICKUP_DRIVER` (ADR-0003 W17-A).

**Outcomes:** Custody-starting success only. `REJECTED` does not emit this event and
remains Pickup-local — do not invent a rejection fact here.

**Aggregate authority:** Pickup must not claim or generate a Shipment
`aggregate_version`. Shipment increments its own version when applying the fact.

**Production path:** Pickup outbox → JetStream → Shipment inbox → Shipment apply →
ACK. W16 Shipment HTTP acceptance remains compatibility/internal only; must not run
as a second independent production writer alongside native consumption.

**Explicit non-actions:** Do not enable staging/production publish/consume without
outbox/inbox tests, topology, credentials, and runtime evidence. Do not add
speculative payload fields (hub, courier ceremony, notification, COD, finance,
policy warnings, raw metadata, rejected-event information, unused packaging/weight
VOs).

---

## Compatibility and evolution rules

**[proposal]** Aligned with ADR-0002:

| Rule | Policy |
|------|--------|
| Add optional payload field | Backward compatible within same `event_version` |
| Remove/rename required field | Increment `event_version`; dual-subscribe ≥ 90 days |
| Unknown fields | Consumers MUST ignore (tolerant reader) |
| Envelope vs payload | Identity fields live in envelope only — not duplicated in payload |
| Bridge → native cutover | Consumers dual-subscribe C1 and C2 during migration; retire C1 per stream |
| Contract registration | JSON Schema under `contracts/events/{event_type}/v{event_version}.json` before first native publish |
| CI enforcement | Compatibility tests required when schemas exist — **not in this ADR scope** |

---

## Source-provenance rules

**[proposal]**

1. Every bridge-generated event MUST include `metadata.source_table`, `metadata.source_pk`, `metadata.source_position`, and `metadata.source_module`.
2. `producer=legacy_bridge` MUST NOT imply canonical authority — consumers treat as observation.
3. Canonical events MUST have `producer` equal to the owning deployable id from `service-boundaries.yaml`.
4. Replays MUST set `metadata.replay=true` and `metadata.replay_source`.
5. Notification projections MUST cite `trigger_event_id` + `trigger_event_type` for traceability.
6. Rows from `wallet_ledger_entries` or `delivery_cod_collections` MUST NOT be promoted to finance contracts without ADR-0005.

---

## Redaction and PII rules

**[proposal]**

| Data | Allowed in payload? | Classification | Logging |
|------|---------------------|----------------|---------|
| Phone, address, recipient name | Notification C8 hints only | `confidential` | Never at INFO |
| OTP codes | **Forbidden** | — | — |
| JWT, API keys, push tokens | **Forbidden** | — | — |
| Evidence bytes | **Forbidden** — URI in C9 only | `internal` | — |
| COD amounts | C4/C6/C3 fields | `confidential` | Redact in metrics |
| IP / user agent | C7 optional | `confidential` | Hash or omit at INFO |
| National ID | **Forbidden** | — | — |

Envelope `pii_present=true` triggers consumer log redaction pipelines.

---

## Schema ownership

| Contract group | Owning bounded context | Schema path (future) |
|----------------|------------------------|----------------------|
| C1 bridge observations | Transitional technical deployable (Legacy Event Bridge) — not a bounded context | `contracts/events/legacy_bridge.observation.shipment_timeline_entry/v1.json` |
| C2, C3 shipment facts | `shipment` | `contracts/events/shipment.fact.*/v1.json` |
| C4, C5, C6 delivery facts | `delivery` | `contracts/events/delivery.fact.*/v1.json` |
| C7 audit facts | `audit` | `contracts/events/audit.fact.entry_recorded/v1.json` |
| C8 notification projections | `notification` | `contracts/events/notification.projection.delivery_requested/v1.json` |
| C9 media observations | `media_proof` (**owner unresolved**) | `contracts/events/media_proof.observation.evidence_registered/v1.json` |

**[decision boundary]** Shared envelope schema remains in `contracts/events/envelope/` (`packages/event_envelope/`). Domain payloads are owned by the publishing context — not `packages/`.

---

## Implementation prerequisites

| # | Prerequisite | Status |
|---|--------------|--------|
| P1 | ADR-0007 bridge strategy accepted or explicitly piloted | Proposed |
| P2 | Bridge cursor monotonicity proof (E2) | Not evidenced |
| P3 | Zero-gap drill (ADR-0006 E3/E7) | Not evidenced |
| P4 | JSON Schema files for accepted contracts | **Out of scope** (this ADR) |
| P5 | Consumer inbox tables per service | Not implemented |
| P6 | NATS stream `HUDHUD_AUDIT`, `HUDHUD_NOTIFICATION` placement | Provisional (ADR-0002) |
| P7 | Media/Proof ownership decision | **Unresolved** |
| P8 | ADR-0005 unblock for C6 / Finance paths | Policy-blocked |
| P9 | Identity/service trust for command contracts (C17) | ADR-0004 Proposed |
| P10 | Notification catalog parity for non-shipment keys | **Unresolved** (ADR-0007 Q4) |

---

## Options

| Option | Summary | Trade-offs |
|--------|---------|------------|
| O1 — Bridge observations only for Wave 1 | Tracking/Notification consume C1 only until Shipment cutover | Lowest authority risk; semantic gap vs target |
| O2 — Define target canonical contracts now, bridge maps toward them | C1 carries mapping hints to C2/C3 shapes | Cleaner consumer migration; mapping maintenance |
| O3 — Broad operational fact set (pickup/hub/linehaul/delivery) | Full ADR-0003 matrix in Wave 1 | Scope creep; Shipment not extracting |
| O4 — Finance/wallet contracts included | Unblock merchant projections early | **Violates** ADR-0005 policy block |

**[proposal]** Adopt **O2** for documentation; implement **O1** for Wave 1 publish scope.

---

## Decision drivers

1. **[evidence]** Sole Shipment lifecycle writer — observations ≠ canonical events.
2. **[evidence]** Finance policy-blocked — no COD/wallet contracts.
3. **[evidence]** Legacy multi-writer — bridge observations must not pretend canonical authority.
4. **[proposal]** Low-risk Wave 1 consumers need read projections only.
5. **[proposal]** Envelope package and S2 grammar already implemented — contracts must align.
6. **[unresolved policy]** Media/Proof ownership blocks C9 implementation.

---

## Decision (summary)

See **Accepted minimal first contract set** above. Status **Accepted** — two
observations only (A1/A2). **W17-A:** C10 lifted only to
`implementation_authorized_not_production_enabled`; A1/A2 unchanged.

---

## Decision (superseded section removed)

**[decision]** Historical proposal sections below retained for traceability only.

---

## Consequences

### Positive

- Clear separation of bridge observations vs canonical Shipment facts.
- Wave 1 consumers can implement inbox/projections against stable versioned names.
- Finance boundary explicitly guarded.
- Aligns with implemented envelope validation, aggregate S2, and non-aggregate subject grammar.

### Negative

- Dual-subscribe period during bridge retirement adds consumer complexity.
- C1 semantic gap vs C2 requires mapping layer in Tracking/Notification.
- Full JSON Schema and CI gates remain future work.

### Neutral

- Operational facts Hub/Linehaul (C11–C13) documented but deferred — no Wave 1 delay.
- C10 (`pickup.fact.accepted`) is implementation-authorized (not production-enabled) per W17-A.
- COD payload drafted but blocked — avoids rework when ADR-0005 resolves.

---

## Migration impact

- **Wave 1:** Consumers deploy with inbox; subscribe to bridge subjects; backfill from legacy snapshot ≤ HWM (ADR-0006).
- **Cutover:** Enable native C2/C3 from Shipment outbox; retire C1 per stream at stage 13.
- **Notification:** Replace synchronous `emit_shipment_status_notification` with C8-driven async dispatch.
- **Bidirectional dual-write:** **Forbidden.**

---

## Observability

**[proposal]**

| Signal | Purpose |
|--------|---------|
| `contract_publish_total{event_type,event_version,producer}` | Contract usage |
| `bridge_mapping_total{legacy_event_type,target_event_type}` | C1 → C2 drift detection |
| `consumer_projection_lag_seconds{consumer,event_type}` | SLO tracking |
| `inbox_duplicate_total{consumer_name,event_type}` | Idempotency health |

Logs: envelope `safe_log_fields()` only — no raw confidential payload.

---

## Security

- Bridge credentials read-only on allowlisted tables (ADR-0007).
- `data_classification` and `pii_present` mandatory on every contract.
- No Delivery→Wallet publish path.
- Notification C8 MUST NOT embed push tokens in JetStream payload.
- Audit C7 metadata sanitized at bridge — no secrets from legacy JSONB.

---

## Rollback

| Scenario | Action |
|----------|--------|
| Wrong contract published | Pin `event_version`; halt producer bump |
| Bridge mapping error | Pause bridge; fix mapper; replay with `metadata.replay=true` |
| Consumer projection corrupt | Rebuild from inbox + deterministic `event_id` |
| Irreversible delivery | Forward reconciliation only — no rollback of C4/C3 |

---

## Unresolved questions

1. **[unresolved policy]** Media/Proof canonical owner — who publishes C9 natively?
2. **[unresolved policy]** Notification parity for pickup-scheduling / merchant-application catalog keys without additional bridge cursors?
3. **[unresolved policy]** Should C1 include full `metadata_jsonb` or a strict allowlist?
4. **[unresolved policy]** Numeric bridge lag SLO for Wave 1 consumer freshness?
5. **[unresolved policy]** Acceptable dual-subscribe window length for C1 → C2 migration?
6. **[unresolved policy]** Is `aggregate_version` on bridge observations always absent vs optional legacy sequence?
7. **[assumption]** UUIDv5 namespace for bridge `event_id` — assign at implementation (ADR-0007 Q8)?
8. **[unresolved policy]** Control Tower: remain legacy read API vs event-only projection for Wave 1?

---

## Alternatives considered

| Alternative | Why rejected or deferred |
|-------------|-------------------------|
| Single generic `legacy.row_observed` payload | Loses type safety; forbidden unversioned generic pattern |
| Skip C1; wait for Shipment cutover | Blocks ADR-0001 Wave 1 consumer extraction |
| Include C6 in minimal set | ADR-0005 policy-blocked |
| `tracking.projection.timeline_row` as public contract | Internal projection — not cross-service |
| CloudEvents-only without HUDHUD payload schema | ADR-0002 chose native envelope + versioned payloads |

---

## Explicit non-goals

- Creating `contracts/events/*/v1.json` JSON Schema files
- Updating `docs/adr/README.md` index
- Modifying `architecture/service-boundaries.yaml` or `ownership-matrix.yaml`
- Implementing bridge, consumers, or NATS configuration
- Resolving ADR-0005 finance policy
- Mutating legacy repository

---

## References

- ADR-0001 @ `docs/adr/0001-transitional-deployables-and-extraction-order.md`
- ADR-0002 @ `docs/adr/0002-event-envelope-outbox-inbox-and-jetstream.md`
- ADR-0003 @ `docs/adr/0003-shipment-lifecycle-authority-and-delivery-facts.md`
- ADR-0005 @ `docs/adr/0005-cod-wallet-ledger-and-settlement.md`
- ADR-0007 @ `docs/adr/0007-legacy-event-bridge-strategy.md`
- Platform: `architecture/invariants.md`, `architecture/service-boundaries.yaml`, `architecture/ownership-matrix.yaml`
- Envelope implementation: `packages/event_envelope/`, `infra/eventing/subject-grammar.md`
- Audits: `docs/audit/legacy-baseline.md`, `legacy-data-ownership-inventory.md`, `legacy-domain-inventory.md`
- Legacy evidence SHA: `2e375057fdf9b9ce8416408a4436303be5301def`
- Legacy files (read-only): `shipment/domain/enums.py`, `shipment/infrastructure/models.py`, `audit/infrastructure/models.py`, `notification/domain/event_catalog.py`, `delivery_task/application/complete_delivery_task.py`

---

```text
ADR path: docs/adr/0009-initial-integration-event-contracts.md
Status: Accepted — minimal observation set only (A1/A2); C10 implementation_authorized_not_production_enabled (W17-A)
Deciders: platform architecture review (Wave 3 capture integration); W17-A acceptance boundary reconciliation
Canonical docs updated: service-boundaries.yaml, ownership-matrix.yaml, docs/adr/README.md (prior waves); ADR-0003 W17-A cross-links
Unresolved questions: 8 (see section above)
Implementation allowed: A1/A2 schemas/publishers gated; C10 contract+adapters authorized next coding wave — not production-enabled
```
