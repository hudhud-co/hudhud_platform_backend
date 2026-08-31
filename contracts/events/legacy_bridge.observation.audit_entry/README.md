# legacy_bridge.observation.audit_entry v1 (ADR-0009 A2)

Transitional Legacy Event Bridge observation of one **append-only** legacy
`audit_logs` row. This is **not** `audit.fact.entry_recorded` and MUST NOT be
renamed or promoted to Audit-native canonical facts while `producer=legacy_bridge`.

## Schema identifiers

| Artifact | `$id` |
|----------|-------|
| Full message | `https://hudhud.platform/contracts/events/legacy_bridge.observation.audit_entry/v1.schema.json` |
| Payload only | `https://hudhud.platform/contracts/events/legacy_bridge.observation.audit_entry/v1.payload.schema.json` |

## Ownership

| Role | Value |
|------|-------|
| Producer | `legacy_bridge` (transitional technical deployable — not a bounded context) |
| Intended consumers (Wave 1) | Audit (transport/projection), cross-module observability |
| Owning schema path | `contracts/events/legacy_bridge.observation.audit_entry/` |
| Canonical audit authority | **Audit** service (post-cutover) — native `audit.fact.entry_recorded` deferred |

## JetStream routing

| Field | Value |
|-------|-------|
| Subject | `hudhud.audit.legacy_bridge.observation.audit_entry.v1` |
| Stream | `HUDHUD_AUDIT` |
| `message_kind` | `integration` |
| `aggregate_scope` | `non_aggregate` (no invented `aggregate_version`) |

Entity correlation uses `payload.entity_type` / `payload.entity_id`. Envelope
aggregate fields MUST remain absent/null.

## Stable event identity

```text
event_id = UUIDv5(
  namespace = 697097cc-6afb-556b-9f9b-4be135ca6282,
  name      = "{source_system}:{source_table}:{source_pk}"
)
```

| Component | Value |
|-----------|-------|
| Namespace derivation | `UUIDv5(NAMESPACE_DNS, "hudhud.platform/events/legacy_bridge.observation.audit_entry/v1")` |
| `source_system` (Bridge constant) | `legacy` |
| `source_table` | `audit_logs` (payload const) |
| `source_pk` | Legacy row UUID |

**Excluded from identity:** capture mechanism, LSN, xid, timestamp, `source_op`,
`correlation_id`, `aggregate_version`. `payload.source_position` is provenance only.

## Payload fields (v1)

| Field | Required | Classification | Notes |
|-------|----------|----------------|-------|
| `source_table` | yes | internal | Must be `audit_logs` |
| `source_pk` | yes | internal | Legacy row id |
| `source_position` | yes | internal | LSN or `{created_at}\|{source_pk}` |
| `source_module` | yes | internal | Legacy module that appended the row |
| `audit_entry_id` | yes | internal | Same as `source_pk` |
| `action` | yes | internal | e.g. `SHIPMENT_DELIVERED` |
| `entity_type` | yes | internal | |
| `entity_id` | yes | internal | |
| `actor_type` | yes | internal | |
| `actor_id` | no | confidential | |
| `source` | yes | internal | Legacy `source` column |
| `occurred_at` | yes | internal | `created_at` from legacy row |
| `metadata` | no | confidential | Sanitized — no secrets |
| `bridge_mapper_version` | yes | internal | Bridge mapper version |

Forbidden in payload: raw CDC tuples, secrets, tokens, inline media bytes.

## Envelope defaults

| Field | Expected |
|-------|----------|
| `data_classification` | `internal` (use `confidential` when IP/user-agent present) |
| `pii_present` | `false` unless metadata contains IP/user-agent or similar |

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
| Ordering | Per-entity by `occurred_at` + `source_pk`; tolerate out-of-order delivery |
| Replay | Set `metadata.replay=true` and `metadata.replay_source` |
| Poison | MaxDeliver + quarantine; no infinite retry (ADR-0002) |

## PII / data classification

- IP address and user agent: classify `confidential`; set `pii_present=true`.
- Never include OTP, JWT, API keys, push tokens, or national ID in payload.
- JetStream is transport only; Audit service owns long-term retention.

## Artifacts

| Path | Purpose |
|------|---------|
| `v1.schema.json` | Full message (envelope + payload constraints) |
| `v1.payload.schema.json` | Payload body only |
| `examples/` | Valid complete, minimal, and redacted envelopes |
| `fixtures/` | Invalid examples for compatibility tests |

## References

- ADR-0009 A2, ADR-0007 bridge identity, ADR-0002 envelope
- `infra/eventing/subject-grammar.md` (non-aggregate pattern)
- Legacy evidence: `audit_logs` @ `2e375057fdf9b9ce8416408a4436303be5301def`
