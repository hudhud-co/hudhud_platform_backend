# Integration Message Envelope Contract

Versioned machine-readable contract for the HUDHUD cross-service integration message
envelope defined in [ADR-0002](../../docs/adr/0002-event-envelope-outbox-inbox-and-jetstream.md).

## Schema ownership

| Aspect | Owner |
|--------|-------|
| Envelope wire shape | Platform architecture (`event_envelope` shared package) |
| Envelope JSON Schema | `contracts/events/envelope/` (this directory) |
| Payload schemas per `event_type` | Owning bounded-context producer (`contracts/events/{event_type}/`) — future |
| Runtime implementation | `packages/event_envelope/` |

## Envelope version

| Field | Current value | Meaning |
|-------|---------------|---------|
| `envelope_version` | `1` | Wire-format and top-level field compatibility |
| `event_version` | per `event_type` | Payload-schema compatibility (independent) |

## Compatibility rules

### Envelope-level (`envelope_version`)

| Change type | Policy |
|-------------|--------|
| Add optional top-level field | Additive within same `envelope_version`; consumers MUST preserve or explicitly reject unknown fields |
| Remove or rename required field | Breaking — increment `envelope_version` |
| Unsupported `envelope_version` | Consumers MUST reject with explicit error (no silent coercion) |

### Payload-level (`event_version`)

| Change type | Policy |
|-------------|--------|
| Add optional payload field | Additive within same `event_version` for that `event_type` |
| Remove/rename required payload field | Increment `event_version` for that `event_type` |
| Dual-subscribe window | Consumers handle multiple `event_version` values during migration |

Envelope compatibility and payload-schema compatibility are **distinct**. A producer may bump
`event_version` without changing `envelope_version`, and vice versa.

## Producer / consumer upgrade expectations

1. **Producers** emit `envelope_version` equal to the schema they implement; MUST NOT emit a
   greater version than published consumers support.
2. **Consumers** on `envelope_version` N MUST preserve additive unknown fields on deserialize
   (default `PRESERVE`) or reject them explicitly (`REJECT`). Silent lossy dropping is forbidden
   as the default interoperability behavior.
3. **Producers** and build-time validation MUST reject unknown top-level fields (`REJECT`).
4. **Consumers** MUST reject `envelope_version` > supported with a structured error (no payload
   logging at INFO when `pii_present` is true).
5. **Payload evolution** follows per-`event_type` contracts; inbox deduplication remains on
   `event_id` regardless of `event_version`.

## Artifacts

| File | Purpose |
|------|---------|
| `v1.schema.json` | JSON Schema for envelope version 1 |
| `examples/aggregate_command.json` | Aggregate-scoped command |
| `examples/aggregate_integration_event.json` | Aggregate-scoped integration fact |
| `examples/non_aggregate_platform.json` | Explicit non-aggregate platform message |

## Size limits

ADR-0002 proposes soft (64 KiB) and hard (256 KiB) limits as **provisional defaults**. They are
configurable at runtime via `EnvelopeLimits` and are **not** frozen as architectural constants.

## Delivery semantics

At-least-once via NATS JetStream. Producers use transactional outbox; consumers use durable
idempotent inbox. Exactly-once delivery is not claimed.
