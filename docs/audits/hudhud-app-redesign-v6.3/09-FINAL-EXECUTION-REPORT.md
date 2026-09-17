# 09 — Platform Execution Report

> **Scope correction (2026-09-14).** The first pass of this report covered the Pickup
> context only, because 96 of 140 requirements had been classified as blocked on the
> grounds that their bounded context had no service, no accepted ADR or an undecided
> owner. That classification was wrong in kind and has been reversed. See
> `10-PLATFORM-BOUNDED-CONTEXT-MAP.md` for the corrected map and
> **§ Platform build-out status** at the end of this document for what has been built
> since. The Pickup sections below remain accurate and are retained.

# Original W19-C Report (Pickup context)

## Baseline and final state

| Item | Value |
|------|-------|
| Branch | `develop` |
| Baseline HEAD | `573eac12d5f70b829667c00a3f724a3b623ac3eb` |
| Commits made | none — no commit or push was requested |
| Final working tree | 48 modified, 45 untracked. Nothing was reset, reverted or reformatted. |
| Pre-existing user work | The uncommitted Driver/Pickup parity wave (87 entries at start) was preserved and built upon. |
| Legacy repository | Untouched — `git status` empty, HEAD still `9e374d1` |

## Product sources audited

All three were found in `docs/input/` and read in full:

1. `Hudhud_Shipment_Journey_Business_Process_Design_v6.3.pdf` — 46 pages, extracted with `pypdf`
2. `HUDHUD Customer App v3 (standalone) .html` — bundle decoded; 3,901 lines of executable source; 105 screens
3. `HUDHUD Driver App v8 (standalone).html` — bundle decoded; 1,168 lines of executable source; 96 screens, 36 scenarios

No source was missing. Guards, disabled-button reasons, validation and state transitions were read
from the executable `class Component extends DCLogic`, not from rendered text.

## Roles discovered (13)

Regular customer · seller/merchant · store team member (read-only branch staff) · receiver ·
sender at handover · pickup driver · last-mile driver · driver applicant · origin hub staff ·
hub cashier · linehaul driver · operations/support · destination hub staff.

## Requirements by status

140 catalogued requirements (`03-REQUIREMENTS-CATALOG.md`):

| Status | Count |
|--------|-------|
| `IMPLEMENTED_EXACT` (16 verified pre-existing + 5 new) | 21 |
| `PARTIAL` closed in this wave | 7 |
| `MISSING` implemented in this wave | 9 |
| `BLOCKED_BY_EXTERNAL_DEPENDENCY` | 96 |
| `UI_ONLY` | 4 |
| `CONFLICT` | 3 |

## Services changed

`services/pickup` only. `shipment`, `tracking`, `audit` and `legacy_event_bridge` were not
touched and their suites are unchanged.

## APIs added or modified

**Five new endpoints** (Pickup OpenAPI 3.1.0 now has 40 operations, validated):

| Method | Path |
|---|---|
| `POST` | `/pickup/tasks/{pickup_task_id}/refuse` |
| `POST` | `/pickup/tasks/{pickup_task_id}/not-presented` |
| `GET` | `/pickup/tasks/{pickup_task_id}/acceptance` |
| `POST` | `/pickup/batches/{assigned_batch_id}/scan-resolution` |
| `GET` | `/pickup/batches/{assigned_batch_id}/stop` |

**One endpoint extended, backward compatibly**: `POST /pickup/tasks/{id}/condition-proof` gains
optional `packaging_assessment` and `decision`. **One guard added**: `POST /tasks/{id}/accept`
refuses with `409 pickup_photo_documentation_missing` when the task requires photo documentation
and no media refs are supplied.

Six new error codes: `pickup_packaging_decision_not_permitted`,
`pickup_photo_documentation_missing`, `pickup_stop_outcome_already_recorded`,
`pickup_stop_outcome_not_allowed`, `pickup_batch_not_found`, `scan_identifier_missing`.

## Database migrations

