-- Disposable lab databases and least-privilege service roles (Wave 17).
-- Credentials are synthetic and scoped to this isolated Compose project only.

SELECT 'CREATE DATABASE pickup_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pickup_db')\gexec

SELECT 'CREATE DATABASE shipment_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'shipment_db')\gexec

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pickup_svc') THEN
        CREATE ROLE pickup_svc WITH LOGIN PASSWORD 'pickup_svc_dev_only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shipment_svc') THEN
        CREATE ROLE shipment_svc WITH LOGIN PASSWORD 'shipment_svc_dev_only';
    END IF;
END
$$;

REVOKE ALL ON DATABASE pickup_db FROM PUBLIC;
REVOKE ALL ON DATABASE shipment_db FROM PUBLIC;

GRANT CONNECT ON DATABASE pickup_db TO pickup_svc;
GRANT CONNECT ON DATABASE shipment_db TO shipment_svc;
