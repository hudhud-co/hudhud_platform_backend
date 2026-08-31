# messaging-conformance

Technical conformance primitives for service-owned outbox/inbox processing (ADR-0008).

This package provides state enums, immutable snapshots, pure decision functions, protocol
ports, lease helpers, retry classification, append-only observation UUIDv5 helpers, and
reusable conformance vectors. It does **not** include SQLAlchemy models, Alembic migrations,
NATS clients, relay daemons, or domain/business rules.

Each service owns its PostgreSQL schema, adapter implementation, and transaction boundaries.

## Install

```bash
cd packages/messaging_conformance
uv lock && uv sync --all-groups
```

## Public API

See `messaging_conformance.__all__` for the stable surface.

## Conformance vectors

Vectors **C1–C10** (ADR-0008) are declared in `messaging_conformance.conformance.vectors`.
Pure decision vectors can run in memory via `run_pure_decision_vector`.

### PostgreSQL adapter still required

These vectors need a real service-owned PostgreSQL adapter (disposable DB) in future CI:

- **C1** — domain rollback leaves zero outbox rows
- **C2** — outbox insert in same transaction as domain commit
- **C3** — multi-replica relay claim safety
- **C4** — stale lease recovery persisted requeue (decision covered in-memory)
- **C5** — publish ACK persisted transition (decision covered in-memory)
- **C6** — duplicate inbox insert side-effect safety (decision covered in-memory)
- **C7** — processed-before-ACK redelivery path (decision covered in-memory)
- **C8** — poison/quarantine persisted path (decision covered in-memory)
- **C10** — sanitized `last_error_*` columns (regex fixture covered in-memory)

**C9** (oversized payload rejection) is enforced by the producing service using
`event_envelope` limits before outbox insert/publish — not by this package alone.

## Tests

```bash
uv run ruff check .
uv run pytest
```
