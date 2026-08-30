# HUDHUD Eventing Foundation Runbook

Local and disposable validation for ADR-0002 NATS JetStream topology.
Infrastructure only — no domain subjects, outbox/inbox tables, or application services.

## Scope and non-claims

- **Single-node JetStream** with `replicas: 1` — file-backed durability on one host.
- **Not HA/quorum** — host loss stops availability until recovery; no R1→R3 upgrade without maintenance.
- **Not legal/accounting audit storage** — `HUDHUD_AUDIT` is a bounded transport window only; the Audit bounded context owns long-term retention.
- **At-least-once transport** — exactly-once is not claimed.
- **Local-dev credentials** — `NATS_AUTH_ENABLED=false` is an explicit **development-only**
  configuration in `config/defaults.env.example`. It does **not** mean production NATS
  authentication is approved or blocked solely by ADR-0004.
- Production requires per-service credentials, subject ACLs, TLS/secret management, and
  ADR-0004 service-identity decisions — all **unresolved** in this foundation scope.

## Layout

```text
infra/eventing/
  config/defaults.env.example   # provisional defaults (names only)
  nats/entrypoint.sh            # renders server config at start
  topology/streams.yaml         # stream subject filters
  topology/consumers.yaml       # durable consumer templates (D4)
  scripts/bootstrap_topology.py # idempotent bootstrap
  scripts/verify-health.sh      # readiness/metrics probe
infra/compose/eventing-foundation.compose.yaml
tests/eventing/                 # isolated infrastructure validation
```

## Start and bootstrap

```bash
cd infra/compose
docker compose -f eventing-foundation.compose.yaml --profile eventing up -d nats
docker compose -f eventing-foundation.compose.yaml --profile eventing run --rm eventing-bootstrap
```

Bootstrap is **idempotent**: existing streams/consumers are left unchanged; missing objects are created.

## Optional host port bindings

Default Compose networking is **internal only** (`nats:4222`, monitor `nats:8222`).
To publish host ports for host-side clients, create a local override (not committed):

```yaml
services:
  nats:
    ports:
      - "${NATS_CLIENT_HOST_PORT:-4222}:4222"
      - "${NATS_MONITOR_HOST_PORT:-8222}:8222"
```

Reconcile bindings with the capacity/port registry before integration staging.

## Health and observability

| Endpoint | Purpose |
|----------|---------|
| `GET /healthz` | Liveness/readiness (`ok`) |
| `GET /varz` | Server metrics |
| `GET /jsz` | JetStream storage, streams, consumers |

```bash
docker compose -f eventing-foundation.compose.yaml --profile eventing exec nats \
  /bin/sh /eventing/scripts/verify-health.sh
```

Mount or copy `verify-health.sh` when exec'ing — or wget `http://127.0.0.1:8222/jsz` from the container.

Foundation labels on the `nats` service: `hudhud.jetstream.replicas=1`, `hudhud.jetstream.ha=false`.

## Stream model (hybrid per context)

| Stream | Subjects | Provisional max age |
|--------|----------|---------------------|
| `HUDHUD_SHIPMENT` | `hudhud.shipment.>` | 7d |
| `HUDHUD_PICKUP` | `hudhud.pickup.>` | 7d |
| `HUDHUD_HUB` | `hudhud.hub.>` | 7d |
| `HUDHUD_LINEHAUL` | `hudhud.linehaul.>` | 7d |
| `HUDHUD_DELIVERY` | `hudhud.delivery.>` | 7d |
| `HUDHUD_FINANCE` | `hudhud.finance.>` | 30d |
| `HUDHUD_WALLET` | `hudhud.wallet.>` | 30d (Finance-authorized projection transport only) |
| `HUDHUD_NOTIFICATION` | `hudhud.notification.>` | 3d |
| `HUDHUD_AUDIT` | `hudhud.audit.>` | 365d (transport only) |
| `HUDHUD_DLQ` | `hudhud.dlq.>` | 30d |

All streams: file storage, `retention: limits`, `num_replicas: 1`, `max_msg_size: 262144` bytes (256 KiB provisional).

## Durable consumer model (D4)

Configured in `topology/consumers.yaml`:

- **One durable** per consuming service + logical subscription/projection.
- **Same consumer group replicas** share one durable.
- **Different services/projections** use different durables.
- **Subject filters** (`filter_subject`) limit scope — no stream-wide global durable.
- **No durable per event instance.**

Example durables (foundation templates):

| Durable | Stream | Filter |
|---------|--------|--------|
| `shipment_pickup_facts_v1` | `HUDHUD_PICKUP` | `hudhud.pickup.pickup.fact.>` |
| `tracking_lifecycle_v1` | `HUDHUD_SHIPMENT` | `hudhud.shipment.shipment.fact.>` |
| `finance_cod_collected_v1` | `HUDHUD_DELIVERY` | `hudhud.delivery.delivery.fact.cod_collected.v1` |
| `finance_shipment_delivered_v1` | `HUDHUD_SHIPMENT` | `hudhud.shipment.shipment.fact.delivered.v1` |
| `wallet_merchant_payable_v1` | `HUDHUD_FINANCE` | `hudhud.finance.finance.fact.merchant_payable_recognized.v1` |
| `notification_lifecycle_v1` | `HUDHUD_SHIPMENT` | `hudhud.shipment.>` |

Pull consumer defaults (provisional): `AckWait=30s`, `MaxDeliver=5`, backoff `[5s,30s,2m,10m,30m]`, `DuplicateWindow=2m`.

## Configurable provisional defaults

| Setting | Location | Production gate |
|---------|----------|-----------------|
| JetStream disk/memory caps | `config/defaults.env.example`, `nats/entrypoint.sh` | Capacity registry + disk budget tests |
| Stream max ages | `topology/streams.yaml` | Sustained publish/load evidence |
| Max message size | `topology/streams.yaml` `defaults.max_msg_size_bytes` | Envelope size proof (ADR-0002 256 KiB hard limit) |
| Consumer AckWait/MaxDeliver/backoff | `topology/consumers.yaml` | p99 handler + DB commit measurements |
| Replica count | `topology/streams.yaml` `defaults.num_replicas` | HA ADR + 3-node cluster cutover |
| Host port bindings | local Compose override | Port registry / integration stage |
| NATS auth | `NATS_AUTH_ENABLED` env | ADR-0004 service credentials |

## Teardown

Removes eventing containers and **only** the dedicated JetStream volume:

```bash
docker compose -f eventing-foundation.compose.yaml --profile eventing down -v
```

## Validation commands

```bash
git diff --check
uv lock --check
uv run ruff check .
uv run python scripts/quality/verify_boundaries.py
uv run python scripts/quality/verify_agent_governance.py
uv run pytest tests/architecture tests/governance tests/eventing
docker compose -f infra/compose/eventing-foundation.compose.yaml --profile eventing config
```

## Remaining gates (not in this workstream)

- 3-node NATS cluster and `replicas: 3` HA cutover
- Per-service NKey/JWT ACLs (ADR-0004)
- Capacity/load proof for retention, storage, and consumer tuning
- Production TLS/mTLS and secret management
- Domain event contracts and outbox/inbox persistence
