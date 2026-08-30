# Shared Packages

Narrow **technical primitives** shared across services. This directory is not a dumping ground for domain code.

## Allowed

- Event envelope types and serialization helpers
- Tracing propagation (`traceparent`, correlation IDs)
- Idempotency key utilities
- HTTP client wrappers (retries, timeouts — no business logic)
- Structured logging helpers

## Forbidden

- Domain models, entities, or value objects
- ORM models, repositories, or Alembic migrations
- Generic repository frameworks
- "Common business logic" shared across bounded contexts

## Adding a Package

```text
packages/
  event_envelope/
    pyproject.toml
    src/event_envelope/
      __init__.py
      envelope.py
    tests/
```

1. Keep the package free of FastAPI route handlers and SQLAlchemy imports.
2. Register the package name in `architecture/service-boundaries.yaml` under `shared_packages`.
3. Services may import declared packages; undeclared imports fail architecture verification.

## Current Stage

Foundation F0 — no shared packages created yet.
