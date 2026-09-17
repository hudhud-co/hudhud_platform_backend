# 12 — Running the platform, and what running it found

Written for: an engineer who wants to start the platform and call its APIs.

## Start it

```bash
uv run python scripts/dev/stack.py up
```

That starts one PostgreSQL and one NATS container, creates **a database per service**
(ADR-0011 — not a schema per service), runs each service's own Alembic migrations against
its own database, and starts all fourteen FastAPI apps.

```bash
uv run python scripts/dev/stack.py status      # what is running, and is it ready
uv run python scripts/dev/stack.py logs finance
uv run python scripts/dev/stack.py down        # stop, and remove the data
```

The database lives in a tmpfs inside the container, so `down` leaves nothing on disk and
nothing touches a developer's own PostgreSQL.

| Service | URL | OpenAPI |
|---|---|---|
| identity | http://127.0.0.1:8101 | `/docs` |
| customer | http://127.0.0.1:8102 | `/docs` |
| merchant | http://127.0.0.1:8103 | `/docs` |
| ordering | http://127.0.0.1:8104 | `/docs` |
| hub | http://127.0.0.1:8105 | `/docs` |
| notification | http://127.0.0.1:8106 | `/docs` |
| workforce | http://127.0.0.1:8107 | `/docs` |
| delivery | http://127.0.0.1:8108 | `/docs` |
| finance | http://127.0.0.1:8109 | `/docs` |
| pickup | http://127.0.0.1:8110 | `/docs` |
| shipment | http://127.0.0.1:8111 | `/docs` |
| tracking | http://127.0.0.1:8112 | `/docs` |
| audit | http://127.0.0.1:8113 | `/docs` |
| claims | http://127.0.0.1:8114 | `/docs` |

**233 documented endpoints.** `audit` publishes only `/health` and `/ready` — it consumes
legacy observations and projects them, and has no command API by design.

## Exercise it

```bash
uv run python scripts/dev/journeys.py            # every journey
uv run python scripts/dev/journeys.py --only finance --verbose
```

Every call is a real HTTP request to a real service backed by real PostgreSQL, with real
Identity tokens. Nothing is mocked and nothing is imported across a service boundary.

```
106 steps passed · 7 blocked by a documented open item · 0 failed
```

A `BLOCKED` line is the platform refusing to invent a business decision. A `FAIL` line is
a bug, and the runner prints what it sent and what came back.

## Getting a token

Sign-in is phone OTP. Locally the code is printed to Identity's log rather than sent —
`load_settings` refuses `console` delivery outside local and test, so this cannot leak
into staging.

```bash
CH=$(curl -s -X POST localhost:8101/identity/otp/request \
      -H 'Content-Type: application/json' \
      -d '{"phone":"+9647700000001"}' | jq -r .challenge_id)

uv run python scripts/dev/stack.py logs identity | grep "reference=$CH"
# [identity][dev-otp] reference=… phone=***0001 code=013408

curl -s -X POST localhost:8101/identity/otp/verify \
  -H 'Content-Type: application/json' \
  -d "{\"challenge_id\":\"$CH\",\"code\":\"013408\",\"device_id\":\"my-device\"}"
```

The **first** principal to sign in with `+9647700000001` is granted `OPERATIONS`. That is
how a fresh platform gets its first administrator; every other grant is made from there.

Two endpoints need a service credential as well as a bearer token —
`/identity/tokens/introspect` and `/identity/me` — because both resolve a token, and
resolving a token is service-to-service. Granting a role needs both for the same reason:
the route introspects the operator's token to check they are Operations.

```bash
-H "X-Service-Credential: hudhud-dev-service-credential"
```

## What running it found

Five defects that every unit test passed over, because each lived in the space *between*
services. They are listed here because the pattern matters more than the individual bugs:
**a fake that is more generous than the real thing will hide a broken contract.**

### 1. Token introspection had never worked against PostgreSQL

`IntrospectionService.introspect` read the session, the principal and the grants without
opening a transaction. The in-memory double silently fell back to committed state; the
SQLAlchemy store raises. Since every service in the platform authorizes through that one
endpoint, **authorization was broken everywhere at once and nowhere in the tests.**

Fixed by opening one transaction — which is also correct on its own terms, since those
three reads have to agree with each other. The in-memory unit of work now **refuses a
read outside a transaction**, exactly as the store does, so the next such mistake fails in
unit tests. All 76 existing Identity tests still passed under the stricter double, which
says the rest of the service was already right.

