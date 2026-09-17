# 08 — Decisions, Conflicts, Assumptions

Statement classes follow the repository convention: **evidence**, **proposal**, **decision**,
**assumption**, **unresolved policy**.

## Decisions taken in this wave

| # | Decision | Basis |
|---|----------|-------|
| D-1 | Implement only inside `pickup`; block everything else with its governing reason | **evidence** — `architecture/service-boundaries.yaml` extraction statuses; `architecture/ownership-matrix.yaml`; ADR-0004, ADR-0005; the mission's own rule "Only implement requirements supported by product evidence or an existing approved ADR" |
| D-2 | Extend `condition-proof` rather than add a parallel endpoint | **evidence** — mission rule "Do not create duplicate endpoints when an existing contract can be extended safely" |
| D-3 | Publish **no** event for refusal / not-presented | **evidence** — DRV:`notAccepted` "No custody event was recorded"; avoids inventing a contract ADR-0009 has not accepted |
| D-4 | Model the packaging assessment as a separate dimension from `PackageConditionStatus` | **proposal** — `PackageConditionStatus` is proof metadata; the assessment drives an acceptance decision. Merging them would overload an existing persisted enum and break replay of stored rows |
| D-5 | `TOO_WEAK` permits only `REFUSE`, enforced in the domain | **evidence** — DRV:`condition` "Too weak packaging can only be refused"; PDF p.17 "Clearly no → Refused" |
| D-6 | Scan resolution is strictly read-only | **evidence** — DRV:`scanEx:unknown` "nothing was created, nothing was accepted"; DRV:`scanEx:pickup` field additions disabled |
| D-7 | All new columns nullable/defaulted; all new request fields optional | **assumption** — expand/contract migration policy and mobile compatibility |
| D-8 | Sender handover discovery is answered by `pickup`, not projected into `merchant` | **proposal** — legacy `171b7ba` builds it in Merchant by importing Pickup repositories onto a shared session. Pickup owns the ceremony, so it answers for it; the Workplace-side eligibility that decision carried stays with Merchant and is not part of the v6.3 catalogue. Recorded in `docs/audit/legacy-provenance.yaml` |
| D-9 | Driver performance metrics do **not** affect assignment priority | **decision** — hudhud-backend `a67b8bb` settles the standing open item by moving `assignment_priority_effect` from `NOT_APPLIED_PENDING_PRODUCT_DECISION` to `NOT_APPLIED_BY_PRODUCT_POLICY`. No driver-performance surface exists in this platform, so nothing is implemented here; the ruling is recorded so that whoever builds one does not reopen the question |

## Conflicts recorded, not resolved

| # | Conflict | Sources | Why not resolved here |
|---|----------|---------|----------------------|
| C-1 | Delivery code 4 digits (Customer App) vs 6 digits (Driver App, and the Customer App's own `rvVals`) | CUS:`parcelTransit` vs DRV:`lmOtp`, CUS `rvVals` | Owning context `delivery` is `pending_cutover`; deciding a code length would be inventing a security parameter with no owner |
| C-2 | ID-fallback evidence: PDF requires photographing the ID card; the Driver App states the ID is checked but **not** stored | PDF p.26 vs DRV:`lmIdCapture` | PDF outranks the app, but this is a PII-retention decision and `media_proof.canonical_writer` is `undecided` with `policy_prerequisites: [evidence_ownership_adr]` |
| C-3 | Delivery-fee ownership and timing | PDF p.13 (per-sender setting) vs CUS:`send5`/DRV:`fee` (sender pays cash at pickup) | ADR-0005 `Implementation allowed: no` |

Per the mission's conflict rule, each conflict is recorded, a backward-compatible implementation is
preferred where one exists, no business decision is silently invented, all unrelated safe work
continued, and only the exact conflicting requirement is marked blocked.

## Assumptions made (and why they are safe)

| # | Assumption | Safety |
|---|------------|--------|
| A-1 | When `packaging_assessment` is omitted, derive it from `package_condition_status` | Preserves the exact behaviour of every existing caller and every stored row |
| A-2 | `photo_documentation_required` defaults to `false` | The gate is inert until an owner explicitly sets it; PDF p.13 "Without this add-on, no photo is taken at any stage" |
| A-3 | A driver "stop" is the set of tasks sharing `assigned_batch_id` | `assigned_batch_id` already exists and is already required for acceptance |
| A-4 | Refusal and not-presented reuse `PickupTaskStatus.FAILED` with an explicit `stop_outcome` discriminator | Avoids widening a status enum that Shipment and the offline replay ranking both depend on |

## Unresolved policy — deliberately not invented

Merchant-application data set · payout procedures per method · high-value declared-value threshold ·
exact customer-facing delivery goal · return-fee waiver · merchant no-show policy · merchant
acknowledgement of a packaging warning · damage evidence rules at pickup · field addition of an
unscheduled parcel · hub handoff exception handling · offline acceptance authorisation · the entire
ADR-0005 finance policy register.

Merchant **Workplace / warehouse operations** (workplace branches, shipment preparation lifecycle,
queue classes) is a legacy surface this platform has never carried: it predates the ported range
and appears in no v6.3 requirement. Legacy `171b7ba` decorates it with handover readiness; only
the Pickup-owned half of that was ported. Building the surface itself is new scope, not a port.

AGENTS.md stop condition honoured: *"Unresolved policy would be silently treated as an approved
decision."*
