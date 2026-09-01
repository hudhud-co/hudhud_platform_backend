# ADR-0010: NATS Service Identities, Subject ACLs, and Credential Rotation

- **Status:** Proposed
- **Date:** 2026-09-01
- **Deciders:** (pending — platform architecture review; security operations)
- **Workstream:** W7-B
- **Implementation allowed:** no — ADR approval, server configuration, secret delivery, ACL proof, rotation/revocation drills, and live Bridge/Audit transport proof remain gated

Label key: **[evidence]** verified from repository, NATS documentation, or accepted ADR; **[proposal]** recommended design not yet accepted; **[decision]** binding only after acceptance; **[assumption]** engineering default pending validation; **[unresolved policy]** requires named deciders.

---

## Context

### Problem statement

**[evidence]** ADR-0002 (Accepted) defines JetStream topology, envelope semantics, and high-level per-service NATS ACL intent, but does not decide the **transport security boundary**: how workload identities authenticate to NATS, which subjects and JetStream API paths each identity may use, how credentials are issued and rotated, or how local development differs from production.

**[evidence]** ADR-0004 (Proposed) covers human authentication, Gateway routing, and HTTP service trust. It lists NATS credentials as complementary to HTTP JWTs but **does not decide** NATS identity mechanics, subject matrices, JetStream API grants, TLS policy, or rotation procedures. ADR-0004 Customer/Organization boundaries remain unresolved — **this ADR must not resolve them or change Identity ownership**.

**[evidence]** ADR-0007 (Accepted) and ADR-0009 (Accepted) define Legacy Event Bridge as transitional publisher of exactly two observation subjects (A1/A2). ADR-0008 (Accepted) defines service-owned outbox/inbox semantics. Wave 7 live eventing proof implements Bridge publish and Audit bind-only pull consume against foundation topology — **without production-grade NATS security**.

**[evidence]** Foundation eventing (`infra/eventing/`) scaffolds streams/consumers and documents `NATS_AUTH_ENABLED=false` as a **local disposable escape hatch** (`config/defaults.env.example`, `runbook.md`). This is **not** production-ready authorization.

**[evidence]** Bridge (`services/legacy_event_bridge`) enforces an application allowlist of exactly two publish subjects (`infrastructure/nats/subjects.py`), validates PubAck stream mapping, and blocks production without TLS + credentials gates (`config.py`, `infrastructure/nats/client.py`). Audit (`services/audit`) binds only to pre-provisioned durable `audit_bridge_entry_v1` on `HUDHUD_AUDIT` and verifies stream/durable/filter binding (`infrastructure/jetstream/binding.py`, `connection.py`). Neither service mutates topology at runtime.

The decision question is:

> **How should HUDHUD workload identities authenticate to NATS JetStream, with what least-privilege subject and JetStream API permissions, under what TLS policy, and with what rotation/revocation procedure — without conflating human auth, domain authorization, HTTP service trust, or topology administration?**

### Security boundary distinctions (binding for this ADR)

| Layer | Owns | This ADR scope |
|-------|------|----------------|
| **Human/user authentication** | Identity service (ADR-0004 Proposed) | **Out of scope** — no JWT/session decisions |
| **Business/domain authorization** | Domain services (membership, RBAC, policy) | **Out of scope** — envelope actor context ≠ NATS login |
| **HTTP service workload identity** | Identity-issued service JWT / mTLS (ADR-0004 Proposed) | **Referenced only** — orthogonal to NATS connection identity |
| **NATS transport authorization** | Per-connection publish/subscribe + JetStream API ACLs | **In scope** |
| **Topology administration** | Bootstrap/admin identity only | **In scope** — exclusive to admin identity |
| **Secret delivery and rotation** | Platform secret store / ops procedure | **In scope** — mechanism gated |

**[decision boundary]** NATS connection identity proves **which deployable may perform which broker operations**. It does **not** prove end-user authorization. Consumers MUST continue to treat envelope `metadata.actor_*` as untrusted for authorization unless separately validated per ADR-0004.

### Verified platform evidence

| Item | Evidence |
|------|----------|
| A1 subject | `hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1` → `HUDHUD_SHIPMENT` |
| A2 subject | `hudhud.audit.legacy_bridge.observation.audit_entry.v1` → `HUDHUD_AUDIT` |
| Audit durable | `audit_bridge_entry_v1`, filter = A2 subject (`topology/consumers.yaml`, `audit/domain/contract.py`) |
| Bootstrap script | `infra/eventing/scripts/bootstrap_topology.py` — creates streams/consumers idempotently |
| Local no-auth | `NATS_AUTH_ENABLED=false` in foundation defaults — disposable local only |
| Bridge production gates | `adr_0004_credentials_configured`, TLS, credentials (`legacy_event_bridge/config.py`) |
| Audit production gates | `adr_0004_credentials_configured`, TLS when NATS enabled (`audit/config.py`) |
| `/health` vs `/ready` | Both services: `/health` liveness only; `/ready` evaluates gates incl. NATS when enabled |

### Peer ADR dependencies

