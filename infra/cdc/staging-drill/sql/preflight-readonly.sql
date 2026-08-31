-- CDC staging drill: READ-ONLY preflight checks (ADR-0007 G1, G2, G7).
-- Execute with a read-only role. Any FAIL row must STOP the drill before privileged steps.
-- This file performs no DDL/DML. Do not run as superuser unless catalog reads require it.

\set ON_ERROR_STOP on
\timing off

-- =============================================================================
-- 1. PostgreSQL version
-- =============================================================================
SELECT 'postgresql_version' AS check_id,
       version() AS observed,
       CASE WHEN current_setting('server_version_num')::int >= 160000
            THEN 'PASS' ELSE 'FAIL' END AS status,
       'Require PostgreSQL 16+ for legacy parity evidence' AS note;

-- =============================================================================
-- 2. WAL / replication server parameters (names only in evidence)
-- =============================================================================
SELECT 'wal_level' AS check_id,
       current_setting('wal_level') AS observed,
       CASE WHEN current_setting('wal_level') = 'logical' THEN 'PASS' ELSE 'FAIL' END AS status,
       'ADR-0007 G1: wal_level must be logical' AS note;

SELECT 'max_replication_slots' AS check_id,
       current_setting('max_replication_slots') AS observed,
       CASE WHEN current_setting('max_replication_slots')::int >= 2 THEN 'PASS' ELSE 'WARN' END AS status,
       'Reserve headroom for drill slot + existing consumers' AS note;

SELECT 'max_wal_senders' AS check_id,
       current_setting('max_wal_senders') AS observed,
       CASE WHEN current_setting('max_wal_senders')::int >= 2 THEN 'PASS' ELSE 'WARN' END AS status,
       'Logical decoding consumers count against wal_senders' AS note;

SELECT 'max_slot_wal_keep_size' AS check_id,
       current_setting('max_slot_wal_keep_size') AS observed,
       'INFO' AS status,
       'Record value; -1 means unlimited retention risk' AS note;

-- =============================================================================
-- 3. Current replication slots and lag
-- =============================================================================
SELECT 'replication_slots' AS check_id,
       slot_name,
       plugin,
       slot_type,
       active,
       restart_lsn::text,
       confirmed_flush_lsn::text,
       wal_status,
       safe_wal_size,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes,
       'INFO' AS status
FROM pg_replication_slots
ORDER BY slot_name;

-- =============================================================================
-- 4. Storage / WAL headroom (database size; disk free requires OS-level check)
-- =============================================================================
SELECT 'database_size_bytes' AS check_id,
       pg_database_size(current_database()) AS observed,
       'INFO' AS status,
       'Compare with CDC_DRILL_MIN_WAL_VOLUME_FREE_BYTES at OS layer' AS note;

SELECT 'current_wal_lsn' AS check_id,
       pg_current_wal_lsn()::text AS observed,
       'INFO' AS status,
       'Point-in-time LSN — NOT a coordinated HWM by itself' AS note;

-- =============================================================================
-- 5. Replication privileges for current session role
-- =============================================================================
SELECT 'session_role' AS check_id,
       current_user AS observed,
       'INFO' AS status,
       'Record role used for preflight' AS note;

SELECT 'role_replication_privilege' AS check_id,
       rolname,
       rolreplication AS has_replication,
       CASE WHEN rolreplication THEN 'PASS' ELSE 'INFO' END AS status,
       'Replication role required for privileged tier only' AS note
FROM pg_roles
WHERE rolname = current_user;

-- =============================================================================
-- 6. Replica identity on allowlisted tables
-- =============================================================================
SELECT 'replica_identity_shipment_events' AS check_id,
       c.relname AS table_name,
       CASE c.relreplident
           WHEN 'd' THEN 'DEFAULT'
           WHEN 'n' THEN 'NOTHING'
           WHEN 'f' THEN 'FULL'
           WHEN 'i' THEN 'INDEX'
       END AS replica_identity,
       CASE WHEN c.relreplident IN ('d', 'f', 'i') THEN 'PASS' ELSE 'WARN' END AS status,
       'INSERT-only append tables: DEFAULT usually sufficient' AS note
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'shipment_events';

SELECT 'replica_identity_audit_logs' AS check_id,
       c.relname AS table_name,
       CASE c.relreplident
           WHEN 'd' THEN 'DEFAULT'
           WHEN 'n' THEN 'NOTHING'
           WHEN 'f' THEN 'FULL'
           WHEN 'i' THEN 'INDEX'
       END AS replica_identity,
       CASE WHEN c.relreplident IN ('d', 'f', 'i') THEN 'PASS' ELSE 'WARN' END AS status,
       'INSERT-only append tables: DEFAULT usually sufficient' AS note
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'audit_logs';

-- =============================================================================
-- 7. Primary keys on allowlisted tables
-- =============================================================================
SELECT 'primary_key_shipment_events' AS check_id,
       tc.table_schema,
       tc.table_name,
       string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS pk_columns,
       CASE WHEN count(*) > 0 THEN 'PASS' ELSE 'FAIL' END AS status
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.table_schema = kcu.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND tc.table_schema = 'public'
  AND tc.table_name = 'shipment_events'
GROUP BY tc.table_schema, tc.table_name;