`w19c_pickup_stop_outcomes_001` (down_revision `w19a_pickup_driver_wave_001`) — expand-only:
five nullable columns (`packaging_assessment`, `condition_decision`, `stop_outcome`,
`stop_outcome_reason`, `stop_outcome_at`), one `NOT NULL DEFAULT false` column
(`photo_documentation_required`), and index `ix_pickup_tasks_batch_outcome`. No backfill, no
`alter_column`, full downgrade. Applied successfully against disposable PostgreSQL 16.

## Events

**None added or modified.** Refusal and not-presented publish nothing by design — the product
states no custody event is recorded for them, and a test asserts the outbox stays empty. This
avoids inventing a contract ADR-0009 has not accepted.

## Security changes

- Five new authorization commands, every route `require_role=PICKUP_DRIVER` through the
  service-owned `PickupAuthorizer`; a merchant token gets 403, a missing token 401.
- Read paths derive the driver from `actor.actor_id`, never from the request body or path, so a
  driver cannot read another driver's stop, scan resolution or acceptance status. Cross-driver
  reads answer 404, not 403, so they leak no existence information.
- Acceptance photo gate is fail-closed for shipments that carry the add-on.
- `TOO_WEAK` packaging cannot be accepted by any code path.
- Scan resolution is read-only — an unregistered label can never become a shipment in the field,
  and an unreadable label is never resolved to a task.
- Refusal notes pass through the existing history sanitizer; no new PII field was introduced.

## Tests added

**77 new tests**, pickup suite **259 → 336**:

- `tests/test_stop_outcomes.py` — 49 (packaging decision table, refusal, not-presented, scan
  resolution, stop readiness, acceptance status, photo gate, concurrency, retry counting)
- `tests/test_stop_api.py` — 26 (HTTP contract, authorization, error mapping, surface shape)
- `tests/test_postgres_store_static.py` — 2 (every model column has a migration; the migration is
  expand-only)

Coverage per workflow includes happy path, invalid transition, repeated request, concurrent
request, unauthorized actor, wrong role and missing prerequisite.

## Exact validation commands and results

```
uv run ruff check .                                        → All checks passed!
uv run python scripts/quality/verify_boundaries.py         → Architecture boundary verification passed.
uv run python scripts/quality/verify_agent_governance.py   → Agent governance verification passed.
(cd services/pickup && uv run pytest)                      → 336 passed   (baseline 259)
(cd services/shipment && uv run pytest)                    → 146 passed   (unchanged)
(cd services/tracking && uv run pytest)                    → 129 passed   (unchanged)
(cd services/audit && uv run pytest)                       →  81 passed   (unchanged)
(cd services/legacy_event_bridge && uv run pytest)         →  59 passed   (unchanged)
uv run pytest tests/architecture tests/governance tests/contracts → 101 passed (unchanged)
uv run pytest tests/service_postgres_proof                 →  45 passed, 1 pre-existing failure
uv run pytest tests/pickup_acceptance_eventing_proof       →  15 passed
uv run pytest tests/nats_security_proof                    →  51 passed
OpenAPI 3.1.0 generated and validated                      →  40 paths / 40 operations
```

Total: **852 passing**, up from 775. One failure, pre-existing and unrelated:
`test_shipment_http_acceptance_against_postgres` — "staging startup blocked — unset gates:
acceptance_ingestion_mode". Its probe was last updated in `dbf873b`; the gate it trips was added
later by `70597ca`. This work does not touch `ShipmentSettings.assert_production_gates`.

## Defects this wave's own tests caught and fixed

| # | Defect | Caught by |
|---|--------|-----------|
| 1 | The six new columns were absent from the SQLAlchemy row mapper, so they would have silently vanished on write | `test_pickup_task_mapping_covers_every_entity_field` (the guard added in the previous wave) |
| 2 | A retried parcel was counted twice at the stop, because the superseded attempt stayed in the expected set — one physical parcel would have shown as two, and the stop could never complete | `test_a_retried_parcel_is_counted_once_not_twice` (written during the review pass) |
| 3 | Three lab constant files and one metadata test pinned the old migration head | `test_single_head_migration_chain` |

