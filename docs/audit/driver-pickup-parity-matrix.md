# Driver / Pickup parity matrix (legacy `a4f7d95..541f407`)

**Reference:** `hudhud-backend` @ `a4f7d95..541f407` (Driver backend waves 0–3 `88cc6fe`,
Driver offline and finance waves `541f407`), plus Driver/Pickup code paths outside that
range that the range depends on.

**Target:** `hudhud_platform_backend` — Pickup and Shipment bounded contexts.

**Evidence class:** unit + in-process HTTP for behaviour, plus the disposable
PostgreSQL 16 lab for the migrations and schema, plus the existing JetStream labs
re-run as regression. The driver / handover / offline *behaviour* has no
PostgreSQL-backed probe and `pickup.fact.handover_completed` has no JetStream runtime
proof — see *Remaining risks*.

Status key: **Implemented** · **Partial** · **Missing** · **N/A** (intentionally not
applicable in this repository, with the governing reason).

---

## 1. Driver authentication, roles and permissions

| Legacy capability | Status | Where / why |
|---|---|---|
| Driver bootstrap projection (`DriverBootstrapUseCase`) | Partial | Pickup exposes the pickup-capability half as `GET /pickup/work-sessions/current` (availability + operational blockers). Profile, role, and hub-scope projection is **N/A** here. |
| Driver roles, capability role checks, hub scopes | N/A | `auth_identity` is `extraction_status: pending_identity_adar`, `data_ownership.strategy: undecided` (ADR-0004 Proposed). Pickup consumes proven identity through `PickupAuthorizer` and stores **no** identity. |
| Per-command permission enforcement | Implemented | `PickupCommand` enum + `authorize(...)` on every route; `DefaultDenyPickupAuthorizer` is the default, so an unwired deployment refuses everything. |
| Driver profile block / inactive gating | N/A | Same ADR-0004 boundary. The authorizer may refuse; Pickup does not model profile state. |
| Cross-capability attendance mode (SHARED / PICKUP / DELIVERY) | N/A | Requires a Driver Workforce context that does not exist in `architecture/ownership-matrix.yaml`; inventing one is an architecture decision, not an implementation detail. |

## 2. Pickup assignment, acceptance, rejection, reassignment

| Legacy capability | Status | Where |
|---|---|---|
| Assignment offer acknowledge | Implemented | `POST /pickup/tasks/{id}/acknowledge` — requires an ONLINE work session. |
| Assignment decline with reason | Implemented | `POST /pickup/tasks/{id}/decline`; `AssignmentDeclineReason`. |
| Reassign / retry / reschedule / cancel recovery | Implemented (pre-existing) | `PickupRecoveryService`; replacement attempts start `OFFERED` so the new driver must re-acknowledge. |
| Custody-starting acceptance | Implemented | `POST /pickup/tasks/{id}/accept` → `PickupAcceptanceService` + transactional outbox `pickup.fact.accepted`. |
| Acceptance gated on the sender ceremony | Implemented | `AcceptanceVerificationGate`; fail-closed — with verification required and no gate composed, acceptance is not served at all. |
| Duplicate-prevention on acceptance | Implemented | `Idempotency-Key` + command fingerprint + outbox `(event_type, aggregate_id)` uniqueness. |

## 3. Driver availability and task lifecycle

| Legacy capability | Status | Where |
|---|---|---|
| Start / pause / resume / end work session | Implemented | `DriverWorkSessionService`, `/pickup/work-sessions/*`. |
| Availability derived, never client-asserted | Implemented | `derive_availability(status)`. |
| One open session per driver | Implemented | Partial unique index `uq_pickup_work_session_open_driver` + `SELECT … FOR UPDATE`. |
| End-shift blockers (open custody, active manifest, active task) | Implemented | `collect_driver_workload` / `DriverWorkload.end_blockers()`. |
| Unsynced offline work blocks release | Implemented | `WorkSessionBlocker.UNSYNCED_OFFLINE_WORK`. |
| Arrive → scan → condition proof | Implemented | Forward-only `PICKUP_TASK_PROGRESSION`; repeats are idempotent replays. |
| Exception report with per-reason evidence | Implemented | `PickupExceptionReason` + `_assert_exception_context`. |
| Fail attempt (recoverable) | Implemented | `FAILED` is terminal for the attempt, still recoverable by `retry`. |
| Operations review / resolution workflow for exceptions | Missing (deferred) | Driver-owned factual report and audit history are implemented; the Operations review decision workflow is not. See *Excluded*. |
| Pickup batch lifecycle (start / pause / resume / complete / decline) | Missing (deferred) | Batching is an assignment-planning concern; `assigned_batch_id` is carried, the batch aggregate is not. |

## 4. Pickup routes, stops and shipment transitions

