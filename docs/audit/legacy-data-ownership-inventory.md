# Legacy Data Ownership Inventory

Documents who writes what in the legacy monolith's shared PostgreSQL database. This inventory informs platform extraction planning and highlights violations of approved platform invariants.

Audit source: `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` @ `2e375057fdf9b9ce8416408a4436303be5301def`.

---

## Database Topology (Legacy)

- **Single database:** One PostgreSQL 16 instance shared by all modules
- **Single migration chain:** 78 Alembic revisions, head `b8c9d0e1f2a3`
- **No per-module migration isolation:** All tables in one schema namespace

---

## Shipment Lifecycle State Writers

Platform policy: **Shipment is the sole canonical writer of shipment lifecycle state.** Legacy violates this — multiple modules mutate `shipments.status` directly.

| Status transition | Legacy writer module | File evidence |
|-------------------|---------------------|---------------|
| → CREATED | shipment | `shipment/application/create_shipment.py` |
| → IN_CUSTODY | pickup | `pickup/application/acceptance_scan_pickup_task.py` |
| → AT_ORIGIN_HUB | hub | `hub/application/origin_hub_inbound_scan.py` |
| → IN_LINEHAUL | linehaul | `linehaul/application/dispatch_linehaul_trip.py` |
| → AT_DESTINATION_HUB | linehaul | `linehaul/application/arrive_linehaul_trip.py` |
| → OUT_FOR_DELIVERY | delivery_task, shipment | `delivery_task/application/start_delivery_task.py`, `shipment/application/delivery_completion.py` |
| → DELIVERED | delivery_task, shipment | `delivery_task/application/complete_delivery_task.py`, `shipment/application/delivery_completion.py` |
| → DELIVERY_FAILED | delivery_task, shipment | `delivery_task/application/fail_delivery_task.py`, `shipment/application/delivery_completion.py` |
| → DELIVERY_CANCELLED | shipment | `shipment/application/delivery_completion.py` |

**Platform extraction implication:** Pickup, Hub, Linehaul, and Delivery must publish facts/commands; Shipment service alone applies canonical lifecycle transitions.

---

## Shipment Table Foreign Keys (Cross-Boundary)

From `app/modules/shipment/infrastructure/models.py`:

| FK column | References | Owning context (semantic) |
|-----------|------------|---------------------------|
| `order_id` | `orders.id` | Order |
| `merchant_id` | `merchants.id` | Merchant |
| `store_location_id` | `store_locations.id` | Merchant/Store |
| `origin_hub_id` | `hubs.id` | Hub |
| `destination_hub_id` | `hubs.id` | Hub |

Shipment events table FK: `shipment_id` → `shipments.id` (same module).

---

## Order Table Foreign Keys (Cross-Boundary)

From `app/modules/order/infrastructure/models.py`:

| FK column | References | Owning context |
|-----------|------------|----------------|
| `merchant_id` | `merchants.id` | Merchant |
| `receiver_contact_id` | `receiver_contacts.id` | Address Book |
| `pickup_address_id` | `pickup_addresses.id` | Address Book |
| `store_location_id` | `store_locations.id` | Merchant/Store |

---

## Wallet and COD State Writers

Platform policy: **COD collection and merchant wallet/payable recognition are separate accounting facts.**

| Fact / state | Writer module | File evidence |
|--------------|---------------|---------------|
| COD collection record | delivery_task | `delivery_task/infrastructure/models.py` → `delivery_cod_collections` |
| Wallet ledger credit | wallet | `wallet/application/credit_cod_collection.py` — idempotent key `cod_collected:{shipment_id}` |
| Orchestration (same transaction) | delivery_task | `delivery_task/application/complete_delivery_task.py` → `_persist_cod_collection_and_wallet_credit` |
| Payout hold/release | wallet | `wallet/application/record_wallet_ledger_entry.py` |
| Payout requests | wallet | `wallet/infrastructure/models.py` → `payout_requests` |

**Platform extraction implication:** Delivery records COD collection fact; Finance/Wallet recognizes payable separately; finance failures must not roll back physical delivery.

---

## Cross-Module Direct Calls (Representative)

Legacy modules invoke each other's application/infrastructure layers directly within the monolith:

| Caller | Callee | Pattern |
|--------|--------|---------|
| send_parcel | order, shipment | Creates order + shipment in confirm flow |
| pickup | shipment | Status mutation on acceptance scan |
| hub | shipment | Status mutation on inbound scan |
| linehaul | shipment | Status mutation on dispatch/arrive |
| delivery_task | shipment, wallet | Status mutation + COD/wallet credit |
| tracking | shipment | Read-only repository queries |
| control_tower | shipment, proof | Read-only aggregation |
| merchant | shipment | Store shipment preparation, warehouse assignments |

**Platform extraction implication:** Replace direct calls with HTTP commands and NATS events (at-least-once, idempotent consumers).

---

## Storage Prefix Ownership

| Prefix / bucket config | Owner module | Evidence |
|------------------------|--------------|----------|
| `pickup-evidence/{owner_type}/{owner_id}/...` | pickup | `pickup/application/evidence_validation.py` |
| `delivery-evidence/{shipment_id}/...` | shipment / delivery_task | `app/core/config.py` → `DELIVERY_EVIDENCE_STORAGE_BUCKET` |
| `support-claims/{claim_id}/...` | support | `support/domain/customer_claim_attachment_validation.py` |
| `PROOF_STORAGE_BUCKET` / `MINIO_BUCKET_PROOFS` | proof (metadata) | `app/core/config.py` |

---

## Audit Log Writers

`AuditLogRepository` written from multiple modules:

- auth (admin actions)
- wallet (ledger operations)
- delivery_task (completion events)
- merchant (store ops)

Audit is append-only cross-cutting concern — platform may remain shared read API or per-service outbox emission.

---

## Notification / Event Emission

| Emitter | Mechanism | Evidence |
|---------|-----------|----------|
| Shipment lifecycle | In-process function calls | `emit_shipment_status_notification` from pickup, hub, linehaul, delivery |
| Notification module | push_outbox table + worker | `notification/infrastructure/models.py`, `scripts/run_push_outbox_worker.py` |
| Event catalog | Domain enum | `notification/domain/event_catalog.py` |

Legacy has no NATS JetStream, no outbox/inbox per bounded context, no event envelope with correlation/causation IDs.

---

## Tables Without Clear Single Owner (Ambiguous)

| Table cluster | Modules touching | Classification |
|---------------|------------------|----------------|
| `shipments` + `shipment_events` | shipment, pickup, hub, linehaul, delivery_task | ambiguous (multi-writer) |
| `delivery_cod_collections` | delivery_task writes, wallet reads for credit | partial split |
| `proof` metadata | proof (stub API), control_tower reads | ambiguous |
| Customer identity | auth tables, no customer module | partial |

---

## Platform Invariant Gaps (Legacy vs Approved Policy)

| Invariant | Legacy state |
|-----------|--------------|
| Shipment sole lifecycle writer | **Violated** — 5 modules write status |
| No cross-service FK | **Violated** — extensive FK graph |
| No shared ORM across services | N/A (monolith) |
| Finance failures don't roll back delivery | **Partially enforced** — COD/wallet in same transaction as delivery complete |
| Double-entry ledger | **Missing** |
| At-least-once events with idempotent consumers | **Missing** — in-process calls only |
| One-writer DB cutover | **Not applicable yet** — single DB |
