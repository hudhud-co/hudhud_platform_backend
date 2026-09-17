# Pickup

Independently managed HUDHUD Pickup service package. Pickup owns task recovery
and attempt history; it does not own or mutate Shipment lifecycle/custody (ADR-0003).

**W17-E / W17-H:** Application-level acceptance records custody-starting outcomes
(`ACCEPTED` / `ACCEPTED_WITH_EXCEPTION`) and inserts a complete
`pickup.fact.accepted` v1 envelope into a Pickup-owned transactional outbox in
the same unit of work. An optional JetStream outbox relay publishes only
`hudhud.pickup.pickup.fact.accepted.v1` to stream `HUDHUD_PICKUP` (disabled by
default). Local disposable JetStream relay proof exists; production/staging
credentials remain deferred.

## Scope (W12 + W15-B + W16-B)

Recovery actions:

- `retry_pickup`
- `reschedule_pickup`
- `reassign_pickup`
- `cancel_pickup`

### Invariants

1. Never overwrite or reuse a previous pickup attempt.
2. Preserve complete pickup-attempt history.
3. Retry/reschedule/reassign creates a replacement `PickupTask`.
4. Replacement tasks link to the previous attempt via parent/superseded relationships.
5. Replacement `attempt_number` increments deterministically.
6. Previous attempts become terminal (`SUPERSEDED` or `CANCELLED`) and remain queryable.
7. Recovery never directly modifies Shipment custody.
8. Recovery is rejected if the task is already accepted.
9. **Source-aligned recovery eligibility (ADR-0003 W17-A):** Shipment existence is
   required; recovery is blocked when canonical custody type is `PICKUP_DRIVER`.
   Do not block solely on Shipment `IN_CUSTODY`, any custody id, or inferred
   `custody_started`. Fail closed when eligibility cannot be obtained.
10. Cancellation preserves the task record — no deletion.
11. Repeated recovery commands with the same idempotency key return the original result.

## HTTP API (W16-B)

Composition root: `pickup.main:create_app`.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/health` | Liveness only |
| `GET` | `/ready` | PostgreSQL, authorization, Shipment eligibility gates |
| `POST` | `/pickup/tasks/{pickup_task_id}/retry` | Requires `Authorization` + `Idempotency-Key` |
| `POST` | `/pickup/tasks/{pickup_task_id}/reschedule` | Requires scheduled window |
| `POST` | `/pickup/tasks/{pickup_task_id}/reassign` | Requires `new_driver_user_id` |
| `POST` | `/pickup/tasks/{pickup_task_id}/cancel` | Cancels without deletion |

Actor identity is established only by the injected `RecoveryAuthorizer` from a bearer
token. Trusted-looking headers (`X-User-Id`, `X-Role`, …) and request-body actor
fields are never proof of identity.

Default production composition uses:

- `SqlAlchemyRecoveryUnitOfWork` when `DATABASE_URL` (or `PICKUP_DATABASE_URL`) is set
- `DefaultDenyRecoveryAuthorizer` (readiness blocker)
- `UnavailableShipmentEligibilityAdapter` (readiness blocker — production Shipment
  HTTP/event adapter deferred)

## Domain design

```text
PickupTask (attempt lineage: root_attempt_id, parent_attempt_id, attempt_number)
    ├── RecoveryHistoryEntry (append-only audit)
    └── IdempotencyRecord (command deduplication)
