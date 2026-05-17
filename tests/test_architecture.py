"""Tests for pytvt.constants, pytvt.exceptions, and pytvt.registry."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from pytvt.constants import (
    CLI_BACKEND_ALIASES,
    CLI_BACKEND_CHOICES,
    BackendFamily,
    CompositeStrategy,
    ExecutionPlan,
    IntegrationMode,
    _make_label,
    resolve_backend,
)
from pytvt.exceptions import BackendError, PytvtError, RegistryError
from pytvt.registry import _registry, dispatch, get, registered_keys, resolve_execution_plan

# ── BackendFamily enum ───────────────────────────────────────────────


class TestBackendFamily:
    def test_values(self):
        assert BackendFamily.PROTOCOL.value == "protocol"
        assert BackendFamily.SDK.value == "sdk"

    def test_str(self):
        assert str(BackendFamily.PROTOCOL) == "protocol"
        assert str(BackendFamily.SDK) == "sdk"

    def test_is_str_subclass(self):
        assert isinstance(BackendFamily.PROTOCOL, str)
        assert BackendFamily.PROTOCOL == "protocol"


# ── IntegrationMode enum ────────────────────────────────────────────


class TestIntegrationMode:
    def test_values(self):
        assert IntegrationMode.COMPAT_BRIDGE.value == "compat_bridge"
        assert IntegrationMode.DIRECT_SDK.value == "direct_sdk"

    def test_str(self):
        assert str(IntegrationMode.COMPAT_BRIDGE) == "compat_bridge"


# ── CompositeStrategy enum ──────────────────────────────────────────


class TestCompositeStrategy:
    def test_values(self):
        assert CompositeStrategy.BOTH.value == "both"


# ── resolve_backend ─────────────────────────────────────────────────


class TestResolveBackend:
    def test_protocol(self):
        family, mode = resolve_backend("protocol")
        assert family is BackendFamily.PROTOCOL
        assert mode is None

    def test_sdk(self):
        family, mode = resolve_backend("sdk")
        assert family is BackendFamily.SDK
        assert mode is IntegrationMode.COMPAT_BRIDGE

    def test_sdk_local_hyphen(self):
        family, mode = resolve_backend("sdk-local")
        assert family is BackendFamily.SDK
        assert mode is IntegrationMode.DIRECT_SDK

    def test_sdk_local_underscore(self):
        family, mode = resolve_backend("sdk_local")
        assert family is BackendFamily.SDK
        assert mode is IntegrationMode.DIRECT_SDK

    def test_both(self):
        strat, mode = resolve_backend("both")
        assert strat is CompositeStrategy.BOTH
        assert mode is None

    def test_canonical_compat_bridge(self):
        family, mode = resolve_backend("compat_bridge")
        assert family is BackendFamily.SDK
        assert mode is IntegrationMode.COMPAT_BRIDGE

    def test_canonical_direct_sdk(self):
        family, mode = resolve_backend("direct_sdk")
        assert family is BackendFamily.SDK
        assert mode is IntegrationMode.DIRECT_SDK

    def test_case_insensitive(self):
        family, _mode = resolve_backend("Protocol")
        assert family is BackendFamily.PROTOCOL

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            resolve_backend("invalid")

    def test_whitespace_stripped(self):
        family, _mode = resolve_backend("  protocol  ")
        assert family is BackendFamily.PROTOCOL


# ── CLI_BACKEND_CHOICES ──────────────────────────────────────────────


class TestCliBackendChoices:
    def test_contains_all_public_names(self):
        assert "protocol" in CLI_BACKEND_CHOICES
        assert "sdk" in CLI_BACKEND_CHOICES
        assert "sdk-local" in CLI_BACKEND_CHOICES
        assert "both" in CLI_BACKEND_CHOICES

    def test_all_choices_resolve(self):
        for name in CLI_BACKEND_CHOICES:
            resolve_backend(name)  # should not raise

    def test_all_aliases_resolve(self):
        for name in CLI_BACKEND_ALIASES:
            resolve_backend(name)  # should not raise


# ── Exceptions ───────────────────────────────────────────────────────


class TestExceptions:
    def test_hierarchy(self):
        assert issubclass(BackendError, PytvtError)
        assert issubclass(RegistryError, PytvtError)
        assert issubclass(PytvtError, Exception)

    def test_backend_error_fields(self):
        e = BackendError("fail", backend="sdk")
        assert str(e) == "fail"
        assert e.backend == "sdk"

    def test_registry_error(self):
        e = RegistryError("duplicate")
        assert str(e) == "duplicate"


# ── Registry ─────────────────────────────────────────────────────────


class TestRegistry:
    def test_defaults_installed(self):
        keys = registered_keys()
        assert (BackendFamily.PROTOCOL, None) in keys
        assert (BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE) in keys
        assert (BackendFamily.SDK, IntegrationMode.DIRECT_SDK) in keys

    def test_get_protocol(self):
        fn = get(BackendFamily.PROTOCOL, None)
        assert callable(fn)

    def test_get_sdk_compat(self):
        fn = get(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE)
        assert callable(fn)

    def test_get_sdk_direct(self):
        fn = get(BackendFamily.SDK, IntegrationMode.DIRECT_SDK)
        assert callable(fn)

    def test_get_unregistered_raises(self):
        with pytest.raises(RegistryError, match="No backend registered"):
            get(BackendFamily.PROTOCOL, IntegrationMode.COMPAT_BRIDGE)


# ── dispatch ─────────────────────────────────────────────────────────


class TestDispatch:
    def test_dispatch_protocol(self, sample_device, default_config):
        from unittest.mock import patch

        with patch("pytvt.scanner._raw_protocol_scan") as mock:
            mock.return_value = {
                "success": True,
                "device_name": "",
                "device_model": "",
                "serial_number": "",
                "firmware": "",
                "total_channels": 0,
                "cameras": [],
                "device_info": {},
            }
            result = dispatch("protocol", sample_device, default_config)
            assert result.success is True
            assert result.backend == "protocol"

    def test_dispatch_unknown_raises(self, sample_device, default_config):
        with pytest.raises(ValueError, match="Unknown backend"):
            dispatch("nonexistent", sample_device, default_config)


# ── _make_label ──────────────────────────────────────────────────────


class TestMakeLabel:
    def test_protocol(self):
        assert _make_label(BackendFamily.PROTOCOL, None, None) == "protocol"

    def test_sdk_compat_bridge(self):
        assert _make_label(BackendFamily.SDK, IntegrationMode.COMPAT_BRIDGE, None) == "sdk/compat_bridge"

    def test_sdk_direct_sdk(self):
        assert _make_label(BackendFamily.SDK, IntegrationMode.DIRECT_SDK, None) == "sdk/direct_sdk"

    def test_both_strategy(self):
        label = _make_label(BackendFamily.PROTOCOL, None, CompositeStrategy.BOTH)
        assert label == "both (protocol → sdk/compat_bridge)"


# ── ExecutionPlan dataclass ──────────────────────────────────────────


class TestExecutionPlan:
    def test_is_frozen(self):
        plan = ExecutionPlan(
            backend_family=BackendFamily.PROTOCOL,
            integration_mode=None,
            strategy=None,
            executor=lambda d, c: None,
            label="protocol",
        )
        with pytest.raises(AttributeError):
            plan.label = "changed"  # type: ignore[misc]

    def test_is_composite_false(self):
        plan = ExecutionPlan(
            backend_family=BackendFamily.PROTOCOL,
            integration_mode=None,
            strategy=None,
            executor=lambda d, c: None,
            label="protocol",
        )
        assert plan.is_composite is False

    def test_is_composite_true(self):
        plan = ExecutionPlan(
            backend_family=BackendFamily.PROTOCOL,
            integration_mode=None,
            strategy=CompositeStrategy.BOTH,
            executor=lambda d, c: None,
            label="both (protocol → sdk/compat_bridge)",
        )
        assert plan.is_composite is True

    def test_execute_delegates(self):
        sentinel = object()
        plan = ExecutionPlan(
            backend_family=BackendFamily.PROTOCOL,
            integration_mode=None,
            strategy=None,
            executor=lambda d, c: sentinel,
            label="protocol",
        )
        assert plan.execute("dev", "cfg") is sentinel

    def test_steps_default_empty(self):
        plan = ExecutionPlan(
            backend_family=BackendFamily.PROTOCOL,
            integration_mode=None,
            strategy=None,
            executor=lambda d, c: None,
            label="protocol",
        )
        assert plan.steps == []


# ── resolve_execution_plan ───────────────────────────────────────────


class TestResolveExecutionPlan:
    def test_protocol(self):
        plan = resolve_execution_plan("protocol")
        assert plan.backend_family is BackendFamily.PROTOCOL
        assert plan.integration_mode is None
        assert plan.strategy is None
        assert plan.label == "protocol"
        assert plan.is_composite is False
        assert plan.steps == ["protocol"]
        assert callable(plan.executor)

    def test_sdk(self):
        plan = resolve_execution_plan("sdk")
        assert plan.backend_family is BackendFamily.SDK
        assert plan.integration_mode is IntegrationMode.COMPAT_BRIDGE
        assert plan.strategy is None
        assert plan.label == "sdk/compat_bridge"
        assert plan.steps == ["sdk/compat_bridge"]

    def test_sdk_local_hyphen(self):
        plan = resolve_execution_plan("sdk-local")
        assert plan.backend_family is BackendFamily.SDK
        assert plan.integration_mode is IntegrationMode.DIRECT_SDK
        assert plan.strategy is None
        assert plan.label == "sdk/direct_sdk"
        assert plan.steps == ["sdk/direct_sdk"]

    def test_sdk_local_underscore(self):
        plan = resolve_execution_plan("sdk_local")
        assert plan.label == "sdk/direct_sdk"

    def test_both(self):
        plan = resolve_execution_plan("both")
        assert plan.backend_family is BackendFamily.PROTOCOL
        assert plan.integration_mode is None
        assert plan.strategy is CompositeStrategy.BOTH
        assert plan.label == "both (protocol → sdk/compat_bridge)"
        assert plan.is_composite is True
        assert plan.steps == ["protocol", "sdk/compat_bridge"]
        assert callable(plan.executor)

    def test_compat_bridge_alias(self):
        plan = resolve_execution_plan("compat_bridge")
        assert plan.backend_family is BackendFamily.SDK
        assert plan.integration_mode is IntegrationMode.COMPAT_BRIDGE
        assert plan.label == "sdk/compat_bridge"

    def test_direct_sdk_alias(self):
        plan = resolve_execution_plan("direct_sdk")
        assert plan.backend_family is BackendFamily.SDK
        assert plan.integration_mode is IntegrationMode.DIRECT_SDK
        assert plan.label == "sdk/direct_sdk"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            resolve_execution_plan("nonexistent")

    def test_all_cli_choices_resolve(self):
        for name in CLI_BACKEND_CHOICES:
            plan = resolve_execution_plan(name)
            assert plan.label  # non-empty
            assert callable(plan.executor)

    def test_all_aliases_resolve(self):
        for name in CLI_BACKEND_ALIASES:
            plan = resolve_execution_plan(name)
            assert plan.label

    def test_protocol_plan_executes(self, sample_device, default_config):
        plan = resolve_execution_plan("protocol")
        with patch("pytvt.scanner._raw_protocol_scan") as mock:
            mock.return_value = {
                "success": True,
                "device_name": "",
                "device_model": "",
                "serial_number": "",
                "firmware": "",
                "total_channels": 0,
                "cameras": [],
                "device_info": {},
            }
            result = plan.execute(sample_device, default_config)
            assert result.success is True
            assert result.backend == "protocol"

    def test_both_plan_executes_protocol_success(self, sample_device, default_config):
        plan = resolve_execution_plan("both")
        with patch("pytvt.scanner._raw_protocol_scan") as mock:
            mock.return_value = {
                "success": True,
                "device_name": "",
                "device_model": "",
                "serial_number": "",
                "firmware": "",
                "total_channels": 0,
                "cameras": [],
                "device_info": {},
            }
            result = plan.execute(sample_device, default_config)
            assert result.success is True
            assert result.backend == "protocol"


# ── Import boundary guardrail ────────────────────────────────────────


class TestImportBoundary:
    """Ensure runtime code does not import from research/ or tools/."""

    def test_no_research_imports_in_runtime(self):
        """No src/pytvt/ module should import from research/ or tools/."""
        import pathlib

        src_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "pytvt"
        violations = []

        for py_file in sorted(src_dir.glob("**/*.py")):
            # Skip files inside the in-package CLI lane (pytvt/tools/*) — those
            # are CLI entrypoints and may legitimately import everything.
            if "tools" in py_file.parts:
                continue
            # __main__.py is the CLI dispatcher; it must reach into pytvt.tools.cli.
            if py_file.name == "__main__.py":
                continue
            lines = py_file.read_text().splitlines()
            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                # Skip comments
                if stripped.startswith("#"):
                    continue
                # Flag imports from the repo-root research/ or tools/ trees, but
                # NOT references to the in-package pytvt.tools.* CLI lane.
                for keyword in ("research.", "research/", "tools/"):
                    if keyword in stripped and ("import" in stripped or "open(" in stripped):
                        violations.append(f"{py_file.name}:{lineno}: {stripped}")
                # bare "tools." (no leading "pytvt.") flags the old repo-root scripts.
                if "tools." in stripped and "pytvt.tools." not in stripped and (
                    "import" in stripped or "open(" in stripped
                ):
                    violations.append(f"{py_file.name}:{lineno}: {stripped}")

        assert violations == [], "Runtime code must not depend on research/ or tools/:\n" + "\n".join(violations)