| Legacy capability | Status | Where |
|---|---|---|
| Shipment custody starts at acceptance | Implemented (pre-existing) | Shipment applies `pickup.fact.accepted` → `IN_CUSTODY` / `PICKUP_DRIVER`. |
| Shipment custody released at hub | Implemented | `pickup.fact.handover_completed` → `PickupHandoverCustodyApplyService` → `ORIGIN_HUB`. |
| Pickup never mutates Shipment | Implemented | Outbox only; enforced by `tests/test_boundaries.py`. |
| Route / stop sequencing | N/A | No route-planning aggregate exists in either repository's Pickup context for this range. |

## 5. Workplace / store handover

| Legacy capability | Status | Where |
|---|---|---|
| Create hub handover manifest from held parcels | Implemented | `POST /pickup/handover-manifests`; refuses parcels not in accepted driver custody and parcels already on an active manifest. |
| Driver arrival at hub (coordination only) | Implemented | `…/arrive` — never releases custody by itself. |
| Cancel manifest before any hub receipt | Implemented | `…/cancel`; refused once any item released custody. |
| Hub inbound receipt per parcel | Implemented | `…/receipts`, hub-scoped actor only; driver self-receipt refused. |
| Manifest completion / completion with discrepancies | Implemented | `_maybe_complete` and `close_manifest`. |
| Listed-but-missing parcel stays with the driver | Implemented | `MISSING` item releases nothing and keeps `OPEN_PICKUP_CUSTODY`. |
| Received at wrong hub → actual hub | Implemented | `_resolve_discrepancy` forces `WRONG_HUB_RECEIVED`. |
| Store/warehouse preparation and workplace-owner surfaces | N/A | `merchant_store` context is not extracted in this repository. |

## 6. Handover verification and challenges

| Legacy capability | Status | Where |
|---|---|---|
| Issue assignment-bound dynamic challenge | Implemented | `POST /pickup/tasks/{id}/courier-challenge`; secret returned once, only the keyed hash persisted. |
| Reissue supersedes the open challenge | Implemented | `VerificationInvalidationReason.REISSUED`. |
| Reissue blocked after sender verification | Implemented | `CourierChallengeIssueBlocked`. |
| Sender verification, constant-time | Implemented | `hmac.compare_digest`. |
| Courier cannot self-verify | Implemented | `SenderMayNotVerifyOwnCourier` (403). |
| Failed-attempt lockout, expiry persisted on rejection | Implemented | Counter and `EXPIRED` status commit even though the request is refused. |
| Reassignment invalidates challenge + manifest | Implemented | `invalidate_for_task`. |
| Manifest submit / confirm with digest binding | Implemented | `build_manifest_digest(shipment_id, scanned_identifier)`. |
| One ceremony authorises exactly one acceptance | Implemented | `consume_for_acceptance` burns both halves. |
| Merchant-vs-customer-direct sender resolution | Partial | Sender type is recorded from the proven actor role; merchant membership resolution belongs to `merchant_store` / `auth_identity`. |

## 7. Barcode, OTP and evidence validation

| Legacy capability | Status | Where |
|---|---|---|
| Scanned identifier capture and rescan protection | Implemented | `ScanTaskCommand`; a different identifier is refused. |
| Scanned identifier verified against waybill identity | Implemented (pre-existing) | Shipment `_scanned_identifier_matches` on fact apply. |
| Condition proof with condition status and evidence rule | Implemented | Damage requires notes or evidence. |
| Exception evidence minimums | Implemented | `CONTACT_ATTEMPT_REQUIRED_REASONS`, `EVIDENCE_REQUIRED_REASONS`. |
| Evidence as external media references, never inline bytes | Implemented | `EvidenceMediaRef` → envelope `media_refs`; payload schema forbids `evidence_bytes`/`storage_uri`. |
| Delivery OTP / customer confirmation | N/A | Delivery context (`extraction_status: pending_cutover`); no delivery service exists. |
| Evidence upload intent / storage lifecycle | N/A | `media_proof` context `proposed_platform_owner: undecided`. |

## 8. Offline command queue, replay and synchronization

