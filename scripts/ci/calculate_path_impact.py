#!/usr/bin/env python3
"""Calculate architecture-aware CI path impact for changed files.

Usage:
    uv run python scripts/ci/calculate_path_impact.py --base <sha> --head <sha>
    uv run python scripts/ci/calculate_path_impact.py --changed-file <path>

Environment:
    CI_BASE_SHA / CI_HEAD_SHA — comparison range when flags are omitted.

Exit code 0 on success. Writes JSON to stdout unless --format github is used.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

SCRIPT_DIR = Path(__file__).resolve().parent


def _load_path_impact_module() -> ModuleType:
    module_path = SCRIPT_DIR / "path_impact.py"
    spec = importlib.util.spec_from_file_location("path_impact", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load path impact module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate CI path impact.")
    parser.add_argument("--base", help="Base git SHA for comparison.")
    parser.add_argument("--head", help="Head git SHA for comparison (default: HEAD).")
    parser.add_argument(
        "--changed-file",
        action="append",
        dest="changed_files",
        help="Explicit changed-file line (git name-status format). Repeatable.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "github"),
        default="json",
        help="Output format (default: json).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file path (for github format or JSON capture).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    path_impact = _load_path_impact_module()

    base_sha = args.base or os.environ.get("CI_BASE_SHA")
    head_sha = args.head or os.environ.get("CI_HEAD_SHA")

    if args.changed_files:
        result = path_impact.calculate_impact(changed_lines=args.changed_files)
    else:
        result = path_impact.calculate_impact(base_sha=base_sha, head_sha=head_sha)

    if args.format == "github":
        rendered = path_impact.render_github_output(result)
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            path_impact.write_github_output(result, Path(github_output))
        elif args.output:
            path_impact.write_github_output(result, args.output)
        else:
            sys.stdout.write(rendered)
            sys.stdout.write("impact_json=")
            sys.stdout.write(json.dumps(result.to_dict(), sort_keys=True))
            sys.stdout.write("\n")
    else:
        rendered = path_impact.render_json(result)
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)

    return 0


if __name__ == "__main__":
    sys.exit(main())
