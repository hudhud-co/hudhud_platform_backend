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
