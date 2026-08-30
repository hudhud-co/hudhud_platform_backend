# HUDHUD Platform Architecture Invariants

Binding rules for the `hudhud_platform_backend` monorepo. Executable enforcement lives in
`scripts/quality/verify_boundaries.py` and `tests/architecture/`.

## Repository vs Runtime

- The monorepo is a **development boundary**, not a runtime boundary.
- Each deployable service owns its composition root, Docker image, dependency lock, database
  credentials, schema migrations, contracts, outbox/inbox, tests, deployment, observability,
  and backup/restore.

## Service Independence

1. **No cross-service imports** — a service must not import another service's Python package.
2. **No cross-service FK** — database foreign keys must not span service boundaries.
3. **No cross-service DB access** — a service must not hold credentials for another service's database.
4. **No shared ORM** — ORM models and Alembic migrations belong to exactly one service.
5. **Independent dependency locks** — each service has its own `pyproject.toml` and `uv.lock`.
6. **Independent Docker build contexts** — each service Dockerfile declares an explicit allowlist;
   building must not implicitly require the entire monorepo.

## Shared Packages (`packages/`)

Allowed: narrow technical primitives (logging helpers, event envelope types, HTTP client wrappers,
idempotency keys, tracing propagation).

Forbidden:

- Shared domain models or entities
- Shared ORM models or repositories
- Generic repository frameworks
- "Common business logic" packages
- Alembic migrations

## Gateway

- Gateway routes, authenticates, and forwards — it does **not** own domain tables or business
  orchestration.

## Shipment Lifecycle

- **Shipment** is the sole canonical writer of shipment lifecycle state.
- Pickup, Hub, Linehaul, and Delivery publish facts or issue commands; they do not directly
  update canonical Shipment state.

## Physical Delivery and Finance

- Physical delivery is an irreversible operational fact.
- Finance failures must never roll back physical delivery.
- COD collection and merchant wallet/payable recognition are separate accounting facts.

## Messaging

- Cross-service communication uses NATS JetStream (at-least-once delivery).
- Commands and consumers must be idempotent.
- Event envelopes must support: `event_id`, `event_type`, `event_version`, `occurred_at`,
  `producer`, `aggregate_type`, `aggregate_id`, `aggregate_version`, `correlation_id`,
  `causation_id`, `traceparent`, and tenant/organization context when applicable.

## Database Extraction

- One-writer cutover per extracted datastore.
- Bidirectional dual-write is forbidden.
- Credential revocation is a mandatory cutover gate.

## Deployment

- Docker Compose is the current orchestrator.
- Blue/Green applies only to the changed service.
- No production source mounts.
- Path-filtered CI per service (when CI is introduced).

## Legacy Repository

- `hudhud-backend` is read-only reference — never a runtime dependency.
- Legacy code copied into this repository requires a provenance record in
  `docs/audit/legacy-provenance.yaml`.

## Manifest Completeness

- Every bounded context in `architecture/service-boundaries.yaml` must declare ownership,
  data strategy, and dependency information.
- Proposed extracted services must declare a database ownership strategy.
