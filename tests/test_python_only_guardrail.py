"""Guardrails that prevent JavaScript and bundled native SDK artifacts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", "__pycache__"}
FORBIDDEN_NAMES = {"package.json", "package-lock.json"}
FORBIDDEN_SUFFIXES = {".js", ".mjs", ".cjs"}
FORBIDDEN_NATIVE_SUFFIXES = {".so", ".dll", ".dylib"}
ALLOWLIST: set[Path] = set()
POLICY_MESSAGE = "pytvt is Python-only; Node/JS is not allowed"


def _forbidden_paths() -> list[Path]:
    matches: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        relative_path = path.relative_to(REPO_ROOT)
        if relative_path in ALLOWLIST:
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            matches.append(relative_path)
    return sorted(matches)


def test_repository_contains_no_javascript_or_npm_artifacts():
    violations = _forbidden_paths()
    assert violations == [], f"{POLICY_MESSAGE}: {violations}"


def test_python_only_patterns_are_ignored_and_hooked():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("node_modules/", "*.js", "*.mjs", "*.cjs", "package.json", "package-lock.json"):
        assert pattern in gitignore

    pre_commit = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "pytvt-python-only-policy" in pre_commit
    assert "tools/check_python_only.py --staged" in pre_commit


def test_built_wheel_contains_no_javascript_or_native_sdk_artifacts(tmp_path):
    out_dir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_path = next(out_dir.glob("*.whl"))

    with ZipFile(wheel_path) as archive:
        names = archive.namelist()

    js_matches = [
        name
        for name in names
        if name.endswith(tuple(FORBIDDEN_SUFFIXES))
        or name.endswith("package.json")
        or name.endswith("package-lock.json")
    ]
    native_matches = [name for name in names if Path(name).suffix in FORBIDDEN_NATIVE_SUFFIXES]

    assert js_matches == [], f"{POLICY_MESSAGE}: wheel contains forbidden entries {js_matches}"
    assert native_matches == [], f"Wheel must not bundle vendor SDK binaries: {native_matches}"
