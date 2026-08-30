# Legacy Domain Inventory

File-level evidence for each target bounded context area in the legacy monolith. Classifications: **verified**, **partial**, **missing**, **ambiguous**, **legacy-only**, **policy-blocked**.

Audit source: `/Users/mohammadakbari/Development/Projects/Python/hudhud-backend` @ `2e375057fdf9b9ce8416408a4436303be5301def`.

---

## Auth and Identity — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/auth/api/routes.py`, `admin_routes.py`, `session_routes.py`, `two_factor_routes.py`, `service_routes.py`, `service_client_admin_routes.py` |
| Use cases | OTP login/register, JWT refresh, MFA, invitations, admin users, merchant scope, driver profiles, legal docs |
| Repos/ORM | `app/modules/auth/infrastructure/models.py`, `repositories.py` |
| Migrations | `e6f7a8b9c0d1`, `f7b8c9d0e1f2`, `d1e2f3a4b5c6`, `p5q6r7s8t9u0` |
| Roles | `app/core/db/auth_rbac_seed.py` — SUPER_ADMIN, OPERATIONS_ADMIN, MERCHANT_*, PICKUP_DRIVER, HUB_OPERATOR, LINEHAUL_OPERATOR, CONTROL_TOWER_OPERATOR, CUSTOMER, RESELLER, DELIVERY_DRIVER |
| External | SMS OTP: `auth/infrastructure/http_sms_otp_sender.py` |
| Tests | `tests/unit/modules/auth/`, `tests/integration/modules/auth/` |

---

## Customer — partial

No standalone `customer` module. Identity and profile live in auth; parcel/tracking/support spread across modules.

| Layer | Evidence |
|-------|----------|
| Identity | `auth/application/customer_otp_registration.py`, `update_my_profile.py`, `accept_legal_documents.py` |
| Routes | `/api/v1/auth/*` (me, profile, legal) |
| Features | Address book, send-parcel, tracking, customer-shipments, support claims |
| Tests | `tests/integration/modules/auth/test_customer_profile_legal_acceptance_api.py`, `tests/integration/customer_app/` |

---

## Address Book — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/address_book/api/routes.py` |
| Use cases | Pickup addresses + receiver contacts CRUD, defaults, scope policy |
| ORM | `app/modules/address_book/infrastructure/models.py` |
| Migration | `j7e8f9a0b1c2_add_customer_rbac_and_address_book.py` |
| Cross-module FK | `order/infrastructure/models.py` → `receiver_contacts`, `pickup_addresses` |
| Tests | `tests/unit/modules/address_book/`, integration mirror |

---

## Merchant and Store — verified

| Layer | Evidence |
|-------|----------|
| Routes | `merchant/api/routes.py`, `store_branches_routes.py`, `store_categories_routes.py`, `store_products_routes.py`, `store_team_routes.py`, `store_workplace_routes.py`, `master_categories_routes.py` |
| Subdomains | Branches, categories/products, team invitations, warehouse/workplace ops, shipment preparation |
| ORM | `merchant/infrastructure/models.py`, `store_team_models.py`, `store_warehouse_models.py`, `product_models.py` |
| Applications | Separate module `merchant_applications` (submit/approve/reject) |
| Tests | Extensive under `tests/unit|integration/modules/merchant/` and `merchant_applications/` |

---

## Serviceability — partial

Embedded in send_parcel; no standalone module.

| Layer | Evidence |
|-------|----------|
| Domain | `app/modules/send_parcel/domain/serviceability.py` |
| Config | `ORDER_SUPPORTED_CITY_AREAS` env; fail-closed in staging/prod (`app/core/config.py`) |
| Routes | None standalone |
| Tests | `tests/unit/core/test_serviceability_fail_closed.py` |

City:area allowlist only — no geo/routing engine.

---

## Pricing and Quote — partial

Send-parcel scoped only.

| Layer | Evidence |
|-------|----------|
| Domain | `app/modules/send_parcel/domain/pricing.py` (`PRICING_RULE_VERSION = send_parcel_v1`) |
| Use cases | `preview_send_parcel.py`, `confirm_send_parcel.py`, `quote.py` |
| Routes | `app/modules/send_parcel/api/routes.py` |
| Tests | `tests/unit/modules/send_parcel/test_send_parcel_pricing.py` |

