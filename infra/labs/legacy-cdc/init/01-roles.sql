-- Lab-only roles for PostgreSQL logical decoding proof (ADR-0007 CDC lab).
-- Credentials are disposable and scoped to the isolated cdc_lab database.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cdc_replicator') THEN
        CREATE ROLE cdc_replicator WITH LOGIN REPLICATION PASSWORD 'cdc_replicator_password_not_for_production';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cdc_app_writer') THEN
        CREATE ROLE cdc_app_writer WITH LOGIN PASSWORD 'cdc_app_writer_password_not_for_production';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE cdc_lab TO cdc_replicator;
GRANT CONNECT ON DATABASE cdc_lab TO cdc_app_writer;
