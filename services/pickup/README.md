# Pickup

Independently managed HUDHUD Pickup service package. Pickup owns task recovery
and attempt history; it does not own or mutate Shipment lifecycle/custody (ADR-0003).

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
9. Recovery is rejected after Shipment custody has started (`IN_CUSTODY` / custody owner present).
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
- NATS/events and production/staging deployment
- Driver assignment algorithms, routing, scheduling engine
- Hub inbound custody transfer
- Notification, Control Tower, Delivery, Finance

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
proof lives in `tests/service_postgres_proof` (`workflow_dispatch` only). No NATS
or legacy repository access in that lab.

## Production readiness

**Not production-ready.** Default authorization and Shipment eligibility adapters
remain fail-closed / deferred. Secured messaging and production deployment remain
future Waves.
