"""Static topology and safety validation for the observation eventing proof lab."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from .constants import (
    A2_DURABLE,
    A2_STREAM,
    A2_SUBJECT,
    AUDIT_ROLE,
    BRIDGE_ROLE,
    COMPOSE_PROJECT,
    HANDLER_CONCURRENCY,
    NETWORK_NAME,
    PULL_BATCH_SIZE,
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
    assert "CREATE DATABASE bridge_db" in init_sql
    assert "CREATE DATABASE audit_db" in init_sql
    assert BRIDGE_ROLE in init_sql
    assert AUDIT_ROLE in init_sql
    assert "hudhud-backend" not in init_sql


def test_bootstrap_script_binds_audit_topology_only() -> None:
    bootstrap = (LAB_ROOT / "scripts" / "bootstrap_audit_topology.py").read_text(encoding="utf-8")
    assert f'"{A2_STREAM}"' in bootstrap or f"'{A2_STREAM}'" in bootstrap
    assert A2_DURABLE in bootstrap
    assert A2_SUBJECT in bootstrap
    assert "HUDHUD_SHIPMENT" not in bootstrap
    assert "add_consumer" not in bootstrap or "ensure_consumer" in bootstrap


def test_compose_config_parses() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "postgres" in rendered["services"]
    assert "nats" in rendered["services"]


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
            "postgresql+psycopg://user:pass@db.example.com/bridge_db",
            expected_database="bridge_db",
        )
    with pytest.raises(AssertionError, match="fragment"):
        assert_lab_nats_url("nats://nats.prod.example.com:4222")


def test_database_url_guard_accepts_loopback_lab_urls() -> None:
    url = build_database_url(database="bridge_db", port=55432)
    assert_lab_database_url(url, expected_database="bridge_db")
    nats = build_nats_url(port=54222)
    assert_lab_nats_url(nats)


def test_no_fixed_host_port_literals_in_compose() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert re.search(r"127\.0\.0\.1:\d{4,5}", compose_text) is None
    assert re.search(r"published:\s*5432", compose_text) is None
    assert re.search(r"published:\s*4222", compose_text) is None


def test_worker_configuration_is_bounded() -> None:
    audit_probe = Path(__file__).resolve().parent / "probes" / "audit_poll_once.py"
    content = audit_probe.read_text(encoding="utf-8")
    assert f"handler_concurrency={HANDLER_CONCURRENCY}" in content
    assert f"pull_batch_size={PULL_BATCH_SIZE}" in content
    assert "run_forever" not in content
    assert "poll_once" in content
