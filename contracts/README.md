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

Foundation F0 — contract artifacts will be added when the first service publishes events.
