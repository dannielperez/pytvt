"""SDK-backed management-server backend for pytvt.

Phase 3 design goals
--------------------
- Inspect a real vendor library using export evidence instead of file presence.
- Keep every management operation explicit about whether its symbol is
    confirmed, merely a candidate, or still missing.
- Reuse validated ctypes signatures already present in ``pytvt.device_sdk`` when
    and only when the exact exported symbol has matching in-repo evidence.
- Stop before unsafe calls whenever the symbol identity or signature evidence is
    incomplete.

Evidence model
--------------
This module distinguishes between:

- symbol presence: what the shared object actually exports
- symbol suitability: whether we have enough evidence to associate that symbol
    with a management capability
- signature readiness: whether pytvt already contains a validated ctypes
    prototype for the exact symbol

Today, the only operation promoted to a real callable path is the narrow login
session lifecycle using ``NET_SDK_Init`` / ``NET_SDK_Login`` /
``NET_SDK_Logout``. All other operations remain blocked behind precise
``CapabilityNotAvailable`` errors until their management semantics are proven.
"""

from __future__ import annotations

import ctypes
import ctypes as ct
import logging
import platform
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pytvt.device_sdk import types as netsdk_types

from .base import BaseManagementBackend
from .context import CapabilityMap, PlatformIdentity, SDKContext, SDKIdentity
from .exceptions import (
    CapabilityNotAvailable,
    ManagementAuthError,
    ManagementNotAuthenticatedError,
    MissingSymbolError,
    ProtocolError,
    SessionExpired,
    TransportError,
    UnsupportedOnPlatformError,
)
from .models import AlarmSubscription, DeviceStatus, ManagedChannel, ManagedDevice, ServerInfo
from .sdk_contract import stable_contract_definition
from .sdk_symbols import (
    build_symbol_capability_evidence,
    build_symbol_inventory,
    build_symbol_presence_checks,
    build_windows_parity_report,
    list_exported_symbols,
)

logger = logging.getLogger(__name__)

SymbolStatus = Literal["confirmed", "candidate", "missing"]
SdkLoginMode = Literal["login", "login_ex"]
SdkConnectType = Literal["tcp", "nat", "nat20"]
EvidenceConfidence = Literal["low", "medium", "high"]
EvidenceSourceType = Literal[
    "linux_sdk_probe",
    "nvms_windows_export",
    "nvms_windows_strings",
    "nvms_windows_log",
    "nvms_windows_config",
    "nvms_macos_export",
    "nvms_macos_strings",
    "runtime_dependency_trace",
]


@dataclass(frozen=True)
class EvidenceRecord:
    """Minimal non-proprietary evidence record for interoperability work.

    Stores only factual metadata derived from local analysis. It does not store
    proprietary code or binary payloads.
    """

    symbol_name: str | None
    suspected_capability: str
    source_type: EvidenceSourceType
    confidence: EvidenceConfidence
    notes: str
    source_path: str | None = None
    observed_value: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_name": self.symbol_name,
            "suspected_capability": self.suspected_capability,
            "source_type": self.source_type,
            "confidence": self.confidence,
            "notes": self.notes,
            "source_path": self.source_path,
            "observed_value": self.observed_value,
        }


@dataclass(frozen=True)
class SymbolEvidence:
    """Evidence definition for one management capability.

    ``status`` resolution is based on the strongest matching symbol in this
    priority order:
    1. confirmed names
    2. candidate names
    3. missing
    """

    purpose: str
    confirmed_names: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()
    signature_source: str | None = None
    signature_ready: bool = False
    notes: str = ""
    confidence: EvidenceConfidence = "low"
    semantics: str = "unknown"
    live_validation_status: str = "not_live_validated"
    evidence_records: tuple[EvidenceRecord, ...] = ()


@dataclass(frozen=True)
class ResolvedSymbol:
    """Resolved status for one SDK management capability."""

    purpose: str
    status: SymbolStatus
    symbol_name: str | None
    signature_ready: bool
    signature_source: str | None
    notes: str
    confidence: EvidenceConfidence
    semantics: str
    live_validation_status: str
    evidence_records: tuple[EvidenceRecord, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "status": self.status,
            "symbol_name": self.symbol_name,
            "signature_ready": self.signature_ready,
            "signature_source": self.signature_source,
            "notes": self.notes,
            "confidence": self.confidence,
            "semantics": self.semantics,
            "live_validation_status": self.live_validation_status,
            "evidence_sources": _summarize_evidence_sources(self.evidence_records),
            "evidence_records": [record.as_dict() for record in self.evidence_records],
        }


@dataclass(frozen=True)
class BoundSdkFunction:
    """Internal binding metadata for a callable SDK function."""

    purpose: str
    symbol_name: str
    argtypes: tuple[Any, ...]
    restype: Any
    signature_source: str


def _normalized_os_family() -> str:
    system_name = (platform.system() or "unknown").lower()
    if system_name.startswith("darwin"):
        return "macos"
    if system_name in {"linux", "windows", "android", "ios"}:
        return system_name
    return "unknown"


def _normalized_sdk_family(legacy_family: str) -> str:
    if legacy_family == "linux_native":
        return "device_sdk"
    if legacy_family == "windows_sidecar" or legacy_family == "management_sdk":
        return "management_sdk"
    return "unknown"


