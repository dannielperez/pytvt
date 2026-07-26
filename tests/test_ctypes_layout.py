"""Compatibility contracts for packed ctypes structures."""

from __future__ import annotations

import ctypes as ct
from types import ModuleType

import pytest

from pytvt.device_sdk import types as device_types
from pytvt.platform_sdk import platform_backend


def _packed_structures(module: ModuleType) -> list[type[ct.Structure]]:
    return [
        candidate
        for candidate in vars(module).values()
        if isinstance(candidate, type)
        and issubclass(candidate, ct.Structure)
        and candidate is not ct.Structure
        and candidate.__module__ == module.__name__
        and bool(getattr(candidate, "_pack_", 0))
    ]


@pytest.mark.parametrize("module", [device_types, platform_backend])
def test_packed_structures_declare_msvc_layout(module: ModuleType) -> None:
    """Do not rely on the implicit layout removed by Python 3.19."""
    packed_structures = _packed_structures(module)

    assert packed_structures
    assert all(getattr(structure, "_layout_", None) == "ms" for structure in packed_structures)
