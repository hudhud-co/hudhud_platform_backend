# 04 — Gap and Change Matrix

Status values: `IMPLEMENTED_EXACT`, `IMPLEMENTED_DIFFERENT`, `PARTIAL`, `MISSING`, `CONFLICT`,
`OBSOLETE`, `UI_ONLY`, `BLOCKED_BY_EXTERNAL_DEPENDENCY`, `UNKNOWN`.

Nothing is marked implemented because a similarly named endpoint exists — every `IMPLEMENTED_*`
row names the guard, persistence and test that back it.

## Summary

| Status | Count |
|--------|-------|
| `IMPLEMENTED_EXACT` | 21 |
| `PARTIAL` | 7 |
| `MISSING` (implemented in this wave) | 9 |
| `BLOCKED_BY_EXTERNAL_DEPENDENCY` | 96 |
| `UI_ONLY` | 4 |
| `CONFLICT` | 3 |
| **Total requirements** | **140** |

The single dominant fact: **17 of the 21 bounded contexts this product needs have no service in
this repository**, and their owners are `undecided`, `policy_blocked`, `pending_legacy_audit`,
`pending_identity_adr` or `not_started` (`architecture/service-boundaries.yaml`). Implementation
was therefore carried out where ownership is decided — `pickup` and `shipment` — and every other
requirement is blocked with its exact governing reason rather than invented.

---

## Section 1 — Implemented in this wave (pickup context, owner decided)

All rows below are implemented, migrated and tested. Evidence: 77 new tests, pickup suite
259 → 336; migration `w19c_pickup_stop_outcomes_001` applied on disposable PostgreSQL 16.

| Requirement | Role | Product evidence | Current code evidence (before) | Status | Owning service | Required change | API impact | DB impact | Event impact | Tests | Risk |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DRV-P06 | Pickup driver | DRV:`scanner` 8 outcomes; `scanEx:*` copy | `task_lifecycle_service.scan()` only compares the identifier to a previously stored one; no classification | **MISSING** | pickup | Add `ScanResolution` domain classification over the driver's batch: VALID · UNKNOWN_LABEL · WRONG_MERCHANT · NOT_IN_THIS_PICKUP · ALREADY_ACCEPTED · CANCELLED_SHIPMENT · DUPLICATE_SCAN · UNREADABLE | new `POST /pickup/batches/{batch_id}/scan-resolution` | none (read-only over `pickup_tasks`) | none | unit + API | Low — read-only resolver |
| DRV-P07 | Pickup driver | DRV:`scanEx:pickup` "Add to this pickup" disabled | n/a | **UI_ONLY** | pickup | Resolver returns `NOT_IN_THIS_PICKUP` and explicitly refuses field addition | — | — | — | unit | None |
| DRV-P08–P12 | Pickup driver | DRV:`condition` 4 outcomes + hints; PDF p.16 | `PackageConditionStatus` = GOOD/MINOR_DAMAGE/MAJOR_DAMAGE/REPACKAGED; no accept-mode, no refuse-only rule | **PARTIAL** | pickup | Add `PackagingAssessment` (GOOD · BORDERLINE · TOO_WEAK · PRE_EXISTING_DAMAGE) and `ConditionDecision` (ACCEPT · ACCEPT_WITH_WARNING · ACCEPT_WITH_NOTE · REFUSE) with the permitted-decision table; **TOO_WEAK ⇒ REFUSE only** | `POST /tasks/{id}/condition-proof` gains `packaging_assessment` + `decision` | 3 columns on `pickup_tasks` | none | unit + API | Medium — new invariant on an existing endpoint; additive/optional |
| DRV-P13 | Pickup driver | DRV:`photo`,`review` disabled reason | acceptance requires `PROOF_CAPTURED` but never a photo | **MISSING** | pickup | `photo_documentation_required` on the task; acceptance refuses without `media_refs` | acceptance 409 `pickup_photo_documentation_missing` | 1 column | none | unit + API | Medium — fail-closed only when the flag is set |
| DRV-P14 | Pickup driver | DRV:`refuse`,`notAccepted` | task-level `exception`/`fail` only | **PARTIAL** | pickup | Parcel-level refusal with reasons TOO_WEAK_PACKAGING · DAMAGED_BEFORE_PICKUP · DOES_NOT_MATCH_SHIPMENT, terminal, **no custody event** | new `POST /tasks/{id}/refuse` | reuses `FAILED` + 2 columns | none | unit + API | Low |
| DRV-P15 | Pickup driver | DRV:`progress` not-presented sheet | no such outcome | **MISSING** | pickup | `NOT_PRESENTED` stop outcome — terminal, no custody, no penalty | new `POST /tasks/{id}/not-presented` | reuses `FAILED` + column | none | unit + API | Low |
| DRV-P17 | Pickup driver | DRV:`progress` disabled reason | no stop aggregate | **MISSING** | pickup | Stop readiness over `assigned_batch_id`: unresolved count blocks completion | new `GET /pickup/batches/{batch_id}/stop` | index on `(assigned_batch_id)` | none | unit + API | Low |
| DRV-P19 | Pickup driver | DRV:`connLost`→`checking`→`recovered`/`safeRetry` | acceptance is idempotent but unqueryable | **MISSING** | pickup | Read-only acceptance-status query returning RECORDED / NOT_RECORDED | new `GET /tasks/{id}/acceptance` | none | none | unit + API | Low |
| DRV-P16, DRV-P18 | Pickup driver | DRV:`review`,`accepted`,`stop:cancelled` | `_validate_prerequisites` rejects terminal + already-accepted; `ACTIVE_PICKUP_TASK_STATUSES` excludes CANCELLED | **IMPLEMENTED_EXACT** | pickup | none | — | — | — | existing | — |

