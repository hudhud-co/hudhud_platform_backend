# 06 — Implementation Waves

> **This document was written before the scope correction.** Its original conclusion — that
> only `pickup` could legally execute — rested on the bounded-context map as it stood, in
> which most contexts had no owning service, an undecided owner, or a proposed ADR. That is
> no longer the situation: a missing service, an undecided owner and a proposed ADR are
> architecture work, not blockers. §W20–W26 below record the waves executed since, and §"No
> longer blocked" replaces the table that said they could not be.

## W1–W7 — `pickup` (original scope)

| Wave | Slice | Requirements | Depends on | Independently testable |
|------|-------|--------------|-----------|------------------------|
| **W1** | Packaging assessment + condition decision, with the `TOO_WEAK ⇒ REFUSE only` invariant | DRV-P08 … DRV-P12 | domain value objects | yes |
| **W2** | Stop outcomes: parcel refusal and not-presented (terminal, no custody) | DRV-P14, DRV-P15 | W1 (refusal is reachable from a condition decision) | yes |
| **W3** | Photo-documentation acceptance gate | DRV-P13 | W1 | yes |
| **W4** | Acceptance status query | DRV-P19 | none | yes |
| **W5** | Scan resolution over the driver's batch | DRV-P06, DRV-P07 | W2 (outcomes participate in resolution) | yes |
| **W6** | Stop readiness / completion gate | DRV-P17 | W2 | yes |
| **W7** | Migration, persistence mapping, API wiring, docs and governance manifests | all | W1–W6 | yes |

## W20–W26 — the platform waves

Each wave is one bounded context: its domain and state machines, an expand-only migration
proven against a disposable PostgreSQL 16, its API, its NATS contracts with a transactional
outbox and a durable inbox, authorization through Identity over HTTP, and its own tests.

| Wave | Service | Requirements | Depends on | Migration proven |
|------|---------|--------------|-----------|------------------|
| **W20-A** | `identity` | SEC-01, SEC-02, SEC-04…06, SEC-09 | none | yes |
| **W20-B** | `customer` | CUS-01…09 | W20-A | yes |
| **W21** | `merchant` | MER-01, MER-03…16 | W20-A | yes |
| **W22** | `ordering` | SHP-01, SHP-03…05, SHP-09…12 | W21 | yes |
| **W23** | `hub` | SHP-06…08, SHP-13, OPS-01…03 | W22 | yes |
| **W24** | `notification` | NTF-01…08, CUS-14 | W20-A | yes |
| **W25** | `workforce` | SEC-03, DRV-A01…A04, OPS-09 | W20-A | yes |
| **W26** | `delivery` | DRV-L01…L21, CUS-11…13, SEC-08, OPS-08 | W23, W25 | yes, and the unit of work too |

W25 created a **fourteenth service** rather than putting driver workforce inside `pickup`:
both `pickup` and `delivery` need the same answer — may this driver be given work right
now? — and it has nothing to do with parcels. ADR-0013 records the reasoning.

## No longer blocked

The table this section replaces listed ten waves as unexecutable. Every reason it gave has
since been resolved as architecture work, which is what those reasons always were.

| Original reason | What was actually done |
|---|---|
| ADR-0004 `proposed`; no identity service | ADR-0004 accepted; `identity` built (W20-A). Every other service resolves callers through it over HTTP and denies everything without it |
| `merchant_store` owner `undecided` | Ownership assigned; `merchant` built (W21) |
| `order`, `send_parcel`, `pricing_quote`, `serviceability` owners `undecided` | Ownership assigned; `ordering` built (W22) |
| `hub` `pending_legacy_audit` | Legacy audited; `hub` built (W23) |
| `delivery` `pending_cutover` | `delivery` built (W26), including OTP, the ID fallback, open-box, proof of delivery, failed attempts, refusal and the three-day hold |
| `notification` owner `undecided` | Ownership assigned; `notification` built (W24) |
| ADR-0005 **`Implementation allowed: no`** | Superseded by ADR-0012. W27 `finance` is the remaining work |
| `support_claims` owner `undecided` | Ownership assigned. W28 `claims` is the remaining work |
| Cross-service end-to-end acceptance | Unblocked once W27 and W28 land; it is the last item before the final report |

## Remaining

| Wave | Service | Requirements | Blocked items that stay blocked |
|------|---------|--------------|----------------------------------|
| **W27** | `finance` | PAY-01…11, DRV-A05…A13, OPS-04 | PAY-07, PAY-08 |
| **W28** | `claims` | CLM-01…07, SEC-07, DRV-P25, OPS-06, OPS-07 | CLM-08 |
| **W29** | cross-service journeys | — | — |
