---
name: define-event-contract
description: >-
  Create or evolve a versioned HUDHUD event contract with envelope compliance,
  producer/consumer ownership, ordering, idempotency, replay, compatibility
  tests, and failure policy. Use when adding or changing contracts/ events.
disable-model-invocation: true
---

# Define event contract

## Purpose

Publish a versioned event (or command) contract that surviving consumers can
implement under at-least-once delivery.

## When to use

- Adding or changing an event under `contracts/`
- Changing envelope fields, compatibility, or owner
- The human instruction names this skill

## When not to use

- Claiming exactly-once delivery
- Letting a consumer write another service's database
- Changing canonical Shipment state from a non-Shipment producer

## Required inputs

- `event_type` and `event_version`
- Producer service and consumer services
- Aggregate identity (`aggregate_type`) and whether `aggregate_version`
  is monotonic for ordering
- Failure/retry/poison policy

## Preconditions

1. Producer is the canonical publisher in
   `architecture/ownership-matrix.yaml`.
2. Read `.cursor/rules/06-events-and-messaging.mdc` and
   [contracts/README.md](../../../contracts/README.md).
3. Envelope fields from `architecture/invariants.md` are known.

## Procedure

1. Add or version the schema under `contracts/` (do not break in-major).
2. Document producer, consumers, idempotency key (usually `event_id`),
   inbox expectation, and replay rules.
3. Require transactional outbox on the producer and durable inbox on
   consumers in the contract notes.
4. Define poison-message, retry budget, and parking. No infinite retry.
5. Add compatibility tests (additive field tolerated; break requires version
   bump).
6. Update `published_events` / `consumed_events` only when ownership YAML
   is in scope and already decided.

## Allowed files or ownership scope

- `contracts/**`
- Producer/consumer test files named in the task
- `architecture/service-boundaries.yaml` event lists if the current task
  includes that ownership update
- Do not implement full NATS cluster topology here

## Required validation

- Envelope field list matches invariants
- Compatibility tests pass
- No "exactly once" wording in the contract
- Consumer mutation scope is the consumer's owned data only

## Stop conditions

- Unknown producer/consumer
- Breaking change without version bump
- Business-critical event proposed as ephemeral

## Prohibited actions

- Exactly-once claims
- Business-critical ephemeral messages
- Push unless the current human instruction explicitly authorizes it
- Pull request creation unless the current human instruction explicitly authorizes it
- Production access or live production mutation
- Destructive Git operations (`reset --hard`, `clean -fd`, force checkout, rewrite of unpublished user work)
- Mutating the legacy repository
- Consumer writes to another context's datastore

## Output contract

```text
event_type:
event_version:
producer:
consumers:
ordering: aggregate_version
idempotency:
replay:
failure_policy:
compatibility_tests:
```

## Completion marker

`HUDHUD_EVENT_CONTRACT_DEFINED`
