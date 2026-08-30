# Legacy Runtime Inventory

Deployment, infrastructure, and operational characteristics of the legacy monolith.

Audit source: `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` @ `2e375057fdf9b9ce8416408a4436303be5301def`.

---

## Application Runtime

| Component | Detail |
|-----------|--------|
| Framework | FastAPI |
| ASGI server (prod) | gunicorn + uvicorn workers |
| ASGI server (dev) | uvicorn |
| Composition root | Single `app/main.py` |
| API prefix | `/api/v1/{module}` |
| Health | `app/core/health.py` — `/health` |
| Middleware | Request ID (`app/core/middleware/request_id.py`) |

---

## Infrastructure Services

| Service | Version / image | Purpose |
|---------|-----------------|---------|
| PostgreSQL | 16 | Primary datastore (all modules) |
| Redis | (compose-defined) | OTP, rate limits, delivery OTP sessions |
| MinIO | (compose-defined) | S3-compatible object storage (evidence, attachments) |

No NATS, no message broker, no separate read replicas documented.

---

## Docker Layout

| File | Environment |
|------|-------------|
| `Dockerfile` | Multi-stage build; `python:3.12-slim` base |
| `deploy/docker-compose.local.yml` | Local development |
| `deploy/docker-compose.dev.yml` | Dev |
| `deploy/docker-compose.staging.yml` | Staging |
| `deploy/docker-compose.prod.yml` | Production |
| `docker.sh` | Wrapper for compose operations |

### Production compose services

- **app** — gunicorn, bound to loopback `:8001`
- **db** — PostgreSQL 16
- **redis**
- **minio**

### Optional worker profiles

| Profile | Command | Purpose |
|---------|---------|---------|
| `push-outbox-worker` | `run.sh push-outbox-worker` | FCM push dispatch from outbox table |
| `delivery-evidence-cleanup-worker` | `run.sh delivery-evidence-cleanup-worker` | Abandoned delivery evidence cleanup |

Workers are script-based, not separate deployable images.

---

## Environment Configuration

Variable names only (no secret values):

| Category | Variables (representative) |
|----------|---------------------------|
| App | `APP_ENV`, `DEBUG`, `API_V1_PREFIX` |
| Database | `DATABASE_URL`, `DB_*` |
| Redis | `REDIS_URL` |
| Auth | `JWT_*`, `OTP_*` |
| Storage | `MINIO_*`, `PROOF_STORAGE_BUCKET`, `DELIVERY_EVIDENCE_STORAGE_BUCKET`, `SUPPORT_CLAIM_ATTACHMENT_BUCKET` |
| SMS | `SMS_*` |
| FCM | `FCM_*` |
| Serviceability | `ORDER_SUPPORTED_CITY_AREAS` |

Files: `.env.example`, `deploy/env.staging.example`, `.env.{local,dev,staging,prod}` (gitignored).

---

## CI/CD Pipelines

### CI (`.github/workflows/ci.yml`)

| Stage | Checks |
|-------|--------|
| fast-gate | ruff check, ruff format, unit pytest |
| integration | Postgres + Redis, `-m integration` |
| migration | `alembic upgrade head` |
| docker-build | Production Docker target build |

### Deploy production (`.github/workflows/deploy-production.yml`)

- Trigger: manual `workflow_dispatch`
- Method: SSH to production host
- Steps: `ENV=prod ./docker.sh build/up`, alembic at head
- Verification: `https://api.hudhudpost.com/health`

No path-filtered CI (monolith — all changes run full suite).

---

## Quality Gate Scripts

| Script | Purpose |
|--------|---------|
| `scripts/check.sh` | Full local gate |
| `scripts/lint.sh` | ruff check |
| `scripts/format.sh` | ruff format |
| `scripts/test.sh` | pytest |
| `run.sh check` | Import smoke + alembic wiring |
| `scripts/db_migrate.sh` | Alembic upgrade |
| `scripts/smoke_test_*.sh` | 40+ domain smoke scripts |

---

## Observability (Legacy)

| Capability | Status |
|------------|--------|
| Structured logging | `app/core/logging.py` — basic setup |
| Request ID propagation | Middleware present |
| Distributed tracing | Not evidenced |
| Metrics/Prometheus | Not evidenced |
| Centralized log aggregation | Not documented |

---

## Backup & Recovery

Not documented in repository beyond Postgres being a compose service. No automated backup scripts evidenced in `scripts/` or `deploy/`.

---

## Host Constraints (Documented)

From project context and deployment docs:

- Single production host model
- 16 GB host provides deployment isolation, not HA
- Blue/green not implemented in legacy (single compose stack)

Platform target: Blue/Green per changed service only; Docker Compose orchestrator; 16 GB host constraint acknowledged.

---

## Messaging & Async (Legacy)

| Mechanism | Status |
|-----------|--------|
| In-process function calls | Primary inter-module communication |
| push_outbox table + polling worker | Verified (notification) |
| PGMQ (Redis) | Documented in cursor rules, not implemented |
| NATS JetStream | Not present — platform target |

---

## Test Infrastructure

| Component | Detail |
|-----------|--------|
| Unit tests | Default local run (`-m 'not integration'`) |
| Integration harness | `tests/integration/conftest.py` — Postgres + Redis |
| Migration tests | `tests/integration/migration/` — EXPECTED_HEAD verification |
| Regression | `tests/integration/modules/regression/` — canonical full-flow (CI) |
| E2E smoke | `scripts/smoke_test_full_flow.sh` — manual/staging |

---

## Platform Runtime Divergence (Intentional)

The new platform will differ from legacy runtime in these approved ways:

1. Independently deployable FastAPI services with own Docker images
2. Database-per-service direction with one-writer cutover
3. NATS JetStream for cross-service events
4. Per-service Alembic migration ownership
5. Outbox/inbox pattern per service
6. Path-filtered CI per service
7. Blue/Green per changed service
8. No production source mounts
9. Gateway without business orchestration
