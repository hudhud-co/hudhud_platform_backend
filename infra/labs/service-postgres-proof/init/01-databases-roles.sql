-- Disposable lab databases and least-privilege service roles (Wave 6-A).
-- Credentials are synthetic and scoped to this isolated Compose project only.

SELECT 'CREATE DATABASE bridge_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'bridge_db')\gexec

SELECT 'CREATE DATABASE audit_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'audit_db')\gexec

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bridge_svc') THEN
        CREATE ROLE bridge_svc WITH LOGIN PASSWORD 'bridge_svc_dev_only';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_svc') THEN
        CREATE ROLE audit_svc WITH LOGIN PASSWORD 'audit_svc_dev_only';
    END IF;
END
$$;

REVOKE ALL ON DATABASE bridge_db FROM PUBLIC;
REVOKE ALL ON DATABASE audit_db FROM PUBLIC;

GRANT CONNECT ON DATABASE bridge_db TO bridge_svc;
GRANT CONNECT ON DATABASE audit_db TO audit_svc;
