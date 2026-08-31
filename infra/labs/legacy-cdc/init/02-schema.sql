-- Lab capture schema — simulates legacy mutable tables without legacy coupling.

CREATE SCHEMA IF NOT EXISTS lab;

GRANT USAGE ON SCHEMA lab TO cdc_replicator;
GRANT USAGE ON SCHEMA lab TO cdc_app_writer;

CREATE TABLE IF NOT EXISTS lab.capture_probe (
    id BIGSERIAL PRIMARY KEY,
    payload TEXT NOT NULL,
    amount NUMERIC(12, 2),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lab.bridge_checkpoint (
    slot_name TEXT PRIMARY KEY,
    confirmed_lsn pg_lsn NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT SELECT ON ALL TABLES IN SCHEMA lab TO cdc_replicator;
GRANT SELECT, INSERT, UPDATE, DELETE ON lab.capture_probe TO cdc_app_writer;
GRANT INSERT, UPDATE, DELETE ON lab.bridge_checkpoint TO cdc_app_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lab TO cdc_app_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA lab
    GRANT SELECT ON TABLES TO cdc_replicator;

ALTER DEFAULT PRIVILEGES IN SCHEMA lab
    GRANT INSERT, UPDATE, DELETE ON TABLES TO cdc_app_writer;

COMMENT ON SCHEMA lab IS 'Isolated CDC lab schema — row changes are transport facts, not domain events.';

-- Illustrative snapshot helper for lab scenario 10 only.
-- NOT equivalent to CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT coordinated protocol.
-- Production Bridge requires a staging drill binding exported snapshot to slot/WAL start.
CREATE OR REPLACE FUNCTION lab.capture_hwm_snapshot()
RETURNS TABLE(snapshot_id text, hwm_lsn text, probe_count bigint)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        pg_export_snapshot(),
        pg_current_wal_lsn()::text,
        (SELECT COUNT(*)::bigint FROM lab.capture_probe);
END;
$$;
