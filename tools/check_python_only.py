#!/usr/bin/env python3
"""Repository policy check for pytvt's Python-only runtime.

Fails when JavaScript or npm artifacts are added to the repository.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".js", ".mjs", ".cjs"}
FORBIDDEN_NAMES = {"package.json", "package-lock.json"}
POLICY_MESSAGE = "pytvt is Python-only; Node/JS is not allowed"


def _git_paths(*args: str) -> list[Path]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _violations(paths: list[Path]) -> list[Path]:
    matches: list[Path] = []
    for path in paths:
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            matches.append(path)
    return sorted(matches)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=POLICY_MESSAGE)
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Check only staged added/copied/renamed files.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.staged:
        paths = _git_paths("diff", "--cached", "--name-only", "--diff-filter=ACR")
    else:
        paths = _git_paths("ls-files")

    violations = _violations(paths)
    if not violations:
        return 0

    print(POLICY_MESSAGE, file=sys.stderr)
    for path in violations:
        print(f"  - {path.as_posix()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())