"""SDK identity/context models for backend portability.

These models normalize backend/platform/SDK metadata without implying runtime
semantics. Capability values must be evidence-driven.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_SUPPORTED_OS = {"linux", "windows", "macos", "android", "ios", "unknown"}
_SUPPORTED_RUNTIME_KIND = {"native", "sidecar", "bridge", "compat"}
_SUPPORTED_SDK_FAMILY = {"device_sdk", "management_sdk", "mobile_sdk", "unknown"}
_SUPPORTED_PRODUCT_SCOPE = {"nvr", "ipc", "management_server"}


@dataclass(frozen=True)
class PlatformIdentity:
    os_family: str
    arch: str | None = None
    runtime_kind: str = "native"

    def __post_init__(self) -> None:
        if self.os_family not in _SUPPORTED_OS:
            raise ValueError(f"Unsupported os_family: {self.os_family}")
        if self.runtime_kind not in _SUPPORTED_RUNTIME_KIND:
            raise ValueError(f"Unsupported runtime_kind: {self.runtime_kind}")


@dataclass(frozen=True)
class SDKIdentity:
    vendor: str = "tvt"
    sdk_name: str | None = None
    sdk_family: str = "unknown"
    sdk_version: str | None = None

    def __post_init__(self) -> None:
        if self.sdk_family not in _SUPPORTED_SDK_FAMILY:
            raise ValueError(f"Unsupported sdk_family: {self.sdk_family}")


@dataclass(frozen=True)
class CapabilityMap:
    supports_init: bool = False
    supports_login: bool = False
    supports_login_ex: bool = False
    supports_logout: bool = False
    supports_device_enumeration: bool = False
    supports_alarm_subscription: bool = False
    supports_management_server_login: str | bool = False

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "supports_init": self.supports_init,
            "supports_login": self.supports_login,
            "supports_login_ex": self.supports_login_ex,
            "supports_logout": self.supports_logout,
            "supports_device_enumeration": self.supports_device_enumeration,
            "supports_alarm_subscription": self.supports_alarm_subscription,
            "supports_management_server_login": self.supports_management_server_login,
        }


@dataclass(frozen=True)
class SDKContext:
    platform: PlatformIdentity
    sdk: SDKIdentity
    product_scope: set[str] = field(default_factory=set)
    capabilities: CapabilityMap = field(default_factory=CapabilityMap)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        invalid = sorted(item for item in self.product_scope if item not in _SUPPORTED_PRODUCT_SCOPE)
        if invalid:
            raise ValueError(f"Unsupported product_scope values: {', '.join(invalid)}")

    def as_dict(self) -> dict[str, object]:
        return {
            "platform": {
                "os_family": self.platform.os_family,
                "arch": self.platform.arch,
                "runtime_kind": self.platform.runtime_kind,
            },
            "sdk": {
                "vendor": self.sdk.vendor,
                "sdk_name": self.sdk.sdk_name,
                "sdk_family": self.sdk.sdk_family,
                "sdk_version": self.sdk.sdk_version,
            },
            "product_scope": sorted(self.product_scope),
            "capabilities": self.capabilities.as_dict(),
            "notes": list(self.notes),
        }
