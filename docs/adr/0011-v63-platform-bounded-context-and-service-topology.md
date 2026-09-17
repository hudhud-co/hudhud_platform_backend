# ADR-0011: v6.3 Platform Bounded-Context Map and Service Topology

- **Status:** accepted
- **Date:** 2026-09-14
- **Deciders:** platform architecture review; product owner (v6.3 authorization)
- **Workstream:** W20 — full-platform audit and build-out
- **Implementation allowed:** yes, for every context named below

Label key: **[evidence]** verified from a product source or this repository; **[decision]**
binding; **[assumption]** engineering default; **[unresolved policy]** requires a named decider.

## Context

**[evidence]** Three product sources now define the platform end to end:

| Source | Path |
|--------|------|
| *The Shipment Journey* v6.3 (46 pp, Aug 2026) | `docs/input/Hudhud_Shipment_Journey_Business_Process_Design_v6.3.pdf` |
| HUDHUD Customer App v3 — multi-role (105 screens) | `docs/input/HUDHUD Customer App v3 (standalone) .html` |
| HUDHUD Driver App v8 (96 screens, 3 modules) | `docs/input/HUDHUD Driver App v8 (standalone).html` |

**[evidence]** `docs/audits/hudhud-app-redesign-v6.3/03-REQUIREMENTS-CATALOG.md` catalogues 160
requirements from those sources. As of the W19-C wave, 96 were classified
`BLOCKED_BY_EXTERNAL_DEPENDENCY` — not because any business decision was missing, but because
the owning bounded context had no service, no accepted ADR, or an `undecided` owner.

**[decision]** That classification was wrong in kind. A missing service, a missing ADR, an
`undecided` owner, a `pending_cutover` status and a stale `Implementation allowed: no` are
**engineering work items, not blockers**. This ADR supersedes those conditions, fixes the target
bounded-context map, assigns an owner to every context the product requires, and authorizes
implementation.

**[decision]** A requirement may be recorded as genuinely blocked only when it needs: a business
decision that cannot safely be represented as configuration; external credentials or
infrastructure unavailable locally; approval for a destructive production migration; or a direct
contradiction between authoritative product sources.

## Decision drivers

Ranked:

1. Platform invariants (`architecture/invariants.md`) — Shipment sole lifecycle writer;
   irreversible physical delivery; COD collection and merchant payable as separate facts.
2. Bounded-context integrity — no shared ORM, no cross-service database access, one canonical
   writer per context.
3. Product evidence — v6.3 Confirmed-decision and Role-boundary blocks are binding policy.
4. Migration risk — every context starts with its own database and its own migrations.
5. Operational cost — contexts with a single lifecycle and a shared transaction boundary may share
   one deployable, with ownership preserved per context (ADR-0001 transitional deployables).

## Decision — target service topology

**[decision]** Thirteen services. Each owns its database, migrations, contracts, and tests. No
service reads another's tables.

| Service | Bounded contexts owned | Status |
|---------|------------------------|--------|
| `identity` | `auth_identity` | **new** |
| `customer` | `customer`, `address_book` | **new** |
| `merchant` | `merchant_store` | **new** |
| `ordering` | `order`, `send_parcel`, `pricing_quote`, `serviceability` | **new** |
| `pickup` | `pickup` | exists |
| `shipment` | `shipment` | exists |
| `hub` | `hub`, `linehaul` | **new** |
| `delivery` | `delivery` | **new** |
| `finance` | `wallet_cod`, `finance_settlement` | **new** |
| `claims` | `support_claims` | **new** |
| `notification` | `notification` | **new** |
| `tracking` | `tracking` (read projection) | exists |
| `audit` | `audit` | exists |

`media_proof` is **[decision]** not a service: evidence is referenced by opaque
`EvidenceMediaRef` (bucket + key) owned by whichever context records it, exactly as `pickup`
already does. `control_tower` and `gateway` remain read/route-only and are out of this wave.

### Grouping justification

`customer` + `address_book`: an address book has no lifecycle independent of the customer who
owns it, and every read is customer-scoped. `ordering`: an order, its quote, its serviceability
check and its shipment requests are created inside one transaction from one app flow; splitting
them would require a distributed transaction to create a single parcel. `hub` + `linehaul`: a
parcel group is sealed at one hub and opened at another; the seal is one aggregate. `finance`:
ADR-0012 requires cash custody, receipt, payable and settlement to share one ledger transaction.

## Decision — canonical writers and cross-context flow

**[decision]** Shipment remains the sole writer of shipment lifecycle state (ADR-0003, unchanged).
Pickup, Hub, Linehaul and Delivery publish facts; Shipment applies them. Delivery publishes COD
collection as a fact; Finance — never Delivery — recognises the merchant payable (ADR-0012).

**[decision]** Identity owns authentication, principals and role grants. Domain services own
their own membership and policy: `merchant` owns store membership, `pickup` owns driver work
sessions, `hub` owns hub staffing. Identity never stores domain membership, and domain services
never store credentials.

**[decision]** Service-to-service trust is an explicit bearer token verified against Identity's
introspection endpoint through an HTTP adapter behind a port. Forwarded identity headers remain
inadmissible as proof of identity.

## Consequences

### Positive

- Every one of the 160 requirements gets a named owner and can be implemented.
- Pickup's driver endpoints stop being dormant: a real authorization adapter can exist.
- Finance stops being a policy vacuum; v6.3 supplies the confirmed decisions it lacked.

### Negative

- Thirteen services is a large operational surface for the current team size. ADR-0001's
  transitional-deployable mechanism still applies: services may be co-deployed while keeping
  their databases and ownership separate.
- Eight new databases and migration chains to operate.

### Neutral

- The legacy monolith remains read-only behavioural evidence. Its module layout is not copied.

## Migration impact

Each new service starts empty with its own Alembic chain and its own database role. There is no
dual-write: contexts that the legacy system still owns are populated forward-only through the
Legacy Event Bridge or by direct creation in the new service. Credential revocation per
ADR-0006 remains the cutover gate.

## Security

Identity is the only store of authentication credentials; OTP codes are stored as keyed hashes
only, never in plaintext. Every service keeps its default-deny authorization posture until a
production identity adapter is configured. PII (phone numbers, names, addresses, ID evidence) is
confined to the owning context and never duplicated into event payloads beyond a reference.

## Rollback

Each service is independently deployable and independently removable while it has no upstream
consumers. Physical delivery and COD collection remain irreversible facts (ADR-0003).

## Unresolved questions

Carried forward as genuine business blockers, unchanged by this ADR: the merchant-application
data set beyond the fields v6.3 itself lists; the exact procedure per payout method; the
high-value declared-value threshold; whether the return-trip fee may be waived. Each is recorded
in `docs/audits/hudhud-app-redesign-v6.3/08-DECISIONS-CONFLICTS-ASSUMPTIONS.md` and represented
in code as configuration, never as an invented default.

## References

- Supersedes the provisional deployable count in ADR-0001 and the `undecided` owners in
  `architecture/ownership-matrix.yaml`.
- Resolves ADR-0004 (see its updated status) and is completed by ADR-0012 for finance.
- Product evidence: `docs/audits/hudhud-app-redesign-v6.3/00-SOURCE-OF-TRUTH.md`
