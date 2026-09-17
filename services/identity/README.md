# HUDHUD Identity Service

Bounded context(s) owned: `auth_identity` (ADR-0004, ADR-0011).

Authentication, principals, role grants and token introspection. Identity is the only store of authentication credentials and never stores domain membership.

This service owns its own database, migrations, contracts and tests. It never reads another
service's tables and never imports another service's Python package.

## Status

Bootstrap domain foundation. `production_ready: false` — see
`architecture/ownership-matrix.yaml`.

## Validation

```bash
cd services/identity && uv run pytest
uv run ruff check .
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
