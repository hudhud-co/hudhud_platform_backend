"""Fail when secret-like generated artifacts are tracked in git."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .constants import SECRET_FILE_SUFFIXES
from .helpers import LAB_ROOT, REPO_ROOT

TRACKED_SECRET_PATTERNS = (
    "BEGIN NATS USER JWT",
    "BEGIN USER NKEY SEED",
    "BEGIN OPERATOR NKEY SEED",
    "BEGIN ACCOUNT NKEY SEED",
)


def test_no_secret_suffix_files_tracked_under_lab() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", str(LAB_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, tracked.stderr
    for line in tracked.stdout.splitlines():
        path = Path(line.strip())
        if not path.name:
            continue
        assert not any(path.name.endswith(suffix) for suffix in SECRET_FILE_SUFFIXES), path
        assert path.name != "generated"


def test_no_secret_markers_in_tracked_lab_files() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", str(LAB_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, tracked.stderr
    for line in tracked.stdout.splitlines():
        path = REPO_ROOT / line.strip()
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        for marker in TRACKED_SECRET_PATTERNS:
            assert marker not in content, f"{path} contains {marker}"


def test_tests_tree_has_no_tracked_secret_suffix_files() -> None:
    tests_root = REPO_ROOT / "tests" / "nats_security_proof"
    tracked = subprocess.run(
        ["git", "ls-files", str(tests_root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, tracked.stderr
    for line in tracked.stdout.splitlines():
        path = Path(line.strip())
        assert not any(path.name.endswith(suffix) for suffix in SECRET_FILE_SUFFIXES), path