@dataclass(frozen=True)
class SdkDiagnostics:
    """Structured diagnostics for SDK capability evaluation."""

    sdk_path: str
    load_success: bool
    load_error: str | None
    symbol_scan_success: bool
    symbol_scan_error: str | None
    discovered_symbol_count: int | None
    symbols: dict[str, ResolvedSymbol]
    login_path_ready: bool
    login_readiness_reason: str
    evidence_record_count: int
    login_mode: SdkLoginMode = "login"
    login_connect_type: SdkConnectType = "tcp"
    runtime_machine: str | None = None
    sdk_machine: str | None = None
    architecture_compatible: bool | None = None
    architecture_note: str | None = None
    sdk_not_ready_blockers: tuple[dict[str, str], ...] = ()
    backend: str = "native_linux_sdk"
    sdk_family: str = "unknown"
    supports_login: bool = False
    supports_login_ex: bool = False
    supports_device_enumeration: bool = False
    supports_management_server_validation: str = "experimental"
    symbol_probe: dict[str, Any] = field(default_factory=dict)
    symbol_inventory: tuple[dict[str, Any], ...] = ()
    symbol_presence_checks: tuple[dict[str, Any], ...] = ()
    windows_symbol_parity: tuple[dict[str, Any], ...] = ()
    capability_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_context(self) -> SDKContext:
        init_symbol = self.symbols.get("init")
        logout_symbol = self.symbols.get("logout")
        alarm_subscribe = self.symbols.get("alarm_subscription")
        alarm_unsubscribe = self.symbols.get("alarm_unsubscribe")
        supports_init = bool(init_symbol and init_symbol.status == "confirmed" and init_symbol.signature_ready)
        supports_logout = bool(logout_symbol and logout_symbol.status == "confirmed" and logout_symbol.signature_ready)
        supports_alarm_subscription = bool(
            alarm_subscribe
            and alarm_unsubscribe
            and alarm_subscribe.status == "confirmed"
            and alarm_unsubscribe.status == "confirmed"
            and alarm_subscribe.signature_ready
            and alarm_unsubscribe.signature_ready
        )
        capabilities = CapabilityMap(
            supports_init=supports_init,
            supports_login=self.supports_login,
            supports_login_ex=self.supports_login_ex,
            supports_logout=supports_logout,
            supports_device_enumeration=self.supports_device_enumeration,
            supports_alarm_subscription=supports_alarm_subscription,
            supports_management_server_login=(
                "provisional" if self.supports_login or self.supports_login_ex else False
            ),
        )
        notes = [
            "Login-related behavior remains provisional until validated against the correct Linux management SDK.",
        ]
        if self.load_error:
            notes.append(f"SDK load error: {self.load_error}")
        if self.architecture_note:
            notes.append(self.architecture_note)
        if self.login_readiness_reason:
            notes.append(f"Login readiness: {self.login_readiness_reason}")

        sdk_name = Path(self.sdk_path).name or None
        return SDKContext(
            platform=PlatformIdentity(
                os_family=_normalized_os_family(),
                arch=self.runtime_machine,
                runtime_kind="native",
            ),
            sdk=SDKIdentity(
                vendor="tvt",
                sdk_name=sdk_name,
                sdk_family=_normalized_sdk_family(self.sdk_family),
                sdk_version=None,
            ),
            product_scope={"management_server"},
            capabilities=capabilities,
            notes=notes,
        )

    def as_dict(self) -> dict[str, Any]:
        context = self.to_context().as_dict()
        device_enum = self.symbols.get("device_enumeration")
        status_query = self.symbols.get("status_query")
        alarm_subscribe = self.symbols.get("alarm_subscription")
        alarm_unsubscribe = self.symbols.get("alarm_unsubscribe")
        login_symbol = self.symbols.get("login")
        return {
            "backend": self.backend,
            "context": context,
            "platform": context["platform"],
            "sdk": context["sdk"],
            "product_scope": context["product_scope"],
            "capabilities": context["capabilities"],
            "notes": context["notes"],
            "symbol_probe": self.symbol_probe,
            "symbol_inventory": list(self.symbol_inventory),
            "symbol_presence_checks": list(self.symbol_presence_checks),
            "windows_symbol_parity": list(self.windows_symbol_parity),
            "capability_evidence": {
                **self.capability_evidence,
                "supports_management_server_validation": {
                    "source": "backend",
                    "confirmed": False,
                    "note": "Provisional until validated against the correct Linux management SDK.",
                },
            },
            "stable_contract": stable_contract_definition(),
            "provisional": {
                "management_login_semantics": True,
                "device_id_requirement": True,
                "connect_type_requirement": True,
                "nat_requirement": True,
            },
            "confirmed": {
                "symbol_presence": True,
                "backend_mode_identity": True,
                "platform_runtime_identity": True,
            },
            "sdk_family": self.sdk_family,
            "supports_login": self.supports_login,
            "supports_login_ex": self.supports_login_ex,
            "supports_device_enumeration": self.supports_device_enumeration,
            "supports_management_server_validation": self.supports_management_server_validation,
            "sdk_path": self.sdk_path,
            "load_success": self.load_success,
            "load_error": self.load_error,
            "symbol_scan_success": self.symbol_scan_success,
            "symbol_scan_error": self.symbol_scan_error,
            "discovered_symbol_count": self.discovered_symbol_count,
            "symbols": {name: item.as_dict() for name, item in self.symbols.items()},
            "login_path_ready": self.login_path_ready,
            "login_readiness_reason": self.login_readiness_reason,
            "evidence_record_count": self.evidence_record_count,
            "login_mode": self.login_mode,
            "login_connect_type": self.login_connect_type,
            "runtime_machine": self.runtime_machine,
            "sdk_machine": self.sdk_machine,
            "architecture_compatible": self.architecture_compatible,
            "architecture_note": self.architecture_note,
            "sdk_not_ready_blockers": [dict(item) for item in self.sdk_not_ready_blockers],
            "login_backend": {
                "mode": self.login_mode,
                "connect_type": self.login_connect_type,
                "connect_type_code": _sdk_connect_type_code(self.login_connect_type),
                "symbol_name": login_symbol.symbol_name if login_symbol else None,
                "status": login_symbol.status if login_symbol else "missing",
                "signature_ready": login_symbol.signature_ready if login_symbol else False,
            },
            "list_devices_backend": {
                "symbol_name": device_enum.symbol_name if device_enum else None,
                "confidence": device_enum.confidence if device_enum else "low",
                "semantics": device_enum.semantics if device_enum else "unknown",
                "status": device_enum.status if device_enum else "missing",
            },
            "get_device_statuses_backend": {
                "symbol_name": status_query.symbol_name if status_query else None,
                "confidence": status_query.confidence if status_query else "low",
                "semantics": status_query.semantics if status_query else "unknown",
                "status": status_query.status if status_query else "missing",
                "aligns_directly_with_list_devices": False,
                "alignment_notes": (
                    "status is channel-scoped connectivity and may not map 1:1 to list_devices device identifiers"
                ),
            },
            "subscribe_alarms_backend": {
                "setup_symbol": alarm_subscribe.symbol_name if alarm_subscribe else None,
                "close_symbol": alarm_unsubscribe.symbol_name if alarm_unsubscribe else None,
                "confidence": alarm_subscribe.confidence if alarm_subscribe else "low",
                "semantics": alarm_subscribe.semantics if alarm_subscribe else "unknown",
                "setup_status": alarm_subscribe.status if alarm_subscribe else "missing",
                "close_status": alarm_unsubscribe.status if alarm_unsubscribe else "missing",
                "payload_semantics": "opaque",
                "lifecycle_notes": (
                    "session-bound alarm-channel registration only; callback payload layout/threading semantics are unconfirmed"
                ),
            },
            "capability": {
                "backend": self.backend,
                "sdk_family": self.sdk_family,
                "supports_login": self.supports_login,
                "supports_login_ex": self.supports_login_ex,
                "supports_device_enumeration": self.supports_device_enumeration,
                "supports_management_server_validation": self.supports_management_server_validation,
            },
        }


def _record(
    *,
    symbol_name: str | None,
    suspected_capability: str,
    source_type: EvidenceSourceType,
    confidence: EvidenceConfidence,
    notes: str,
    source_path: str | None = None,
    observed_value: str | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        symbol_name=symbol_name,
        suspected_capability=suspected_capability,
        source_type=source_type,
        confidence=confidence,
        notes=notes,
        source_path=source_path,
        observed_value=observed_value,
    )


def _source_platform(source_type: EvidenceSourceType) -> str:
    if source_type.startswith("linux_"):
        return "linux"
    if source_type.startswith("nvms_windows_"):
        return "windows"
    if source_type.startswith("nvms_macos_"):
        return "macos"
    if source_type.startswith("runtime_"):
        return "runtime"
    return "unknown"


def _summarize_evidence_sources(records: tuple[EvidenceRecord, ...]) -> dict[str, Any]:
    source_types = sorted({record.source_type for record in records})
    platforms = sorted({_source_platform(record.source_type) for record in records})
    confidence_counts: dict[str, int] = {}
    for record in records:
        confidence_counts[record.confidence] = confidence_counts.get(record.confidence, 0) + 1
    return {
        "source_types": source_types,
        "platforms": platforms,
        "confidence_counts": confidence_counts,
        "record_count": len(records),
    }


