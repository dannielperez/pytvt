# Contributing to pytvt

Thanks for your interest in contributing. This is a solo-maintained project, but bug reports, fixes, and well-scoped improvements are welcome.

## Getting Started

```bash
git clone --recurse-submodules https://github.com/dannielperez/pytvt.git
cd pytvt
pip install -e ".[dev]"
```

## Development

### Running tests

```bash
pytest
```

### Linting + formatting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

### Type checking (optional)

```bash
mypy src/pytvt/
```

## Code Style

- Python 3.10+ — use modern syntax (`X | Y` unions, etc.)
- Formatting and linting handled by [Ruff](https://docs.astral.sh/ruff/)
- Prefer stdlib `dataclasses` for data models
- Keep functions small and testable — avoid side effects in library code
- Include type annotations on public APIs

## Pull Requests

1. Fork the repo and create a feature branch
2. Keep changes focused — one concern per PR
3. Add or update tests for changed behavior
4. Run `ruff check` and `pytest` before submitting
5. Fill out the PR template

## Code Tiers

pytvt separates code into three tiers. Understand which tier you are working in:

| Tier | Location | CI-gated | Imported at runtime |
|---|---|---|---|
| **Supported runtime** | `src/pytvt/`, `bridges/` | Yes | Yes |
| **Compatibility runtime** | `tvt-api/` | Separate repo | Via HTTP (sdk backend) |
| **Research / reference** | `research/` | No | **No** — never import |
| **Operational tools** | `tools/` | No | Imports *from* pytvt |

**Key rule:** code in `src/pytvt/` must never import from `research/` or `tools/`. This boundary is enforced by `tests/test_architecture.py::TestImportBoundary`.

If you are adding protocol research scripts, put them in `research/` with the standard header:

```python
#!/usr/bin/env python3
"""
RESEARCH / REFERENCE ONLY — not part of the pytvt runtime.

<description>

See research/README.md for context.
"""
```

## Bug Reports

Use the GitHub issue templates. Include:

- Python version and OS
- Steps to reproduce
- Expected vs actual behavior
- Full error output / traceback

## Scope

This project focuses on TVT NVR device management. Out-of-scope:

- Support for non-TVT vendors (see related repos)
- Features requiring always-on infrastructure
- GUI / web interface

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
