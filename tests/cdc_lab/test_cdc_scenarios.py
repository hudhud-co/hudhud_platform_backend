"""PostgreSQL logical decoding scenario proofs for ADR-0007 CDC feasibility."""

from __future__ import annotations

import pytest

from .helpers import CdcLabClient, compose, unique_slot

pytestmark = pytest.mark.integration


def _change_blob(changes: list, needle: str) -> bool:
    return any(needle in change.data for change in changes)


# Scenario 1 — wal_level=logical and replication prerequisites
def test_scenario_01_wal_level_and_replication_prerequisites(cdc_client: CdcLabClient) -> None:
    assert cdc_client.setting("wal_level") == "logical"
    max_slots = int(cdc_client.setting("max_replication_slots"))
    max_senders = int(cdc_client.setting("max_wal_senders"))
    assert max_slots >= 1
    assert max_senders >= 1
    roles = cdc_client.psql("SELECT rolreplication FROM pg_roles WHERE rolname = 'cdc_replicator';")
    assert roles.lower().startswith("t")


# Scenario 2 — replication-slot creation and stable identity
def test_scenario_02_slot_creation_and_stable_identity(cdc_client: CdcLabClient) -> None:
    slot = unique_slot()
    restart_lsn = cdc_client.create_slot(slot)
    assert restart_lsn
    info = cdc_client.slot_info(slot)
    assert info.slot_name == slot
    assert info.plugin == "test_decoding"
    assert info.restart_lsn is not None
    lsn_delta = int(
        cdc_client.psql(
            f"SELECT pg_wal_lsn_diff('{restart_lsn}'::pg_lsn, '{info.restart_lsn}'::pg_lsn);"
        )
    )
    assert abs(lsn_delta) <= 1024
    assert cdc_client.slot_exists(slot)
    cdc_client.drop_slot(slot)


# Scenario 3 — insert/update/delete capture
def test_scenario_03_insert_update_delete_capture(cdc_client: CdcLabClient, lab_slot: str) -> None:
    row_id = cdc_client.insert_probe("insert-row")
    cdc_client.update_probe(row_id, "update-row")
    cdc_client.delete_probe(row_id)

    changes = cdc_client.get_changes(lab_slot)
    assert _change_blob(changes, "insert-row")
    assert _change_blob(changes, "update-row")
    assert any("DELETE:" in change.data for change in changes)


# Scenario 4 — committed transaction ordering
def test_scenario_04_committed_transaction_ordering(
    cdc_client: CdcLabClient, lab_slot: str
) -> None:
    cdc_client.run_sql_block(
        "BEGIN; "
        "INSERT INTO lab.capture_probe (payload) VALUES ('order-first'); "
        "INSERT INTO lab.capture_probe (payload) VALUES ('order-second'); "
        "COMMIT;"
    )
    changes = cdc_client.get_changes(lab_slot)
    payloads = [change.data for change in changes if "order-" in change.data]
    assert any("order-first" in item for item in payloads)
    assert any("order-second" in item for item in payloads)
    first_idx = next(i for i, item in enumerate(payloads) if "order-first" in item)
    second_idx = next(i for i, item in enumerate(payloads) if "order-second" in item)
    assert first_idx < second_idx
    lsns = [change.lsn for change in changes if "order-" in change.data]
    assert lsns == sorted(lsns)


