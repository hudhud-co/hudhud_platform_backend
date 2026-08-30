# Services

Independently deployable FastAPI macroservices/microservices live here.

Each service is a **runtime boundary**, not merely a folder in the monorepo. A genuine service owns:

- FastAPI composition root (`main.py` or equivalent)
- `pyproject.toml` and its own `uv.lock`
- `Dockerfile` with an explicit build-context allowlist
- Database credentials and Alembic migration history
- API and event contracts (published under `contracts/`)
- Outbox/inbox tables and consumers
- Service-scoped tests
- Deployment and rollback process
- Observability and backup/restore responsibilities

## Adding a Service

```text
services/
  shipment/
    pyproject.toml          # independent dependencies
    uv.lock
    Dockerfile              # BUILD_CONTEXT_ALLOWLIST required if copying from parent
    alembic/                # service-owned migrations only
    src/shipment/
      main.py               # composition root
    tests/
```

Steps:

1. Create the directory structure above.
2. Add an entry to `architecture/service-boundaries.yaml` with ownership and data strategy.
3. Declare allowed shared package imports in the service manifest.
4. Add a Compose profile in `infra/compose/` (when infrastructure is introduced).
5. Ensure `uv run python scripts/quality/verify_boundaries.py` passes.

## Forbidden

- Importing another service's Python package
- Sharing ORM models across services
- Cross-service database foreign keys
- Referencing another service's Alembic directory
- Declaring a path dependency on `hudhud-backend`

## Current Stage

Foundation F0 — no services scaffolded yet. Boundaries and architecture gates are established first.