| Legacy capability | Status | Where |
|---|---|---|
| Signed, device-bound, time-boxed authorization | Implemented | `POST /pickup/offline/authorizations`; HMAC token, device id hashed. |
| Revoke authorization | Implemented | `…/revoke`. |
| Sync deadline and batch size limit | Implemented | `OfflineSyncDeadlinePassed`, `OfflineBatchTooLarge`. |
| Append-only preserved capture log | Implemented | Every submission stored, applied or not. |
| Contiguous sequencing, sequence gap → case | Implemented | `SEQUENCE_GAP`; batch continues. |
| Resumable sequence gap | Implemented | Case resolves `ORDER_RESTORED_AND_APPLIED`. |
| `operation_id` idempotency; reuse with new content preserved | Implemented | `OPERATION_ID_REUSE` + `conflicting_replays`. |
| Sequence reuse rejected | Implemented | `SEQUENCE_REUSE`. |
| Payload fingerprint verification | Implemented | `offline_event_fingerprint`. |
| Capture-time clock-skew window | Implemented | ±5 minutes; before-authorization and expiry cases. |
| Stale assignment revision / no longer owned | Implemented | Revision covers assignment identity only, so a whole batch replays under one download. |
| Custody acceptance never applied offline | Implemented | Excluded from issuable operations *and* from `DEFERRED_OFFLINE_OPERATIONS`. |
| Replay through the owning workflow, not direct writes | Implemented | `OfflineOperationDispatcher` → `PickupTaskLifecycleService.for_replay()`. |
| One failed capture does not discard the batch | Implemented | `unit_of_work.savepoint()` per command. |
| Operations reconciliation case read / resolve | Implemented | `GET`/`POST /pickup/offline/reconciliation-cases…`, OPERATIONS role only. |
| Offline authorization for pickup **batches** and **delivery tasks** | N/A | No batch aggregate here; delivery context not extracted. |

## 9. Duplicate prevention, idempotency, concurrency

| Legacy capability | Status | Where |
|---|---|---|
| Command idempotency keys | Implemented | Recovery and acceptance idempotency tables. |
| State-based idempotent replay | Implemented | Every driver command returns `idempotent_replay` instead of erroring. |
| Optimistic version on the pickup task | Implemented (pre-existing) | `StalePickupTaskVersion`. |
| Row locking on contended aggregates | Implemented | `FOR UPDATE` on work session, challenge, courier manifest, handover manifest, offline stream, reconciliation case. |
| Database-level single-open constraints | Implemented | Partial unique indexes for work session, challenge, courier manifest, open manifest line, open reconciliation case. |
| Exactly-one integration fact per aggregate | Implemented | Outbox `(event_type, aggregate_id)` and `(aggregate_id, aggregate_version)` uniqueness. |
| Inbox convergence on redelivery | Implemented | `(consumer_name, event_id)` inbox plus a unique `pickup_task_id` custody transfer. |

## 10. Audit history and actor identity

| Legacy capability | Status | Where |
|---|---|---|
| Append-only history per task with actor and role | Implemented | `pickup_task_history`; `GET /pickup/tasks/{id}/history`. |
| Actor identity never from request body or header | Implemented | Actor comes only from the authorization decision. |
| Sanitised audit metadata | Implemented | `_sanitize_details` + `sanitize_error_message`. |
| Shipment-side audit for custody changes | Implemented | `SHIPMENT_HUB_HANDOVER_RECEIPT` audit entry and `HUB_HANDOVER_RECEIPT` timeline event. |
| Central audit service ingestion of driver events | Missing (deferred) | `audit` consumes `legacy_bridge.observation.audit_entry` only; a native Pickup audit fact is not registered (ADR-0009). |

## 11. COD, cash custody, finance and reconciliation

| Legacy capability | Status | Where / why |
|---|---|---|
| Driver COD cash liability ledger | N/A | **ADR-0005 `Proposed — Policy Blocked`, `Implementation allowed: no`.** |
| Cash remittance submit / review / approve / reverse | N/A | Same. |
| Driver earnings, compensation rate plans, payouts | N/A | Same, plus unresolved policy P-01…P-17 (commission, settlement frequency, payout channels). |
| Finance reconciliation cases (shortage / overage / beneficiary) | N/A | Same. |
| Merchant wallet credit on COD | N/A | `wallet_cod` `extraction_status: policy_blocked`. |
| Compensable event on hub receipt (`PICKUP_HUB_RECEIPT`) | N/A | Legacy calls Driver Finance from the pickup inbound scan. Reproducing that call here would require the blocked Finance context; the *operational* fact it is derived from (`pickup.fact.handover_completed`) is published, so Finance can consume it once ADR-0005 is decided. |

---

## Excluded scope and the exact reason