IQD flat tariff; no merchant-specific pricing tables.

---

## Order — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/order/api/routes.py` |
| Use cases | `create_order.py`, `validate_order.py`, address book snapshots |
| ORM | `app/modules/order/infrastructure/models.py` |
| Migrations | `c3a8f1d92e41`, `d4e2a8b91c03`, `i4j5k6l7m8n9`, `q4e5f6a7b8c9` |
| Cross-module FK | → merchants, receiver_contacts, pickup_addresses, store_locations |

---

## Send Parcel — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/send_parcel/api/routes.py` — preview + confirm |
| Flow | Creates order + shipment; idempotency + request fingerprint |
| COD policy | `app/modules/send_parcel/domain/cod_eligibility.py` (seller-only COD) |
| Migrations | `k7l8m9n0o1p2`, `q6r7s8t9u0v1`, `h3i4j5k6l7m8` |
| Tests | `tests/integration/modules/send_parcel/test_send_parcel_api.py` |

---

## Shipment — verified

| Layer | Evidence |
|-------|----------|
| Routes | `shipment/api/routes.py`, `customer_shipments_routes.py`, `store_shipments_routes.py`, `operations_routes.py` |
| ORM | `shipment/infrastructure/models.py` (shipments, shipment_events, delivery evidence) |
| Status enum | `shipment/domain/enums.py` — CREATED → … → DELIVERED/FAILED/CANCELLED |
| Tests | Extensive unit + integration + customer outcome contracts |

**Note for platform:** Legacy allows multiple modules to mutate shipment status directly (see data-ownership inventory). Platform policy requires Shipment as sole canonical lifecycle writer.

---

## Pickup — verified

| Layer | Evidence |
|-------|----------|
| Routes | 10 route files: batches, tasks, shifts, handover, evidence, courier verification, recovery, ops review, notifications |
| ORM | `pickup/infrastructure/models.py` |
| Storage prefix | `pickup-evidence/{owner_type}/{owner_id}/{evidence_file_id}/{file}` |
| Related | `pickup_scheduling/` — customer scheduling + ops confirm/reject/cancel (**verified**) |
| Cross-module | Writes shipment status on acceptance scan |

---

## Hub — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/hub/api/routes.py` |
| Use cases | `origin_hub_inbound_scan.py`, `origin_hub_condition_check.py` |
| ORM | `hub/infrastructure/models.py` |
| Migration | `c3d4e5f6a7b8_add_hubs_and_hub_operations.py` |
| Shipment writer (legacy) | `origin_hub_inbound_scan.py` → AT_ORIGIN_HUB |

---

## Linehaul — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/linehaul/api/routes.py` |
| Use cases | create, assign, dispatch, arrive |
| ORM | `linehaul/infrastructure/models.py` |
| Migration | `d4e5f6a7b8c9_add_linehaul_trips_and_shipments.py` |
| Shipment writers (legacy) | `dispatch_linehaul_trip.py`, `arrive_linehaul_trip.py` |

---

## Delivery — verified

Split across `delivery` (OTP) and `delivery_task` (last-mile).

| Submodule | Role | Key path |
|-----------|------|----------|
| delivery | Customer/recipient OTP | `delivery/api/routes.py`, Redis OTP store |
| delivery_task | Driver tasks, completion, COD | `delivery_task/` (~50 files) |

| Layer | Evidence |
|-------|----------|
| ORM | `delivery_task/infrastructure/models.py` (tasks, evidence, delivery_cod_collections) |
| Migration | `m9n0o1p2q3r4`, evidence migrations |
| Shipment writers (legacy) | start/complete/fail delivery task use cases |
| Tests | `tests/integration/modules/delivery_task/` |

---

## Tracking — verified

| Layer | Evidence |
|-------|----------|
| Routes | `app/modules/tracking/api/routes.py` |
| Persistence | Read-only projection from shipments/events (no dedicated tracking tables) |
| Migration | `l9a0b1c2d3e4_add_tracking_read_permission.py` |
| Tests | `tests/integration/modules/tracking/` |

---

## Control Tower — partial

