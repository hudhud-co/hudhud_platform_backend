# 10 — Target Bounded-Context and Service Ownership Map

Supersedes the "blocked because no service exists" classification in `04`. Authority:
**ADR-0011** (topology) and **ADR-0012** (finance), both Accepted.

## The correction

A missing service, a missing ADR, an `undecided` owner, a `pending_cutover` status and a
stale `Implementation allowed: no` are **engineering work items, not blockers**. Of the 96
requirements previously marked blocked, **91 were blocked for one of those reasons** and
are now assigned an owner. Five remain genuinely blocked (see §4).

## Target topology — 13 services

| Service | Bounded contexts | Status | Tests |
|---------|------------------|--------|-------|
| `identity` | `auth_identity` | **built (W20-A)** | 76 |
| `customer` | `customer`, `address_book` | **built (W20-B)** | 67 |
| `merchant` | `merchant_store` | scaffolded | — |
| `ordering` | `order`, `send_parcel`, `pricing_quote`, `serviceability` | scaffolded | — |
| `pickup` | `pickup` | pre-existing, extended | 354 |
| `shipment` | `shipment` | pre-existing | 146 |
| `hub` | `hub`, `linehaul` | scaffolded | — |
| `delivery` | `delivery` | scaffolded | — |
| `finance` | `wallet_cod`, `finance_settlement` | scaffolded | — |
| `claims` | `support_claims` | scaffolded | — |
| `notification` | `notification` | scaffolded | — |
| `tracking` | `tracking` | pre-existing | 129 |
| `audit` | `audit` | pre-existing | 81 |

`media_proof` is **not** a service: evidence is an opaque `EvidenceMediaRef` (bucket + key)
owned by whichever context records it, as `pickup` already does. `control_tower` and
`gateway` remain read/route-only.

Every service owns its database, migrations, contracts and tests. `verify_boundaries.py`
enforces no cross-service imports, no shared ORM, no cross-service database access.

## Requirement ownership after the correction

| Owner | Requirements |
|-------|--------------|
| `identity` | SEC-01, SEC-02, SEC-03, SEC-04, SEC-05 (role model) |
| `customer` | CUS-10, CUS-14, CUS-15, MER-06, MER-07 (contact minimum) |
| `merchant` | MER-01, MER-03, MER-04, MER-10, MER-11, MER-12, MER-14, MER-16, MER-17, MER-20, MER-21, SEC-05 (store membership) |
| `ordering` | CUS-01, CUS-02, CUS-08, MER-05, MER-08, MER-09, MER-13, MER-18, MER-19, MER-22, MER-23, SHP-01, SHP-02, SHP-10, SHP-11, SHP-12 |
| `pickup` | DRV-P01 … DRV-P26 (complete) |
| `shipment` | SHP-03, SHP-04, SHP-09 (custody authority) |
| `hub` | CUS-03, CUS-04, CUS-05, CUS-06, CUS-07, SHP-05, SHP-06, SHP-07, SHP-08, SHP-13, OPS-01, OPS-02, OPS-05, OPS-10 |
| `delivery` | DRV-L01 … DRV-L21, CUS-11, CUS-12, CUS-13, OPS-08 |
| `finance` | PAY-01 … PAY-11, DRV-A05 … DRV-A13, OPS-04 |
| `claims` | CLM-01 … CLM-07, DRV-P25, OPS-06, OPS-07 |
| `notification` | NTF-01 … NTF-10 |
| `tracking` | CUS-09, OPS-03, OPS-11 |
| **genuinely blocked** | MER-02, PAY-07, CLM-08, SHP-02 (display), PAY-08 |

Driver attendance (DRV-A01 … DRV-A04) is owned by `identity` for the principal status
half (suspension ends live sessions — implemented and tested) and by a driver-workforce
capability inside `pickup` for the session half (already implemented).

## §4 — The five genuinely blocked requirements

Each meets the mission's own bar: a business decision that cannot safely be represented as
configuration, and that v6.3 **itself** lists as an Open Item for executive decision
(Appendix A, p.44).

| ID | Requirement | Why it is genuinely blocked | How the platform represents it meanwhile |
|----|-------------|-----------------------------|------------------------------------------|
| MER-02 | The merchant-application data set and approval rules | v6.3 p.10: "This has not yet been defined and needs a deliberate answer **before the application flow can be built**." Inventing the fields would fabricate a KYC policy. | `merchant` models the application as a state machine with an open attribute bag; the required-field list is configuration. |
| PAY-07 | Exact procedure per payout method | v6.3 p.34: "needs to be defined in detail by an accountant." Fee, timing and reconciliation rules per method are financial policy. | `finance` models the request, method, destination and state; it refuses to invent fees or timing. |
| CLM-08 | The high-value declared-value threshold | v6.3 p.44: "still not formally set." A guessed threshold silently changes liability. | Configuration value with no default. |
| SHP-02 (display) | Showing the customer their exact delivery goal | v6.3 p.44: without it "the receiver cannot be shown a reliable countdown." | The 24-hour goal is measured from the acceptance scan and stored; only the customer-facing promise is withheld. |
| PAY-08 | Whether the return-trip fee may be waived | v6.3 p.44 open question; p.38 currently always charges the merchant. | Implemented as written (always charged), with the waiver as a future policy flag. |

Two source conflicts also remain recorded rather than invented — delivery-code length
(4 vs 6 digits) and ID-photo retention (PDF vs Driver App). Both are direct contradictions
between authoritative product sources, which the mission names as a genuine blocker.

## Delivered so far

- **ADR-0011** and **ADR-0012** written and Accepted; **ADR-0004** moved Proposed → Accepted;
  **ADR-0005** marked superseded.
- `architecture/ownership-matrix.yaml` and `architecture/service-boundaries.yaml` rewritten:
  no context is `undecided`, `policy_blocked` or `not_started` any more.
- Architecture tests re-pointed at the new invariants and **strengthened** (3 added):
  every product context has a named owner; Identity stores no domain membership; Delivery
  never writes the wallet.
- `identity` built: OTP sign-in, sessions, role grants, introspection (76 tests).
- `customer` built: profile, legal acceptance, address book (67 tests).
- `pickup` wired to real Identity authorization, **proven no longer dormant** by a
  cross-service HTTP integration suite (6 tests).
