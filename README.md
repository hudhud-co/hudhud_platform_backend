# HUDHUD Platform Backend

Production-grade monorepo for independently deployable FastAPI macroservices/microservices.

Fifteen services, each with its own database, migrations, API and contracts.

## Quickstart

```bash
make up          # Postgres + NATS + all 14 services, migrated and serving
make journeys    # drive the real HTTP APIs end to end
make down        # stop everything and remove the data
```

`make up` syncs the root and every service virtualenv first when a lockfile has changed,
then hands over to `scripts/dev/stack.py`. The stack starts its services with
`UV_NO_SYNC=1`, so a stale environment would otherwise surface as a dozen confusing
service failures instead of one clear step.

### Or entirely in containers

```bash
make docker-up      # build 14 images, then run the platform in containers
make docker-status
make docker-logs s=finance
make docker-down
```

Same ports, same URLs, same journeys — the difference is that nothing runs on the host
and no virtualenv is needed. Each service gets its own image built from its own
`Dockerfile`, migrates its own database on start, and reaches the others by service name
on a private bridge network. `infra/compose/platform.compose.yaml` explains the three
decisions worth knowing about.

Use `make up` while developing — it restarts a service in a second and reads source
straight from the tree. Use `make docker-up` when you want the platform without a Python
toolchain, or to check a service behaves the same when it is packaged.

Services listen on `127.0.0.1:8101`–`8114`, each serving `/docs`, `/health` and `/ready`.
`make status` prints the table. **`ready: no` is often correct** — it is a production
readiness gate, and several services deliberately refuse to start serving a decision the
business has not made (MER-02, CLM-08). `health: ok` is what says a service is running.

### All the APIs on one page

```bash
make api-docs      # http://127.0.0.1:8115 — Swagger UI over all 14 services
```

The platform has no gateway yet, so every service documents itself at its own `/docs` on
its own port. This serves one Swagger page instead: a merged document of all 205 business
endpoints, plus a dropdown to read any single service on its own.

It aggregates server-side because no service sets CORS headers — a page fetching fourteen
origins would be blocked, and opening fourteen delivery APIs to the browser to fix a docs
page would be a poor trade. Nothing about the services changes. In the merged document
each path carries its own `servers` entry, so "Try it out" reaches the right port.
`make api-docs-build out=docs/api` writes the same thing as static files.

| | |
|---|---|
| `make help` | every target, with its arguments |
| `make check` | lint, governance verifiers, root suites, all 15 service suites |
| `make check-all` | the above plus every Docker-backed lab and migration proof |
| `make logs s=finance` | tail one service |

`docs/audits/hudhud-app-redesign-v6.3/12-RUNNING-THE-PLATFORM.md` covers getting a token,
the service credential those two Identity routes need, and what running it found.

## Repository vs Runtime Independence

The monorepo is a **development boundary**, not a runtime boundary. Multiple services live in one git repository but deploy, scale, fail, and migrate independently.

| Concern | Repository (this repo) | Runtime (each service) |
|---------|------------------------|------------------------|
| Source code | Shared git history | Independent Docker image |
| Dependencies | Per-service `pyproject.toml` + `uv.lock` | Locked at build time |
| Database | Documented ownership in `architecture/` | Dedicated credentials + schema |
| Migrations | Per-service `alembic/` directory | One-writer cutover per datastore |
| Communication | Contracts in `contracts/` | HTTP + NATS JetStream at runtime |

## Directory Layout

```text
AGENTS.md              # Root agent instructions (authority hierarchy)
.cursor/               # Version-controlled Cursor Rules and Skills
architecture/          # Boundaries, ownership matrix, invariants
contracts/             # API and event contracts (future)
docs/
  adr/                 # Architecture Decision Records
  audit/               # Legacy baseline inventories
infra/
  compose/             # Docker Compose profiles (future)
packages/              # Narrow technical primitives only
scripts/
  quality/             # Architecture and governance verification
services/              # Independently deployable FastAPI services
tests/
  architecture/        # Executable architecture fitness tests
  governance/          # Cursor Rules/Skills governance tests
```

## Adding a Future Service

Example: extracting the Shipment bounded context.

```text
services/shipment/
  pyproject.toml              # own dependencies — NOT the root lock
  uv.lock
  Dockerfile                  # BUILD_CONTEXT_ALLOWLIST if context > service dir
  alembic/
    versions/                 # shipment-owned migrations only
  src/shipment/
    main.py                   # FastAPI composition root
  tests/
```

1. Register ownership in `architecture/service-boundaries.yaml`.
2. Run `cd services/shipment && uv lock && uv sync`.
3. Build with an isolated Docker context: `docker build -f services/shipment/Dockerfile services/shipment`.
4. Add a Compose profile under `infra/compose/` when infrastructure is introduced.
5. Verify: `uv run python scripts/quality/verify_boundaries.py`.

