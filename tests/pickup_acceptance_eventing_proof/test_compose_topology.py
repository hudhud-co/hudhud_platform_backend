"""Static topology and safety validation for the Pickup acceptance eventing lab."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from .constants import (
    ACK_POLICY,
    COMPOSE_PROJECT,
    NETWORK_NAME,
    PICKUP_DURABLE,
    PICKUP_ROLE,
    PICKUP_STREAM,
    PICKUP_SUBJECT,
    SHIPMENT_ROLE,
    VOLUME_JS_NAME,
    VOLUME_PG_NAME,
)
from .helpers import (
    COMPOSE_FILE,
    LAB_ROOT,
    assert_lab_database_url,
    assert_lab_nats_url,
    build_database_url,
    build_nats_url,
    compose,
    docker_available,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_lab_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file()


def test_init_sql_declares_isolated_databases_and_roles() -> None:
    init_sql = (LAB_ROOT / "init" / "01-databases-roles.sql").read_text(encoding="utf-8")
    assert "CREATE DATABASE pickup_db" in init_sql
    assert "CREATE DATABASE shipment_db" in init_sql
    assert PICKUP_ROLE in init_sql
    assert SHIPMENT_ROLE in init_sql
    assert "hudhud-backend" not in init_sql
    assert "CREATE DATABASE bridge_db" not in init_sql
    assert "CREATE DATABASE audit_db" not in init_sql


def test_bootstrap_script_binds_pickup_accepted_topology_only() -> None:
    bootstrap = (LAB_ROOT / "scripts" / "bootstrap_pickup_topology.py").read_text(encoding="utf-8")
    assert PICKUP_STREAM in bootstrap
    assert PICKUP_DURABLE in bootstrap
    assert PICKUP_SUBJECT in bootstrap
    assert "HUDHUD_SHIPMENT" not in bootstrap
    assert "HUDHUD_AUDIT" not in bootstrap
    assert "ensure_consumer" in bootstrap
    assert ACK_POLICY in bootstrap


def test_compose_config_parses() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "postgres" in rendered["services"]
    assert "nats" in rendered["services"]
    assert "api" not in rendered["services"]
    assert len([name for name in rendered["services"] if name in {"postgres", "nats"}]) == 2


def test_compose_uses_dedicated_project_network_and_volumes() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert rendered.get("name") == COMPOSE_PROJECT
    assert NETWORK_NAME in rendered["networks"]
    assert VOLUME_PG_NAME in rendered["volumes"]
    assert VOLUME_JS_NAME in rendered["volumes"]


def test_compose_publishes_loopback_ephemeral_ports_only() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    for service_name in ("postgres", "nats"):
        ports = rendered["services"][service_name].get("ports", [])
        assert len(ports) == 1
        port = ports[0]
        if isinstance(port, dict):
            assert port.get("host_ip") == "127.0.0.1"
            assert port.get("published") in (0, "0")
        else:
            assert port.startswith("127.0.0.1::")


def test_compose_labels_nats_auth_as_local_disposable_proof() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    labels = rendered["services"]["nats"].get("labels", {})
    assert labels.get("hudhud.nats.auth") == "false-local-disposable-proof"
    assert rendered["services"]["nats"]["environment"]["NATS_AUTH_ENABLED"] == "false"


def test_compose_has_no_production_source_mounts() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    assert ".:/app" not in result.stdout
    assert "hudhud-backend" not in result.stdout


def test_cleanup_script_is_executable_and_targets_dedicated_resources() -> None:
    cleanup = LAB_ROOT / "scripts" / "cleanup.sh"
    assert cleanup.is_file()
    text = cleanup.read_text(encoding="utf-8")
    assert COMPOSE_PROJECT in text
    assert NETWORK_NAME in text
    assert VOLUME_PG_NAME in text
    assert VOLUME_JS_NAME in text
    assert "docker system prune" not in text
    if shutil.which("sh") is None:
        pytest.skip("sh not available")
    result = subprocess.run(["sh", "-n", str(cleanup)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_compose_commands_include_project_and_profile_guards() -> None:
    helpers = Path(__file__).resolve().parent / "helpers.py"
    content = helpers.read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT" in content
    assert "COMPOSE_PROFILE" in content
    assert "docker system prune" not in content


def test_database_and_nats_url_guards_reject_external_hosts() -> None:
    with pytest.raises(AssertionError, match="non-loopback"):
        assert_lab_database_url(
            "postgresql+psycopg://user:pass@db.example.com/pickup_db",
            expected_database="pickup_db",
        )
    with pytest.raises(AssertionError, match="fragment"):
        assert_lab_nats_url("nats://nats.prod.example.com:4222")


def test_database_url_guard_accepts_loopback_lab_urls() -> None:
    url = build_database_url(database="pickup_db", port=55432)
    assert_lab_database_url(url, expected_database="pickup_db")
    nats = build_nats_url(port=54222)
    assert_lab_nats_url(nats)


def test_no_fixed_host_port_literals_in_compose() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert re.search(r"127\.0\.0\.1:\d{4,5}", compose_text) is None
    assert re.search(r"published:\s*5432", compose_text) is None
    assert re.search(r"published:\s*4222", compose_text) is None


def test_worker_configuration_is_bounded() -> None:
    poll = Path(__file__).resolve().parent / "probes" / "shipment_poll_once.py"
    content = poll.read_text(encoding="utf-8")
    assert "handler_concurrency=1" in content
    assert "pull_batch_size=1" in content
    assert "poll_once" in content
    assert "run_forever" not in content
    assert "build_coordinator" in content


def test_runtime_runner_is_single_execution() -> None:
    runner = Path(__file__).resolve().parent / "run_pipeline_proof.py"
    content = runner.read_text(encoding="utf-8")
    assert "HUDHUD_PICKUP_ACCEPTANCE_EVENTING_PROOF_COMPLETE" in content
    assert "compose_down" in content
    assert "leftover_worker_processes" in content
