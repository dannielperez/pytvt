# tools/ — Operational Utilities

Scripts in this directory are **operational utilities** that build on top of the
`pytvt` package. They are not part of the installed package and are not required
for normal scanning or discovery.

Unlike the scripts in [`research/`](../research/), these tools are intended for
real operational use — they import from `pytvt` and perform useful batch
operations against live infrastructure.

## Retained Scripts

| Script | Purpose |
|---|---|
| `enable_nvr_services.py` | Batch-enable RTSP + API Server on TVT NVRs from a Ruijie inventory |
| `management_live_validate.py` | Primary management validation entrypoint (single SDK and multi-SDK manifest comparison) |
| `management_validation_lib.py` | Shared parser, diagnostics, report helpers, and manifest-aware SDK path resolution |
| `manifest_resolver.py` | Resolve SDK binaries from tvt-sdk manifest inventory (`--sdk-manifest-id`) |
| `sdk_comparator.py` | Evidence-driven multi-SDK comparison over diagnostics outputs |
| `management_report_summary.py` | Compact terminal summary for JSON reports |

## Usage

```bash
# Requires pytvt to be installed
pip install -e .

# Run from repo root
TVT_PASSWORD='example-password' python tools/enable_nvr_services.py
```

## Local Output Policy

Artifacts written under `tools/out/` are local-only operational output.

- Keep captures, headers, cookies, and validation reports out of git.
- Treat `tools/out/` as disposable workspace data, especially when it contains lab targets, session material, or environment-specific paths.

## SDK Artifact Policy

`pytvt` does not vendor SDK binaries or headers in this public repository.
SDK artifacts may be stored in a private/internal repository and referenced at
runtime using explicit paths or manifest-driven resolution.

## Management Backend Modes

`management_live_validate.py` accepts `--backend-mode` with these values:

| Mode | Description |
|---|---|
| `auto` | SDK-first with fallback to native protocol stub |
| `native_linux_sdk` | Explicit Linux SDK backend (requires `libdvrnetsdk.so`) |
| `platform_sdk` | Explicit NVMS PlatformSDK backend (requires `libPlatClientSDK.so` / `PlatClientSDK.dll`) |
| `sidecar` | Sidecar runtime backend — SDK-agnostic runtime mode; delegates to external bridge process |
| `native_protocol` | Native protocol stub (not implemented) |

The `sidecar` mode is configured via `--sidecar-command` or `PYTVT_MGMT_SIDECAR_CMD`.

## Management Validation Runtime Requirement

`management_live_validate.py` is SDK-first by default (`--require-sdk`) and will
exit with `final_status: sdk_not_ready` when pre-login blockers are detected.

For real-device validation runs, use one of these compatible setups:

- Linux `x86_64` runtime with an `x86_64` `libdvrnetsdk.so`
- Linux `aarch64` runtime with an `aarch64` `libdvrnetsdk.so`

If runtime and SDK architectures do not match, diagnostics report
`sdk_arch_mismatch` and validation exits before login.

## Retention Policy

The maintained tooling surface in this directory is intentionally narrow:

- Manifest-aware SDK resolution
- Symbol and capability/context diagnostics (`--dump-sdk-symbols`)
- Multi-SDK comparison (`--compare-sdk-manifests`)
- One primary live validation path (`management_live_validate.py`)

Additional exploratory scripts should be developed outside this retained
operational tooling surface unless they are intended for long-term maintenance.
