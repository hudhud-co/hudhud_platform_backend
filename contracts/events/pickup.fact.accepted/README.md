# pickup.fact.accepted v1 (ADR-0009 C10)

Pickup-owned **custody-starting** acceptance fact. Shipment consumes this fact and
applies the canonical `CREATED` → `IN_CUSTODY` transition (custody terminology
`PICKUP_DRIVER` per ADR-0003 W17-A).

**Status:** `implementation_authorized_not_production_enabled` — contract
registration only. Outbox publish, inbox consume, topology credentials, and
runtime evidence remain gated. Registration does **not** enable production
publication.

## Schema identifiers

| Artifact | `$id` |
|----------|-------|
| Full message | `https://hudhud.platform/contracts/events/pickup.fact.accepted/v1.schema.json` |
| Payload only | `https://hudhud.platform/contracts/events/pickup.fact.accepted/v1.payload.schema.json` |

## Ownership

| Role | Value |
|------|-------|
| Producer | `pickup` |
| Intended consumer | `shipment` (inbox → acceptance apply) |
| Owning schema path | `contracts/events/pickup.fact.accepted/` |
| Canonical lifecycle authority | **Shipment** — this fact does not mutate Shipment storage by itself |

## JetStream routing

| Field | Value |
|-------|-------|
| Subject | `hudhud.pickup.pickup.fact.accepted.v1` |
| Stream | `HUDHUD_PICKUP` |
| Envelope `message_kind` | `integration` (semantic class `fact` lives in `event_type`) |
| `aggregate_scope` | `aggregate` |

## Aggregate authority

| Envelope field | Value |
|----------------|-------|
| `aggregate_type` | `pickup_task` |
| `aggregate_id` | PickupTask id (`pickup_task_id`) |
| `aggregate_version` | PickupTask-owned monotonic version (**required**) |

`payload.shipment_id` is correlation only. Pickup MUST NOT claim or generate a
Shipment `aggregate_version`.

`payload.pickup_task_id` MUST equal envelope `aggregate_id`.

## Stable event identity

`event_id` is assigned once when Pickup writes the transactional outbox row for
this acceptance fact and MUST be reused unchanged on outbox relay retries
(`Nats-Msg-Id` = `event_id` per ADR-0002).

| Concern | Policy |
|---------|--------|
| Idempotency key | `event_id` |
| Outbox retries | Same `event_id` — no new identity per publish attempt |
| Consumer inbox | Durable dedupe on `(consumer_name, event_id)` |
| Ordering | Per `pickup_task` via `aggregate_version` |

Exactly-once delivery is not claimed.

## Payload fields (v1)

Minimum set Shipment needs to apply custody-starting acceptance **without**
reading Pickup storage, inventing operator/custody identities, or treating
compatibility `PickupTaskSnapshot` as a permanent cross-context authority:

| Field | Required | Notes |
|-------|----------|-------|
| `pickup_task_id` | yes | Same as envelope `aggregate_id` |
| `shipment_id` | yes | Correlation only |
| `outcome` | yes | `ACCEPTED` or `ACCEPTED_WITH_EXCEPTION` only |
| `accepted_at` | yes | Operational acceptance timestamp from Pickup; Shipment sets `accepted_at` and `sla_started_at` |
| `assigned_driver_user_id` | yes | PickupTask assigned driver → Shipment `current_custody_id`. Never producer, `event_id`, or a placeholder |
| `acting_driver_user_id` | yes | Authorized Pickup acceptance actor → audit `actor_id` and `AcceptanceDecisionRecord.acting_driver_user_id`. MUST equal `assigned_driver_user_id` |
| `scanned_identifier` | yes | Identifier actually scanned; Shipment verifies against its `waybill_identity` and persists on the decision record |

**Custody type:** `PICKUP_DRIVER` is this event's semantics (ADR-0003 W17-A), not a
payload field. Shipment sets `current_custody_type` from the event type.

**Outcomes:** Custody-starting success only. `REJECTED` does **not** produce this
fact and remains Pickup-local — no rejection event is introduced by this contract.

**Evidence:** External evidence MUST remain references only (envelope `media_refs`).
`ACCEPTED_WITH_EXCEPTION` requires at least one `media_refs` entry. Do not embed
inline evidence, `exception_evidence`, credentials, raw CDC tuples, or arbitrary
metadata blobs in the payload.

**Not in this contract (not required to apply; not Pickup-persisted at acceptance):**
packaging/seal assessment, approximate weight/dimensions, hub, courier ceremony,
notification, COD, finance, policy warnings, raw metadata, rejected-event
information. Pickup-side publication gates (`PROOF_CAPTURED`, assigned batch,
condition-proof existence) stay in Pickup — Shipment does not re-read them.

Forbidden in payload: raw CDC fields (`lsn`, `xid`, `source_op`, `before`,
`after`, …), secrets/tokens, inline evidence, `shipment_aggregate_version`.

## Compatibility policy

| Change | Policy |
|--------|--------|
| Add optional payload field | Backward compatible within v1 |
| Remove/rename required payload field | Increment `event_version`; dual-subscribe |
| Unknown payload fields at publish | **Rejected** (`additionalProperties: false`) |
| Unknown payload fields at consume | **Ignored** (tolerant reader per ADR-0002) |
| Envelope evolution | Follow `contracts/events/envelope/README.md` |

## Delivery semantics

At-least-once via NATS JetStream. Pickup uses transactional outbox (ADR-0008);
Shipment uses durable inbox before apply. Production path:

```text
Pickup outbox → JetStream → Shipment inbox → Shipment apply → ACK
```

W16 Shipment HTTP acceptance remains compatibility/internal only and MUST NOT run
as a second independent production writer alongside native consumption
(ADR-0003 W17-A). Native apply uses this fact's payload and envelope fields;
it MUST persist `AcceptanceDecisionRecord` and MUST NOT query Pickup DB or treat
`PickupTaskSnapshot` as production authority.

## Explicit non-actions

- A1/A2 observation contracts remain unchanged.
- No rejection event registration.
- No services/, infra/eventing/, architecture/, CI, or Compose changes in
  this registration wave.
- Staging/production publish/consume remain gated.

## Artifacts

| Path | Purpose |
|------|---------|
| `v1.schema.json` | Full message (envelope + payload constraints) |
| `v1.payload.schema.json` | Payload body only |
| `examples/` | Valid envelopes |
| `fixtures/` | Invalid examples for compatibility tests |

## References

- ADR-0009 C10, ADR-0003 W17-A, ADR-0002 envelope, ADR-0008 outbox/inbox
- `infra/eventing/subject-grammar.md` (aggregate S2)
- Committed domain: Pickup `PickupTask` / `PickupTaskAcceptanceState`; Shipment
  acceptance apply path