| ADR | Relationship |
|-----|--------------|
| ADR-0002 | Topology, envelope, D4 durables — not re-decided |
| ADR-0004 | HTTP/human/service JWT trust — **not extended or accepted here** |
| ADR-0007 | Bridge transitional publisher scope |
| ADR-0008 | Outbox/inbox — NATS creds per service |
| ADR-0009 | A1/A2 subjects — permission source of truth |

---

## Options

### O1 — Shared NATS username/password

One broker user/password shared by all platform services and bootstrap tooling.

| Criterion | Assessment |
|-----------|------------|
| Least privilege | **Low** — any compromise grants full subject space |
| Revocation | **Low** — rotating shared secret breaks all services simultaneously |
| Rotation | **Low** — coordinated big-bang rollout required |
| Operational burden | **Low** initial setup |
| Single-node compatibility | **High** |
| Future 3-node/HA | **Med** — same weakness at scale |
| Secret distribution | **Low** — one secret, high leakage blast radius |
| Auditability | **Low** — cannot attribute broker actions to deployable |
| Failure blast radius | **High** |
| Local dev ergonomics | **High** |

**Verdict:** **[proposal] Reject** for staging/production.

### O2 — Per-service username/password with static permissions

Static `authorization { users = [...] }` entries per deployable with explicit publish/subscribe allow lists.

| Criterion | Assessment |
|-----------|------------|
| Least privilege | **Med–High** — when diligently scoped per service |
| Revocation | **Med** — disable one user; requires server config reload |
| Rotation | **Med** — per-service password rotation; overlapping validity needs dual-user pattern |
| Operational burden | **Med** — grows with service count; config file churn |
| Single-node compatibility | **High** |
| Future 3-node/HA | **High** — JWT model scales better for clustered accounts |
| Secret distribution | **Med** — N passwords via secret store |
| Auditability | **Med** — connection user maps to deployable |
| Failure blast radius | **Med** — per-service isolation |
| Local dev ergonomics | **High** — mirrors production model simply |

**Verdict:** **[proposal] Acceptable** for disposable local/staging proofs; **not** recommended as production end state.

### O3 — Per-service NKeys in static server configuration

NKey seed credentials in server config or creds files; permissions in static authorization blocks.

| Criterion | Assessment |
|-----------|------------|
| Least privilege | **Med–High** — same as O2 when scoped |
| Revocation | **Med** — remove NKey from server config |
| Rotation | **Med** — new NKey + dual-validity window manual |
| Operational burden | **Med–High** — NKey handling discipline required |
| Single-node compatibility | **High** |
| Future 3-node/HA | **Med** — static config does not leverage decentralized JWT |
| Secret distribution | **Med** — `.creds` files per service |
| Auditability | **High** — NKey maps to identity |
| Failure blast radius | **Med** |
| Local dev ergonomics | **Med** — more setup than no-auth |

**Verdict:** **[proposal] Viable** intermediate; superseded by O4/O6 for production scale.

### O4 — NATS operator/account/user JWT model (NKeys)

Decentralized auth: operator → account → user JWTs with embedded permissions; services connect with `.creds` containing user JWT + NKey seed.

| Criterion | Assessment |
|-----------|------------|
| Least privilege | **High** — permissions embedded per user JWT |
| Revocation | **High** — disable user JWT, shorten `exp`, revoke signing keys |
| Rotation | **High** — overlapping JWT validity windows native |
| Operational burden | **Med** — initial PKI bootstrap; `nats` CLI/account tooling |
| Single-node compatibility | **High** |
| Future 3-node/HA | **High** — account model is NATS cluster native |
| Secret distribution | **Med** — creds files from secret store; no secrets in git |
| Auditability | **High** — user name / NKey in connection logs |
| Failure blast radius | **Low–Med** — per-user isolation |
| Local dev ergonomics | **Med** — code-generated dev JWTs acceptable |

**Verdict:** **[proposal] Recommended** production identity model.

### O5 — mTLS workload identity (client certificates)

TLS client certificates identify each deployable; optional combine with username/JWT.

| Criterion | Assessment |
|-----------|------------|
| Least privilege | **High** — when mapped 1:1 to deployable |
| Revocation | **High** — CRL/OCSP or short-lived certs |
| Rotation | **Med** — cert lifecycle ops |
| Operational burden | **High** — internal CA, cert issuance per pod |
| Single-node compatibility | **High** |
| Future 3-node/HA | **High** |
| Secret distribution | **Med–High** — cert + key per workload |
| Auditability | **High** — cert SAN/CN |
| Failure blast radius | **Low** |
| Local dev ergonomics | **Low** — heavy for disposable Compose |

**Verdict:** **[proposal] Deferred** as primary NATS identity (ADR-0004 Phase 2 option). May complement O6.

### O6 — Hybrid NATS JWT/NKeys plus TLS

O4 for identity and subject permissions; TLS required in production for encryption and server authentication; optional future client cert pinning.

