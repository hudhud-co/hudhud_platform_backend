# Contracts

API and event contracts shared between services and external consumers.

## Contents (Future)

- OpenAPI specifications per service
- AsyncAPI or JSON Schema event definitions
- Versioned event envelope schemas
- Consumer compatibility matrices

## Event Envelope Requirements

Cross-service events must support:

- `event_id`, `event_type`, `event_version`, `occurred_at`
- `producer`, `aggregate_type`, `aggregate_id`, `aggregate_version`
- `correlation_id`, `causation_id`, `traceparent`
- Tenant/organization context when applicable

## Compatibility Rules

- Producers must not break consumers within the same major `event_version`.
- New fields are additive; removing or renaming fields requires a version bump.
- Commands and consumers must be idempotent (at-least-once delivery via NATS JetStream).

## Current Stage

Foundation F0 — `event_envelope` package and `contracts/events/envelope/` schema are available.
See `packages/event_envelope/` and `contracts/events/envelope/README.md`.

Wave 4 observation contracts (ADR-0009 A1/A2) are registered under
`contracts/events/legacy_bridge.observation.*/` with index
`contracts/events/registry.yaml`.

ADR-0009 C10 `pickup.fact.accepted` v1 is registered under
`contracts/events/pickup.fact.accepted/` as
`implementation_authorized_not_production_enabled` (contract only — not
production-enabled).