| Excluded | Reason |
|---|---|
| Driver cash custody, COD ledger, remittance, earnings, payouts, finance reconciliation | ADR-0005 is `Proposed — Policy Blocked` with `Implementation allowed: no`, and states *"Do **not** implement Finance in this ADR"*. Its unresolved policy register (commission, settlement frequency, payout channels, reversal authority) must not be invented. `AGENTS.md` stop condition: *"Unresolved policy would be silently treated as an approved decision."* |
| Driver identity, roles, permissions, profile, hub-scope storage | ADR-0004 is `Proposed`; `auth_identity.data_ownership.strategy` is `undecided`. Storing identity in Pickup would create a second writer for an undecided context. |
| Delivery-capability attendance, delivery task lifecycle, delivery reattempts, delivery COD collection | `delivery` context `extraction_status: pending_cutover`; no delivery service exists in this repository. Implementing it inside Pickup would violate `AGENTS.md` *"Do not implement adjacent services … as a side effect"*. |
| Cross-capability (SHARED) driver attendance | Requires a Driver Workforce bounded context that is not declared in `architecture/ownership-matrix.yaml`. Adding a bounded context is an ADR decision. |
| Driver notifications and push delivery | `notification` context `proposed_platform_owner: undecided`. |
| Evidence file upload / storage lifecycle | `media_proof` context `proposed_platform_owner: undecided`; Pickup carries external media references only. |
| Pickup batch aggregate and batch lifecycle commands | Assignment-planning concern not required by any custody or parity invariant in this range; `assigned_batch_id` is preserved for when the batch owner is decided. |
| Operations exception review/resolution workflow | Driver-side factual report, evidence minimums, and audit history are implemented. The Operations review decision surface is a control-tower concern (`control_tower` `canonical_writer: none`). |
| Live JetStream topology for the handover durable consumer | ADR-0010 is `Proposed` with `Implementation allowed: no`; the durable binding is now parameterised in code, but server configuration, credentials, and ACL proof remain gated. |

---

## Verification

| Gate | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run python scripts/quality/verify_boundaries.py` | Architecture boundary verification passed |
| `uv run python scripts/quality/verify_agent_governance.py` | Agent governance verification passed |
| `services/pickup` — `uv run pytest` | 259 passed (was 98) |
| `services/shipment` — `uv run pytest` | 146 passed (was 127) |
| root `tests/architecture tests/governance tests/contracts` | 101 passed (was 84) |
| root `uv run pytest -m "not integration"` | 289 passed, 162 deselected |
| `tests/service_postgres_proof` + `tests/pickup_acceptance_eventing_proof` | 60 passed, 1 pre-existing failure |
| `tests/nats_security_proof` | 51 passed |
| `services/audit`, `services/legacy_event_bridge`, `services/tracking` | 81 / 59 / 129 passed — unchanged by this work |

### Pre-existing failure, not caused by this work

`tests/service_postgres_proof/test_shipment_integration.py::test_shipment_http_acceptance_against_postgres`
fails with `staging startup blocked — unset gates: acceptance_ingestion_mode`.

The staging app build inside `tests/service_postgres_proof/probes/shipment_http.py` was
last updated in `dbf873b`; the `acceptance_ingestion_mode` staging gate it trips was
introduced later by `70597ca`, and the probe never sets the mode. This wave does not
touch `ShipmentSettings.assert_production_gates`. Fixing the probe belongs to the
eventing cutover scope.

### Docker lab contention

Running the whole root suite in one process leaves several NATS labs failing on port
and container contention. `tests/nats_security_proof` passes 51/51 when run alone,
including `test_w18_pickup_shipment_positive_negative_tls_rotation`, which exercises
both services changed here.

---

## Remaining risks

1. **No PostgreSQL-backed behaviour probe for the new paths.** Migrations, schema,
   constraints, single head, idempotent upgrade, and restart persistence are proven on
   disposable PostgreSQL 16. The driver, handover, and offline *services* are proven
   against the in-memory unit of work and in-process HTTP. Row-locking behaviour under
   real concurrent transactions (`FOR UPDATE` on work session, challenge, handover
   manifest, offline stream) is therefore asserted by construction, not observed.
2. **`pickup.fact.handover_completed` has no JetStream runtime proof.** The producer
   writes to the proven outbox and the relay is subject-agnostic, but no lab has carried
   this subject end to end, and the `shipment_pickup_handover_facts_v1` durable is not
   provisioned (ADR-0010 gated).
3. **Driver command authorization is default-deny.** Every new route refuses until a
   production `PickupAuthorizer` exists; readiness reports
   `driver_command_authorization_adapter_not_configured`. The service is safe but not
   usable until ADR-0004 resolves.
4. **The signing key is a single shared secret** with no rotation story. Rotating
   `PICKUP_SIGNING_KEY` invalidates every outstanding challenge and offline
   authorization at once.
5. **Offline replay applies commands under the submitting driver's actor.** History
   records the driver, the operation id, and the capture time, but a driver who keeps a
   device offline past `sync_deadline` loses the ability to replay at all — captures
   then land as reconciliation cases for Operations rather than being applied.
6. **Finance is absent by design.** A driver can hold parcels and release them at a hub
   with no cash-custody accounting anywhere in this repository. That is correct under
   ADR-0005 today, but it means COD parity is genuinely unmet, not merely deferred in
   code.