| Criterion | Assessment |
|-----------|------------|
| Least privilege | **High** |
| Revocation | **High** |
| Rotation | **High** — independent TLS cert and JWT rotation tracks |
| Operational burden | **Med–High** |
| Single-node compatibility | **High** |
| Future 3-node/HA | **High** |
| Secret distribution | **Med** — TLS trust bundle + creds file per service |
| Auditability | **High** |
| Failure blast radius | **Low–Med** |
| Local dev ergonomics | **Med** — explicit no-auth local profile remains available |

**Verdict:** **[proposal] Recommended** production transport boundary.

---

## Option matrix (summary)

| Criterion | O1 Shared pwd | O2 Per-svc pwd | O3 Static NKey | O4 JWT/NKey | O5 mTLS | O6 Hybrid JWT+TLS |
|-----------|---------------|----------------|----------------|-------------|---------|-------------------|
| Least privilege | Low | Med–High | Med–High | High | High | **High** |
| Revocation | Low | Med | Med | High | High | **High** |
| Rotation | Low | Med | Med | High | Med | **High** |
| Ops burden | Low | Med | Med–High | Med | High | Med–High |
| Single-node | High | High | High | High | High | **High** |
| 3-node HA | Med | High | Med | High | High | **High** |
| Secret distribution | Low | Med | Med | Med | Med–High | **Med** |
| Auditability | Low | Med | High | High | High | **High** |
| Blast radius | High | Med | Med | Low–Med | Low | **Low–Med** |
| Local dev | High | High | Med | Med | Low | **Med** |

---

## Decision drivers

