# 07 — Test and Review Gates

## Gates run for every wave

```
(cd services/pickup && uv run pytest)
uv run pytest tests/architecture tests/governance tests/contracts
uv run ruff check .
uv run python scripts/quality/verify_boundaries.py
uv run python scripts/quality/verify_agent_governance.py
```

Migration and runtime proof (Docker):

```
uv run pytest tests/service_postgres_proof
uv run pytest tests/pickup_acceptance_eventing_proof
uv run pytest tests/nats_security_proof
```

## Per-workflow test obligations

For each new capability the suite covers: happy path · invalid transition · repeated request
(idempotent replay) · concurrent request · unauthorized actor · wrong role · missing prerequisite ·
retry after partial failure.

| Capability | Happy | Invalid transition | Replay | Concurrent | Unauthorized | Missing prerequisite |
|---|---|---|---|---|---|---|
| Packaging decision | ✓ | ✓ (`TOO_WEAK`+`ACCEPT`) | ✓ | ✓ | ✓ | ✓ (no assessment) |
| Refusal | ✓ | ✓ (after acceptance) | ✓ | ✓ | ✓ | ✓ (no reason) |
| Not presented | ✓ | ✓ (after acceptance) | ✓ | ✓ | ✓ | — |
| Photo gate | ✓ | — | ✓ | — | ✓ | ✓ (no media refs) |
| Acceptance status | ✓ | — | ✓ (stable) | — | ✓ | ✓ (unknown task) |
| Scan resolution | ✓ | ✓ (all 8 outcomes) | ✓ | — | ✓ | ✓ (blank code) |
| Stop readiness | ✓ | — | ✓ | — | ✓ | ✓ (empty batch) |

## Review checklist applied to the finished diff

Service ownership · authorization · idempotency · concurrency · transaction boundaries ·
outbox/inbox reliability · event duplication · backward compatibility · monetary correctness
(n/a — no money in scope) · PII and secret exposure · error handling · observability · migration
safety · test quality.

## Results (final run)

```
ruff / boundaries / governance            → all passed
services/pickup                           → 336 passed   (baseline 259, +77)
services/shipment                         → 146 passed   (unchanged)
services/tracking                         → 129 passed   (unchanged)
services/audit                            →  81 passed   (unchanged)
services/legacy_event_bridge              →  59 passed   (unchanged)
tests/architecture governance contracts   → 101 passed   (unchanged)
tests/service_postgres_proof              →  45 passed, 1 pre-existing failure
tests/pickup_acceptance_eventing_proof    →  15 passed
tests/nats_security_proof                 →  51 passed
OpenAPI 3.1.0                             →  40 paths / 40 operations, validated
```

The one failure is pre-existing and unrelated — see `09-FINAL-EXECUTION-REPORT.md`.

## Explicit non-claims

- No integration is claimed to work on mocked unit tests alone. New behaviour is proven on the
  in-memory unit of work **and** through the in-process HTTP API; schema is proven on disposable
  PostgreSQL. Concurrent `FOR UPDATE` behaviour is asserted by construction, not observed under
  real contention.
- Nothing here is "production-ready": `pickup.runtime_evidence.production_ready` remains `false`.
