# HUDHUD JetStream subject grammar (ADR-0002 S2)

This document defines the accepted namespace grammar for foundation topology subjects.
Infrastructure examples are **provisional** until registered in `contracts/events/`.

## Pattern

```text
hudhud.{producer}.{event_type}.v{event_version}
```

| Segment | Meaning | Example |
|---------|---------|---------|
| `hudhud` | Platform root namespace | fixed |
| `{producer}` | Publishing bounded-context deployable id | `pickup`, `delivery`, `finance` |
| `{event_type}` | Dot-separated logical type (may repeat context segment) | `pickup.fact.accepted`, `delivery.fact.cod_collected` |
| `v{event_version}` | Payload schema version for that `event_type` | `v1` |

`message_kind` (`integration`, `command`, `fact`, …) lives in the envelope — **not** as a
separate subject segment (ADR-0002 pattern S2).

## Repeated context segments

Subjects such as `hudhud.pickup.pickup.fact.>` are **intentional** under S2 when
`event_type` begins with the bounded-context name:

```text
hudhud.{producer}.{event_type}.v{ver}
→ hudhud.pickup.pickup.fact.accepted.v1
   └ producer ─┘ └──── event_type ────┘
```

The repeated `pickup` segment is not a typo — it follows `{producer}` + `{event_type}`
where `event_type = pickup.fact.accepted`.

## Stream wildcard binding

Streams bind with a trailing glob:

```text
hudhud.{producer}.>
```

Durable consumers use **filter subjects** scoped to their subscription interest (D4 pattern).

## Authority boundaries (foundation examples)

| Subject example | Publisher | Consumer | Notes |
|-----------------|-----------|----------|-------|
| `hudhud.delivery.delivery.fact.cod_collected.v1` | `delivery` | `finance` | Operational COD fact — Finance owns posting |
| `hudhud.finance.finance.fact.posting_completed.v1` | `finance` | `wallet_cod` | Finance-authorized projection trigger |
| `hudhud.wallet.wallet.fact.balance_updated.v1` | `wallet_cod` | read models | Projection fact — not financial authority |

Delivery MUST NOT publish to `hudhud.wallet.>`.

## Provisional foundation consumers

Durable names in `topology/consumers.yaml` are **foundation templates** for local topology
proof only. They do not register accepted domain-event contracts.
