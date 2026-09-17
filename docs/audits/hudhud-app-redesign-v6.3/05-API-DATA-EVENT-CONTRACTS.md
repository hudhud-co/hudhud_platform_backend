# 05 — API, Data and Event Contracts

Scope: the `pickup` bounded context only. No other context has a decided owner and an existing
service (see `04-GAP-AND-CHANGE-MATRIX.md` §4).

## Design rules applied

- No duplicate endpoints: `condition-proof` is **extended**, not replaced.
- Every new field is optional with a behaviour-preserving default, so existing mobile clients keep
  working unchanged. No versioned break was needed.
- Authorization is enforced **inside** the service through `PickupAuthorizer`, never in a gateway.
- No request model carries actor identity; the actor comes from the authenticated dependency.
- No business logic in routers — routers map schema → command → service → error mapping only.
- No new event contract: refusal and not-presented are explicitly **non-custody** outcomes
  ("No custody event was recorded" — DRV:`notAccepted`), so nothing is published. This avoids
  inventing a contract that ADR-0009 has not accepted.

## Existing endpoints reused unchanged

`POST /pickup/tasks/{id}/acknowledge` · `/decline` · `/arrive` · `/scan` · `/exception` · `/fail` ·
`/accept` · `GET /tasks/{id}/history` · all work-session, handover and offline routes.

## Existing endpoint modified (backward compatible)

### `POST /pickup/tasks/{pickup_task_id}/condition-proof`

| Field | Type | Required | Behaviour |
|-------|------|----------|-----------|
| `package_condition_status` | enum | existing | unchanged |
| `packaging_assessment` | `GOOD` \| `BORDERLINE` \| `TOO_WEAK` \| `PRE_EXISTING_DAMAGE` | **optional** | when omitted, derived from `package_condition_status` — existing callers unaffected |
| `decision` | `ACCEPT` \| `ACCEPT_WITH_WARNING` \| `ACCEPT_WITH_NOTE` \| `REFUSE` | **optional** | when omitted, defaults to the single permitted decision for the assessment |

Permitted decision table (DRV-P08…P12) — enforced in the domain, not the router:

| `packaging_assessment` | permitted `decision` |
|---|---|
| `GOOD` | `ACCEPT` |
| `BORDERLINE` | `ACCEPT_WITH_WARNING`, `REFUSE` |
| `TOO_WEAK` | `REFUSE` **only** |
| `PRE_EXISTING_DAMAGE` | `ACCEPT_WITH_NOTE`, `REFUSE` |

Errors: `409 pickup_packaging_decision_not_permitted`.

### `POST /pickup/tasks/{pickup_task_id}/accept`

No schema change. New fail-closed guard: when `photo_documentation_required` is set on the task and
no `media_refs` are supplied, acceptance is refused with
`409 pickup_photo_documentation_missing` (DRV-P13).

## New endpoints

| Method | Path | Requirement | Auth command | Idempotency | Response |
|---|---|---|---|---|---|
| `POST` | `/pickup/tasks/{id}/refuse` | DRV-P14 | `REFUSE_PICKUP_TASK` | state-based replay | task state |
| `POST` | `/pickup/tasks/{id}/not-presented` | DRV-P15 | `MARK_TASK_NOT_PRESENTED` | state-based replay | task state |
| `GET` | `/pickup/tasks/{id}/acceptance` | DRV-P19 | `READ_TASK_ACCEPTANCE` | n/a (read) | `{state, recorded, accepted_at, event_id}` |
| `POST` | `/pickup/batches/{batch_id}/scan-resolution` | DRV-P06 | `RESOLVE_PICKUP_SCAN` | n/a (read) | `{outcome, pickup_task_id?}` |
| `GET` | `/pickup/batches/{batch_id}/stop` | DRV-P17 | `READ_PICKUP_STOP` | n/a (read) | counts + `can_complete` + `blocking_reason` |

### Scan resolution outcomes (DRV-P06)

`VALID` · `UNKNOWN_LABEL` · `WRONG_MERCHANT` · `NOT_IN_THIS_PICKUP` · `ALREADY_ACCEPTED` ·
`CANCELLED_SHIPMENT` · `DUPLICATE_SCAN` · `UNREADABLE`.

Resolution is read-only and never mutates state: an unknown label must not be able to create a
shipment in the field ("An unregistered parcel cannot become a shipment in the field").

### Refusal reasons (DRV-P14)

`TOO_WEAK_PACKAGING` · `DAMAGED_BEFORE_PICKUP` · `DOES_NOT_MATCH_SHIPMENT`.

## Database changes (expand-only, additive, nullable)

Migration `w19c_pickup_stop_outcomes_001` on `pickup_tasks`:

| Column | Type | Null | Purpose |
|---|---|---|---|
| `packaging_assessment` | `VARCHAR(32)` | yes | DRV-P08 |
| `condition_decision` | `VARCHAR(32)` | yes | DRV-P09–P12 |
| `photo_documentation_required` | `BOOLEAN NOT NULL DEFAULT false` | no | DRV-P13 |
| `stop_outcome` | `VARCHAR(32)` | yes | DRV-P14/P15 |
| `stop_outcome_reason` | `VARCHAR(64)` | yes | DRV-P14 |
| `stop_outcome_at` | `TIMESTAMPTZ` | yes | audit |

Plus index `ix_pickup_tasks_batch_outcome` on `(assigned_batch_id, stop_outcome)` for the stop
readiness query (DRV-P17).

All columns are additive and nullable (or defaulted), so the migration is expand-phase only and the
downgrade simply drops them — no data loss for pre-existing rows.

## Events

**No new event contract.** Refusal and not-presented deliberately publish nothing: the product
states no custody event is recorded for them. `pickup.fact.accepted` and
`pickup.fact.handover_completed` are unchanged.

## Backward compatibility and mobile impact

- All new request fields are optional; all new endpoints are additive.
- Existing acceptance behaviour changes only for tasks that explicitly carry
  `photo_documentation_required = true`, which defaults to `false`.
- No response field was removed or retyped.
- Mobile migration: none required to keep working; new capability is opt-in.
