# Infrastructure

Deployment and local development infrastructure for the HUDHUD platform.

## Planned Layout

```text
infra/
  compose/
    docker-compose.yml        # base services (NATS, observability — future)
    docker-compose.local.yml  # local development overrides
  scripts/                    # deploy helpers (future)
```

## Principles

- **Docker Compose** is the current deployment orchestrator.
- Each service has an independent Docker build context with an explicit allowlist.
- **No production source mounts** — images are immutable at deploy time.
- **Blue/Green** applies only to the service being changed, not the entire platform.
- The 16 GB host provides deployment isolation, not high availability.

## Compose Profiles (Future)

Services will register Compose profiles for selective local startup:

```bash
docker compose --profile shipment --profile gateway up
```

## Current Stage

Foundation F0 — compose files and NATS JetStream infrastructure are not yet scaffolded.
See `docs/audit/legacy-runtime-inventory.md` for legacy deployment reference.