## Independent Dependency Locks

The root `pyproject.toml` provides **repository tooling only** (pytest, ruff, PyYAML for the architecture verifier). Each service maintains its own lock:

```bash
# Root tooling
uv sync --dev

# Future service (example)
cd services/shipment && uv lock && uv sync
```

Services must never share a single dependency lock file.

## Independent Docker Build Contexts

Each service Dockerfile must build from its own directory. Copying from the monorepo root requires an explicit allowlist marker:

```dockerfile
# BUILD_CONTEXT_ALLOWLIST: packages/event_envelope
COPY packages/event_envelope /app/packages/event_envelope
COPY src/shipment /app/src/shipment
```

Building the entire monorepo into every service image is forbidden unless explicitly allowlisted.

## Service-Owned Migrations

Alembic migrations live inside the owning service:

```text
services/shipment/alembic/versions/001_initial_shipments.py   # OK
alembic/versions/001_initial_shipments.py                     # FORBIDDEN (root-level)
```

Database extraction uses **one-writer cutover**. Bidirectional dual-write is forbidden. Credential revocation is a mandatory cutover gate.

## Database-per-Service Direction

Each bounded context that owns mutable state gets dedicated database credentials. Cross-service foreign keys are forbidden — reference by ID and reconcile via events.

Read projections (Tracking, Control Tower) may consume events without owning domain tables.

## Allowed Shared Packages

`packages/` contains narrow technical primitives only:

- Event envelope types
- Tracing propagation helpers
- Idempotency key utilities
- HTTP client wrappers (no business logic)

## Forbidden Shared Code

- Shared domain models or entities
- Shared ORM models or repositories
- Generic repository frameworks
- "Common business logic" packages
- Cross-service Alembic migrations

## HTTP vs NATS Communication

| Pattern | Use when |
|---------|----------|
| HTTP (sync) | Query/read operations, command submission with immediate acknowledgement |
| NATS JetStream (async) | Cross-service facts, lifecycle events, at-least-once delivery |

Gateway routes and authenticates HTTP traffic. It does **not** contain business orchestration.

## Outbox/Inbox Expectations

Each service that publishes events owns:

- An **outbox** table written in the same transaction as domain state
- A relay process that publishes to NATS JetStream
- **Inbox** deduplication for consumed events (idempotent handlers)

Event envelopes must include: `event_id`, `event_type`, `event_version`, `occurred_at`, `producer`, `aggregate_type`, `aggregate_id`, `aggregate_version`, `correlation_id`, `causation_id`, `traceparent`, and tenant context when applicable.

## Event Compatibility

- Additive changes within the same major version
- Breaking changes require a new `event_version`
- Consumers must be idempotent

## Local Docker Compose Profiles

When infrastructure is scaffolded (post-F0), services will register Compose profiles:

```bash
docker compose -f infra/compose/docker-compose.yml --profile shipment up
```

## Path-Filtered CI Direction

CI will run service-scoped checks based on changed paths:

```yaml
# Future .github/workflows/ci.yml pattern
paths:
  - 'services/shipment/**'
  - 'packages/event_envelope/**'
```

Root architecture tests run on every change.

## No Production Source Mounts

Production containers run immutable images. Source code bind mounts are for local development only.

## One-Writer Migration Principle

During database extraction from the legacy monolith:

1. Choose the canonical writer service for each table cluster.
2. Cut over writes in one direction (one-writer).
3. Revoke legacy credentials as a mandatory gate.
4. Never use bidirectional dual-write.

## Legacy Repository Reference

The legacy monolith at `hudhud-backend` is a **read-only** reference for behavior, data contracts, tests, and migration planning. It must never become a runtime dependency.

Consult `docs/audit/` for evidence-based inventories. Legacy Cursor rules (including monolith-only constraints) do not apply to this repository.

Copied legacy code requires a provenance record in `docs/audit/legacy-provenance.yaml`.

## Quality Commands

```bash
# Install root tooling
uv lock
uv sync --dev

# Lint
uv run ruff check .

# Architecture tests
uv run pytest tests/architecture

# Architecture verifier (direct)
uv run python scripts/quality/verify_boundaries.py

# Agent governance verifier
uv run python scripts/quality/verify_agent_governance.py

# Architecture + governance tests
uv run pytest tests/architecture tests/governance
```

## Architecture Documents

- `AGENTS.md` — root agent instructions
- `.cursor/README.md` — Rules vs Skills and invocation
- `architecture/invariants.md` — binding platform rules
- `architecture/service-boundaries.yaml` — bounded context manifest
- `architecture/ownership-matrix.yaml` — data and API ownership
- `docs/adr/` — Architecture Decision Records

## Current Stage

Foundation F0 complete when architecture verification and tests pass. Next stage (F1) will scaffold the first transitional deployable — subject to ADR decisions on deployable grouping.
