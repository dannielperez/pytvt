"""Guardrails that prevent JavaScript and npm artifacts from returning."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist"}
FORBIDDEN_NAMES = {"package.json", "package-lock.json"}
FORBIDDEN_SUFFIXES = {".js", ".mjs", ".cjs"}


def _forbidden_paths() -> list[Path]:
    matches: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            matches.append(path.relative_to(REPO_ROOT))
    return sorted(matches)


def test_repository_contains_no_javascript_or_npm_artifacts():
    assert _forbidden_paths() == []
