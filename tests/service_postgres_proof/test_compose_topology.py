"""Static topology and safety validation for the service PostgreSQL proof lab."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from .constants import (
    AUDIT_ROLE,
    BRIDGE_ROLE,
    COMPOSE_PROJECT,
    FORBIDDEN_URL_FRAGMENTS,
    NETWORK_NAME,
    VOLUME_NAME,
)
from .helpers import (
    COMPOSE_FILE,
    LAB_ROOT,
    assert_lab_database_url,
    build_database_url,
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
    assert "CREATE DATABASE shipment_db" in init_sql
    assert "CREATE DATABASE pickup_db" in init_sql
    assert BRIDGE_ROLE in init_sql
    assert AUDIT_ROLE in init_sql
    assert "shipment_svc" in init_sql
    assert "pickup_svc" in init_sql
    assert "hudhud-backend" not in init_sql


def test_compose_config_parses() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "postgres" in rendered["services"]


def test_compose_uses_dedicated_project_network_and_volume() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert rendered.get("name") == COMPOSE_PROJECT
    assert NETWORK_NAME in rendered["networks"]
    assert VOLUME_NAME in rendered["volumes"]


def test_compose_publishes_loopback_ephemeral_port_only() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    ports = rendered["services"]["postgres"].get("ports", [])
    assert len(ports) == 1
    port = ports[0]
    if isinstance(port, dict):
        assert port.get("host_ip") == "127.0.0.1"
        assert port.get("published") in (0, "0")
        assert port.get("target") == 5432
    else:
        assert port == "127.0.0.1::5432"


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
    assert VOLUME_NAME in text
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


def test_database_url_guard_rejects_non_lab_hosts_and_names() -> None:
    with pytest.raises(AssertionError, match="non-loopback"):
        assert_lab_database_url(
            "postgresql+psycopg://user:pass@db.example.com/bridge_db",
            expected_database="bridge_db",
        )
    with pytest.raises(AssertionError, match="fragment"):
        assert_lab_database_url(
            "postgresql+psycopg://user:pass@127.0.0.1/staging_audit",
            expected_database="staging_audit",
        )


def test_database_url_guard_accepts_loopback_lab_urls() -> None:
    url = build_database_url(database="bridge_db", port=55432)
    assert_lab_database_url(url, expected_database="bridge_db")
    for fragment in FORBIDDEN_URL_FRAGMENTS:
        assert fragment not in url or fragment == "legacy"


def test_compose_has_no_nats_or_api_containers() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "nats" not in compose_text.lower()
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert set(rendered["services"]) == {"postgres"}


def test_compose_uses_postgres_16_alpine_image() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    image = rendered["services"]["postgres"]["image"]
    assert image == "postgres:16-alpine"


def test_no_fixed_host_port_literals_in_compose() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert re.search(r"127\.0\.0\.1:\d{4,5}", compose_text) is None
    assert re.search(r'published:\s*5432', compose_text) is None
