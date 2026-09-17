# HUDHUD Merchant Service

Bounded context owned: `merchant_store` (ADR-0011).

Merchant application and activation, stores and store locations, team membership, barcode
label and packaging-seal stock, and the standing shipment policy.

This service owns its own database, migrations, contracts and tests. It never reads
another service's tables and never imports another service's Python package: it resolves
callers by asking Identity over HTTP, exactly as Pickup does.

## Requirements covered

| ID | Requirement |
|----|-------------|
| MER-01 | Apply for merchant status; Hudhud reviews; approved → merchant, otherwise stays a regular customer and may re-apply |
| MER-03 | A merchant keeps a stock of **pre-printed** labels — there is no per-parcel print operation anywhere in this service |
| MER-04 | Self-printing only against a live Hudhud printer authorization |
| MER-10 | Open-box allowed per shipment or as standing policy, **off by default** |
| MER-11 | Photo-documentation add-on, free, opt-in |
| MER-12 | Hudhud packaging with per-parcel scannable seal stock |
| MER-14 | The sender sets open-box, delivery-fee payer, photo and packaging add-ons |
| MER-16 | Stores, branches and the default pickup point |
| MER-17 | Saved products and product categories |
| MER-20 | Store team: invite, accept, branches, remove |
| MER-21 | Store and merchant read models |
| SEC-05 | Store membership half of role-aware access |

**MER-02 is a v6.3 executive open item and is the one blocked operation.** p.10 records
that the merchant-application data set "has not yet been defined and needs a deliberate
answer before the application flow can be built", so:

* applications can be started, edited and withdrawn;
* `POST /merchant/applications/{id}/submit` is refused with
  `merchant_application_data_set_not_defined` until
  `MERCHANT_APPLICATION_REQUIRED_ATTRIBUTES` names the approved field list;
* `/ready` reports `merchant_application_data_set_decided: false` so an operator can see
  the flow is still closed;
* `GET /merchant/applications/requirements` tells the app the same thing.

Nothing about the field list is guessed. Configuring it opens the flow with no code change.

## Security model

Three distinct reaches, checked explicitly per route rather than inferred from "signed in":

* **Operations** decides applications, issues label stock and authorizes printers. An
  applicant can never decide their own case.
* **The owner** configures the merchant: branches, team, standing policy, catalogue.
* **A team member** (warehouse keeper) reads and prepares parcels and completes the
  courier handover — and can never create, edit or cancel a shipment, or see the wallet or
  COD figures. That ceiling is a denylist in the domain, not a per-member setting.

A membership grants nothing until the invitee accepts in their own account. Only the last
four digits of an invited number ever leave the service, and no published event carries a
phone number, an application's answers or a review reason.

## Events

| Event | Aggregate | Notes |
|-------|-----------|-------|
| `merchant.fact.application_decided` v1 | `merchant_application` | An approval names the merchant and code it created |
| `merchant.fact.team_membership_changed` v1 | `merchant` | Each change advances the merchant version, so consumers can order them |

Both are written to the transactional outbox in the same transaction as the state they
describe, validated against the registered JSON Schema before insertion, and restricted to
the Merchant subject allowlist.

## Validation

```bash
uv run --project services/merchant pytest services/merchant/tests   # 189 tests
uv run ruff check services/merchant
uv run pytest tests/new_service_migration_proof -m integration      # real PostgreSQL 16
```

## Audit

Requirements, evidence and gap analysis: `docs/audits/hudhud-app-redesign-v6.3/`.