1. **[evidence]** Platform invariant: service-to-service identity must be explicit; least privilege on subjects (`AGENTS.md`, `architecture/invariants.md`).
2. **[evidence]** Bridge publishes exactly two subjects; Audit consumes exactly one durable — permissions must be narrow, not `hudhud.>` wildcards for runtime identities.
3. **[evidence]** Topology bootstrap is a separate admin function (`bootstrap_topology.py`) — runtime services must not hold stream/consumer CREATE permissions.
4. **[evidence]** JetStream API access uses request-reply (`$JS.API.*` publish + `_INBOX.>` subscribe) — domain subject ACLs alone are insufficient ([NATS JetStream API reference](https://docs.nats.io/reference/reference-protocols/nats_api_reference); [NATS authorization](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization)).
5. **[proposal]** Single-node Compose now; 3-node HA later — choose model that survives cluster cutover without permission redesign.
6. **[evidence]** Local `NATS_AUTH_ENABLED=false` exists for disposable dev — must not be mistaken for production policy (`runbook.md`).
7. **[evidence]** ADR-0004 remains Proposed — this ADR decides **NATS transport only**, not HTTP Identity ownership.

---

## Decision

**[proposal] Recommended direction (requires named deciders to accept):**

Adopt **O6 — Hybrid NATS operator/account/user JWT (NKeys) plus TLS** as the production NATS transport security model.

| Environment | Model | Classification |
|-------------|-------|----------------|
| Production / staging | O6 — per-deployable user JWT + TLS + scoped ACLs | **Proposed** until gates below pass |
| Disposable local Compose | Explicit no-auth OR dev JWT users matching production ACL shape | **Proposed** escape hatch — forbidden outside local/test |
| Break-glass / operator | Separate admin identity — not shared with runtime pods | **Proposed** |

**Explicitly rejected for production:** O1 (shared password).

**Acceptable interim only:** O2/O3 for staged ACL proof before JWT infrastructure is ready — must not be labeled production-ready.

**Deferred:** O5 as primary identity; may layer client certificate verification later without replacing JWT subject ACLs.

**Status: Proposed.** No NATS server configuration, secrets, or service code changes are authorized by this ADR alone.

---

## Service identity model

### Identity catalog

| Identity | Purpose | Topology admin | Typical credential form |
|----------|---------|----------------|-------------------------|
| `hudhud-eventing-bootstrap` | Create/update streams and durables; smoke tests | **Yes** | Admin user JWT or break-glass creds |
| `legacy-event-bridge` | Publish A1/A2 observations | **No** | Service user JWT (`.creds`) |
| `audit` | Pull-consume A2 durable; ACK/NAK/defer | **No** | Service user JWT (`.creds`) |
| `tracking` | Pull-consume A1 durable; ACK/NAK/defer | **No** | Service user JWT (`.creds`) |
| `shipment`, `pickup`, `hub`, … | Future native publishers/consumers per ADR-0002 | **No** | Service user JWT per deployable |
| `hudhud-nats-operator` | Human break-glass, incident response, ACL audit | **Yes** (emergency) | Short-lived operator creds; audited |

**[proposal]** NATS user name SHOULD match deployable id from `architecture/service-boundaries.yaml` (e.g. `legacy-event-bridge`, `audit`). Envelope `producer` field MUST match the authorized publish identity for publishers.

**[decision boundary]** Runtime service identities (`legacy-event-bridge`, `audit`, `tracking`, future services) MUST NOT receive:

- `$JS.API.STREAM.*` (CREATE, UPDATE, DELETE, PURGE, …)
- `$JS.API.CONSUMER.CREATE.>` or `$JS.API.CONSUMER.DELETE.>`
- `$JS.API.STREAM.CREATE.>` / broad `$JS.API.>`

Topology bootstrap identity MUST NOT be mounted in application service pods.

### Human vs workload separation

| Principal | NATS identity? | Notes |
|---------|----------------|-------|
| Human operator | Optional `hudhud-nats-operator` only | Break-glass; not for app traffic |
| Gateway / mobile user | **No** | HTTP Identity JWT only |
| Legacy Event Bridge pod | `legacy-event-bridge` | Publishes observations |
| Audit worker pod | `audit` | Consumes A2 only |
| Future domain service | `{service_id}` | Scoped to owning context subjects |

---

## Permissions model

### Domain subjects — Legacy Event Bridge (`legacy-event-bridge`)

**Publish allow (only):**

```text
hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1
hudhud.audit.legacy_bridge.observation.audit_entry.v1
```

**Publish deny:** all other `hudhud.>`, all `$JS.API.STREAM.*`, all `$JS.API.CONSUMER.CREATE.>`, all `$JS.API.CONSUMER.DELETE.>`, `hudhud.dlq.>` (unless separate DLQ design accepted).

**Subscribe:** `_INBOX.>` only (PubAck and JetStream API replies).

**Must not:** wildcard publish `hudhud.>`; subscribe to domain event subjects; create consumers; publish canonical `shipment.fact.*` or `audit.fact.*`.

**[evidence]** Application allowlist already enforces A1/A2 in `legacy_event_bridge/infrastructure/nats/subjects.py` — NATS ACLs are the broker-level backstop.

### Domain subjects — Audit (`audit`)

**Subscribe:** none required for domain subjects (pull consumer delivers via JetStream API + inbox).

**Publish allow:**

- `$JS.API.CONSUMER.MSG.NEXT.HUDHUD_AUDIT.audit_bridge_entry_v1` — pull fetch ([`JSApiRequestNextT`](https://github.com/nats-io/nats-server/blob/main/server/jetstream_api.go))
- `$JS.API.CONSUMER.INFO.HUDHUD_AUDIT.audit_bridge_entry_v1` — binding verification at startup/readiness
- `$JS.ACK.HUDHUD_AUDIT.audit_bridge_entry_v1.>` — ACK, NAK, in-progress, term ([`$JS.ACK.<stream>.<consumer>.>`](https://github.com/nats-io/nats.docs/blob/master/using-nats/jetstream/nats_api_reference.md))

**Optional narrow grant if flow control enabled for pull:**

- `$JS.FC.HUDHUD_AUDIT.>` — flow control ([nats-server `jsFlowControlPre`](https://github.com/nats-io/nats-server/blob/main/server/jetstream_api.go))

**Subscribe allow:** `_INBOX.>` — request-reply responses for CONSUMER.INFO and MSG.NEXT.

**Explicit deny / omit:**

- All `hudhud.>` publish (Audit is not a publisher in Wave 7)
- `$JS.API.CONSUMER.CREATE.>`, `$JS.API.STREAM.*`
- Any durable other than `audit_bridge_entry_v1`
- Any stream other than `HUDHUD_AUDIT` for consumer API paths

**DLQ publish:** **not granted** unless a separate ADR accepts Audit DLQ publisher design.

**[evidence]** Audit binds with `pull_subscribe_bind` + `consumer_info` only — never `add_consumer` (`audit/infrastructure/jetstream/connection.py`).

### Topology bootstrap / admin (`hudhud-eventing-bootstrap`)

**Publish allow (minimum):**

```text
$JS.API.STREAM.CREATE.>
$JS.API.STREAM.UPDATE.>
$JS.API.STREAM.INFO.>
$JS.API.STREAM.NAMES
$JS.API.STREAM.LIST
$JS.API.CONSUMER.CREATE.>
$JS.API.CONSUMER.INFO.>
```

**Subscribe:** `_INBOX.>`

**Operational deny (recommended):** `$JS.API.STREAM.DELETE.>`, `$JS.API.STREAM.PURGE.>` except in explicit maintenance windows with separate break-glass identity.

**Must not:** publish `hudhud.>` business events (admin is not a domain producer).

**[evidence]** `bootstrap_topology.py` uses `add_stream` / `add_consumer` — maps to STREAM.CREATE and CONSUMER.CREATE API paths.

### Future services (template)

**[proposal]** Each future deployable receives:

| Role | Publish | Subscribe |
|------|---------|-----------|
| Publisher (e.g. `shipment`) | `hudhud.{context}.>` subset per contract registry | `_INBOX.>` |
| Consumer (e.g. `tracking`) | `$JS.API.CONSUMER.MSG.NEXT.{stream}.{durable}`, `$JS.ACK.{stream}.{durable}.>`, `$JS.API.CONSUMER.INFO.{stream}.{durable}` | `_INBOX.>` |
| Finance/wallet (when unblocked) | per ADR-0005 contracts only | `_INBOX.>` |

Consumer CREATE remains bootstrap-only; services bind to pre-provisioned durables (Audit precedent).

---

## JetStream API authorization (evidence-backed)

Domain subject permissions **alone are insufficient** because JetStream operations use request-reply on `$JS.API.*` subjects ([NATS authorization — JetStream timeouts on missing grants](https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization)).

### Publisher waiting for PubAck (Bridge)

| Operation | NATS permission | Evidence |
|-----------|-----------------|----------|
| Publish message to stream | **Publish** to exact domain subject (A1 or A2) | NATS docs: stream publish uses the message subject, not `$JS.API.STREAM.PUBLISH` for client publish ([Stack Overflow / NATS maintainer guidance](https://stackoverflow.com/questions/73357175/nats-how-to-set-the-subscribe-and-publish-permission-when-using-request-reply-in)) |
| Receive PubAck | **Subscribe** `_INBOX.>` | Request-reply pattern |
| Optional stream info | **Publish** `$JS.API.STREAM.INFO.{STREAM}` | Only if runtime verifies stream existence — Bridge uses PubAck stream field instead |

Bridge `js.publish()` path ([`LiveNatsJetStreamClient._async_publish`](services/legacy_event_bridge/src/legacy_event_bridge/infrastructure/nats/client.py)): publish domain subject + headers `Nats-Msg-Id`; validate `ack.stream` against topology mapping.

**Minimum Bridge JWT permissions:**

```text
publish:
  - hudhud.shipment.legacy_bridge.observation.shipment_timeline_entry.v1
  - hudhud.audit.legacy_bridge.observation.audit_entry.v1
subscribe:
  - _INBOX.>
```

### Bind-only durable pull consumer (Audit)

| Operation | NATS permission | Evidence |
|-----------|-----------------|----------|
| Verify consumer exists | **Publish** `$JS.API.CONSUMER.INFO.HUDHUD_AUDIT.audit_bridge_entry_v1` | [`JSApiConsumerInfoT`](https://github.com/nats-io/nats-server/blob/main/server/jetstream_api.go) |
| Bind pull subscription | Client `pull_subscribe_bind` — no CREATE | [`bind_existing_pull_consumer`](services/audit/src/audit/infrastructure/jetstream/connection.py) |
| Fetch batch | **Publish** `$JS.API.CONSUMER.MSG.NEXT.HUDHUD_AUDIT.audit_bridge_entry_v1` | [`JSApiRequestNextT`](https://github.com/nats-io/nats-server/blob/main/server/jetstream_api.go); [nats-server #3295](https://github.com/nats-io/nats-server/issues/3295) |
| Receive fetched messages | **Subscribe** `_INBOX.>` | Per-fetch delivery inbox |
| Flow control (if enabled) | **Subscribe** `$JS.FC.HUDHUD_AUDIT.>` | [`jsFlowControlPre`](https://github.com/nats-io/nats-server/blob/main/server/jetstream_api.go) |

### ACK / NAK / defer

| Operation | NATS permission | Evidence |
|-----------|-----------------|----------|
| ACK / NAK / in-progress / term | **Publish** `$JS.ACK.HUDHUD_AUDIT.audit_bridge_entry_v1.>` | [JetStream API reference — `$JS.ACK.<stream>.<consumer>.>`](https://github.com/nats-io/nats.docs/blob/master/using-nats/jetstream/nats_api_reference.md); [`JetStreamBrokerAckClient`](services/audit/src/audit/infrastructure/jetstream/broker.py) |

**Note:** NATS response permissions (`allow_responses`) may further restrict ACK to messages received by the connection — **[proposal]** enable in production JWT where supported.

### Consumer-info verification (readiness)

Audit readiness calls `consumer_info` before work ([`verify_nats_readiness`](services/audit/src/audit/infrastructure/jetstream/connection.py)) — requires `$JS.API.CONSUMER.INFO.HUDHUD_AUDIT.audit_bridge_entry_v1` publish + `_INBOX.>` subscribe.

**Must not grant** `$JS.API.>` wildcard when narrower subjects suffice.

---

## TLS and local development policy

### Production / staging

| Requirement | Policy |
|-------------|--------|
| TLS | **Required** — encrypted transport |
| Server verification | **Required** — services MUST verify server certificate against configured trust bundle (`nats_tls_ca_file` / system CA) |
| Client certificates | **Optional** in v1 — not required for initial O6 acceptance; **[unresolved policy]** for future mTLS layering |
| Hostname verification | **Required** — TLS Server Name must match intended NATS endpoint |
| Plaintext NATS | **Forbidden** outside explicit local/test profile |

**[evidence]** Bridge and Audit already gate production on `nats_tls_enabled` when credentials are required.

### Local disposable development

| Control | Policy |
|---------|--------|
| `NATS_AUTH_ENABLED=false` | **Permitted** only in disposable local Compose / `local`/`test` runtime environment |
| `LEGACY_BRIDGE_NATS_DEV_NO_AUTH` / `AUDIT_ALLOW_NO_AUTH_LOCAL` | **Permitted** only when environment is `local` or `test` — **forbidden** in `production` |
| Production-like local profiles | **Encouraged** — dev JWT users mirroring production ACL shape |

**[decision boundary]** Current Compose no-auth behavior is a **development escape hatch**, not an approved production control. Documentation and operators MUST NOT cite `defaults.env.example` as production authorization.

### Secret handling

- **Forbidden:** credentials, tokens, seeds, or `.creds` contents in repository, logs, test fixtures, or ADR text.
- Configuration audits list **names only** (`NATS_CREDS_FILE`, `AUDIT_NATS_USER`, …).
- Clients MUST NOT log connection URLs with embedded credentials (Audit `log_connection_failure` precedent).

---

## Rotation and revocation

### Overlapping rotation (proposal)

1. **Issuance** — platform secret store generates new user JWT + NKey seed (or password for interim O2/O3).
2. **Activation** — mount new creds as canary pod / secondary file path; configure dual-trust if TLS certs rotate independently.
3. **Dual-validity window** — old and new credentials both authenticate during bounded overlap (**[unresolved policy]** exact window duration — do not freeze arbitrary production interval).
4. **Readiness verification** — `/ready` must pass with new creds: Bridge `nats_reachable` + publish smoke; Audit `nats_binding_verified` + `consumer_info`.
5. **Traffic cutover** — roll deployment to new secret version.
6. **Revocation** — remove old JWT from account; force disconnect if needed; audit log revocation event.
7. **Rollback** — re-mount prior secret version; old JWT valid until explicitly revoked.

### Maximum credential lifetime

**[unresolved policy]** Production JWT `exp`, password rotation cadence, and TLS cert TTL require named security/ops deciders. This ADR does **not** freeze values (e.g. "90 days") without evidence.

**[proposal]** User JWT lifetime SHOULD be short enough to limit exposure (hours–days for runtime pods) with automated re-issuance at deploy time; operator credentials shorter still.

### Emergency revocation

| Scenario | Action |
|----------|--------|
| Compromised Bridge creds | Revoke `legacy-event-bridge` user; Bridge `/ready` → not ready; publishing fail-closed; rotate A1/A2 unaffected consumers if inbox poisoned |
| Compromised Audit creds | Revoke `audit` user; consumer stalls; no topology mutation |
| Compromised bootstrap creds | Revoke admin user; freeze topology changes; rotate before resuming bootstrap |
| Compromised operator JWT signing key | Operator key rotation per NATS account procedure |

### Audit evidence for rotation

**[proposal]** Each rotation drill produces: ticket id, timestamp, identity rotated, dual-validity start/end, readiness probe output (pass/fail), revocation confirmation, and connection count before/after — stored in operational audit (not JetStream).

---

## Failure behavior (fail-closed)

| Condition | Bridge behavior | Audit behavior | `/health` | `/ready` |
|-----------|-----------------|----------------|-----------|----------|
| Missing credentials (prod) | `NatsNotConfiguredError`; relay does not publish | `NatsAuthRequiredError`; worker does not start | **ok** (process up) | **not ready** — `production_gates_unset` / `nats_*` blockers |
| TLS verification failure | Connection error; publish fails; outbox backs up | Connection/binding fails | **ok** | **not ready** — `nats_unreachable` / TLS blocker |
| Incorrect account/user | Authorization violation; publish error `NATS_ACL_DENIED` | Fetch/info fails | **ok** | **not ready** when NATS enabled |
| Subject permission denial | `NatsAclDeniedError`; outbox row retries/quarantines per ADR-0008 | Fetch fails; pull loop retries | **ok** | **not ready** if probe detects |
| Durable binding denial | N/A (publisher) | `ConsumerBindingMismatchError` — process fails closed | **ok** | **not ready** — `nats_binding_unverified` |
| Topology mismatch (PubAck stream) | `StreamMismatchError`; no `published_at` | N/A | **ok** | **not ready** when relay active and misconfigured |
| Expired / revoked credential | Auth failure; fail-closed publish/consume | Same | **ok** | **not ready** |
| Rotation partial failure | Canary `/ready` fails — roll back; old creds remain | Same | **ok** | canary **not ready** — blocks rollout |

**[decision boundary]** `/health` remains a **liveness** probe (HTTP process responding). `/ready` aggregates configuration, database, and **optional NATS dependency checks** when enabled — it MUST NOT be used as a synthetic cross-service dependency chain beyond NATS binding for the service's own consumer/publisher role.

---

## Wave 7 local evidence boundary (W7-A)

**[evidence]** W7-A (`infra/labs/observation-eventing-proof/`, `tests/observation_eventing_proof/`) proves **local disposable** A2 runtime behavior: Legacy Event Bridge outbox relay publishes to JetStream; Audit pull consumer binds to durable `audit_bridge_entry_v1`, fetches, ACKs, and persists inbox/observation rows against real PostgreSQL and JetStream in a dedicated no-auth Compose lab.

**[evidence]** W7-A proves the current Bridge publisher and Audit consumer can interoperate with real PostgreSQL and JetStream. It does **not** prove production credentials, TLS, ACL enforcement, HA, staging CDC, or production capacity.

**[decision boundary]** Local functional evidence may be recorded as **available** for gates G9 and G10. Staging identity/TLS/ACL evidence remains **open**. G9 and G10 MUST NOT be marked globally complete until scoped-credential proof runs in staging or production-like environments.

**[decision boundary]** ADR-0010 remains **Proposed**. W7-A local no-auth evidence MUST NOT be converted into a staging or production acceptance claim.

---

## Wave 8 local NATS security evidence boundary (W8-B)

**[evidence]** W8-B (`infra/labs/nats-security-proof/`, `tests/nats_security_proof/`) proves **local disposable** JWT/NKeys + TLS transport against a dedicated Compose lab. The proof exercised Bridge publish, Audit bind/pull/ACK, and Tracking-scoped ACL entries from `identity-manifest.yaml` — not production or staging endpoints.

**[evidence]** Positive cases verified locally:

- Per-deployable user JWT (`.creds`) authentication with TLS server verification against a generated CA bundle
- Exact scoped ACLs for `legacy-event-bridge`, `audit`, and `tracking` identities (no broad `$JS.API.>` runtime grant)
- Negative authorization cases: Bridge cannot publish wildcard domain subjects; Audit and Tracking cannot `$JS.API.CONSUMER.CREATE`; runtime identities cannot mutate topology
- Credential overlap/rotation drill with dual-validity publisher and consumer identities (`v1`/`v2`)
- Targeted revocation via account JWT refresh — propagation in this lab required **NATS server container restart** after copying the refreshed account JWT to the resolver directory (2s resolver interval alone was insufficient)

**[evidence]** Tracking Wave 8-A implementation and tests use **fakes/mocks only** — no live PostgreSQL migration proof and no secured JetStream runtime proof for Tracking in W8-A.

**[decision boundary]** W8-B local disposable evidence may be recorded as **available** for ACL/TLS model validation in a lab. Staging identity/TLS/ACL evidence, HA cluster replay, production secret delivery, and operational governance remain **open**.

**[decision boundary]** ADR-0010 remains **Proposed**. W8-B local evidence MUST NOT be converted into staging or production acceptance claims. ADR-0004 status is unchanged.

---

## Implementation gates

Production NATS transport security is **blocked** until:

| Gate | Evidence required |
|------|-------------------|
| G1 | **ADR-0010 accepted** with named deciders (platform architecture + security operations) |
| G2 | Selected model **O6** implemented in server/account configuration (or documented interim O2/O3 with expiry date) |
| G3 | Server/account JWT topology: operator, account, service users issued |
| G4 | **Exact ACL verification** — automated negative tests: Bridge cannot publish `hudhud.shipment.>`; Audit cannot `$JS.API.CONSUMER.CREATE`; bootstrap cannot publish `hudhud.*` events — **local disposable evidence available (W8-B); staging replay open** |
| G5 | **Secret delivery mechanism** chosen and documented (K8s secrets / Vault / etc.) — names only in repo — **open** |
| G6 | **Rotation drill** — dual-validity + rollback executed in staging — **local overlap verified (W8-B); staging drill open** |
| G7 | **Revocation drill** — emergency revoke + readiness failure observed — **local targeted revocation verified (W8-B); staging drill open** |
| G8 | **TLS verification** — staging with production-like TLS trust (no `allow_no_auth`) — **local TLS + CA verification verified (W8-B); staging open** |
| G9 | **Bridge publisher live proof** — local functional evidence **available** (W7-A no-auth; W8-B scoped JWT/TLS/ACL); staging scoped-creds + TLS/ACL proof **open** |
| G10 | **Audit bind/pull/ACK proof** — local functional evidence **available** (W7-A no-auth; W8-B scoped JWT/TLS/ACL); staging scoped-creds + TLS/ACL proof **open** |
| G11 | **Monitoring and audit logs** — connection identity, auth failures, ACL violations (no secret values) |
| G12 | **Three-node compatibility evidence** — same JWT/account model on clustered NATS (config replay or staging cluster) |

---

## Consequences

### Positive

- Clear separation of NATS transport auth from ADR-0004 human/HTTP trust.
- Least-privilege ACLs aligned with A1/A2 and Audit bind-only consumer.
- JWT model supports rotation, revocation, and HA without redeploying static password files.
- Broker ACLs backstop application allowlists (defense in depth).

### Negative

- Operational overhead vs current no-auth Compose.
- JetStream API permission matrix must be maintained per durable/stream.
- Pull consumers require `_INBOX.>` and precise `$JS.API.*` grants — easy to misconfigure (silent timeouts).
- Dual rotation (TLS + JWT) adds coordination.

### Neutral

- HTTP service JWT (ADR-0004) remains orthogonal — services carry two credential types.
- Application subject allowlists remain valuable even after broker ACLs deploy.

---

## Migration impact

- **No schema or contract changes.**
- **No topology changes** — streams/durables unchanged (`infra/eventing/topology/`).
- **Staged rollout:** enable auth on staging NATS → issue service JWTs → flip `adr_0004_credentials_configured` / remove dev no-auth flags → production.
- **Bridge/Audit code** already anticipates gates — implementation is configuration + secret mount, not new business logic (out of this ADR scope).
- **Bidirectional dual-write:** forbidden (unchanged).

---

## Observability

**[proposal]**

| Signal | Purpose |
|--------|---------|
| `nats_connection_auth_failures_total{service}` | ACL / credential failures |
| `nats_connection_last_success_timestamp{service}` | Staleness detection |
| `nats_acl_denied_total{service,operation}` | Permission misconfiguration |
| `bridge_publish_acl_denied_total` | Bridge subject violations |
| `audit_pull_acl_denied_total` | Consumer API violations |
| Readiness blockers | `nats_binding_unverified`, `nats_unreachable`, `nats_tls_required_in_production` |

Logs: connection user/NKey id, error class — **never** passwords, seeds, JWT bodies, or creds file contents.

---

## Security

- Least privilege per deployable NATS user.
- Topology admin isolated from runtime pods.
- TLS required production; no-auth forbidden outside local/test.
- No secrets in repository or logs.
- Envelope actor fields not trusted for authorization.
- This ADR does **not** change Identity ownership or resolve Customer/Organization (ADR-0004 status unchanged).

---

## Rollback

| Stage | Action |
|-------|--------|
| Pre-acceptance | Discard ADR — no implementation |
| Staging auth enabled | Re-enable previous creds; or `NATS_AUTH_ENABLED=false` in disposable env only |
| Production partial rollout | Roll deployment; dual-validity old JWT |
| ACL misconfiguration | Widen **staging** ACL temporarily — never `>` wildcard in production; fix-forward narrow grants |
| Irreversible | Published events during ACL outage remain in JetStream — reconcile via inbox idempotency |

---

## Unresolved questions

1. **[unresolved policy]** Exact user JWT `exp` and rotation cadence for runtime vs operator identities.
2. **[unresolved policy]** Secret store selection (Vault, cloud SM, K8s secrets) and issuance automation owner.
3. **[unresolved policy]** Whether to require NATS `allow_responses` for ACK hardening in v1.
4. **[unresolved policy]** Client certificate (mTLS) introduction timeline as O5 overlay.
5. **[unresolved policy]** Account boundaries — single HUDHUD account vs per-environment accounts.
6. **[unresolved policy]** Audit DLQ publish permissions if poison quarantine routes to `HUDHUD_DLQ`.
7. **[unresolved policy]** Staging parity — must staging always mirror production ACL shape?
8. **[assumption]** NATS server version supports scoped `$JS.API.CONSUMER.MSG.NEXT.{stream}.{durable}` — verify against pinned `nats:2.x` image at implementation.

---

## Alternatives considered

| Alternative | Why rejected or deferred |
|-------------|-------------------------|
| O1 Shared password | Unacceptable blast radius |
| Broad `$JS.API.>` for all services | Violates least privilege; hides misconfigurations |
| Runtime consumer CREATE by services | Audit bind-only precedent; bootstrap owns topology |
| Wildcard `hudhud.>` Bridge publish | Violates A1/A2-only decision |
| Resolving ADR-0004 Customer boundary here | Explicit non-goal |
| mTLS as sole identity (O5) | Ops cost; deferred |
| Treating Compose no-auth as production | Contradicts runbook and service production gates |

---

## Explicit non-goals

- Implementing NATS server configuration or committing secrets
- Changing Bridge, Audit, or other service code
- Changing Compose files
- Changing eventing topology, contracts, or architecture YAML
- Changing ADR-0004 status or Identity/Customer/Organization ownership
- Claiming production readiness
- Accessing or citing legacy repository evidence (out of scope for this workstream)

---

## References

- ADR-0002 — JetStream topology and ACL intent
- ADR-0004 — Identity and HTTP service trust (Proposed; not extended here)
- ADR-0007 — Legacy Event Bridge strategy
- ADR-0008 — Outbox/inbox processing
- ADR-0009 — A1/A2 observation contracts
- `infra/eventing/subject-grammar.md`, `topology/streams.yaml`, `topology/consumers.yaml`
- `infra/eventing/runbook.md`, `scripts/bootstrap_topology.py`
- `services/legacy_event_bridge/src/legacy_event_bridge/infrastructure/nats/`
- `services/audit/src/audit/infrastructure/jetstream/`
- NATS JetStream API: https://docs.nats.io/reference/reference-protocols/nats_api_reference
- NATS authorization: https://docs.nats.io/running-a-nats-service/configuration/securing_nats/authorization
- nats-server `jetstream_api.go` — `$JS.API.CONSUMER.INFO`, `$JS.API.CONSUMER.MSG.NEXT`, `$JS.ACK`

---

## Output contract

```text
ADR path: docs/adr/0010-nats-service-identities-subject-acls-and-rotation.md
Status: Proposed
Deciders: (pending — platform architecture review; security operations)
Canonical docs updated: docs/adr/README.md (index only)
Unresolved questions: 8 (see section above)
Implementation allowed: no (gates G1–G12)
```