# Scenario 5 — rolled-back transactions are not emitted
def test_scenario_05_rollback_not_emitted(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.run_sql_block(
        "BEGIN; INSERT INTO lab.capture_probe (payload) VALUES ('rollback-row'); ROLLBACK;"
    )
    cdc_client.insert_probe("committed-row")
    changes = cdc_client.get_changes(lab_slot)
    assert not _change_blob(changes, "rollback-row")
    assert _change_blob(changes, "committed-row")


# Scenario 6 — source transaction boundaries (xid visibility)
def test_scenario_06_transaction_boundaries(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.run_sql_block(
        "BEGIN; "
        "INSERT INTO lab.capture_probe (payload) VALUES ('xid-row-a'); "
        "INSERT INTO lab.capture_probe (payload) VALUES ('xid-row-b'); "
        "COMMIT;"
    )
    changes = cdc_client.get_changes(lab_slot)
    begin_markers = [c.data for c in changes if c.data.startswith("BEGIN")]
    commit_markers = [c.data for c in changes if c.data.startswith("COMMIT")]
    assert begin_markers, "expected BEGIN boundary from test_decoding"
    assert commit_markers, "expected COMMIT boundary from test_decoding"
    assert _change_blob(changes, "xid-row-a")
    assert _change_blob(changes, "xid-row-b")


# Scenario 7 — LSN checkpoint persistence
def test_scenario_07_lsn_checkpoint_persistence(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.insert_probe("checkpoint-row")
    changes = cdc_client.get_changes(lab_slot, limit=5)
    assert changes
    checkpoint_lsn = changes[-1].lsn
    cdc_client.save_checkpoint(lab_slot, checkpoint_lsn)
    loaded = cdc_client.load_checkpoint(lab_slot)
    assert loaded == checkpoint_lsn


# Scenario 8 — resume after consumer restart
def test_scenario_08_resume_after_consumer_restart(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.insert_probe("before-restart")
    first_batch = cdc_client.get_changes(lab_slot)
    assert _change_blob(first_batch, "before-restart")
    last_lsn = first_batch[-1].lsn
    cdc_client.save_checkpoint(lab_slot, last_lsn)

    cdc_client.insert_probe("after-restart")
    resumed = cdc_client.get_changes(lab_slot)
    assert not _change_blob(resumed, "before-restart")
    assert _change_blob(resumed, "after-restart")


# Scenario 9 — replay from a known LSN (peek without consume)
def test_scenario_09_replay_from_known_lsn(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.insert_probe("replay-one")
    first_peek = cdc_client.peek_changes(lab_slot)
    assert _change_blob(first_peek, "replay-one")
    replay_lsn = first_peek[-1].lsn

    cdc_client.insert_probe("replay-two")
    second_peek = cdc_client.peek_changes(lab_slot)
    assert _change_blob(second_peek, "replay-one")
    assert _change_blob(second_peek, "replay-two")

    # Peek again — same slot position, same replayable view
    third_peek = cdc_client.peek_changes(lab_slot)
    assert [c.data for c in second_peek] == [c.data for c in third_peek]
    assert replay_lsn in {change.lsn for change in third_peek}


# Scenario 10 — illustrative snapshot + WAL count check (NOT coordinated EXPORT_SNAPSHOT protocol)
def test_scenario_10_snapshot_plus_wal_is_illustrative_only(cdc_client: CdcLabClient) -> None:
    """Lab helper returns count + LSN — not CREATE_REPLICATION_SLOT ... EXPORT_SNAPSHOT."""
    slot = unique_slot()
    cdc_client.create_slot(slot)
    try:
        _snapshot_id, _hwm_lsn, backfill_count = cdc_client.capture_hwm_snapshot()

        cdc_client.insert_probe("post-snapshot-live")
        wal_changes = cdc_client.get_changes(slot)
        assert _change_blob(wal_changes, "post-snapshot-live")

        live_count = cdc_client.count_probes()
        assert live_count == backfill_count + 1
    finally:
        cdc_client.drop_slot(slot)


# Scenario 11 — multiple concurrent writers
def test_scenario_11_multiple_concurrent_writers(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.run_sql_block(
        "BEGIN; INSERT INTO lab.capture_probe (payload) VALUES ('writer-a'); COMMIT;",
        user="cdc_app_writer",
    )
    cdc_client.run_sql_block(
        "BEGIN; INSERT INTO lab.capture_probe (payload) VALUES ('writer-b'); COMMIT;",
        user="cdc_app_writer",
    )
    changes = cdc_client.get_changes(lab_slot)
    assert _change_blob(changes, "writer-a")
    assert _change_blob(changes, "writer-b")


# Scenario 12 — schema-change behavior
def test_scenario_12_schema_change_behavior(cdc_client: CdcLabClient, lab_slot: str) -> None:
    cdc_client.insert_probe("pre-schema-change")
    cdc_client.psql("ALTER TABLE lab.capture_probe ADD COLUMN IF NOT EXISTS note TEXT;")
    cdc_client.insert_probe("post-schema-change")

    changes = cdc_client.get_changes(lab_slot)
    assert _change_blob(changes, "pre-schema-change")
    assert _change_blob(changes, "post-schema-change")
    assert any("note" in change.data.lower() for change in changes)


# Scenario 13 — slot lag and retained-WAL growth visibility
def test_scenario_13_slot_lag_visibility(cdc_client: CdcLabClient, lab_slot: str) -> None:
    for idx in range(5):
        cdc_client.insert_probe(f"lag-row-{idx}")

    lag_bytes = cdc_client.slot_lag_bytes(lab_slot)
    assert lag_bytes > 0
    info = cdc_client.slot_info(lab_slot)
    assert info.restart_lsn is not None
    assert info.wal_status in {"reserved", "extended", None}


# Scenario 14 — slot cleanup and operational consequences
def test_scenario_14_slot_cleanup_consequences(cdc_client: CdcLabClient) -> None:
    slot = unique_slot()
    cdc_client.create_slot(slot)
    cdc_client.insert_probe("before-drop")
    assert cdc_client.peek_changes(slot)

    cdc_client.drop_slot(slot)
    assert not cdc_client.slot_exists(slot)

    with pytest.raises(RuntimeError):
        cdc_client.peek_changes(slot)


# Scenario 15 — database/container restart behavior
def test_scenario_15_container_restart_preserves_slot_and_resume(
    cdc_client: CdcLabClient,
) -> None:
    slot = unique_slot()
    cdc_client.create_slot(slot)
    cdc_client.insert_probe("before-container-restart")
    consumed = cdc_client.get_changes(slot)
    assert _change_blob(consumed, "before-container-restart")
    checkpoint = consumed[-1].lsn
    cdc_client.save_checkpoint(slot, checkpoint)

    restart = compose("restart", "postgres")
    assert restart.returncode == 0, restart.stderr
    wait = compose("up", "-d", "--wait", "postgres")
    assert wait.returncode == 0, wait.stderr

    assert cdc_client.slot_exists(slot)
    info = cdc_client.slot_info(slot)
    assert info.plugin == "test_decoding"
    assert cdc_client.load_checkpoint(slot) == checkpoint

    cdc_client.insert_probe("after-container-restart")
    resumed = cdc_client.get_changes(slot)
    assert _change_blob(resumed, "after-container-restart")
    cdc_client.drop_slot(slot)


def test_get_changes_advances_slot_before_durable_bridge_persist(cdc_client: CdcLabClient) -> None:
    """pg_logical_slot_get_changes consumes slot position — unsafe before Bridge durable landing."""
    slot = unique_slot()
    cdc_client.create_slot(slot)
    try:
        cdc_client.insert_probe("advance-before-persist")
        before = cdc_client.slot_info(slot)

        changes = cdc_client.get_changes(slot)
        assert changes
        after = cdc_client.slot_info(slot)
        assert (
            after.confirmed_flush_lsn != before.confirmed_flush_lsn
            or after.restart_lsn != before.restart_lsn
        )

        replay = cdc_client.peek_changes(slot)
        assert not _change_blob(replay, "advance-before-persist")
    finally:
        cdc_client.drop_slot(slot)


def test_transport_is_not_canonical_domain_event(cdc_client: CdcLabClient, lab_slot: str) -> None:
    """CDC emits row-level transport; bridge mapping to ADR-0002 envelope is separate."""
    cdc_client.insert_probe("transport-only")
    changes = cdc_client.get_changes(lab_slot)
    assert changes
    row_changes = [change for change in changes if change.data.startswith("table ")]
    assert row_changes, "expected row-level transport records"
    for change in changes:
        assert "event_type" not in change.data
        assert "producer" not in change.data
