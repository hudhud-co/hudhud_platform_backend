"""Cleanup proof for dedicated observation eventing lab resources."""

from __future__ import annotations

import subprocess

import pytest

from .helpers import LAB_ROOT, dedicated_resources_absent

pytestmark = pytest.mark.integration


def test_cleanup_script_removes_only_dedicated_resources(eventing_proof_stack: dict) -> None:
    _ = eventing_proof_stack
    cleanup = LAB_ROOT / "scripts" / "cleanup.sh"
    result = subprocess.run(["sh", str(cleanup)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "HUDHUD_OBSERVATION_EVENTING_PROOF_CLEANED" in result.stdout
    assert dedicated_resources_absent()
