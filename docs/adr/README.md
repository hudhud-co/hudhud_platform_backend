# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs) for the HUDHUD platform backend.

## Process

1. Copy `0000-template.md` to `NNNN-short-title.md` with the next sequential number.
2. Fill every required section in the template (context, options, decision drivers,
   decision, consequences, migration impact, observability, security, rollback,
   unresolved questions). Keep status `proposed` until named deciders accept it.
3. Link related ADRs and update `architecture/service-boundaries.yaml` when a decision
   affects bounded context ownership or deployable boundaries. Do not invent policy
   or treat a suggested deployable count as an architectural fact.

## Status Values

- **proposed** — under discussion
- **accepted** — binding for implementation direction (does not imply implementation-complete)
- **Proposed — Policy Blocked** — design direction exists; business/accounting policy unresolved
- **deprecated** — superseded but retained for history
- **superseded by ADR-NNNN** — replaced by a newer decision

## Wave 1 ADR Index

| ADR | Title | Status | Decision scope | Implementation gate | Dependencies |
|-----|-------|--------|----------------|---------------------|--------------|
| [ADR-0001](0001-transitional-deployables-and-extraction-order.md) | Transitional deployables and extraction order | **Accepted** | Staged transitional deployables; low-risk consumer-first extraction; Hub ≠ Linehaul preserved | Capacity proof for exact runtime count (3–5 provisional); exit criteria per plateau | ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0006 |
| [ADR-0002](0002-event-envelope-outbox-inbox-and-jetstream.md) | Event envelope, outbox/inbox, and JetStream | **Accepted** | Versioned envelope; transactional outbox; durable inbox; JetStream; at-least-once delivery; per-service durables | Numeric retention/retry/size defaults; NATS HA; schema bootstrap | ADR-0001, ADR-0003, ADR-0004, ADR-0005, ADR-0006 |
| [ADR-0003](0003-shipment-lifecycle-authority-and-delivery-facts.md) | Shipment lifecycle authority | **Accepted** | Shipment canonical single-writer; irreversible physical delivery facts; Finance-mediated COD flow | Unresolved operational policies (reattempt, return, lost parcel, etc.) | ADR-0002, ADR-0004, ADR-0005 |
| [ADR-0004](0004-identity-gateway-and-service-trust.md) | Identity, Gateway, and service trust | **Proposed** | Identity owns auth identity; domain services own membership/policy; Gateway routes only | Customer/Organization ownership; hub/driver grant ownership finalization | ADR-0001, ADR-0002, ADR-0006 |
| [ADR-0005](0005-cod-wallet-ledger-and-settlement.md) | COD, wallet, ledger, and settlement | **Proposed — Policy Blocked** | Double-entry finance authority recommended; Wallet as projection; Delivery→Finance not Delivery→Wallet | Policy register P-01–P-17; COA; commission/settlement rules | ADR-0002 (Accepted), ADR-0003 (Accepted), ADR-0004 (Proposed) |
| [ADR-0006](0006-one-writer-data-cutover-and-reconciliation.md) | One-writer data cutover | **Accepted** | One-writer cutover; semantic reconciliation; credential revocation; zero-gap HWM capture | CDC/replication tooling; per-context cutover execution | ADR-0001, ADR-0002, ADR-0003, ADR-0004, ADR-0005 |
| [ADR-0007](0007-legacy-event-bridge-strategy.md) | Legacy event bridge strategy | **Proposed** | Transitional legacy→JetStream capture; read-only bridge candidate; hybrid poll/CDC/WAL/outbox options | Capture mechanism completeness proof; bridge lag SLO; zero-gap drill; contract registration | ADR-0001, ADR-0002, ADR-0003, ADR-0005, ADR-0006 |

## ADR Numbering Reference

| ADR | Subject |
|-----|---------|
| ADR-0001 | Transitional deployables and extraction order |
| ADR-0002 | Event envelope, outbox/inbox, and JetStream |
| ADR-0003 | Shipment lifecycle authority |
| ADR-0004 | Identity, Gateway, and service trust |
| ADR-0005 | COD, wallet, ledger, and settlement |
| ADR-0006 | One-writer data cutover |
| ADR-0007 | Legacy event bridge strategy |

## Relationship to Legacy

Legacy design docs in `hudhud-backend` are reference material only. Platform ADRs in this
directory are authoritative for `hudhud_platform_backend`.

## Canonical Architecture Updates

Wave 1 integration updates accepted facts in:

- `architecture/service-boundaries.yaml`
- `architecture/ownership-matrix.yaml`

Unresolved fields remain `undecided`, `policy_blocked`, or `Proposed` — not filled with guesses.
