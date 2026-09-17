# HUDHUD Ordering Service

Bounded contexts owned: `order`, `send_parcel`, `pricing_quote`, `serviceability` (ADR-0011).

Order creation, shipment registration, label linking, pickup booking, edit rules,
cancellation, pricing and the public tracking-code lookup.

This service owns its own database, migrations, contracts and tests. It asks Identity who
a caller is and Merchant what they may do inside a merchant — both over HTTP, never by
import or by reading another service's tables.

## Where it stops

Ordering owns a parcel from the first keystroke until `REGISTERED`. Custody, checkpoint
scans and the delivery outcome belong to Shipment, which is the sole lifecycle writer
(ADR-0003). Duplicating any of that here would create a second source of truth about
where a parcel is.

## Requirements covered

| ID | Requirement |
|----|-------------|
| CUS-01 | Any individual can create a shipment without merchant status |
| CUS-02 | **A regular customer's parcel has no COD option** — refused on sender kind, not on amount |
| CUS-08 | Track by code without signing in |
| MER-05 | Enter details → stick a pre-printed label → scan it to link |
| MER-08 | Weight and size optional; a description is required |
| MER-09 | Prepaid / postpaid / COD |
| MER-13 | One order, many shipments |
| MER-15 | The sender may not set company-wide rules |
| MER-18 | Bulk creation, all or nothing |
| MER-19 | Pickup opens only once every parcel carries a label |
| MER-22 | Prohibited-goods notice and refusal |
| MER-23 | Goods taxonomy |
| SHP-01 | Order creation, the first stage of the journey |
| SHP-10 | Cancellation before custody |
| SHP-11 | Edit rules by stage, including courier release |
| SHP-12 | COD change must reach the courier before the door |

**SHP-02 is a v6.3 executive open item**: the 24-hour goal is measured from the acceptance
scan and stored, but whether to show the customer an exact delivery goal is undecided
(Appendix A p.44), so no countdown is exposed.

## Edit rules (SHP-11)

| Stage | What the sender may change | Effect |
|-------|---------------------------|--------|
| `UNASSIGNED` | everything | — |
| `COURIER_ASSIGNED` | everything | changing the pickup address or window **releases the courier** |
| `IN_CUSTODY` | nothing | support may correct the receiver, address and COD amount |
| `CLOSED` | nothing | — |

The stage is derived from where the parcel is, never stored: a stored copy would drift the
moment a courier was assigned.

## Money

Every amount is an integer count of minor units plus an explicit currency. There is no
float anywhere in the service, no `NUMERIC` money column, and amounts cross the wire and
the event bus as integers. A fractional amount is rejected by the API schema.

## Pricing and serviceability

Both are data, not code, and both fail closed when unset:

* an empty serviceable-governorate list serves nowhere, rather than everywhere;
* a route with no published tariff is refused with `tariff_not_configured` (503) rather
  than priced from a guess. v6.3 publishes no rates, and the numbers in the app's
  prototype screens are fixtures.

Loading the approved values opens both behaviours with no code change.

## Public tracking

`GET /track/{code}` takes no token. It returns a status and a destination governorate and
nothing else — no phone, name, address, COD amount or label code. Unknown, malformed and
cancelled codes all answer 404 identically, so the endpoint cannot confirm which codes
exist, and tracking codes are random rather than sequential so the space cannot be walked.

## Events

| Event | Aggregate | Notes |
|-------|-----------|-------|
| `order.fact.shipment_registered` v1 | `shipment_request` | The schema itself refuses COD on a customer parcel and a customer pickup |
| `order.fact.shipment_cancelled` v1 | `shipment_request` | Only for a parcel that never entered custody |

Both go through the transactional outbox, are validated against the registered JSON Schema
before insertion, and are restricted to the Ordering subject allowlist.

## Validation

```bash
uv run --project services/ordering pytest services/ordering/tests   # 198 tests
uv run ruff check services/ordering
uv run pytest tests/new_service_migration_proof -m integration      # real PostgreSQL 16
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