SYMBOL_EVIDENCE: tuple[SymbolEvidence, ...] = (
    SymbolEvidence(
        purpose="init",
        confirmed_names=("NET_SDK_Init",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_Init",
        signature_ready=True,
        notes="Export present in staged libdvrnetsdk.so and validated in pytvt.device_sdk.",
        confidence="high",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_Init",
                suspected_capability="init",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed as exported symbol in staged Linux libdvrnetsdk.so via nm.",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="login",
        confirmed_names=("NET_SDK_Login",),
        candidate_names=("NET_SDK_LoginEx",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_Login",
        signature_ready=True,
        notes="Exact NET_SDK_Login prototype already exists in pytvt.device_sdk bindings.",
        confidence="medium",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_Login",
                suspected_capability="login",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed as exported symbol in staged Linux libdvrnetsdk.so via nm.",
            ),
            _record(
                symbol_name="NET_SDK_LoginEx",
                suspected_capability="login",
                source_type="linux_sdk_probe",
                confidence="medium",
                notes="Observed as alternate exported login symbol in staged Linux libdvrnetsdk.so.",
            ),
            _record(
                symbol_name="NET_CLIENT_LoginServerUnit",
                suspected_capability="login",
                source_type="nvms_macos_export",
                confidence="medium",
                notes="Observed in installed MonitorClient macOS libNetClientSDK.dylib; suggests management login flow exists on client stack.",
                source_path="evidence/nvms/macos/libNetClientSDK.dylib",
            ),
            _record(
                symbol_name=None,
                suspected_capability="login",
                source_type="nvms_windows_log",
                confidence="high",
                notes="Observed repeated login success/failure events for type=33 at port 6006 in MonitorClient.log.",
                source_path="evidence/nvms/windows/MonitorClient.log",
                observed_value="port=6006,type=33",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="logout",
        confirmed_names=("NET_SDK_Logout",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_Logout",
        signature_ready=True,
        notes="Used by existing pytvt.device_sdk session code.",
        confidence="high",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_Logout",
                suspected_capability="logout",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed as exported symbol in staged Linux libdvrnetsdk.so via nm.",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="set_nat2_addr",
        confirmed_names=("NET_SDK_SetNat2Addr",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_SetNat2Addr",
        signature_ready=True,
        notes=("NAT2 pre-login endpoint configuration used by LoginEx NAT20 mode in the vendor demo path."),
        confidence="medium",
        semantics="transport_setup",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_SetNat2Addr",
                suspected_capability="set_nat2_addr",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed Linux export for NAT2 endpoint setup before LoginEx NAT20.",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="server_info",
        confirmed_names=("NET_SDK_GetDeviceInfo",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_GetDeviceInfo",
        signature_ready=True,
        notes=("Semantics assumed minimal: device-level info, not yet confirmed as management-server-specific."),
        confidence="high",
        semantics="unknown",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_GetDeviceInfo",
                suspected_capability="server_info",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed Linux export with validated ctypes signature already present in pytvt.device_sdk.",
            ),
            _record(
                symbol_name="GetDeviceInfo",
                suspected_capability="server_info",
                source_type="nvms_windows_strings",
                confidence="high",
                notes="Observed in Windows NetClientSDK.dll strings next to management-style operations.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
            _record(
                symbol_name="GetDeviceDetail",
                suspected_capability="server_info",
                source_type="nvms_windows_strings",
                confidence="high",
                notes="Observed in Windows NetClientSDK.dll strings and may refine metadata retrieval scope.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="device_enumeration",
        confirmed_names=("NET_SDK_GetDeviceIPCInfo",),
        candidate_names=("NET_SDK_DiscoverDevice",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_GetDeviceIPCInfo",
        signature_ready=True,
        notes=(
            "Selected NET_SDK_GetDeviceIPCInfo as the only session-bound Linux SDK path "
            "with a validated ctypes signature for inventory-like records. This is treated "
            "as configured IPC inventory (often one row per channel), not generic LAN "
            "discovery. NET_SDK_DiscoverDevice is explicitly rejected for list_devices "
            "because it returns discovery results. NVMS-only names (queryChannelList, "
            "queryAcsDeviceList, NET_CLIENT_RequestAllChannelsInfo) remain evidence but are "
            "not callable via the validated Linux NET_SDK_* binding path."
        ),
        confidence="medium",
        semantics="configured_inventory",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_GetDeviceIPCInfo",
                suspected_capability="device_enumeration",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed Linux export with validated ctypes signature in pytvt.device_sdk.bindings and existing client usage for IPC info retrieval.",
            ),
            _record(
                symbol_name="NET_SDK_DiscoverDevice",
                suspected_capability="device_enumeration",
                source_type="linux_sdk_probe",
                confidence="low",
                notes="Rejected for list_devices because this API is discovery-oriented and can include unmanaged LAN noise.",
            ),
            _record(
                symbol_name="queryChannelList",
                suspected_capability="device_enumeration",
                source_type="nvms_windows_strings",
                confidence="low",
                notes="Observed in NVMS strings only; not part of validated Linux NET_SDK_* callable exports.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
            _record(
                symbol_name="queryAcsDeviceList",
                suspected_capability="device_enumeration",
                source_type="nvms_windows_strings",
                confidence="low",
                notes="Observed in NVMS strings only and appears ACS-scoped rather than generic device inventory.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
            _record(
                symbol_name="NET_CLIENT_RequestAllChannelsInfo",
                suspected_capability="device_enumeration",
                source_type="nvms_macos_export",
                confidence="low",
                notes="Observed in NVMS macOS export set only; no validated Linux NET_SDK binding path currently available.",
                source_path="evidence/nvms/macos/libNetClientSDK.dylib",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="status_query",
        confirmed_names=("NET_SDK_GetDeviceCHStatus",),
        candidate_names=("NET_SDK_GetAlarmStatus", "NET_SDK_GetAlarmStatusEx"),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_GetDeviceCHStatus",
        signature_ready=True,
        notes=(
            "Selected NET_SDK_GetDeviceCHStatus as a narrow channel-connectivity status path. "
            "This phase defines get_device_statuses as channel-level online/offline connectivity, "
            "not alarm/network/disk health. NET_SDK_GetAlarmStatus and NET_SDK_GetAlarmStatusEx are "
            "explicitly rejected for this method because they are alarm-domain APIs."
        ),
        confidence="medium",
        semantics="channel_status",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_GetDeviceCHStatus",
                suspected_capability="status_query",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed Linux export with validated ctypes signature and existing netsdk client usage for per-channel status.",
            ),
            _record(
                symbol_name="NET_SDK_GetAlarmStatus",
                suspected_capability="status_query",
                source_type="linux_sdk_probe",
                confidence="low",
                notes="Rejected for get_device_statuses in this phase because it represents alarm state, not channel connectivity.",
            ),
            _record(
                symbol_name="NET_SDK_GetAlarmStatusEx",
                suspected_capability="status_query",
                source_type="linux_sdk_probe",
                confidence="low",
                notes="Rejected for get_device_statuses in this phase because it represents extended alarm state, not channel connectivity.",
            ),
            _record(
                symbol_name="queryChlStatus",
                suspected_capability="status_query",
                source_type="nvms_windows_strings",
                confidence="medium",
                notes="Observed explicit channel status string in Windows NetClientSDK.dll; aligns with channel-scoped semantics.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
            _record(
                symbol_name="queryNetStatus",
                suspected_capability="status_query",
                source_type="nvms_windows_strings",
                confidence="low",
                notes="Observed network status string, but rejected for this phase because it is network-domain status.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
            _record(
                symbol_name="queryDiskStatus",
                suspected_capability="status_query",
                source_type="nvms_windows_strings",
                confidence="low",
                notes="Observed disk status query string, but rejected for this phase because it is storage-domain status.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="alarm_subscription",
        confirmed_names=("NET_SDK_SetupAlarmChan",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_SetupAlarmChan",
        signature_ready=True,
        notes=(
            "Phase 7 maps subscribe_alarms to alarm-channel registration only via "
            "NET_SDK_SetupAlarmChan. This does not claim callback payload semantics "
            "or event-stream structure."
        ),
        confidence="medium",
        semantics="alarm_channel_registration",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_SetupAlarmChan",
                suspected_capability="alarm_subscription",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed Linux export with validated ctypes signature already present in pytvt.device_sdk; returns alarm-channel handle.",
            ),
            _record(
                symbol_name="NET_SDK_GetAlarmStatus",
                suspected_capability="alarm_subscription",
                source_type="linux_sdk_probe",
                confidence="low",
                notes="Rejected for subscribe_alarms because it is status polling, not subscription registration.",
            ),
            _record(
                symbol_name="NET_SDK_GetAlarmStatusEx",
                suspected_capability="alarm_subscription",
                source_type="linux_sdk_probe",
                confidence="low",
                notes="Rejected for subscribe_alarms because it is extended status polling, not subscription registration.",
            ),
            _record(
                symbol_name="NET_CLIENT_GetDevAlarmChannel",
                suspected_capability="alarm_subscription",
                source_type="nvms_macos_export",
                confidence="medium",
                notes="Observed macOS export indicating device alarm channel query path aligned with channel registration semantics.",
                source_path="evidence/nvms/macos/libNetClientSDK.dylib",
            ),
            _record(
                symbol_name="alarm",
                suspected_capability="alarm_subscription",
                source_type="nvms_windows_strings",
                confidence="low",
                notes="General alarm strings observed in NVMS evidence do not confirm callback payload layout.",
                source_path="evidence/nvms/windows/NetClientSDK.dll",
            ),
        ),
    ),
    SymbolEvidence(
        purpose="alarm_unsubscribe",
        confirmed_names=("NET_SDK_CloseAlarmChan",),
        signature_source="pytvt.device_sdk.bindings.NET_SDK_CloseAlarmChan",
        signature_ready=True,
        notes=(
            "Teardown path for alarm-channel registration. Required for safe lifecycle "
            "management because callback threading and global registration semantics remain unconfirmed."
        ),
        confidence="medium",
        semantics="alarm_channel_registration",
        evidence_records=(
            _record(
                symbol_name="NET_SDK_CloseAlarmChan",
                suspected_capability="alarm_unsubscribe",
                source_type="linux_sdk_probe",
                confidence="high",
                notes="Observed Linux export paired with SetupAlarmChan for alarm-channel teardown.",
            ),
        ),
    ),
)


def export_evidence_records() -> list[dict[str, Any]]:
    """Return all configured interoperability evidence records as plain dicts."""
    records: list[dict[str, Any]] = []
    for symbol in SYMBOL_EVIDENCE:
        for record in symbol.evidence_records:
            records.append(record.as_dict())
    return records


def export_evidence_schema() -> dict[str, Any]:
    """Return a non-proprietary schema/template for evidence capture."""
    return {
        "fields": {
            "symbol_name": "str | null",
            "suspected_capability": "str",
            "source_type": list(EvidenceSourceType.__args__),
            "confidence": list(EvidenceConfidence.__args__),
            "notes": "str",
            "source_path": "str | null",
            "observed_value": "str | null",
        },
        "example": {
            "symbol_name": "NET_SDK_Login",
            "suspected_capability": "login",
            "source_type": "linux_sdk_probe",
            "confidence": "high",
            "notes": "Observed as exported symbol in staged Linux SDK via nm.",
            "source_path": None,
            "observed_value": None,
        },
    }


def _build_function_bindings() -> dict[str, BoundSdkFunction]:
    return {
        "init": BoundSdkFunction(
            purpose="init",
            symbol_name="NET_SDK_Init",
            argtypes=(),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_Init",
        ),
        "login": BoundSdkFunction(
            purpose="login",
            symbol_name="NET_SDK_Login",
            argtypes=(
                ct.c_char_p,
                ct.c_ushort,
                ct.c_char_p,
                ct.c_char_p,
                ct.POINTER(netsdk_types.NET_SDK_DEVICEINFO),
            ),
            restype=ct.c_long,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_Login",
        ),
        "login_ex": BoundSdkFunction(
            purpose="login_ex",
            symbol_name="NET_SDK_LoginEx",
            argtypes=(
                ct.c_char_p,
                ct.c_ushort,
                ct.c_char_p,
                ct.c_char_p,
                ct.POINTER(netsdk_types.NET_SDK_DEVICEINFO),
                ct.c_int,
                ct.c_char_p,
            ),
            restype=ct.c_long,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_LoginEx",
        ),
        "set_nat2_addr": BoundSdkFunction(
            purpose="set_nat2_addr",
            symbol_name="NET_SDK_SetNat2Addr",
            argtypes=(ct.c_char_p, ct.c_ushort),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_SetNat2Addr",
        ),
        "logout": BoundSdkFunction(
            purpose="logout",
            symbol_name="NET_SDK_Logout",
            argtypes=(ct.c_long,),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_Logout",
        ),
        "server_info": BoundSdkFunction(
            purpose="server_info",
            symbol_name="NET_SDK_GetDeviceInfo",
            argtypes=(ct.c_long, ct.POINTER(netsdk_types.NET_SDK_DEVICEINFO)),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_GetDeviceInfo",
        ),
        "device_enumeration": BoundSdkFunction(
            purpose="device_enumeration",
            symbol_name="NET_SDK_GetDeviceIPCInfo",
            argtypes=(
                ct.c_long,
                ct.POINTER(netsdk_types.NET_SDK_IPC_DEVICE_INFO),
                ct.c_long,
                ct.POINTER(ct.c_long),
            ),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_GetDeviceIPCInfo",
        ),
        "status_query": BoundSdkFunction(
            purpose="status_query",
            symbol_name="NET_SDK_GetDeviceCHStatus",
            argtypes=(
                ct.c_long,
                ct.POINTER(netsdk_types.NET_SDK_CH_DEVICE_STATUS),
                ct.c_long,
                ct.POINTER(ct.c_long),
            ),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_GetDeviceCHStatus",
        ),
        "alarm_subscription": BoundSdkFunction(
            purpose="alarm_subscription",
            symbol_name="NET_SDK_SetupAlarmChan",
            argtypes=(ct.c_long,),
            restype=ct.c_long,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_SetupAlarmChan",
        ),
        "alarm_unsubscribe": BoundSdkFunction(
            purpose="alarm_unsubscribe",
            symbol_name="NET_SDK_CloseAlarmChan",
            argtypes=(ct.c_long,),
            restype=ct.c_bool,
            signature_source="pytvt.device_sdk.bindings.NET_SDK_CloseAlarmChan",
        ),
    }


class _SdkAlarmSubscriptionHandle(AlarmSubscription):
    """Alarm registration handle with explicit close lifecycle."""

    def __init__(
        self,
        *,
        handle: str,
        transport: str,
        raw_data: dict[str, Any],
        closer: Callable[[], None],
    ) -> None:
        super().__init__(handle=handle, transport=transport, raw_data=raw_data)
        self._closer = closer
        self._active = True

    @property
    def is_active(self) -> bool:
        return self._active

    def close(self) -> None:
        if not self._active:
            return
        self._closer()
        self._active = False


def _decode_char_array(value: ct.Array) -> str:
    raw = bytes(value)
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def _ctypes_to_python(value: Any) -> Any:
    """Convert ctypes values into Python primitives without assuming schemas."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    if isinstance(value, ct.Array):
        if getattr(value, "_type_", None) is ct.c_char:
            return _decode_char_array(value)
        return [_ctypes_to_python(item) for item in value]
    if isinstance(value, ct.Structure):
        return _structure_to_dict(value)
    if isinstance(value, ct._SimpleCData):  # type: ignore[attr-defined]
        return value.value
    return value


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _to_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _coerce_status(value: Any) -> str:
    parsed = _to_int(value)
    if parsed is None:
        return ""
    return "online" if parsed == 1 else "offline"


def _structure_to_dict(struct: ct.Structure) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name, _field_type in getattr(struct, "_fields_", []):
        try:
            field_value = getattr(struct, field_name)
        except Exception:
            payload[field_name] = "<unreadable>"
            continue
        payload[field_name] = _ctypes_to_python(field_value)
    return payload


def _best_device_identifier(raw: dict[str, Any]) -> str:
    device_id = _to_int(raw.get("deviceID"))
    if device_id is not None and device_id > 0:
        return str(device_id)

    text_id = _to_text(raw.get("szID"))
    if text_id:
        return text_id

    guid = raw.get("guid")
    if isinstance(guid, list):
        non_zero = [item for item in guid if isinstance(item, int) and item != 0]
        if non_zero:
            try:
                return "guid:" + "".join(f"{int(item):02x}" for item in guid)
            except (TypeError, ValueError):
                pass

    ip = _to_text(raw.get("szServer"))
    if ip:
        return f"ip:{ip}"
    return ""


def _best_device_sn(raw: dict[str, Any], *, fallback: str = "") -> str:
    for key in ("szID", "szSN", "deviceSN", "serialNumber", "serial", "uid"):
        value = _to_text(raw.get(key))
        if value:
            return value

    channel_records = raw.get("channel_records")
    if isinstance(channel_records, list):
        for item in channel_records:
            if not isinstance(item, dict):
                continue
            for key in ("szID", "szSN", "deviceSN", "serialNumber", "serial", "uid"):
                value = _to_text(item.get(key))
                if value:
                    return value

    return fallback


def _best_status_identifier(raw: dict[str, Any]) -> str:
    channel = _to_int(raw.get("channel"))
    if channel is not None and channel > 0:
        return f"channel:{channel}"
    name = _to_text(raw.get("name"))
    if name:
        return f"name:{name}"
    return ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _SDKLoadError(Exception):
    """Internal: library failed to load — not part of public API."""


def _load_library(sdk_path: str) -> ctypes.CDLL:
    """Load the TVT SDK shared library from *sdk_path*.

    Raises _SDKLoadError on any failure so callers can convert it to the
    appropriate public exception type without exposing ctypes internals.
    """
    path = Path(sdk_path)
    if not path.exists():
        raise _SDKLoadError(f"SDK path does not exist: {sdk_path!r}")
    if not path.is_file():
        raise _SDKLoadError(f"SDK path is not a file: {sdk_path!r}")
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        raise _SDKLoadError(f"ctypes could not load SDK: {exc}") from exc
    logger.debug("TVT SDK library loaded from %s", sdk_path)
    return lib


def _enumerate_exported_symbols(sdk_path: str) -> tuple[set[str] | None, str | None]:
    """Return exported symbol names from *sdk_path* when ``nm`` is available.

    This is diagnostics-only; failure to enumerate exports does not prevent
    library loading, but it does prevent evidence-based capability promotion.
    """
    nm_path = shutil.which("nm")
    if not nm_path:
        return None, "nm not available on PATH"

    try:
        result = subprocess.run(
            [nm_path, "-D", "--defined-only", sdk_path],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"nm execution failed: {exc}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or f"nm exited with status {result.returncode}"
        return None, stderr

    symbols: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts:
            symbols.add(parts[-1])
    return symbols, None


def _probe_known_symbols_with_ctypes(
    sdk_path: str,
    *,
    login_mode: SdkLoginMode = "login",
) -> tuple[set[str] | None, str | None]:
    """Best-effort fallback when nm is unavailable.

    Probes only known symbol names from the evidence table using ctypes/dlsym.
    """
    try:
        lib = _load_library(sdk_path)
    except _SDKLoadError as exc:
        return None, f"ctypes probe load failed: {exc}"

    candidate_names: set[str] = set()
    for evidence in SYMBOL_EVIDENCE:
        candidate_names.update(evidence.confirmed_names)
        candidate_names.update(evidence.candidate_names)
    if login_mode == "login_ex":
        candidate_names.add("NET_SDK_LoginEx")
        candidate_names.add("NET_SDK_Login")

    discovered: set[str] = set()
    for name in candidate_names:
        if getattr(lib, name, None) is not None:
            discovered.add(name)

    return discovered, "nm unavailable; used ctypes symbol probe fallback"


def _detect_sdk_machine(sdk_path: str) -> str | None:
    """Best-effort architecture detection for ELF shared libraries."""
    try:
        with open(sdk_path, "rb") as fh:
            header = fh.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None

    # e_machine field at offset 18 (2 bytes, little-endian for common SDK builds).
    e_machine = int.from_bytes(header[18:20], byteorder="little", signed=False)
    mapping = {
        0x03: "x86",
        0x3E: "x86_64",
        0x28: "arm",
        0xB7: "aarch64",
    }
    return mapping.get(e_machine, f"elf_machine_{e_machine}")


def _resolve_symbol_registry(
    exported_symbols: set[str] | None,
    *,
    login_mode: SdkLoginMode = "login",
) -> dict[str, ResolvedSymbol]:
    """Resolve the configured evidence table against discovered exports."""
    registry: dict[str, ResolvedSymbol] = {}
    exported_symbols = exported_symbols or set()

    for evidence in SYMBOL_EVIDENCE:
        confirmed_names = evidence.confirmed_names
        candidate_names = evidence.candidate_names
        signature_source = evidence.signature_source
        signature_ready = evidence.signature_ready

        if evidence.purpose == "login" and login_mode == "login_ex":
            confirmed_names = ("NET_SDK_LoginEx",)
            candidate_names = ("NET_SDK_Login",)
            signature_source = "pytvt.device_sdk.bindings.NET_SDK_LoginEx"
            signature_ready = True

        confirmed_match = next((name for name in confirmed_names if name in exported_symbols), None)
        if confirmed_match is not None:
            registry[evidence.purpose] = ResolvedSymbol(
                purpose=evidence.purpose,
                status="confirmed",
                symbol_name=confirmed_match,
                signature_ready=signature_ready,
                signature_source=signature_source,
                notes=evidence.notes,
                confidence=evidence.confidence,
                semantics=evidence.semantics,
                live_validation_status=evidence.live_validation_status,
                evidence_records=evidence.evidence_records,
            )
            continue

        candidate_match = next((name for name in candidate_names if name in exported_symbols), None)
        if candidate_match is not None:
            registry[evidence.purpose] = ResolvedSymbol(
                purpose=evidence.purpose,
                status="candidate",
                symbol_name=candidate_match,
                signature_ready=False,
                signature_source=None,
                notes=evidence.notes,
                confidence=evidence.confidence,
                semantics=evidence.semantics,
                live_validation_status=evidence.live_validation_status,
                evidence_records=evidence.evidence_records,
            )
            continue

        registry[evidence.purpose] = ResolvedSymbol(
            purpose=evidence.purpose,
            status="missing",
            symbol_name=None,
            signature_ready=False,
            signature_source=None,
            notes=evidence.notes,
            confidence=evidence.confidence,
            semantics=evidence.semantics,
            live_validation_status=evidence.live_validation_status,
            evidence_records=evidence.evidence_records,
        )
    return registry


def _evaluate_login_readiness(symbols: dict[str, ResolvedSymbol]) -> tuple[bool, str]:
    """Return whether the real login path can be called safely."""
    required = ("init", "login", "logout")
    missing = [name for name in required if symbols[name].status == "missing"]
    if missing:
        return False, f"Missing required SDK symbols: {', '.join(missing)}"

    unresolved = [name for name in required if not symbols[name].signature_ready]
    if unresolved:
        return False, f"Required SDK signatures are unresolved: {', '.join(unresolved)}"

    ambiguous = [name for name in required if symbols[name].status != "confirmed"]
    if ambiguous:
        return False, f"Required SDK symbols are not confirmed: {', '.join(ambiguous)}"

    return True, "init/login/logout symbols confirmed with validated ctypes signatures"


def _sdk_connect_type_code(connect_type: SdkConnectType) -> int:
    mapping = {
        "tcp": 0,
        "nat": 1,
        "nat20": 2,
    }
    return mapping[connect_type]


def _build_sdk_not_ready_blockers(
    *,
    sdk_path: str,
    load_success: bool,
    load_error: str | None,
    symbol_scan_success: bool,
    symbol_scan_error: str | None,
    symbols: dict[str, ResolvedSymbol],
    login_path_ready: bool,
    login_reason: str,
    architecture_compatible: bool | None,
    architecture_note: str | None,
) -> tuple[dict[str, str], ...]:
    blockers: list[dict[str, str]] = []

    path_obj = Path(sdk_path)
    if not path_obj.exists():
        blockers.append(
            {
                "code": "sdk_missing_file",
                "detail": f"SDK path does not exist: {sdk_path}",
            }
        )
    elif not path_obj.is_file():
        blockers.append(
            {
                "code": "sdk_missing_file",
                "detail": f"SDK path is not a file: {sdk_path}",
            }
        )

    if not load_success:
        blockers.append(
            {
                "code": "sdk_load_failure",
                "detail": load_error or "SDK failed to load",
            }
        )

    if architecture_compatible is False:
        blockers.append(
            {
                "code": "sdk_arch_mismatch",
                "detail": architecture_note or "SDK architecture does not match runtime architecture",
            }
        )

    if load_success and not symbol_scan_success:
        blockers.append(
            {
                "code": "sdk_symbol_scan_unavailable",
                "detail": symbol_scan_error or "SDK loaded but exported symbol scan failed",
            }
        )

    required = ("init", "login", "logout")
    missing = [name for name in required if symbols.get(name) and symbols[name].status == "missing"]
    if missing:
        blockers.append(
            {
                "code": "sdk_missing_symbols",
                "detail": ", ".join(missing),
            }
        )

    unresolved = [name for name in required if symbols.get(name) and not symbols[name].signature_ready]
    if unresolved:
        blockers.append(
            {
                "code": "sdk_unresolved_signatures",
                "detail": ", ".join(unresolved),
            }
        )

    ambiguous = [
        name for name in required if symbols.get(name) and symbols[name].status not in {"confirmed", "missing"}
    ]
    if ambiguous:
        blockers.append(
            {
                "code": "sdk_unconfirmed_symbols",
                "detail": ", ".join(ambiguous),
            }
        )

    if not login_path_ready:
        blockers.append(
            {
                "code": "sdk_login_path_not_ready",
                "detail": login_reason,
            }
        )

    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in blockers:
        marker = (item["code"], item["detail"])
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return tuple(unique)


def inspect_sdk_library(
    sdk_path: str,
    *,
    login_mode: SdkLoginMode = "login",
    login_connect_type: SdkConnectType = "tcp",
) -> SdkDiagnostics:
    """Inspect a staged SDK library and return structured diagnostics."""
    load_success = False
    load_error: str | None = None
    symbol_scan_success = False
    symbol_scan_error: str | None = None
    discovered_symbol_count: int | None = None
    runtime_machine = platform.machine() or None
    sdk_machine = _detect_sdk_machine(sdk_path)
    architecture_compatible: bool | None = None
    architecture_note: str | None = None

    if runtime_machine and sdk_machine:
        architecture_compatible = runtime_machine == sdk_machine
        if not architecture_compatible:
            architecture_note = (
                f"SDK binary machine '{sdk_machine}' does not match runtime machine '{runtime_machine}'."
            )

    try:
        _load_library(sdk_path)
        load_success = True
    except _SDKLoadError as exc:
        load_error = str(exc)

    exported_symbols: set[str] | None = None
    symbol_probe: dict[str, Any] = {}
    symbol_inventory: tuple[dict[str, Any], ...] = ()
    symbol_presence_checks: tuple[dict[str, Any], ...] = ()
    windows_symbol_parity: tuple[dict[str, Any], ...] = ()
    capability_evidence: dict[str, dict[str, Any]] = {}
    exported_symbols, symbol_probe = list_exported_symbols(sdk_path)
    symbol_scan_success = True
    symbol_scan_error = symbol_probe.get("error")
    discovered_symbol_count = len(exported_symbols)
    symbol_inventory = tuple(build_symbol_inventory(exported_symbols))
    symbol_presence_checks = tuple(
        build_symbol_presence_checks(
            exported_symbols,
            [
                "NET_SDK_Init",
                "NET_SDK_Login",
                "NET_SDK_LoginEx",
                "NET_SDK_Logout",
                "NET_SDK_Cleanup",
                "NET_SDK_GetDeviceIPCInfo",
            ],
        )
    )
    windows_symbol_parity = tuple(build_windows_parity_report(exported_symbols))
    capability_evidence = build_symbol_capability_evidence(exported_symbols)

    resolved = _resolve_symbol_registry(exported_symbols, login_mode=login_mode)
    login_path_ready, login_reason = _evaluate_login_readiness(resolved)
    if not symbol_scan_success:
        login_path_ready = False
        if load_success:
            login_reason = f"SDK loaded but symbol scan unavailable: {symbol_scan_error}"

    observed_symbols = exported_symbols or set()
    has_netsdk_symbols = any(name.startswith("NET_SDK_") for name in observed_symbols)
    has_netclient_symbols = any(name.startswith("NET_CLIENT_") for name in observed_symbols)
    if has_netsdk_symbols:
        sdk_family = "linux_native"
    elif has_netclient_symbols:
        sdk_family = "management_sdk"
    else:
        sdk_family = "unknown"

    init_symbol = resolved.get("init")
    logout_symbol = resolved.get("logout")
    lifecycle_ready = bool(
        init_symbol
        and logout_symbol
        and init_symbol.status == "confirmed"
        and logout_symbol.status == "confirmed"
        and init_symbol.signature_ready
        and logout_symbol.signature_ready
    )
    supports_login = lifecycle_ready and "NET_SDK_Login" in observed_symbols
    supports_login_ex = lifecycle_ready and "NET_SDK_LoginEx" in observed_symbols
    device_enum_symbol = resolved.get("device_enumeration")
    supports_device_enumeration = bool(
        device_enum_symbol and device_enum_symbol.status == "confirmed" and device_enum_symbol.signature_ready
    )

    blockers = _build_sdk_not_ready_blockers(
        sdk_path=sdk_path,
        load_success=load_success,
        load_error=load_error,
        symbol_scan_success=symbol_scan_success,
        symbol_scan_error=symbol_scan_error,
        symbols=resolved,
        login_path_ready=login_path_ready,
        login_reason=login_reason,
        architecture_compatible=architecture_compatible,
        architecture_note=architecture_note,
    )

    return SdkDiagnostics(
        sdk_path=sdk_path,
        load_success=load_success,
        load_error=load_error,
        symbol_scan_success=symbol_scan_success,
        symbol_scan_error=symbol_scan_error,
        discovered_symbol_count=discovered_symbol_count,
        symbols=resolved,
        login_path_ready=login_path_ready,
        login_readiness_reason=login_reason,
        evidence_record_count=sum(len(item.evidence_records) for item in resolved.values()),
        login_mode=login_mode,
        login_connect_type=login_connect_type,
        runtime_machine=runtime_machine,
        sdk_machine=sdk_machine,
        architecture_compatible=architecture_compatible,
        architecture_note=architecture_note,
        sdk_not_ready_blockers=blockers,
        backend="native_linux_sdk",
        sdk_family=sdk_family,
        supports_login=supports_login,
        supports_login_ex=supports_login_ex,
        supports_device_enumeration=supports_device_enumeration,
        supports_management_server_validation="experimental",
        symbol_probe=symbol_probe,
        symbol_inventory=symbol_inventory,
        symbol_presence_checks=symbol_presence_checks,
        windows_symbol_parity=windows_symbol_parity,
        capability_evidence=capability_evidence,
    )


# ---------------------------------------------------------------------------
# SDKClient  — thin wrapper around an active SDK session
# ---------------------------------------------------------------------------


class SDKClient:
    """Manages a single authenticated session against the TVT management SDK.

    This class holds the session handle returned by the SDK login call, and
    provides typed wrappers for each management operation.  All wrappers are
    stubs pending SDK symbol validation.
    """

    def __init__(
        self,
        lib: ctypes.CDLL,
        host: str,
        port: int,
        symbol_registry: dict[str, ResolvedSymbol] | None = None,
        login_mode: SdkLoginMode = "login",
        login_connect_type: SdkConnectType = "tcp",
    ) -> None:
        self._lib = lib
        self._host = host
        self._port = port
        self._symbol_registry = symbol_registry or _resolve_symbol_registry(set(), login_mode=login_mode)
        self._login_mode: SdkLoginMode = login_mode
        self._login_connect_type: SdkConnectType = login_connect_type
        self._function_bindings = _build_function_bindings()
        self._session_handle: int | None = None
        self._authenticated = False
        self._alarm_subscriptions: dict[int, _SdkAlarmSubscriptionHandle] = {}

    def _read_last_error(self) -> int | None:
        """Best-effort retrieval of NET_SDK_GetLastError using validated prototype."""
        func = getattr(self._lib, "NET_SDK_GetLastError", None)
        if func is None:
            return None
        try:
            func.argtypes = []
            func.restype = ct.c_uint
            return int(func())
        except Exception:
            return None

    def _resolve_function(self, purpose: str) -> Any:
        """Return a bound ctypes function pointer for a confirmed SDK symbol."""
        symbol = self._symbol_registry.get(purpose)
        if symbol is None or symbol.status == "missing":
            raise MissingSymbolError(
                f"SDK {purpose} symbol is missing. Run the probe and capture exported symbols first."
            )
        if symbol.status != "confirmed":
            raise CapabilityNotAvailable(
                f"SDK {purpose} symbol is only a candidate ({symbol.symbol_name}). "
                "Do not call it until the symbol identity is validated."
            )
        if not symbol.signature_ready:
            raise CapabilityNotAvailable(
                f"SDK {purpose} symbol is present ({symbol.symbol_name}) but ctypes signature is unresolved."
            )

        binding = self._function_bindings.get(purpose)
        if purpose == "login" and symbol.symbol_name == "NET_SDK_LoginEx":
            binding = self._function_bindings.get("login_ex")
        if binding is None:
            raise CapabilityNotAvailable(
                f"SDK {purpose} binding metadata is unavailable. Add a validated ctypes prototype first."
            )
        func = getattr(self._lib, binding.symbol_name, None)
        if func is None:
            raise MissingSymbolError(
                f"SDK symbol {binding.symbol_name} disappeared after inspection. Reload diagnostics."
            )
        func.argtypes = list(binding.argtypes)
        func.restype = binding.restype
        return func

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def login(self, username: str, password: str, device_id: str | None = None) -> None:
        """Authenticate with the management server and obtain a session handle.

        Safe go/no-go rules:
        - GO only when init/login/logout symbols are confirmed and signatures are
          sourced from existing pytvt.device_sdk bindings.
        - STOP if the symbol is only a candidate or if its signature evidence is
          incomplete.

        TODO:
        - validate whether management-server login should use ``NET_SDK_Login`` or
          ``NET_SDK_LoginEx`` for TD-A510 specifically.
        - integrate SDK error-code translation once management flows are exercised
          against a live server.
        """
        init_func = self._resolve_function("init")
        login_func = self._resolve_function("login")

        if not init_func():
            raise TransportError("NET_SDK_Init returned failure")

        info = netsdk_types.NET_SDK_DEVICEINFO()
        login_symbol_meta = self._symbol_registry.get("login")
        login_symbol = login_symbol_meta.symbol_name if login_symbol_meta else "NET_SDK_Login"
        if self._login_mode == "login_ex":
            # LoginEx deviceSN is the final positional argument in the validated binding.
            device_sn_bytes = device_id.encode("utf-8") if device_id is not None else b""
            connect_type_code = _sdk_connect_type_code(self._login_connect_type)
            if self._login_connect_type == "nat20":
                # SDK demo sets NAT2 address explicitly before LoginEx NAT20.
                set_nat2_addr = self._resolve_function("set_nat2_addr")
                ok = set_nat2_addr(self._host.encode("utf-8"), self._port)
                if not ok:
                    error_code = self._read_last_error()
                    if error_code is None:
                        raise TransportError("NET_SDK_SetNat2Addr failed (error_code unavailable)")
                    raise TransportError(f"NET_SDK_SetNat2Addr failed (error_code={error_code})")
            handle = login_func(
                self._host.encode("utf-8"),
                self._port,
                username.encode("utf-8"),
                password.encode("utf-8"),
                ct.byref(info),
                connect_type_code,
                device_sn_bytes,
            )
        else:
            handle = login_func(
                self._host.encode("utf-8"),
                self._port,
                username.encode("utf-8"),
                password.encode("utf-8"),
                ct.byref(info),
            )
        if handle < 0:
            error_code = self._read_last_error()
            if error_code is None:
                raise ManagementAuthError(f"{login_symbol} returned an invalid handle (error_code unavailable).")
            raise ManagementAuthError(f"{login_symbol} returned an invalid handle (error_code={error_code}).")

        self._session_handle = int(handle)
        self._authenticated = True

    def get_server_info(self) -> ServerInfo:
        """Retrieve device/server metadata using NET_SDK_GetDeviceInfo.

        Semantics are intentionally minimal: this call is treated as a
        device-level information query that may target NVR, hybrid, or
        management-server nodes depending on the endpoint.
        """
        if self._session_handle is None or not self._authenticated:
            raise SessionExpired("SDK session not established or expired for get_server_info().")

        get_info_func = self._resolve_function("server_info")
        info = netsdk_types.NET_SDK_DEVICEINFO()
        ok = get_info_func(self._session_handle, ct.byref(info))
        if not ok:
            error_code = self._read_last_error()
            if error_code is None:
                raise TransportError("NET_SDK_GetDeviceInfo failed (error_code unavailable)")
            raise TransportError(f"NET_SDK_GetDeviceInfo failed (error_code={error_code})")

        try:
            raw_data = _structure_to_dict(info)
        except Exception as exc:
            raise ProtocolError(f"Failed to parse NET_SDK_DEVICEINFO payload: {exc}") from exc

        if not raw_data:
            raise ProtocolError("NET_SDK_DEVICEINFO payload is empty or unreadable")

        model = str(raw_data.get("deviceProduct") or "").strip()
        firmware = str(raw_data.get("firmwareVersion") or "").strip()
        serial_number = str(raw_data.get("szSN") or "").strip()

        return ServerInfo(
            host=self._host,
            port=self._port,
            model=model,
            firmware=firmware,
            serial_number=serial_number,
            raw_data=raw_data,
        )

    def list_devices(self) -> list[ManagedDevice]:
        """Enumerate configured IPC inventory using NET_SDK_GetDeviceIPCInfo.

        This path is intentionally narrow: it maps only fields with validated
        structure names and keeps every unknown field in ``raw_data``.
        """
        if self._session_handle is None or not self._authenticated:
            raise SessionExpired("SDK session not established or expired for list_devices().")

        get_devices_func = self._resolve_function("device_enumeration")
        max_records = 512
        records = (netsdk_types.NET_SDK_IPC_DEVICE_INFO * max_records)()
        count = ct.c_long(0)
        ok = get_devices_func(
            self._session_handle,
            records,
            max_records,
            ct.byref(count),
        )
        if not ok:
            error_code = self._read_last_error()
            if error_code is None:
                raise TransportError("NET_SDK_GetDeviceIPCInfo failed (error_code unavailable)")
            raise TransportError(f"NET_SDK_GetDeviceIPCInfo failed (error_code={error_code})")

        if count.value < 0 or count.value > max_records:
            raise ProtocolError(f"NET_SDK_GetDeviceIPCInfo returned invalid count {count.value} (max={max_records})")

        if count.value == 0:
            return []

        devices_by_id: dict[str, ManagedDevice] = {}
        for index in range(count.value):
            raw_data = _structure_to_dict(records[index])
            identifier = _best_device_identifier(raw_data)
            address = _to_text(raw_data.get("szServer"))

            if not identifier and not address:
                raise ProtocolError(
                    "NET_SDK_GetDeviceIPCInfo returned records without device identifiers "
                    "or addresses; payload appears channel-only or semantically incompatible "
                    "with list_devices()."
                )

            key = identifier or f"addr:{address}"
            status = _coerce_status(raw_data.get("status"))
            name = (
                _to_text(raw_data.get("szChlname"))
                or _to_text(raw_data.get("productModel"))
                or _to_text(raw_data.get("manufacturerName"))
                or key
            )

            if key in devices_by_id:
                existing = devices_by_id[key]
                channels = existing.raw_data.setdefault("channel_records", [])
                if isinstance(channels, list):
                    channels.append(raw_data)
                continue

            raw_payload = dict(raw_data)
            raw_payload["inventory_semantics"] = "configured_ipc_inventory"
            raw_payload["inventory_source_symbol"] = "NET_SDK_GetDeviceIPCInfo"
            raw_payload["channel_records"] = [dict(raw_data)]
            devices_by_id[key] = ManagedDevice(
                device_id=identifier or key,
                name=name,
                ip_address=address,
                status=status,
                raw_data=raw_payload,
            )

        if not devices_by_id:
            raise ProtocolError("NET_SDK_GetDeviceIPCInfo returned entries but none could be mapped to ManagedDevice")

        return list(devices_by_id.values())

    def list_devices_for_login_routing(self) -> list[dict[str, str]]:
        """Return a compact post-login view focused on routing-capable identifiers."""
        devices = self.list_devices()
        rows: list[dict[str, str]] = []
        for device in devices:
            raw_data = device.raw_data if isinstance(device.raw_data, dict) else {}
            rows.append(
                {
                    "device_sn": _best_device_sn(raw_data, fallback=device.device_id),
                    "name": _to_text(device.name),
                    "ip": _to_text(device.ip_address),
                }
            )
        return rows

    def get_device_statuses(self) -> list[DeviceStatus]:
        """Return channel connectivity status using NET_SDK_GetDeviceCHStatus.

        Semantics for this phase are intentionally narrow: per-channel
        online/offline connectivity, not alarm/network/disk status.
        """
        if self._session_handle is None or not self._authenticated:
            raise SessionExpired("SDK session not established or expired for get_device_statuses().")

        get_status_func = self._resolve_function("status_query")
        max_records = 1024
        records = (netsdk_types.NET_SDK_CH_DEVICE_STATUS * max_records)()
        count = ct.c_long(0)
        ok = get_status_func(
            self._session_handle,
            records,
            max_records,
            ct.byref(count),
        )
        if not ok:
            error_code = self._read_last_error()
            if error_code is None:
                raise TransportError("NET_SDK_GetDeviceCHStatus failed (error_code unavailable)")
            raise TransportError(f"NET_SDK_GetDeviceCHStatus failed (error_code={error_code})")

        if count.value < 0 or count.value > max_records:
            raise ProtocolError(f"NET_SDK_GetDeviceCHStatus returned invalid count {count.value} (max={max_records})")

        if count.value == 0:
            return []

        statuses: list[DeviceStatus] = []
        for index in range(count.value):
            raw_data = _structure_to_dict(records[index])
            identifier = _best_status_identifier(raw_data)
            channel = _to_int(raw_data.get("channel"))
            name = _to_text(raw_data.get("name"))
            if not identifier:
                raise ProtocolError(
                    "NET_SDK_GetDeviceCHStatus returned records without channel identifiers "
                    "or names; payload appears semantically incompatible with channel status mapping."
                )

            status_value = _to_int(raw_data.get("status"))
            online = True if status_value == 1 else False if status_value is not None else None
            raw_payload = dict(raw_data)
            raw_payload["status_semantics"] = "channel_connectivity"
            raw_payload["status_source_symbol"] = "NET_SDK_GetDeviceCHStatus"
            if channel is not None:
                raw_payload["channel"] = channel
            if name:
                raw_payload["channel_name"] = name

            statuses.append(
                DeviceStatus(
                    device_id=identifier,
                    online=online,
                    raw_data=raw_payload,
                )
            )

        return statuses

    def subscribe_alarms(self) -> AlarmSubscription:
        """Register an SDK alarm channel and return a closable handle.

        Semantics are intentionally narrow for this phase:
        - registration lifecycle only (setup/close)
        - callback payload/event schema remains opaque and unconfirmed
        """
        if self._session_handle is None or not self._authenticated:
            raise SessionExpired("SDK session not established or expired for subscribe_alarms().")

        if self._alarm_subscriptions:
            raise CapabilityNotAvailable(
                "Multiple alarm subscriptions are disabled until callback threading semantics are validated."
            )

        setup_func = self._resolve_function("alarm_subscription")
        close_binding = self._function_bindings["alarm_unsubscribe"]
        close_func = getattr(self._lib, close_binding.symbol_name, None)
        if close_func is None:
            raise CapabilityNotAvailable(
                "Alarm teardown function NET_SDK_CloseAlarmChan is unavailable; "
                "cannot guarantee safe lifecycle for subscribe_alarms()."
            )

        close_func.argtypes = list(close_binding.argtypes)
        close_func.restype = close_binding.restype

        raw_handle = int(setup_func(self._session_handle))
        if raw_handle < 0:
            error_code = self._read_last_error()
            if error_code is None:
                raise TransportError("NET_SDK_SetupAlarmChan failed (error_code unavailable)")
            raise TransportError(f"NET_SDK_SetupAlarmChan failed (error_code={error_code})")

        def _close_alarm() -> None:
            success = bool(close_func(raw_handle))
            if not success:
                error_code = self._read_last_error()
                if error_code is None:
                    raise TransportError("NET_SDK_CloseAlarmChan failed (error_code unavailable)")
                raise TransportError(f"NET_SDK_CloseAlarmChan failed (error_code={error_code})")
            self._alarm_subscriptions.pop(raw_handle, None)

        subscription = _SdkAlarmSubscriptionHandle(
            handle=f"alarm-channel:{raw_handle}",
            transport="sdk_alarm_channel_registration",
            raw_data={
                "subscription_semantics": "alarm_channel_registration",
                "payload_semantics": "opaque",
                "setup_symbol": "NET_SDK_SetupAlarmChan",
                "close_symbol": "NET_SDK_CloseAlarmChan",
                "raw_alarm_handle": raw_handle,
                "callback_semantics_confirmed": False,
                "threading_semantics_confirmed": False,
            },
            closer=_close_alarm,
        )
        self._alarm_subscriptions[raw_handle] = subscription
        return subscription

    def close(self) -> None:
        """Release the SDK session handle and reset state."""
        for subscription in list(self._alarm_subscriptions.values()):
            try:
                subscription.close()
            except Exception:
                logger.exception("Failed closing alarm subscription handle=%s", subscription.handle)
        self._alarm_subscriptions.clear()

        handle = self._session_handle
        self._session_handle = None
        self._authenticated = False
        if handle is None:
            return

        try:
            logout_func = self._resolve_function("logout")
        except CapabilityNotAvailable:
            logger.warning("SDK logout binding unresolved; local session state cleared only")
            return

        try:
            if not logout_func(handle):
                logger.warning("NET_SDK_Logout reported failure for handle=%s", handle)
        except Exception:
            logger.exception("NET_SDK_Logout raised unexpectedly for handle=%s", handle)


# ---------------------------------------------------------------------------
# SdkManagementBackend
# ---------------------------------------------------------------------------


class SdkManagementBackend(BaseManagementBackend):
    """SDK-backed backend for the TVT management-server interface.

    Attempts to load the shared library at construction.  If loading fails the
    load error is recorded and every call raises CapabilityNotAvailable with the
    reason.  This prevents silent partial failures.
    """

    def __init__(
        self,
        host: str,
        port: int = 6003,
        sdk_path: str | None = None,
        login_mode: SdkLoginMode = "login",
        login_connect_type: SdkConnectType = "tcp",
    ) -> None:
        self.host = host
        self.port = port
        self.sdk_path = (sdk_path or "").strip()
        self._login_mode: SdkLoginMode = login_mode
        self._login_connect_type: SdkConnectType = login_connect_type
        self._lib: ctypes.CDLL | None = None
        self._load_error: str | None = None
        self._client: SDKClient | None = None
        self._authenticated = False
        self._diagnostics = (
            inspect_sdk_library(
                self.sdk_path,
                login_mode=self._login_mode,
                login_connect_type=self._login_connect_type,
            )
            if self.sdk_path
            else SdkDiagnostics(
                sdk_path="",
                load_success=False,
                load_error="No SDK path configured",
                symbol_scan_success=False,
                symbol_scan_error="symbol scan skipped because no SDK path configured",
                discovered_symbol_count=None,
                symbols=_resolve_symbol_registry(None, login_mode=self._login_mode),
                login_path_ready=False,
                login_readiness_reason="No SDK path configured",
                evidence_record_count=len(export_evidence_records()),
                login_mode=self._login_mode,
                login_connect_type=self._login_connect_type,
                symbol_probe={"source": "none", "error": "sdk_path_not_configured"},
                symbol_inventory=(),
                symbol_presence_checks=tuple(
                    build_symbol_presence_checks(
                        set(),
                        [
                            "NET_SDK_Init",
                            "NET_SDK_Login",
                            "NET_SDK_LoginEx",
                            "NET_SDK_Logout",
                            "NET_SDK_Cleanup",
                            "NET_SDK_GetDeviceIPCInfo",
                        ],
                    )
                ),
                windows_symbol_parity=tuple(build_windows_parity_report(set())),
                capability_evidence=build_symbol_capability_evidence(set()),
            )
        )

        if self._diagnostics.load_success and self.sdk_path:
            try:
                self._lib = _load_library(self.sdk_path)
            except _SDKLoadError as exc:
                self._load_error = str(exc)
                logger.warning("TVT SDK backend unavailable: %s", exc)
        elif self._diagnostics.load_error:
            self._load_error = self._diagnostics.load_error

    # ------------------------------------------------------------------
    # Capability probes
    # ------------------------------------------------------------------

    def load_sdk(self) -> bool:
        return self._lib is not None

    def diagnostics(self) -> dict[str, Any]:
        return self.get_sdk_diagnostics()

    def get_context(self) -> SDKContext:
        return self._diagnostics.to_context()

    def supports_sdk(self) -> bool:
        """Return True only if the real login path is ready to call safely."""
        return self._diagnostics.login_path_ready and self._lib is not None

    def supports_native_protocol(self) -> bool:
        return False

    def get_sdk_diagnostics(self) -> dict[str, Any]:
        """Return structured diagnostics describing SDK readiness."""
        return self._diagnostics.as_dict()

    # ------------------------------------------------------------------
    # Internal guard
    # ------------------------------------------------------------------

    def _require_sdk(self) -> ctypes.CDLL:
        """Return the loaded library or raise CapabilityNotAvailable."""
        os_family = self.get_context().platform.os_family
        if os_family != "linux":
            raise UnsupportedOnPlatformError(
                f"native_linux_sdk backend requires linux runtime; current platform is {os_family}."
            )
        if self._lib is None:
            reason = self._load_error or "No SDK path configured"
            raise CapabilityNotAvailable(f"TVT SDK is not available: {reason}")
        if not self._diagnostics.login_path_ready:
            raise CapabilityNotAvailable(
                f"TVT SDK library loaded but login path is not ready: {self._diagnostics.login_readiness_reason}"
            )
        return self._lib

    def _require_session(self) -> SDKClient:
        """Return the active SDKClient or raise ManagementNotAuthenticatedError."""
        if self._client is None or not self._authenticated:
            raise ManagementNotAuthenticatedError("SDK session not established. Call login() first.")
        return self._client

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def login(self, username: str, password: str, device_id: str | None = None) -> bool:
        lib = self._require_sdk()
        client = SDKClient(
            lib,
            self.host,
            self.port,
            symbol_registry=self._diagnostics.symbols,
            login_mode=self._login_mode,
            login_connect_type=self._login_connect_type,
        )
        try:
            client.login(username, password, device_id=device_id)
        except CapabilityNotAvailable:
            # Symbol not mapped yet — propagate without wrapping.
            raise
        except Exception as exc:
            # SDK runtime errors become ManagementAuthError or TransportError.
            # TODO: refine error discrimination once SDK error codes are known.
            raise ManagementAuthError(f"SDK login failed: {exc}") from exc
        self._client = client
        self._authenticated = True
        return True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._authenticated = False

    # ------------------------------------------------------------------
    # Management operations (all require active session)
    # ------------------------------------------------------------------

    def get_server_info(self) -> ServerInfo:
        self._require_sdk()
        if self._client is None or not self._authenticated:
            raise SessionExpired("SDK session not established. Call login() first.")
        client = self._client
        try:
            return client.get_server_info()
        except (CapabilityNotAvailable, SessionExpired):
            raise
        except Exception as exc:
            raise TransportError(f"SDK get_server_info failed: {exc}") from exc

    def list_devices(self) -> list[ManagedDevice]:
        self._require_sdk()
        if self._client is None or not self._authenticated:
            raise SessionExpired("SDK session not established. Call login() first.")
        client = self._client
        try:
            return client.list_devices()
        except (CapabilityNotAvailable, SessionExpired):
            raise
        except Exception as exc:
            raise TransportError(f"SDK list_devices failed: {exc}") from exc

    def list_devices_for_login_routing(self) -> list[dict[str, str]]:
        self._require_sdk()
        if self._client is None or not self._authenticated:
            raise SessionExpired("SDK session not established. Call login() first.")
        client = self._client
        try:
            return client.list_devices_for_login_routing()
        except (CapabilityNotAvailable, SessionExpired):
            raise
        except Exception as exc:
            raise TransportError(f"SDK list_devices_for_login_routing failed: {exc}") from exc

    def list_channels(self) -> list[ManagedChannel]:
        self._require_sdk()
        self._require_session()
        # TODO: identify SDK channel enumeration function.
        raise CapabilityNotAvailable(
            "SDK list_channels symbol not yet mapped. Requires channel enumeration capture before wiring."
        )

    def get_device_statuses(self) -> list[DeviceStatus]:
        self._require_sdk()
        if self._client is None or not self._authenticated:
            raise SessionExpired("SDK session not established. Call login() first.")
        client = self._client
        try:
            return client.get_device_statuses()
        except (CapabilityNotAvailable, SessionExpired):
            raise
        except Exception as exc:
            raise TransportError(f"SDK get_device_statuses failed: {exc}") from exc

    def subscribe_alarms(self, *_args: Any, **_kwargs: Any) -> AlarmSubscription:
        self._require_sdk()
        if self._client is None or not self._authenticated:
            raise SessionExpired("SDK session not established. Call login() first.")
        client = self._client
        try:
            return client.subscribe_alarms()
        except (CapabilityNotAvailable, SessionExpired):
            raise
        except Exception as exc:
            raise TransportError(f"SDK subscribe_alarms failed: {exc}") from exc