## Remaining conflicts

C-1 delivery-code length (4 vs 6 digits) · C-2 ID-fallback photo retention (PDF vs Driver App) ·
C-3 delivery-fee ownership and timing. All three sit in contexts with no service and no decided
owner; details and reasoning in `08-DECISIONS-CONFLICTS-ASSUMPTIONS.md`.

## External blockers

| Blocker | Governing record | Requirements blocked |
|---|---|---|
| Identity/customer ownership undecided | ADR-0004 `proposed`; `customer` `policy_blocked`; `auth_identity` `pending_identity_adr` | 11 |
| Finance policy blocked | ADR-0005 `Proposed — Policy Blocked`, **`Implementation allowed: no`** | 26 |
| No `delivery` service | `delivery.extraction_status: pending_cutover` | 21 |
| No `hub` / `linehaul` service | `pending_legacy_audit` | 13 |
| No `order` / `send_parcel` / `merchant_store` / `address_book` / `pricing_quote` / `serviceability` service | `pending_legacy_audit`, owner `undecided` | 25+ |
| No `support_claims` / `media_proof` service | `pending_legacy_audit`, owner `undecided` | 11 |
| No `notification` service | `pending_legacy_audit`, owner `undecided` | 10 |
| Product Open Items (PDF Appendix A) | merchant-application data set, payout procedures, high-value threshold, delivery-goal display, return-fee waiver | 5 |

## Deferred work, with reason

| Deferred | Reason |
|---|---|
| `WRONG_MERCHANT` scan outcome | Separating it from `NOT_IN_THIS_PICKUP` needs merchant identity, owned by `merchant_store` (owner `undecided`). Both outcomes govern the same driver action: do not accept it here. |
| Offline capture of refusal / not-presented | Would widen the offline authorization scope, a security surface. The product's own sync queue shows only acceptance and photo upload queued. No evidence to widen it. |
| Driver incident reports (DRV-P25) | An incident is a claim record owned by `support_claims`; `media_proof` needs `evidence_ownership_adr`. Pickup already keeps the custody half correct — a flagged parcel never leaves driver custody. |
| Merchant no-show policy (DRV-P20) | The Driver App itself marks it "not defined yet". |
| Field addition of an unscheduled parcel (DRV-P07) | The Driver App disables the button: "field additions are not defined yet". |

## Known risks

1. New behaviour is proven on the in-memory unit of work and through the in-process HTTP API;
   schema is proven on disposable PostgreSQL. Real concurrent `FOR UPDATE` contention is asserted
   by construction, not observed.
2. `photo_documentation_required` has no producer yet — no service owns the merchant's photo
   add-on, so the gate is correct but dormant until `order`/`merchant_store` exist.
3. Driver command authorization remains default-deny (ADR-0004), so the new routes are safe but
   unusable in production until identity resolves. This is unchanged by this wave.
4. The stop aggregate is derived from `assigned_batch_id` rather than stored. It is correct for
   the current model but will need revisiting if a stop ever spans batches.
5. `pickup.runtime_evidence.production_ready` remains `false`. Nothing here is production-ready.

## Mobile integration notes

No breaking change. Every new request field is optional and every new endpoint additive; no
response field was removed or retyped. A client that ignores this wave entirely keeps working.
To adopt it: send `packaging_assessment` + `decision` on `condition-proof` and handle
`409 pickup_packaging_decision_not_permitted`; call `scan-resolution` before progressing a scan;
call `GET .../acceptance` instead of retrying after a connection drop; use `refuse` /
`not-presented` for parcels that do not enter custody; gate the "Complete stop" button on
`GET .../stop` → `can_complete`.

## Rollout and rollback

- **Rollout**: apply `w19c_pickup_stop_outcomes_001` (expand-only, no backfill, no lock-heavy
  operation), then deploy. The new behaviour is inert until clients send the new fields, except
  the photo gate, which is inert until a task sets `photo_documentation_required`.