ShipmentEligibilitySnapshot (port — production Shipment adapter deferred)
```

Cross-context boundary: Pickup reads Shipment eligibility facts through
`ShipmentEligibilityPort` only. No import of `services.shipment` and no shared database.

## Application API (in-process)

`PickupRecoveryService` exposes:

- `register_pickup_task` — seed initial attempt (tests/bootstrap).
- `retry_pickup`, `reschedule_pickup`, `reassign_pickup`, `cancel_pickup` — recovery actions.

Unit of work port: `RecoveryUnitOfWork`.

- **W12 tests:** `InMemoryRecoveryUnitOfWork` (copy-on-write rollback) and
  `InMemoryShipmentEligibilityAdapter`.
- **W15-B persistence:** service-owned SQLAlchemy models, sync/async session
  factories, Alembic migration, and `SqlAlchemyRecoveryUnitOfWork` with optimistic
  concurrency, atomic recovery commits, and idempotency/lineage uniqueness constraints.
- **W16-B HTTP:** FastAPI adapters, readiness gates, fake authorizer for tests.

## Explicit non-goals (deferred)

- Production Shipment HTTP/event eligibility adapter
- Production identity/authorization adapter (JWT/mTLS)
- NATS/events production/staging deployment and ADR-0010 credential/TLS proof
- Driver assignment algorithms, routing, scheduling engine
- Hub inbound custody transfer
- Notification, Control Tower, Delivery, Finance

## Accepted-fact relay (W17-H)

Entry point: `python -m pickup.runtime.relay_main` (signal-safe drain/close).

| Setting | Default | Notes |
|---------|---------|-------|
| `PICKUP_RELAY_ENABLED` | `false` | Relay off until explicitly enabled |
| `PICKUP_NATS_DEV_NO_AUTH` | `false` | Local/test escape hatch only — forbidden in staging/production |
| `PICKUP_NATS_TLS_ENABLED` | `false` | Required with verified CA trust in staging/production |
| `PICKUP_ADR_0010_CREDENTIALS_CONFIGURED` | `false` | Required with TLS + scoped credentials in staging/production |
| `PICKUP_SHIPMENT_ACCEPTANCE_INGESTION_MODE_NATIVE_CONFIRMED` | `false` | Staging/production relay gate (config flag, not external proof) |
| `PICKUP_SHIPMENT_COMPATIBILITY_HTTP_ACCEPTANCE_DISABLED` | `false` | Staging/production relay gate |
| `PICKUP_LEGACY_PICKUP_ACCEPTANCE_WRITER_REVOCATION_EXTERNALLY_CONFIRMED` | `false` | Staging/production relay gate |
| `PICKUP_PRODUCTION_READY` | `false` | Must remain false |

Publish contract: subject `hudhud.pickup.pickup.fact.accepted.v1`, stream
`HUDHUD_PICKUP`, `Nats-Msg-Id` = stable outbox `event_id`.

Relay staging/production activation requires native Shipment mode confirmed,
compatibility HTTP disabled, external legacy-writer revocation confirmation,
scoped credentials, and verified TLS. Flags are configuration gates only.

## Validation

```bash
cd services/pickup
git diff --check
uv lock --check
uv run ruff check .
uv run pytest -q
uv run python ../../scripts/quality/verify_boundaries.py
```

Service-local tests are unit/fake. PostgreSQL/Alembic and disposable lab
persistence proof exist (W15). Local disposable HTTP+PostgreSQL command-API
proof lives in `tests/service_postgres_proof` (`workflow_dispatch` only). Local
disposable Pickup→Shipment JetStream pipeline proof lives in
`tests/pickup_acceptance_eventing_proof` (`workflow_dispatch` only; local
no-auth labelled lab — not ADR-0010 production credential proof). Local
disposable Pickup/Shipment JWT+TLS+ACL proof lives in
`tests/nats_security_proof` (`workflow_dispatch` only).

## Production readiness

**Not production-ready.** Default authorization and Shipment eligibility adapters
remain fail-closed / deferred. Relay is disabled by default; ADR-0010 remains
Proposed. Local disposable JWT/TLS/ACL evidence exists (W18); staging/production
credential delivery, HA, and real cutover remain open.

## W19-A: driver workforce, sender handover, hub handover, offline work

Pickup now owns the driver-facing half of pickup operations. Driver identity, roles,
and cross-capability attendance stay with `auth_identity` (ADR-0004) and reach this
service only through `PickupAuthorizer` — nothing about identity is stored here.

### Driver work session (pickup capability only)

| Endpoint | Notes |
|---|---|
| `POST /pickup/work-sessions/start` | One open session per driver (partial unique index + `FOR UPDATE`). |
| `POST /pickup/work-sessions/{id}/pause` | `OTHER` requires notes. |
| `POST /pickup/work-sessions/{id}/resume` | |
| `POST /pickup/work-sessions/{id}/end` | Blocked by open custody, an active hub manifest, an active assigned task, or unreconciled offline work. |
| `GET /pickup/work-sessions/current` | Availability is derived from session state, never client-asserted. |

### Driver task lifecycle

`acknowledge` → `arrive` → `scan` → `condition-proof` → `accept`, plus `decline`,
`exception`, and `fail`. Progress is forward-only; repeating a step the task already
reached is an idempotent replay, which is what makes offline replay safe. Acceptance
additionally requires a completed sender ceremony.

`GET /pickup/tasks/{id}/history` returns the append-only audit trail with the actor
identity taken from the authorization decision.

## W19-C: packaging decision and merchant-stop outcomes

Audit: `docs/audits/hudhud-app-redesign-v6.3/`. Product evidence: Driver App v8
(`condition`, `refuse`, `notAccepted`, `progress`, `scanner`, `scanEx:*`, `connLost`) and
*The Shipment Journey* v6.3 chapter 3.

### Packaging decision

`condition-proof` optionally carries a `packaging_assessment` (`GOOD`, `BORDERLINE`,
`TOO_WEAK`, `PRE_EXISTING_DAMAGE`) and a `decision` (`ACCEPT`, `ACCEPT_WITH_WARNING`,
`ACCEPT_WITH_NOTE`, `REFUSE`). The permitted-decision table is enforced in the domain, and
its binding entry is that **`TOO_WEAK` can only be refused** — packaging that will not
survive handling can never be accepted with a warning or a note. Both fields are optional:
omitting them derives the assessment from `package_condition_status`, so clients and rows
that predate this wave behave exactly as before.

### Merchant-stop outcomes

`POST /pickup/tasks/{id}/refuse` (`TOO_WEAK_PACKAGING`, `DAMAGED_BEFORE_PICKUP`,
`DOES_NOT_MATCH_SHIPMENT`) and `POST /pickup/tasks/{id}/not-presented` record terminal
outcomes for one expected parcel. Neither starts custody, so **neither publishes anything
and neither changes Shipment** — the parcel stays with the merchant. A parcel already in
custody can no longer receive either outcome.

`GET /pickup/batches/{batch_id}/stop` reports the outcome counts for a merchant stop and
whether it may be completed: a stop closes only when every expected parcel has an outcome.

### Scan resolution and acceptance status

`POST /pickup/batches/{batch_id}/scan-resolution` classifies a scanned label as `VALID`,
`UNKNOWN_LABEL`, `NOT_IN_THIS_PICKUP`, `ALREADY_ACCEPTED`, `CANCELLED_SHIPMENT`,
`DUPLICATE_SCAN` or `UNREADABLE`. It is strictly read-only: an unregistered label can
never become a shipment in the field, and an unreadable label is never resolved to a task.
`WRONG_MERCHANT` is deliberately not produced — separating it from `NOT_IN_THIS_PICKUP`
needs merchant identity, which belongs to the `merchant_store` context.

`GET /pickup/tasks/{id}/acceptance` answers "was my acceptance recorded?" after a
connection drop, so a driver checks instead of accepting twice.

### Photo documentation

A task created with `photo_documentation_required` refuses acceptance until evidence media
refs are supplied. The flag defaults to off, matching "Without this add-on, no photo is
taken at any stage", and it survives retry, reschedule and reassignment because the add-on
belongs to the shipment rather than to one attempt.

### Sender handover ceremony

A dynamic challenge proves the sender handed the parcel to the **assigned** courier; a
parcel manifest proves **which** parcel. The assigned courier can never satisfy the
sender half, only the keyed hash of the challenge secret is persisted, failed attempts
lock the challenge out, reassignment invalidates both halves, and one ceremony
authorises exactly one acceptance.

Set `PICKUP_SIGNING_KEY` to enable the ceremony and offline work. Without it those
routes are not served **and acceptance is not served either** — the service fails
closed rather than degrading to a driver-unilateral custody start.

`GET /pickup/shipments/{shipment_id}/handover` tells the sender where that ceremony
stands. A sender holds a shipment, not a pickup task, and without this read its app has
to enumerate pickup tasks or hard-code identifiers to find the ceremony at all. The
answer names the state, whether verification is required, whether the ceremony is still
open, and which half — if either — this sender may act on. It carries no challenge
payload, no courier identity and no driver detail.

The two `can_*` flags are **informational**. Every mutation re-derives its own authority,
so a stale or optimistic flag can never widen what a sender is allowed to do. Discovery
is a pure read and needs no signing key: the sender can still see where a ceremony stands
in an environment where the mutations are not served.

### Hub handover

`POST /pickup/handover-manifests` … `/arrive` … `/receipts` … `/close`. A received or
disputed parcel releases custody and enqueues `pickup.fact.handover_completed` on the
transactional outbox; a parcel listed but not produced at the hub stays in
pickup-driver custody and publishes nothing. Shipment applies the canonical
`PICKUP_DRIVER` → `ORIGIN_HUB` transfer (ADR-0003).

### Offline work

`POST /pickup/offline/authorizations` issues a signed, device-bound, time-boxed
authorization (device id stored hashed, token stored hashed). `POST /pickup/offline/sync`
replays captures through the same application services as online commands. Every
submission is preserved: sequence gaps, fingerprint mismatches, stale assignment
revisions, and domain rejections become Operations-owned reconciliation cases rather
than silent drops. Custody acceptance can never be authorised offline.

The assignment revision covers assignment identity only — driver, batch, attempt,
assignment state, supersede — so an entire offline batch replays under the one revision
the driver downloaded, while reassignment or decline invalidates it.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PICKUP_SIGNING_KEY` | unset | Challenge and offline-token HMAC key. Required in production. |
| `PICKUP_REQUIRE_COURIER_VERIFICATION` | `true` | Must stay true in production. |
| `PICKUP_COURIER_CHALLENGE_TTL_SECONDS` | `180` | |
| `PICKUP_COURIER_VERIFICATION_VALID_SECONDS` | `900` | |
| `PICKUP_COURIER_CONFIRMATION_VALID_SECONDS` | `900` | |
| `PICKUP_COURIER_MAX_FAILED_ATTEMPTS` | `5` | |
| `PICKUP_COURIER_LOCKOUT_SECONDS` | `300` | |
| `PICKUP_OFFLINE_AUTHORIZATION_TTL_MINUTES` | `720` | |
| `PICKUP_OFFLINE_SYNC_GRACE_DAYS` | `3` | |
| `PICKUP_OFFLINE_SYNC_MAX_EVENTS` | `200` | |