SELECT 'primary_key_audit_logs' AS check_id,
       tc.table_schema,
       tc.table_name,
       string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS pk_columns,
       CASE WHEN count(*) > 0 THEN 'PASS' ELSE 'FAIL' END AS status
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.table_schema = kcu.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND tc.table_schema = 'public'
  AND tc.table_name = 'audit_logs'
GROUP BY tc.table_schema, tc.table_name;

-- =============================================================================
-- 8. Table existence and row-count baseline (illustrative — NOT zero-gap proof)
-- =============================================================================
SELECT 'table_exists_shipment_events' AS check_id,
       EXISTS (
           SELECT 1 FROM information_schema.tables
           WHERE table_schema = 'public' AND table_name = 'shipment_events'
       ) AS observed,
       CASE WHEN EXISTS (
           SELECT 1 FROM information_schema.tables
           WHERE table_schema = 'public' AND table_name = 'shipment_events'
       ) THEN 'PASS' ELSE 'FAIL' END AS status,
       'Allowlisted ADR-0007 source' AS note;

SELECT 'table_exists_audit_logs' AS check_id,
       EXISTS (
           SELECT 1 FROM information_schema.tables
           WHERE table_schema = 'public' AND table_name = 'audit_logs'
       ) AS observed,
       CASE WHEN EXISTS (
           SELECT 1 FROM information_schema.tables
           WHERE table_schema = 'public' AND table_name = 'audit_logs'
       ) THEN 'PASS' ELSE 'FAIL' END AS status,
       'Allowlisted ADR-0007 source' AS note;

-- Illustrative count only — does NOT substitute for coordinated snapshot export
SELECT 'illustrative_row_count_shipment_events' AS check_id,
       count(*) AS observed,
       'INFO' AS status,
       'Count at query time — NOT bound to replication slot HWM' AS note
FROM public.shipment_events;

SELECT 'illustrative_row_count_audit_logs' AS check_id,
       count(*) AS observed,
       'INFO' AS status,
       'Count at query time — NOT bound to replication slot HWM' AS note
FROM public.audit_logs;

-- =============================================================================
-- 9. Publication / plugin inventory (read-only)
-- =============================================================================
SELECT 'publications' AS check_id,
       pubname,
       puballtables,
       pubinsert,
       pubupdate,
       pubdelete,
       pubtruncate,
       'INFO' AS status
FROM pg_publication
ORDER BY pubname;

-- =============================================================================
-- 10. Slot name collision check (operator must set expected name in config)
-- =============================================================================
-- Replace :expected_slot with operator value when executing, e.g. \set expected_slot 'hudhud_bridge_staging_drill_001'
-- SELECT 'slot_name_collision' AS check_id,
--        :'expected_slot' AS expected_slot_name,
--        EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = :'expected_slot') AS slot_exists,
--        CASE WHEN EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = :'expected_slot')
--             THEN 'FAIL' ELSE 'PASS' END AS status,
--        'Expected unused slot name before drill' AS note;

-- =============================================================================
-- 11. SSL/TLS (connection-level — record from client, not SQL)
-- =============================================================================
SELECT 'ssl_in_use' AS check_id,
       COALESCE(ssl, false) AS observed,
       CASE WHEN ssl IS TRUE THEN 'PASS' ELSE 'WARN' END AS status,
       'Verify sslmode=verify-full for staging replication user' AS note
FROM pg_stat_ssl
WHERE pid = pg_backend_pid();

-- =============================================================================
-- 12. Read-only verification: shipment_events eligibility
-- =============================================================================
SELECT 'shipment_events_append_only_signal' AS check_id,
       count(*) FILTER (WHERE column_name IN ('updated_at', 'deleted_at')) AS mutable_marker_columns,
       CASE WHEN count(*) FILTER (WHERE column_name IN ('updated_at', 'deleted_at')) = 0
            THEN 'PASS' ELSE 'WARN' END AS status,
       'Legacy evidence: append-only; verify no UPDATE/DELETE triggers' AS note
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'shipment_events';

SELECT 'shipment_events_ordering_columns' AS check_id,
       string_agg(column_name, ', ' ORDER BY column_name) AS observed,
       'INFO' AS status,
       'Legacy cursor columns for display/reconciliation tie-break — not canonical order authority' AS note
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'shipment_events'
  AND column_name IN ('id', 'occurred_at', 'created_at', 'event_type');

-- =============================================================================
-- 13. Read-only verification: audit_logs eligibility
-- =============================================================================
SELECT 'audit_logs_append_only_signal' AS check_id,
       count(*) FILTER (WHERE column_name = 'updated_at') AS has_updated_at,
       CASE WHEN count(*) FILTER (WHERE column_name = 'updated_at') = 0
            THEN 'PASS' ELSE 'WARN' END AS status,
       'Legacy evidence: append-only audit_logs' AS note
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'audit_logs';

SELECT 'audit_logs_ordering_columns' AS check_id,
       string_agg(column_name, ', ' ORDER BY column_name) AS observed,
       'INFO' AS status,
       'Bridge cursor uses (created_at, id) per ADR-0007' AS note
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'audit_logs'
  AND column_name IN ('id', 'created_at', 'action', 'entity_type');
