# HUDHUD JetStream subject grammar (ADR-0002 S2)

This document defines the accepted namespace grammar for foundation topology subjects.
Infrastructure examples are **provisional** until registered in `contracts/events/`.

The grammar distinguishes five concerns on every subject:

1. **Domain context / stream** — first segment after `hudhud`; binds the subject to a context stream (`hudhud.{domain_context}.>`).
2. **Producer / source** — publishing identity (envelope `producer`).
3. **Message semantic class** — logical class in `event_type` (`fact`, `command`, `observation`, `projection`, …). Envelope `message_kind` is **not** a subject segment.
4. **Event name** — the specific fact/observation/command name.
5. **Version** — `v{event_version}` payload schema version.

Two patterns share those five concerns. Aggregate S2 is unchanged. Non-aggregate is required
because A1/A2 observations use `producer=legacy_bridge` and `aggregate_scope=non_aggregate`
and MUST route on a domain stream without implying Bridge domain ownership.

## Aggregate pattern (S2)

```text
hudhud.{producer}.{event_type}.v{event_version}
```

| Segment | Meaning | Example |
|---------|---------|---------|
| `hudhud` | Platform root namespace | fixed |
| `{producer}` | Publishing bounded-context deployable id **and** stream-routing domain context | `pickup`, `delivery`, `finance` |
| `{event_type}` | Dot-separated logical type (may repeat context segment) | `pickup.fact.accepted`, `delivery.fact.cod_collected` |
| `v{event_version}` | Payload schema version for that `event_type` | `v1` |

`message_kind` (`integration`, `command`, `fact`, …) lives in the envelope — **not** as a
separate subject segment (ADR-0002 pattern S2).

### Repeated context segments

Subjects such as `hudhud.pickup.pickup.fact.>` are **intentional** under S2 when
`event_type` begins with the bounded-context name:

```text
hudhud.{producer}.{event_type}.v{ver}
→ hudhud.pickup.pickup.fact.accepted.v1
   └ producer ─┘ └──── event_type ────┘
```

The repeated `pickup` segment is not a typo — it follows `{producer}` + `{event_type}`
where `event_type = pickup.fact.accepted`. Under S2, `{producer}` is also the
domain-context/stream token. It is **not** an extra aggregate identifier.

## Non-aggregate pattern

Accepted for documented non-aggregate messages (`aggregate_scope=non_aggregate`), including
ADR-0009 A1/A2 Legacy Event Bridge observations.

```text
hudhud.{domain_context}.{producer}.{semantic_class}.{event_name}.v{event_version}
```

| Segment | Meaning | A1 | A2 |
|---------|---------|----|----|
| `hudhud` | Platform root namespace | fixed | fixed |
| `{domain_context}` | Stream-routing domain — **not** an aggregate identifier | `shipment` | `audit` |
| `{producer}` | Envelope producer / capture source — **not** an aggregate identifier | `legacy_bridge` | `legacy_bridge` |
| `{semantic_class}` | Logical class (`observation`) | `observation` | `observation` |
| `{event_name}` | Observation name | `shipment_timeline_entry` | `audit_entry` |
| `v{event_version}` | Payload schema version | `v1` | `v1` |

Envelope fields (binding): `producer=legacy_bridge`, `aggregate_scope=non_aggregate`.

`legacy_bridge` in the subject is the **producer/source**, not a bounded context, not a
canonical aggregate owner, and not a stream name. Do **not** introduce a
`HUDHUD_LEGACY_BRIDGE` stream or `hudhud.legacy_bridge.>` subject tree. Subject syntax
MUST NOT imply Bridge domain ownership.

Accepted subjects (match their streams):

```text
hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1
  → HUDHUD_SHIPMENT  (hudhud.shipment.>)

hudhud.audit.legacy_bridge.observation.audit_entry.v1
  → HUDHUD_AUDIT     (hudhud.audit.>)
```

These subjects do **not** use aggregate S2 with `producer=legacy_bridge`, which would
incorrectly yield `hudhud.legacy_bridge.…` and require a Bridge domain stream.

## Stream wildcard binding

Streams bind with a trailing glob:

```text
hudhud.{domain_context}.>
```

For aggregate S2, `{domain_context}` equals `{producer}`. For non-aggregate Bridge
observations, `{domain_context}` is the owning domain stream (`shipment`, `audit`),
not the producer.

Durable consumers use **filter subjects** scoped to their subscription interest (D4 pattern).
Provisional Bridge durables MUST filter the accepted subjects above.

## Authority boundaries (foundation examples)

| Subject example | Publisher | Consumer | Notes |
|-----------------|-----------|----------|-------|
| `hudhud.delivery.delivery.fact.cod_collected.v1` | `delivery` | `finance` | Operational COD fact — Finance owns posting |
| `hudhud.finance.finance.fact.posting_completed.v1` | `finance` | `wallet_cod` | Finance-authorized projection trigger |
| `hudhud.wallet.wallet.fact.balance_updated.v1` | `wallet_cod` | read models | Projection fact — not financial authority |
| `hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1` | `legacy_bridge` | Tracking / Control Tower | Non-aggregate observation — not Shipment authority |
| `hudhud.audit.legacy_bridge.observation.audit_entry.v1` | `legacy_bridge` | Audit | Non-aggregate observation — not canonical Audit fact |

Delivery MUST NOT publish to `hudhud.wallet.>`.
Legacy Event Bridge MUST NOT publish to `hudhud.legacy_bridge.>`.

## Provisional foundation consumers

Durable names in `topology/consumers.yaml` are **foundation templates** for local topology
proof only. They do not register accepted domain-event contracts. Bridge observation
filters are the accepted A1/A2 subjects.
