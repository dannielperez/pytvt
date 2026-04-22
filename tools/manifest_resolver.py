#!/usr/bin/env python3
"""Manifest resolver for tvt-sdk inventory integration.

Loads SDK manifests from tvt-sdk repository and resolves to specific binaries.
Isolated module with no dependencies on pytvt internals.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml


class ManifestNotFoundError(Exception):
    """Manifest ID not found in index."""
    pass


class InvalidManifestError(Exception):
    """Manifest structure is invalid."""
    pass


class SDKBinaryNotFoundError(Exception):
    """No matching binary found in manifest."""
    pass


def _get_default_inventory_root() -> Path:
    """Get default SDK inventory root from env or config."""
    if env_root := os.environ.get("TVT_SDK_INVENTORY_ROOT"):
        return Path(env_root)
    if env_root := os.environ.get("TVT_SDK_REPO_ROOT"):
        return Path(env_root)
    return Path.home() / "tvt-sdk"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and parse YAML file."""
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise InvalidManifestError(f"Invalid YAML structure (not a dict): {path}")
        return data
    except FileNotFoundError:
        raise InvalidManifestError(f"File not found: {path}")
    except yaml.YAMLError as e:
        raise InvalidManifestError(f"YAML parse error in {path}: {e}")


def _select_binary_for_platform(
    binaries: list[dict[str, Any]],
    os_family: str,
) -> dict[str, Any]:
    """Select appropriate binary for platform.
    
    Rules:
    - Prefer .so on Linux, .dll on Windows, .dylib on macOS
    - If multiple candidates, choose first with NET_SDK / NetClientSDK pattern
    - Return first matching if no pattern match found
    """
    if not binaries:
        raise SDKBinaryNotFoundError("No binaries listed in manifest")
    
    # Platform-specific extensions and naming patterns
    preferences = {
        "linux": ([".so"], ["NET_SDK", "net_sdk"]),
        "windows": ([".dll"], ["NET_SDK", "NetClientSDK"]),
        "macos": ([".dylib", ".framework"], ["NET_SDK", "net_sdk", "NetClientSDK"]),
        "android": ([".so"], ["NET_SDK"]),
        "ios": ([".framework"], ["NET_SDK"]),
    }
    
    extensions, patterns = preferences.get(os_family, ([], []))
    
    # First pass: match extension and naming pattern
    for pattern in patterns:
        for binary in binaries:
            name = binary.get("name", "")
            path = binary.get("path", "")
            
            if pattern.lower() in name.lower() or pattern.lower() in path.lower():
                for ext in extensions:
                    if path.lower().endswith(ext.lower()):
                        return binary
    
    # Second pass: match extension only
    for binary in binaries:
        path = binary.get("path", "")
        for ext in extensions:
            if path.lower().endswith(ext.lower()):
                return binary
    
    # Fallback: return first binary (deterministic but may not be ideal)
    return binaries[0]


def resolve_manifest(
    manifest_id: str,
    inventory_root: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Resolve manifest by ID and return {sdk_path, manifest, binary}.
    
    Args:
        manifest_id: Manifest entry ID (e.g., 'tvt-windows-mgmt-20260401-v2.1.0')
        inventory_root: Path to tvt-sdk inventory root (default: env or hardcoded)
    
    Returns:
        {
            "sdk_path": "/path/to/binary",
            "manifest": {...},
            "binary": {...},
            "manifest_id": "...",
        }
    
    Raises:
        ManifestNotFoundError: Manifest ID not found in index
        InvalidManifestError: Manifest structure invalid
        SDKBinaryNotFoundError: No matching binary found
    """
    if inventory_root is None:
        inventory_root = _get_default_inventory_root()
    else:
        inventory_root = Path(inventory_root)
    
    if not inventory_root.exists():
        raise InvalidManifestError(f"SDK inventory root not found: {inventory_root}")
    
    # Load index
    index_path = inventory_root / "manifest" / "index.yaml"
    if not index_path.exists():
        raise InvalidManifestError(f"Manifest index not found: {index_path}")
    
    index = _load_yaml(index_path)
    sdks = index.get("sdks", [])
    
    # Find SDK entry by ID
    sdk_entry = None
    for entry in sdks:
        if entry.get("id") == manifest_id:
            sdk_entry = entry
            break
    
    if sdk_entry is None:
        available_ids = [e.get("id") for e in sdks if e.get("id")]
        raise ManifestNotFoundError(
            f"Manifest ID not found: {manifest_id}\n"
            f"Available: {', '.join(available_ids[:5])}"
        )
    
    # Load manifest file
    manifest_path = inventory_root / sdk_entry.get("path", "")
    if not manifest_path.exists():
        raise InvalidManifestError(
            f"Manifest file not found: {manifest_path}"
        )
    
    manifest = _load_yaml(manifest_path)
    
    # Validate required fields
    required = ["id", "os_family", "artifact_root", "binaries"]
    for field in required:
        if field not in manifest:
            raise InvalidManifestError(
                f"Manifest missing required field: {field} (in {manifest_path})"
            )
    
    # Select binary
    os_family = manifest.get("os_family", "")
    binaries = manifest.get("binaries", [])
    
    selected_binary = _select_binary_for_platform(binaries, os_family)
    
    # Construct SDK path
    artifact_root = Path(manifest.get("artifact_root", ""))
    sdk_path = artifact_root / selected_binary.get("path", "")
    
    if not sdk_path.exists():
        raise SDKBinaryNotFoundError(
            f"SDK binary not found at: {sdk_path}\n"
            f"Artifact root: {artifact_root}\n"
            f"Binary path: {selected_binary.get('path')}"
        )
    
    return {
        "sdk_path": str(sdk_path),
        "manifest": manifest,
        "binary": selected_binary,
        "manifest_id": manifest_id,
    }


def extract_artifact_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract artifact metadata block for diagnostics.
    
    Returns minimal metadata suitable to attach to diagnostics output.
    """
    return {
        "manifest_id": manifest.get("id", "unknown"),
        "os_family": manifest.get("os_family", "unknown"),
        "sdk_family": manifest.get("sdk_family", "unknown"),
        "artifact_root": manifest.get("artifact_root", ""),
        "source_origin": manifest.get("source_origin", {}),
        "product_scope": manifest.get("product_scope", []),
        "classification": manifest.get("classification", {}),
    }
