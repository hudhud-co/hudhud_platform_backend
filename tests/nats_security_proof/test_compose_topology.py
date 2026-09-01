"""Static topology and safety validation for the NATS security proof lab."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
import yaml

from .constants import (
    COMPOSE_PROJECT,
    NATS_IMAGE,
    NETWORK_NAME,
    SECURITY_INIT_IMAGE,
    VOLUME_GENERATED_NAME,
    VOLUME_JS_NAME,
)
from .helpers import (
    COMPOSE_FILE,
    LAB_ROOT,
    MANIFEST_FILE,
    assert_lab_nats_url,
    build_tls_nats_url,
    compose,
    docker_available,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_lab_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file()


def test_identity_manifest_exists_and_has_required_identities() -> None:
    manifest = yaml.safe_load(MANIFEST_FILE.read_text(encoding="utf-8"))
    identities = manifest["identities"]
    for name in (
        "hudhud-eventing-bootstrap",
        "legacy-event-bridge",
        "audit",
        "tracking",
        "hudhud-nats-break-glass",
    ):
        assert name in identities
    assert manifest["forbidden_wildcards"] == ["$JS.API.>"]


def test_compose_config_parses() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert "nats" in rendered["services"]


def test_compose_uses_dedicated_project_network_and_volumes() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert rendered.get("name") == COMPOSE_PROJECT
    assert NETWORK_NAME in rendered["networks"]
    assert VOLUME_JS_NAME in rendered["volumes"]
    assert VOLUME_GENERATED_NAME in rendered["volumes"]


def test_compose_publishes_loopback_ephemeral_ports_only() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    ports = rendered["services"]["nats"].get("ports", [])
    assert len(ports) == 1
    port = ports[0]
    if isinstance(port, dict):
        assert port.get("host_ip") == "127.0.0.1"
        assert port.get("published") in (0, "0")
    else:
        assert port.startswith("127.0.0.1::")


def test_compose_uses_pinned_images_not_latest() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    assert rendered["services"]["nats"]["image"] == NATS_IMAGE
    assert rendered["services"]["security-init"]["image"] == SECURITY_INIT_IMAGE
    assert "latest" not in rendered["services"]["nats"]["image"]


def test_compose_labels_nats_auth_as_jwt_tls_proof() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)
    labels = rendered["services"]["nats"].get("labels", {})
    assert labels.get("hudhud.nats.auth") == "jwt-nkeys-tls-local-disposable-proof"


def test_compose_has_no_production_source_mounts() -> None:
    if not docker_available():
        pytest.skip("docker CLI not available")
    result = compose("config")
    assert result.returncode == 0, result.stderr
    assert ".:/app" not in result.stdout
    assert "hudhud-backend" not in result.stdout


def test_init_script_requires_tls_and_jwt_resolver() -> None:
    init_script = (LAB_ROOT / "scripts" / "init_security_material.sh").read_text(encoding="utf-8")
    assert "tls {" in init_script
    assert "resolver:" in init_script
    assert "operator:" in init_script
    assert "NATS_AUTH_ENABLED=false" not in init_script


def test_cleanup_script_is_executable_and_targets_dedicated_resources() -> None:
    cleanup = LAB_ROOT / "scripts" / "cleanup.sh"
    assert cleanup.is_file()
    text = cleanup.read_text(encoding="utf-8")
    assert COMPOSE_PROJECT in text
    assert NETWORK_NAME in text
    assert VOLUME_JS_NAME in text
    assert VOLUME_GENERATED_NAME in text
    assert "docker system prune" not in text
    if shutil.which("sh") is None:
        pytest.skip("sh not available")
    result = subprocess.run(["sh", "-n", str(cleanup)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_nats_url_guards_reject_external_hosts() -> None:
    with pytest.raises(AssertionError, match="fragment"):
        assert_lab_nats_url("tls://nats.prod.example.com:4222")
    with pytest.raises(AssertionError, match="fragment"):
        assert_lab_nats_url("tls://nats.staging.internal:4222")


def test_nats_url_guard_accepts_loopback_tls_urls() -> None:
    url = build_tls_nats_url(port=54222)
    assert_lab_nats_url(url)


def test_no_fixed_client_port_literals_in_compose() -> None:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    assert re.search(r"published:\s*4222", compose_text) is None
    assert re.search(r"published:\s*5432", compose_text) is None


def test_gitignore_blocks_generated_secret_artifacts() -> None:
    gitignore = (LAB_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".creds", "*.pem", "*.jwt", "generated/"):
        assert pattern in gitignore
