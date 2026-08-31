# legacy_bridge.observation.shipment_timeline_entry v1 (ADR-0009 A1)

Transitional Legacy Event Bridge observation of one **append-only** legacy
`shipment_events` row. This is **not** a canonical Shipment lifecycle event and
MUST NOT be treated as `shipment.fact.lifecycle_changed`.

## Schema identifiers

| Artifact | `$id` |
|----------|-------|
| Full message | `https://hudhud.platform/contracts/events/legacy_bridge.observation.shipment_timeline_entry/v1.schema.json` |
| Payload only | `https://hudhud.platform/contracts/events/legacy_bridge.observation.shipment_timeline_entry/v1.payload.schema.json` |

## Ownership

| Role | Value |
|------|-------|
| Producer | `legacy_bridge` (transitional technical deployable — not a bounded context) |
| Intended consumers (Wave 1) | Tracking, Control Tower, Notification (interim) |
| Owning schema path | `contracts/events/legacy_bridge.observation.shipment_timeline_entry/` |
| Canonical lifecycle authority | **Shipment** (post-cutover) — not this contract |

## JetStream routing

| Field | Value |
|-------|-------|
| Subject | `hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1` |
| Stream | `HUDHUD_SHIPMENT` |
| `message_kind` | `integration` |
| `aggregate_scope` | `non_aggregate` (no invented `aggregate_version`) |

Correlation to a shipment uses `payload.shipment_id` only. Envelope aggregate
fields MUST remain absent/null per `contracts/events/envelope/v1.schema.json`.

## Stable event identity

```text
event_id = UUIDv5(
  namespace = 5c4b4b77-2b6b-5d2c-bcfd-efea8ce399c3,
  name      = "{source_system}:{source_table}:{source_pk}"
)
```

| Component | Value |
|-----------|-------|
| Namespace derivation | `UUIDv5(NAMESPACE_DNS, "hudhud.platform/events/legacy_bridge.observation.shipment_timeline_entry/v1")` |
| `source_system` (Bridge constant) | `legacy` |
| `source_table` | `shipment_events` (payload const) |
| `source_pk` | Legacy row UUID |

**Excluded from identity:** capture mechanism, LSN, xid, timestamp, `source_op`,
`correlation_id`, `aggregate_version`. `payload.source_position` is provenance only.

Backfill and CDC INSERT for the same append-only row MUST produce the same `event_id`.

## Payload fields (v1)

| Field | Required | Classification | Notes |
|-------|----------|----------------|-------|
| `source_table` | yes | internal | Must be `shipment_events` |
| `source_pk` | yes | internal | Legacy row id |
| `source_position` | yes | internal | LSN or `{occurred_at}\|{source_pk}` |
| `source_module` | yes | internal | Legacy module that appended the row |
| `legacy_event_type` | yes | internal | e.g. `SHIPMENT_DELIVERED` |
| `occurred_at` | yes | internal | From legacy row |
| `old_status` | no | internal | Legacy enum string |
| `new_status` | no | internal | Legacy enum string |
| `shipment_id` | yes | internal | Correlation reference (non-aggregate) |
| `actor_type` | no | confidential | From legacy metadata |
| `actor_id` | no | confidential | |
| `metadata` | no | internal | Sanitized subset — no secrets |
| `bridge_mapper_version` | yes | internal | Bridge mapper version |

Forbidden in payload: raw CDC tuples (`lsn`, `xid`, `source_op`, `before`, `after`,
`row_data`, …), secrets, tokens, inline media bytes.

## Envelope defaults

| Field | Expected |
|-------|----------|
| `data_classification` | `internal` (default) |
| `pii_present` | `false` unless sanitized metadata contains address/phone |
| `media_refs` | Optional URI references when evidence pointers exist |

## Compatibility policy

| Change | Policy |
|--------|--------|
| Add optional payload field | Backward compatible within v1 |
| Remove/rename required payload field | Increment `event_version`; dual-subscribe |
| Unknown payload fields at publish | **Rejected** (`additionalProperties: false`) |
| Unknown payload fields at consume | **Ignored** (tolerant reader per ADR-0002) |
| Envelope evolution | Follow `contracts/events/envelope/README.md` |

## Delivery semantics

At-least-once via NATS JetStream. Bridge uses transactional outbox (ADR-0008);
consumers use durable inbox deduplication on `(consumer_name, event_id)`.
Exactly-once is not claimed.

## Idempotency / ordering / failure

| Concern | Policy |
|---------|--------|
| Idempotency key | `event_id` |
| Ordering | Per-`shipment_id` by `occurred_at` + `source_pk`; tolerate out-of-order delivery |
| Replay | Set `metadata.replay=true` and `metadata.replay_source` |
| Poison | MaxDeliver + quarantine; no infinite retry (ADR-0002) |

## PII / data classification

- Default: `data_classification=internal`, `pii_present=false`.
- Set `pii_present=true` when sanitized metadata may contain address/phone/recipient hints.
- Never include OTP, JWT, API keys, push tokens, national ID, or inline evidence bytes.
- Log envelope `safe_log_fields()` only at INFO.

## Artifacts

| Path | Purpose |
|------|---------|
| `v1.schema.json` | Full message (envelope + payload constraints) |
| `v1.payload.schema.json` | Payload body only |
| `examples/` | Valid complete, minimal, and redacted envelopes |
| `fixtures/` | Invalid examples for compatibility tests |

## References

- ADR-0009 A1, ADR-0007 bridge identity, ADR-0002 envelope
- `infra/eventing/subject-grammar.md` (non-aggregate pattern)
- Legacy evidence: `shipment_events` @ `2e375057fdf9b9ce8416408a4436303be5301def`
