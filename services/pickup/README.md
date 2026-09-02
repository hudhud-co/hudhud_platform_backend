# Pickup

Independently managed HUDHUD Pickup service package. This Wave establishes the
**PickupTask recovery lifecycle** domain foundation only. Pickup owns task recovery
and attempt history; it does not own or mutate Shipment lifecycle/custody (ADR-0003).

## Scope (W12)

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

Unit of work port: `RecoveryUnitOfWork`. Tests use `InMemoryRecoveryUnitOfWork`
(copy-on-write rollback) and `InMemoryShipmentEligibilityAdapter`.

## Explicit non-goals (deferred)

- Production Shipment HTTP/event integration
- HTTP endpoints, PostgreSQL, Alembic, NATS/events
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

W12 evidence is **unit/in-memory only** — no Docker, network, NATS, PostgreSQL, or
legacy repository access in targeted tests.

## Production readiness

**Not production-ready.** Runtime persistence, API surface, secured messaging, and
production Shipment eligibility integration remain future Waves.