### 2. Three services mapped role names Identity has never granted

`delivery`, `finance` and `workforce` each translated `LAST_MILE_DRIVER` and
`MERCHANT_OWNER` from introspection. Identity grants `DELIVERY_DRIVER` and
`MERCHANT_MEMBER`. An unrecognised name is *dropped*, so a real driver would authenticate
successfully and hold no driver role — a 403 on every route they need.

Every unit test passed because each service's fake authorizer handed out that service's
own vocabulary. Nothing compared the two.

Fixed by publishing `contracts/identity/roles.yaml` — Identity's actual vocabulary,
with each role's required scope. Identity has a test asserting the file matches its own
enum; each consumer has a boundary test asserting its map keys are all in the file. The
services keep their own domain words (`LAST_MILE_DRIVER` reads better in Delivery than
`DELIVERY_DRIVER` does); the adapter translates, which is what an adapter is for.

### 3. A merchant balance 404'd until someone administered the merchant

Finance required a `finance_merchant_accounts` row before it would report a balance. But
a balance lives in the ledger — the row only carries whether payouts are on hold. So
every merchant who had been paid and never administered, which is all of them, looked
like they did not exist.

Fixed: a missing row now means "no policy set, nothing blocked". Blocking payouts still
writes a row, because a block is a deliberate act that has to be recorded.

### 4. Six services could not be told which database to use

`identity`, `customer`, `pickup`, `shipment`, `tracking` and `audit` only accepted their
database URL as an Alembic CLI argument. The proof labs passed one, so nothing noticed.
They now read `<SERVICE>_DATABASE_URL` like the newer services. (`tracking`'s Alembic
environment also still described itself as Audit's.)

### 5. Finance read a `merchant_id` field Identity does not publish

Identity publishes scoped **grants**, not a top-level merchant. Finance now reads the
merchant from a `MERCHANT`-scoped grant, and treats two different merchant scopes as
ambiguous rather than guessing — guessing would hand one merchant another's balance.

## What is deliberately not ready

`/ready` reports these; none of them is a failure.

| Service | Blocker | Why |
|---|---|---|
| `identity` | `otp_delivery_configured` | Console delivery is local-only; a real transport is a deployment concern |
| `merchant` | `merchant_application_data_set_decided` | **MER-02** — the required application fields are a v6.3 Open Item |
| `delivery` | `delivery_code_length_decided` | **DRV-L05** — the Customer App says 4 digits, the Driver App collects 6 |
| `notification` | `sms_transport_configured`, `templates_loaded` | No transport wired locally; it refuses to send rather than pretending |
| `ordering` | `merchant_access_configured`, `serviceability_configured` | Cross-service reads not wired in this stack |
| `pickup` | `handover_signing_key_missing` | A real key is a deployment secret |
| `claims` | `high_value_threshold_decided` | **CLM-08** — the high-value declared-value threshold is a v6.3 Open Item |

Set `HUDHUD_DEV_DELIVERY_CODE_LENGTH=4` (or `6`) before `up` to try one reading of
DRV-L05 — the whole code path then works, which is the point of leaving it configurable.

## The seven blocked journey steps

| Step | Requirement | What is implemented around it |
|---|---|---|
| Submit a merchant application | MER-02 | The whole application, store and policy model; submission fails closed |
| The delivery-code length | DRV-L05 | Both readings reported by `GET /delivery/code-policy` |
| Verify with the delivery code | DRV-L05 | The ID fallback, the seal, the inspection, payment and all three outcomes |
| Retaining an ID photograph | DRV-L07 | The ID verification itself; no column exists to retain one in |
| Paying a payout out | PAY-07 | Request, method, destination, balance check, approval, rejection, both facts |
| Waiving the return-trip fee | PAY-08 | The charge itself — v6.3 p.38/p.39 settle it — posted to the ledger |
| Requesting a return after acceptance | CLM-06 | Filing a claim, which is the recourse that does remain |
| Asking whether a parcel is high value | CLM-08 | Filing, evidence, review, approval, rejection and the support thread, none of which ask |

`CLM-06` is the one entry here that is not waiting on anything. v6.3 settles it: the
decision at the door is final. It is listed as blocked because the route refuses, and the
refusal names the claim the receiver can still file.
