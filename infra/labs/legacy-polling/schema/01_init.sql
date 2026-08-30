-- Synthetic lab schema for ADR-0007 polling completeness proof.
-- Does NOT mirror legacy Alembic migrations; patterns are analogous only.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Append-only events (analogous to shipment_events / audit_logs).
CREATE TABLE lab_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX lab_events_occurred_at_id_idx ON lab_events (occurred_at, id);

-- Mutable entity (analogous to TimestampMixin tables).
CREATE TABLE lab_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX lab_entities_updated_at_idx ON lab_entities (updated_at);

-- Hypothetical capture sequence (lab-only schema change candidate).
CREATE TABLE lab_events_sequenced (
    capture_seq BIGSERIAL PRIMARY KEY,
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX lab_events_sequenced_id_idx ON lab_events_sequenced (id);

-- Bridge cursor store (platform-owned; not legacy).
CREATE TABLE lab_bridge_cursor (
    stream_name TEXT NOT NULL,
    strategy TEXT NOT NULL,
    cursor_ts TIMESTAMPTZ,
    cursor_id UUID,
    cursor_seq BIGINT,
    overlap_seconds INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stream_name, strategy)
);

-- Deterministic scenario seed marker (tests reset via TRUNCATE).
CREATE TABLE lab_scenario_runs (
    scenario_id TEXT PRIMARY KEY,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
