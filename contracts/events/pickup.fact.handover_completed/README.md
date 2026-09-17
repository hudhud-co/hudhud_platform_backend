# pickup.fact.handover_completed v1 (ADR-0009 C11)

Pickup-owned **custody-releasing** hub handover fact. Shipment consumes this fact and
applies the canonical custody move `PICKUP_DRIVER` → `ORIGIN_HUB` (ADR-0003). Pickup
never writes Shipment state.

**Status:** `implementation_authorized_not_production_enabled` — contract registration
and service-owned outbox/inbox adapters only. Topology credentials and runtime cutover
gates remain closed (ADR-0010, ADR-0006).

## Schema identifiers

| Artifact | `$id` |
|----------|-------|
| Full message | `https://hudhud.platform/contracts/events/pickup.fact.handover_completed/v1.schema.json` |
| Payload only | `https://hudhud.platform/contracts/events/pickup.fact.handover_completed/v1.payload.schema.json` |

## Ownership

| Role | Value |
|------|-------|
| Producer | `pickup` |
| Intended consumer | `shipment` (inbox → custody transfer apply) |
| Owning schema path | `contracts/events/pickup.fact.handover_completed/` |
| Canonical lifecycle authority | **Shipment** — this fact does not mutate Shipment storage by itself |

## JetStream routing

| Field | Value |
|-------|-------|
| Subject | `hudhud.pickup.pickup.fact.handover_completed.v1` |
| Stream | `HUDHUD_PICKUP` |
| Envelope `message_kind` | `integration` |
| `aggregate_scope` | `aggregate` |

## Aggregate authority

| Envelope field | Value |
|----------------|-------|
| `aggregate_type` | `pickup_task` |
| `aggregate_id` | PickupTask id (`pickup_task_id`) |
| `aggregate_version` | PickupTask-owned monotonic version (**required**) |

One parcel is released to a hub exactly once, so Pickup's outbox holds at most one
`pickup.fact.handover_completed` row per `aggregate_id`. `payload.shipment_id` is
correlation only; Pickup MUST NOT claim a Shipment `aggregate_version`.

## Custody semantics

| Manifest item outcome | Emits this fact | Shipment custody after |
|---|---|---|
| `RECEIVED` | yes (`outcome: RECEIVED`) | `ORIGIN_HUB` at `receiving_hub_id` |
| `DISCREPANCY` (damaged / wrong hub / other) | yes (`outcome: RECEIVED_WITH_DISCREPANCY`) | `ORIGIN_HUB` at the **actual** `receiving_hub_id`; the dispute is preserved separately |
| `MISSING` | **no** | stays `PICKUP_DRIVER` — the driver still owes the parcel |

`MISSING_FROM_DRIVER` is therefore not a valid `discrepancy_reason` on this event: a
parcel that never reached the hub cannot release custody.

## Invariants enforced outside JSON Schema

JSON Schema cannot compare two payload fields, so the producer and the consumer both
enforce:

- `receiving_actor_id` MUST NOT equal `releasing_driver_user_id` — a driver may never
  record its own hub receipt.
- `releasing_driver_user_id` MUST equal Shipment's `current_custody_id` at apply time;
  a mismatch is a permanent contract rejection, not a retry.

## Stable event identity

`event_id` is generated once, inside the transaction that closes the manifest item, and
stored on the outbox row. Relay retries reuse it, so a redelivered message is the same
event and the consumer inbox deduplicates on `(consumer_name, event_id)`.