## Section 2 — Already correct (verified, not assumed)

| Requirement | Status | Evidence in code |
|---|---|---|
| DRV-P01 pickup for merchants only | `IMPLEMENTED_EXACT` | `PickupTask` is created only for merchant shipments; no customer-drop path exists in pickup |
| DRV-P03 stop lifecycle, arrival neutral | `IMPLEMENTED_EXACT` | `PickupTaskStatus.ARRIVED` documented "coordination and identity evidence only — neither starts custody, SLA, or acceptance" |
| DRV-P05 scan an existing label only | `IMPLEMENTED_EXACT` | `scan()` takes `scanned_identifier`; no label-issuing endpoint exists |
| DRV-P16 acceptance is the one custody event, once | `IMPLEMENTED_EXACT` | `AcceptanceIdempotencyRecord` + command fingerprint + `PickupTaskAlreadyAccepted` |
| DRV-P18 cancelled pickup blocks everything | `IMPLEMENTED_EXACT` | `is_terminal` guard + `ACTIVE_PICKUP_TASK_STATUSES` |
| DRV-P21 driver inventory in custody | `IMPLEMENTED_EXACT` | `collect_driver_workload` + `_still_in_driver_custody` |
| DRV-P22 hub handoff parcel-by-parcel, never overridden | `IMPLEMENTED_EXACT` | `HubHandoverService`; unscanned items never release custody |
| DRV-P23 handoff issue keeps custody | `IMPLEMENTED_EXACT` | `MISSING_FROM_DRIVER` excluded from the release contract |
| DRV-P24 pickup history | `IMPLEMENTED_EXACT` | `TaskHistoryEntry`, `GET /tasks/{id}/history` |
| DRV-P26 offline queue, nothing lost or duplicated | `IMPLEMENTED_EXACT` | `OfflineSyncService` append-only replay, `OPERATION_ID_REUSE`, resumable `SEQUENCE_GAP` |
| SHP-03 custody begins at the acceptance scan | `IMPLEMENTED_EXACT` | `pickup.fact.accepted` → Shipment custody `PICKUP_DRIVER` |
| SHP-09 custody transfers on a receiving scan | `IMPLEMENTED_EXACT` (origin hub half) | `pickup.fact.handover_completed` → `ORIGIN_HUB` |
| SEC-06 driver never sees receiver contact/codes/payments | `IMPLEMENTED_EXACT` | no such field exists on any pickup task, schema or response |
| SEC-09 proven actor identity | `IMPLEMENTED_EXACT` | `PickupActor`, no request model carries actor identity; `record_history` stores actor |
| DRV-A-sync | `IMPLEMENTED_EXACT` | offline authorization is signed, device-bound, time-boxed |
| Sender handover ceremony (CUS `courierScan`/`rvVerify`) | `IMPLEMENTED_EXACT` | `CourierHandoverVerificationService`: assignment-bound single-use challenge, assigned courier can never satisfy the sender half, one ceremony authorises one acceptance |

## Section 3 — Conflicts

| # | Conflict | Sources | Resolution |
|---|----------|---------|-----------|
| C-1 | **Delivery code length**: 4 digits vs 6 digits | CUS:`parcelTransit` "Give this 4-digit code"; DRV:`lmOtp` 6 boxes + `otp: '482913'`; CUS `rvVals` 6 cells | Unresolved — recorded, not decided. Both are app-layer; the owning context (`delivery`) has no service, so nothing is implemented either way. |
| C-2 | **ID-fallback evidence retention**: PDF requires photographing the ID card; the Driver App states the opposite | PDF p.26 "takes a photo of the ID card as a record"; DRV:`lmIdCapture` "The ID is checked, **not stored as a photo**" | PDF (business invariant) outranks app behaviour, but this is a privacy-relevant retention decision with an undecided `media_proof` owner. Recorded as blocked, not implemented. |
| C-3 | **Who pays the delivery fee, and when** | PDF p.13 "who pays the delivery fee" is a per-sender setting; CUS:`send5`/DRV:`fee` make it sender-paid in cash at pickup | Finance policy — ADR-0005 `Implementation allowed: no`. Recorded, not resolved. |