Implemented despite "future" label in cursor rules.

| Layer | Evidence |
|-------|----------|
| Routes | `control_tower/api/routes.py` — search, shipment detail, proof detail |
| Use cases | Read-only aggregation from shipment/proof data |
| ORM | None in control_tower module |
| Permission | CONTROL_TOWER_OPERATOR role |
| Tests | `tests/unit/modules/control_tower/` |

Ops visibility API only — no full tower workflows.

---

## Wallet and COD — partial

| Layer | Evidence |
|-------|----------|
| Routes | `wallet/api/routes.py` — account, ledger, payout requests |
| ORM | `wallet/infrastructure/models.py` — wallet_accounts, wallet_ledger_entries, payout_requests |
| Migrations | `p3e4f5a6b7c8`, `t0u1v2w3x4y5` |
| COD collection | `delivery_task` → `delivery_cod_collections` on complete |
| Wallet credit | `wallet/application/credit_cod_collection.py` — idempotent `cod_collected:{shipment_id}` |
| Orchestration | `delivery_task/application/complete_delivery_task.py` |
| Tests | `tests/unit/modules/wallet/`, `tests/integration/modules/delivery_task/test_cod_wallet_credit_api.py` |

COD→ledger credit verified; payout requests exist; no settlement pipeline.

---

## Finance and Settlement — missing / policy-blocked

| Evidence | Detail |
|----------|--------|
| No module | No `finance` or `settlement` under `app/modules/` |
| Deferral | `credit_cod_collection.py`: "Does not settle, pay out, or reconcile" |
| Docs | `docs/production_v1/store_wallet_phase_1b.md` — deferred finance boundaries |

---

## Notification — verified

| Layer | Evidence |
|-------|----------|
| Routes | preferences, device tokens, in-app feed |
| ORM | preferences, device_tokens, in_app_notifications, push_outbox |
| Event catalog | `notification/domain/event_catalog.py` |
| Workers | `scripts/run_push_outbox_worker.py`, `dispatch_push_outbox.py` |
| External | FCM: `notification/infrastructure/fcm_push_provider.py` (dry-run default) |
| Cross-module | `emit_shipment_notifications` from shipment lifecycle |

---

## Support and Claims — verified

| Module | Role | Key path |
|--------|------|----------|
| support | Support tickets + customer support claims | `support/api/` (7 route files) |
| claims | Separate claims/refunds module | `claims/api/routes.py` |

Storage prefix: `support-claims/{claim_id}/{attachment_id}/{file}`.

---

## Media and Proof — partial

| Component | Status | Path |
|-----------|--------|------|
| proof module | legacy-only stub | `proof/api/routes.py` — only `/ping` |
| proof ORM | verified | `proof/infrastructure/models.py` |
| Pickup evidence | verified | pickup evidence routes + MinIO upload intents |
| Delivery evidence | verified | shipment + delivery_task evidence attachments |
| Cleanup worker | verified | `shipment/application/delivery_evidence_attachment_cleanup_worker.py` |

Storage prefixes: `pickup-evidence/...`, `delivery-evidence/{shipment_id}/...`, `support-claims/...`.

---

## Audit — verified

| Layer | Evidence |
|-------|----------|
| Routes | `audit/api/routes.py` |
| ORM | `audit/infrastructure/models.py` |
| Cross-module | Written from auth, wallet, delivery, merchant ops via `AuditLogRepository` |
| Tests | `tests/unit/modules/audit/` |

---

## Summary Classification

| Area | Classification |
|------|----------------|
| Auth and Identity | verified |
| Customer | partial |
| Address Book | verified |
| Merchant and Store | verified |
| Serviceability | partial |
| Pricing and Quote | partial |
| Order | verified |
| Send Parcel | verified |
| Shipment | verified |
| Pickup (+ scheduling) | verified |
| Hub | verified |
| Linehaul | verified |
| Delivery (+ delivery_task) | verified |
| Tracking | verified |
| Control Tower | partial |
| Wallet and COD | partial |
| Finance and Settlement | missing / policy-blocked |
| Notification | verified |
| Support and Claims | verified |
| Media and Proof | partial |
| Audit | verified |
| PGMQ workers | missing (documented only) |
