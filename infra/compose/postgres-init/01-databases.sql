-- A database per service, not a schema per service (ADR-0011).
--
-- The official postgres image runs this once, on first initialisation of an empty
-- data directory. The data lives in a tmpfs, so every `compose down` starts clean.

CREATE DATABASE "identity_db";
CREATE DATABASE "customer_db";
CREATE DATABASE "merchant_db";
CREATE DATABASE "ordering_db";
CREATE DATABASE "hub_db";
CREATE DATABASE "notification_db";
CREATE DATABASE "workforce_db";
CREATE DATABASE "delivery_db";
CREATE DATABASE "finance_db";
CREATE DATABASE "pickup_db";
CREATE DATABASE "shipment_db";
CREATE DATABASE "tracking_db";
CREATE DATABASE "audit_db";
CREATE DATABASE "claims_db";
