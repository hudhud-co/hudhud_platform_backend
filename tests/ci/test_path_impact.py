"""Tests for architecture-aware CI path impact calculation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"
CALCULATE_SCRIPT = SCRIPTS_CI / "calculate_path_impact.py"

sys.path.insert(0, str(SCRIPTS_CI))
from path_impact import (  # noqa: E402
    calculate_impact,
    classify_path,
    parse_changed_files,
    render_json,
)


def _impact(*lines: str):
    return calculate_impact(changed_lines=list(lines))


_GITHUB_FORMAT_DOCS_ONLY_CMD = [
    sys.executable,
    str(CALCULATE_SCRIPT),
    "--format",
    "github",
    "--changed-file",
    "M\tdocs/audit/legacy-baseline.md",
]


def _cli_env(*, github_output: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if github_output is None:
        env.pop("GITHUB_OUTPUT", None)
    else:
        env["GITHUB_OUTPUT"] = github_output
    return env


def test_one_service_change() -> None:
    result = _impact("M\tservices/shipment/src/shipment/main.py")
    assert result.affected_services == ["shipment"]
    assert result.full_validation is False
    assert result.run_service_scoped is True
    assert result.docs_only is False


def test_multiple_service_change() -> None:
    result = _impact(
        "M\tservices/shipment/src/shipment/main.py",
        "M\tservices/pickup/src/pickup/main.py",
    )
    assert result.affected_services == ["pickup", "shipment"]
    assert result.full_validation is False
    assert result.run_service_scoped is True


def test_shared_package_change_triggers_full_validation() -> None:
    result = _impact("M\tpackages/event_envelope/src/event_envelope/envelope.py")
    assert result.affected_packages == ["event_envelope"]
    assert result.full_validation is True
    assert result.fail_safe is True
    assert "shared_package_change" in result.fail_safe_reasons


def test_contract_change_triggers_full_validation() -> None:
    result = _impact("M\tcontracts/shipment/events/shipment.delivered.v1.json")
    assert result.impact_flags["contracts"] is True
    assert result.full_validation is True
    assert "contract_change" in result.fail_safe_reasons


def test_architecture_change_triggers_full_validation() -> None:
    result = _impact("M\tarchitecture/service-boundaries.yaml")
    assert result.impact_flags["architecture"] is True
    assert result.full_validation is True
    assert "architecture_change" in result.fail_safe_reasons


def test_governance_change_triggers_full_validation() -> None:
    result = _impact("M\t.cursor/rules/02-architecture-boundaries.mdc")
    assert result.impact_flags["governance"] is True
    assert result.full_validation is True
    assert "governance_change" in result.fail_safe_reasons


def test_infrastructure_change_triggers_full_validation() -> None:
    result = _impact("M\tinfra/compose/docker-compose.yml")
    assert result.impact_flags["infrastructure"] is True
    assert result.full_validation is True
    assert "infrastructure_change" in result.fail_safe_reasons


def test_docs_only_change_allows_docs_optimization() -> None:
    result = _impact(
        "M\tdocs/audit/legacy-baseline.md",
        "M\tREADME.md",
    )
    assert result.docs_only is True
    assert result.full_validation is False
    assert result.run_service_scoped is False
    assert result.run_architecture_gates is True
    assert result.run_governance_gates is True


def test_unknown_path_fails_safe() -> None:
    result = _impact("M\tmystery/config.yaml")
    assert result.unknown_paths == ["mystery/config.yaml"]
    assert result.full_validation is True
    assert result.fail_safe is True


def test_eventing_tests_classified_as_infrastructure() -> None:
    result = _impact("M\ttests/eventing/test_topology_config.py")
    assert result.impact_flags["infrastructure"] is True
    assert result.full_validation is True


def test_port_registry_classified_as_architecture() -> None:
    result = _impact("M\tarchitecture/runtime-port-registry.yaml")
    assert result.impact_flags["architecture"] is True
    assert result.full_validation is True


def test_port_verifier_script_triggers_full_validation() -> None:
    result = _impact("M\tscripts/quality/verify_port_allocations.py")
    assert result.full_validation is True


def test_deleted_file_still_classified() -> None:
    result = _impact("D\tservices/shipment/src/shipment/main.py")
    assert result.affected_services == ["shipment"]
    assert result.run_service_scoped is True


def test_renamed_file_classifies_both_paths() -> None:
    result = _impact("R100\tservices/shipment/old.py\tservices/shipment/new.py")
    paths = {item.path for item in result.path_changes}
    assert paths == {"services/shipment/new.py", "services/shipment/old.py"}
    assert result.affected_services == ["shipment"]


def test_missing_base_sha_fails_safe() -> None:
    result = calculate_impact(base_sha=None)
    assert result.fail_safe is True
    assert "missing_base_sha" in result.fail_safe_reasons
    assert result.full_validation is True


def test_deterministic_output_ordering() -> None:
    result = _impact(
        "M\tservices/pickup/src/pickup/main.py",
        "M\tservices/shipment/src/shipment/main.py",
        "M\tpackages/event_envelope/src/event_envelope/envelope.py",
    )
    payload = json.loads(render_json(result))
    assert payload["changed_paths"] == sorted(payload["changed_paths"])
    assert payload["impact"]["affected_services"] == sorted(payload["impact"]["affected_services"])
    classifications = sorted(payload["classifications"], key=lambda item: item["path"])
    assert payload["classifications"] == classifications
    assert payload["fail_safe_reasons"] == sorted(payload["fail_safe_reasons"])


def test_dependency_lock_change_triggers_full_validation() -> None:
    result = _impact("M\tuv.lock")
    assert result.full_validation is True
    assert "dependency_lock_change" in result.fail_safe_reasons


def test_ci_tooling_change_triggers_full_validation() -> None:
    result = _impact("M\tscripts/ci/path_impact.py")
    assert result.impact_flags["ci_tooling"] is True
    assert result.full_validation is True


def test_governance_gates_always_enabled() -> None:
    result = _impact("M\tdocs/audit/legacy-baseline.md")
    assert result.run_governance_gates is True
    assert result.run_architecture_gates is True
    assert result.run_quality_gates is True


def test_classify_path_service() -> None:
    item = classify_path("services/shipment/src/main.py")
    assert item.category.value == "service"
    assert item.service == "shipment"


def test_parse_changed_files_simple_path() -> None:
    changes = parse_changed_files(["services/shipment/main.py"])
    assert len(changes) == 1
    assert changes[0].path == "services/shipment/main.py"
    assert changes[0].status == "M"


def test_cli_with_changed_file_flag() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CALCULATE_SCRIPT),
            "--changed-file",
            "M\tservices/shipment/src/shipment/main.py",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["impact"]["affected_services"] == ["shipment"]


def test_cli_github_format() -> None:
    result = subprocess.run(
        _GITHUB_FORMAT_DOCS_ONLY_CMD,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_cli_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "full_validation=false" in result.stdout
    assert "docs_only=true" in result.stdout
    assert "fail_safe=false" in result.stdout
    assert "run_service_scoped=false" in result.stdout
    assert "affected_services=[]" in result.stdout
    assert "affected_packages=[]" in result.stdout
    assert "impact_json=" in result.stdout


def test_cli_github_format_writes_github_output_file(tmp_path: Path) -> None:
    github_output = tmp_path / "github_output.txt"
    result = subprocess.run(
        _GITHUB_FORMAT_DOCS_ONLY_CMD,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_cli_env(github_output=str(github_output)),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""

    contents = github_output.read_text(encoding="utf-8")
    expected_prefix = (
        "full_validation=false\n"
        "fail_safe=false\n"
        "docs_only=true\n"
        "run_service_scoped=false\n"
        "affected_services=[]\n"
        "affected_packages=[]\n"
        "impact_json<<EOF\n"
    )
    assert contents.startswith(expected_prefix)
    assert contents.endswith("\nEOF\n")

    raw_json = contents.removeprefix(expected_prefix).removesuffix("\nEOF\n")
    payload = json.loads(raw_json)
    assert raw_json == json.dumps(payload, sort_keys=True)
    assert payload["fail_safe"] is False
    assert payload["impact"]["docs_only"] is True
    assert payload["impact"]["affected_services"] == []
    assert payload["impact"]["affected_packages"] == []
    assert payload["validation_scope"]["full_validation"] is False
    assert payload["validation_scope"]["run_service_scoped"] is False
    assert payload["validation_scope"]["run_architecture_gates"] is True
    assert payload["validation_scope"]["run_governance_gates"] is True
    assert payload["validation_scope"]["run_quality_gates"] is True


def test_polling_lab_tests_classified_as_infrastructure() -> None:
    result = _impact("M\ttests/polling_lab/test_strategies_matrix.py")
    assert result.impact_flags["infrastructure"] is True
    assert result.full_validation is True


def test_cdc_lab_tests_classified_as_infrastructure() -> None:
    result = _impact("M\ttests/cdc_lab/test_operational_analysis.py")
    assert result.impact_flags["infrastructure"] is True
    assert result.full_validation is True


def test_legacy_polling_lab_infra_path() -> None:
    result = _impact("M\tinfra/labs/legacy-polling/REPORT.md")
    assert result.impact_flags["infrastructure"] is True


def test_git_diff_integration_when_base_available() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = calculate_impact(base_sha=head, head_sha=head)
    assert result.changed_paths == []
    assert result.docs_only is True
    assert result.full_validation is False