## Section 4 — Blocked by external dependency (96 requirements)

Every row below is blocked by a **repository-governing** fact, not by effort. Grouped by cause.

### 4.1 No identity/customer ownership decision — ADR-0004 `proposed`; `customer.extraction_status: policy_blocked`; `auth_identity.extraction_status: pending_identity_adr`

SEC-01, SEC-02, SEC-03, SEC-04, SEC-05, CUS-15, DRV-A01, DRV-A02, DRV-A03, DRV-A04, MER-20.

Also: the pickup service's own `driver_command_authorization_adapter` stays `deferred_default_deny`
for the same reason, and `architecture/ownership-matrix.yaml` lists
`driver_identity_role_or_permission_storage` and `cross_capability_driver_attendance` as
**forbidden** for pickup.

### 4.2 Finance policy blocked — ADR-0005 `Proposed — Policy Blocked`, **`Implementation allowed: no`**; `wallet_cod.extraction_status: policy_blocked`; `finance_settlement.transitional_deployable_candidate: policy_blocked`

PAY-01 … PAY-11, DRV-A05, DRV-A06, DRV-A07, DRV-A08, DRV-A09, DRV-A10, DRV-A11, DRV-A12,
DRV-A13, DRV-L12, DRV-L13, DRV-L14, DRV-L15, DRV-L16, MER-14 (fee half), OPS-04, SHP-12.

ADR-0005 states plainly: do **not** implement Finance. Its policy register (P-01…P-17) must not be
invented. `architecture/ownership-matrix.yaml` additionally lists
`driver_cash_custody_or_finance_posting` as **forbidden** for pickup — so even the driver-side cash
custody screens cannot be built here.

### 4.3 No `delivery` service — `delivery.extraction_status: pending_cutover`

DRV-L01 … DRV-L11, DRV-L17 … DRV-L21, CUS-11, CUS-12, CUS-13, SHP-11 (transit half), NTF-07,
OPS-08.

The entire last-mile module (25 screens) belongs to a bounded context that has not been extracted.
Building it inside pickup would violate the ownership matrix and ADR-0003.

### 4.4 No `hub` / `linehaul` service — `pending_legacy_audit`

CUS-04, CUS-05, CUS-06, CUS-07, SHP-05, SHP-06, SHP-07, SHP-08, SHP-13, OPS-01, OPS-02, OPS-05, OPS-10.

Note CUS-03/CUS-05: the regular-customer hub drop-off path — the second way custody can begin —
is a **hub** capability. Pickup must not implement it (`pickup` owns driver pickup only), and no
hub service exists.

### 4.5 No `order` / `send_parcel` / `merchant_store` / `address_book` / `pricing_quote` / `serviceability` service — all `pending_legacy_audit`, owner `undecided`

CUS-01, CUS-02, CUS-08, CUS-09, CUS-10, CUS-14, MER-01, MER-03 … MER-19, MER-21, MER-22, MER-23,
SHP-01, SHP-02, SHP-04, SHP-10, SHP-11.

### 4.6 No `support_claims` / `media_proof` service — `pending_legacy_audit`, owner `undecided`

CLM-01 … CLM-08, DRV-P25, CUS-12, OPS-06, OPS-07.

DRV-P25 is the closest to pickup, but an incident is a claim record owned by `support_claims`, and
`media_proof.canonical_writer` is `undecided` with `policy_prerequisites: [evidence_ownership_adr]`.
Pickup already keeps the custody half of this correctly: a parcel with an open issue stays in
driver custody (`MISSING_FROM_DRIVER` never releases custody).

### 4.7 No `notification` service — `pending_legacy_audit`, owner `undecided`

NTF-01 … NTF-10.

### 4.8 No `control_tower` service

OPS-03, OPS-05, OPS-11 (aggregation half).

### 4.9 Product Open Items — must not be invented (PDF Appendix A)

MER-02 (merchant-application data set), PAY-07 (payout procedures), CLM-08 (high-value threshold),
SHP-02 display (exact delivery goal), PAY-08 (return-fee waiver).

### 4.10 App-declared TBDs

DRV-P20 merchant no-show policy · warning acknowledgement by merchant · damage evidence rules at
pickup · DRV-P07 field additions · hub handoff exception handling · offline acceptance
authorisation (already correctly refused — custody acceptance is never authorised offline).