- **Rollback**: deploy the previous image, then `alembic downgrade w19a_pickup_driver_wave_001`.
  The downgrade drops only the six new columns and one index. Any stop outcome recorded in the
  meantime is lost, but no custody, acceptance or event state is affected, because refusal and
  not-presented never start custody and never publish.

## Requirement-to-code traceability

| Requirement | Code | Test |
|---|---|---|
| DRV-P06, DRV-P07 | `application/stop_service.py::resolve_scan`; `ScanResolutionOutcome` | `test_stop_outcomes.py` scan-resolution block; `test_stop_api.py` |
| DRV-P08–P12 | `value_objects.PERMITTED_CONDITION_DECISIONS`; `task_lifecycle_service._resolve_decision` | `test_packaging_too_weak_to_survive_transport_can_only_be_refused` (+7) |
| DRV-P13 | `acceptance_service._apply_acceptance` photo gate; `PickupTask.photo_documentation_required` | `test_acceptance_is_blocked_until_the_required_photo_is_attached` (+4) |
| DRV-P14 | `task_lifecycle_service.refuse`; `PickupRefusalReason` | `test_a_refused_parcel_stays_with_the_merchant_and_starts_no_custody` (+5) |
| DRV-P15 | `task_lifecycle_service.mark_not_presented` | `test_a_parcel_the_merchant_never_handed_over_is_marked_not_presented` (+2) |
| DRV-P17 | `stop_service.stop_readiness`; `PickupTask.is_stop_resolved` | `test_a_stop_cannot_complete_while_a_parcel_is_unresolved` (+6) |
| DRV-P19 | `stop_service.acceptance_status` | `test_an_unaccepted_task_reports_safe_to_retry` (+4) |
| DRV-P16, DRV-P18 | `acceptance_service._validate_prerequisites` (pre-existing) | `test_acceptance_outbox.py` |


---

# Platform build-out status (W20)

## Governance corrected

| Record | Before | After |
|--------|--------|-------|
| ADR-0004 identity | `proposed` | **Accepted** — Identity owns principals/credentials/role grants; domain services own their own membership |
| ADR-0005 finance | `Proposed — Policy Blocked`, `Implementation allowed: no` | **superseded by ADR-0012** |
| ADR-0011 topology | did not exist | **Accepted** — 13-service map, every context owned |
| ADR-0012 finance | did not exist | **Accepted** — v6.3 Confirmed decisions supply the missing policy; double-entry ledger, cash custody, exchange-office settlement, payout request |
| `ownership-matrix.yaml` | 5 contexts `undecided`, 2 `policy_blocked` | every context has a named canonical writer and service |
| `service-boundaries.yaml` | `not_started` / `pending_legacy_audit` / `policy_blocked` | every product context `in_progress` with `dedicated_database` |

Architecture tests were re-pointed at the new invariants and **strengthened**, not weakened
— three were added: every product context has a named owner; Identity stores no domain
membership; Delivery never writes the wallet.

## Services built

### `identity` (W20-A) — 76 tests

OTP sign-in with E.164 normalisation, keyed-hash storage of every secret, per-phone rate
limiting, attempt limits, single-use codes, invalidation of a previous live code, opaque
server-revocable bearer sessions, optional device binding, role grants with scope rules,
and service-credential-gated token introspection. Migration `w20a_identity_core_001` with
partial unique indexes for one live code per phone and one live grant per scope.

Security properties fixed by test: the code, phone, token and device id never exist in
recoverable form; every authentication failure returns one opaque answer so the endpoint
cannot enumerate registered phone numbers; a failed attempt is committed even though the
request fails; suspending a principal ends its live sessions; console OTP delivery is
refused outside local/test.

### `customer` (W20-B) — 67 tests

