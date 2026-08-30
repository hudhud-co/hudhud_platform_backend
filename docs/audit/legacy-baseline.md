# Legacy Baseline Audit

Evidence snapshot of the read-only legacy repository used as behavioral and migration reference for HUDHUD platform bootstrap (Foundation Stage F0).

## Repository Identity

| Field | Value |
|-------|-------|
| Absolute path | `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` |
| Branch | `develop` |
| HEAD SHA | `2e375057fdf9b9ce8416408a4436303be5301def` |
| Last commit | `fix(store): expose workplace handover readiness` (Mohammad Akbari, 2026-08-20) |
| Remote | `origin` → `git@github.com-hudhud:hudhud-co/hudhud-backend.git` (fetch/push) |
| Worktree | **Dirty** — modified: `scripts/dev_pickup_driver_simulator.py` (unstaged) |
| Ahead of remote | 1 commit |

## Runtime Architecture

- **Pattern:** Clean modular monolith (FastAPI)
- **Entry:** `app/main.py` — single composition root registering 46+ routers under `/api/v1/*`
- **Layers per module:** `domain/`, `application/`, `infrastructure/`, `api/` (where implemented)
- **Shared code:** `app/core/` (config, DB, Redis, security, RBAC seed), `app/shared/` (MinIO storage, pagination, geo)
- **Workers:** Script-based (`scripts/run_push_outbox_worker.py`, delivery evidence cleanup); optional Compose profiles — no `app/workers/` package
- **Database:** Single PostgreSQL 16 instance, single Alembic chain (78 revisions, head `b8c9d0e1f2a3`)

## Python & Dependency Tooling

| Item | Evidence |
|------|----------|
| Python | `>=3.12` (`pyproject.toml`, Docker `python:3.12-slim`) |
| Package manager | **uv** (`pyproject.toml`, `uv.lock`) |
| Build backend | hatchling |
| Lint/format | ruff |
| Test runner | pytest (default excludes integration: `-m 'not integration'`) |
| Key runtime deps | FastAPI, SQLAlchemy, alembic, asyncpg, redis, pyjwt, minio, httpx |
| requirements.txt / Poetry | Not present |

## Module Inventory (22 modules)

```
app/modules/address_book
app/modules/audit
app/modules/auth
app/modules/claims
app/modules/control_tower
app/modules/delivery
app/modules/delivery_task
app/modules/hub
app/modules/linehaul
app/modules/merchant
app/modules/merchant_applications
app/modules/notification
app/modules/order
app/modules/pickup
app/modules/pickup_scheduling
app/modules/proof
app/modules/send_parcel
app/modules/shipment
app/modules/support
app/modules/tracking
app/modules/wallet
```

## Tests & Quality Commands

| Command | Path | Purpose |
|---------|------|---------|
| `./scripts/check.sh` | Full gate: ruff + format + import smoke + compose config + unit pytest |
| `./scripts/lint.sh` | ruff check |
| `./scripts/format.sh` | ruff format |
| `./scripts/test.sh` | `uv run pytest` |
| `./run.sh check` | Import smoke + alembic wiring |
| `./scripts/db_migrate.sh` | Alembic upgrade |

- **Test layout:** `tests/unit/`, `tests/integration/` (~330 test files)
- **Markers:** `unit`, `integration`, `docker`, `slow`

## Migrations

| Metric | Value |
|--------|-------|
| Directory | `alembic/versions/` |
| Count | 78 revision files |
| Base | `b7c4e1f92a30` |
| Head | `b8c9d0e1f2a3` (`add_warehouse_ops_foundation`) |
| Chain | Single linear chain |

Migration verification: `tests/integration/migration/` (EXPECTED_HEAD = `b8c9d0e1f2a3`).

## Docker & Deployment

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage, prod target with gunicorn |
| `deploy/docker-compose.local.yml` | Local dev stack |
| `deploy/docker-compose.dev.yml` | Dev environment |
| `deploy/docker-compose.staging.yml` | Staging |
| `deploy/docker-compose.prod.yml` | Production (app, db, redis, minio; optional worker profiles) |
| `docker.sh` | Compose wrapper |
| `docs/DEPLOYMENT.md` | Deployment guide |

**Production stack:** app (gunicorn, loopback `:8001`), Postgres 16, Redis, MinIO. Optional profiles: `push-outbox-worker`, `delivery-evidence-cleanup-worker`.

## CI/CD

| Workflow | Path | Jobs |
|----------|------|------|
| CI | `.github/workflows/ci.yml` | fast-gate (ruff + unit) → integration (Postgres+Redis) → migration → docker-build |
| Deploy production | `.github/workflows/deploy-production.yml` | Manual `workflow_dispatch`, SSH deploy, alembic at head, health check |

## Cursor Rules & Project Guides

### Cursor rules (`.cursor/rules/`)

| File | Topic |
|------|-------|
| `00-project-context.mdc` | Scope, stack, active vs future modules |
| `01-architecture.mdc` | Monolith layers |
| `02-backend-code-style.mdc` | Style |
| `03-fastapi-patterns.mdc` | API patterns |
| `04-auth-security-rbac.mdc` | Auth/RBAC |
| `05-database-postgres.mdc` | Database |
| `06-redis-pgmq-workers.mdc` | Redis/PGMQ (planned) |
| `07-testing-quality.mdc` | Testing |
| `08-devops-docker-env.mdc` | Docker/env |
| `09-domain-rules-hudhud.mdc` | Custody, shipment events |
| `10-ai-coding-behavior.mdc` | AI behavior |

### Guides & audits

| Document | Path |
|----------|------|
| AI guide | `AI_PROJECT_GUIDE.md` |
| Master audit | `HUDHUD_BACKEND_MASTER_AUDIT.md` |
| Execution plan | `HUDHUD_BACKEND_EXECUTION_PLAN.md` |
| Production V1 | `docs/production_v1/` |
| Phase audits | `docs/audits/` (50+ reports) |
| Domain design | `docs/domain/` |

**AGENTS.md:** not present  
**Formal ADRs:** not present (design captured in audits and domain docs)

## External Integrations

| Integration | Status | Evidence |
|-------------|--------|----------|
| PostgreSQL | verified | SQLAlchemy async |
| Redis | verified | OTP, rate limits, delivery OTP |
| MinIO/S3 | verified | `app/shared/storage/minio_storage.py` |
| SMS OTP | verified | `auth/infrastructure/http_sms_otp_sender.py` |
| FCM push | partial | `notification/infrastructure/fcm_push_provider.py` (dry-run default) |
| PGMQ | missing | Referenced in cursor rules only |
| NATS JetStream | missing | Not present in legacy |

## Documentation vs Code Tensions

- Cursor rules list control_tower, notification, claims as "future" — all are implemented.
- `HUDHUD_BACKEND_MASTER_AUDIT.md` verdict: `PRODUCTION_V1_BACKEND_NOT_READY`.
- Legacy rules requiring modular monolith do **not** apply to `hudhud_platform_backend`.

## Audit Methodology

- Read-only inspection; legacy worktree left untouched (including dirty `scripts/dev_pickup_driver_simulator.py`).
- Capabilities classified only with file-level evidence — folder names alone are insufficient.
- No secret values recorded; configuration audits report variable names only.