Profile completion, version-scoped legal acceptance (publishing new terms requires a fresh
acceptance), notification preferences, contacts and the address book. Enforces v6.3 p.12:
a receiver needs only a phone and a governorate; name, street and map pin are optional.
Addresses are archived, never deleted; the only default cannot be archived; delivery and
pickup defaults are independent. Migration `w20b_customer_core_001` with a partial unique
index for one live default per (owner, kind) and a check constraint rejecting a
half-specified map pin.

### `pickup` — real authorization, no longer dormant

`IdentityIntrospectionAuthorizer` + `HttpIntrospectionTransport` resolve a bearer token
through Identity's introspection contract. Pickup does not import Identity; it speaks
HTTP. Unmapped roles are dropped rather than widened, unparsable scopes grant nothing, a
suspended principal is refused, and an Identity outage raises unavailability rather than
blaming the user. Production now **requires** a configured Identity.

## The non-dormancy proof

`tests/identity_pickup_integration/` — 6 tests, all passing. Identity runs in its own
process and environment under uvicorn; Pickup runs in its own; they talk over a socket.
The suite signs a driver in with a real one-time code read from the service's own output,
has the configured bootstrap operator grant `PICKUP_DRIVER`, and then drives Pickup's
endpoints with the resulting token:

| Assertion | Result |
|---|---|
| No token | 401 |
| Forged token, rejected by real introspection | 401 |
| Customer token on a driver endpoint | 403 |
| Real driver token → `POST /pickup/work-sessions/start` | **200, session ACTIVE** |
| Real driver token → `POST /pickup/tasks/{id}/acknowledge` | **200, ACKNOWLEDGED** |
| Real driver token → `GET /pickup/batches/{id}/stop` (new in W19-C) | **200** |

## Validation at this checkpoint

```
ruff / verify_boundaries / verify_agent_governance   → all passed
identity                 →   76 passed   (new)
customer                 →   67 passed   (new)
pickup                   →  354 passed   (was 336; +18 authorizer)
shipment                 →  146 passed
tracking                 →  129 passed
audit                    →   81 passed
legacy_event_bridge      →   59 passed
root architecture/governance/contracts → 104 passed (was 101; +3)
identity↔pickup integration →    6 passed   (new)
                                  TOTAL → 1,022 passed, 0 failed
```

## Remaining build-out

Seven services are scaffolded (pyproject, alembic, health, readiness, session factory) and
have a named owner, a requirement set and a wave, but their domain logic is not yet
written:

| Wave | Service | Requirements | Notes |
|------|---------|--------------|-------|
| 3 | `merchant` | MER-01, 03, 04, 10–12, 14, 16, 17, 20, 21 | application state machine (fields configurable — MER-02 stays blocked), stores, locations, team, label stock, standing policy |
| 4 | `ordering` | CUS-01, 02, 08; MER-05, 08, 09, 13, 18, 19, 22, 23; SHP-01, 02, 10–12 | order → shipment requests, serviceability, quote, label assignment gating pickup booking, edit/cancel stage rules |
| 6 | `hub` | CUS-03–07; SHP-05–08, 13; OPS-01, 02, 05, 10 | customer drop-off (the second way custody begins), sorting, parcel groups, optional seals, per-hub cut-off, linehaul |
| 7 | `notification` | NTF-01 … NTF-10 | acceptance fan-out (app + SMS/OTP + WhatsApp), pre-delivery trio, intact-seal confirmation |
| 8–10 | `delivery` | DRV-L01 … L21; CUS-11–13; OPS-08 | assignment, 10-minute wait, OTP/ID verification, open-box, POD, failed attempts, 3-day hold, returns |
| 11 | `finance` | PAY-01 … 11; DRV-A05 … A13; OPS-04 | ADR-0012 double-entry ledger, driver cash custody and limits, exchange-office settlement, wallet projection, payouts, refunds |
| 12 | `claims` | CLM-01 … 07; DRV-P25; OPS-06, 07 | support tickets, compensation claims, custody-record review |

Each follows the same gate: audit → architecture decision → tests → implementation →
migration → contracts → targeted tests → integration tests → diff review → fix → full
revalidation → documentation.
