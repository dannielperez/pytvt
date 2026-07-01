# pytvt 1.0 Migration Guide

pytvt 1.0 reorganises the package into a **seven-lane architecture**.
This document maps every old import path to its new home and lists
the renames that downstream consumers need to apply.

The migration is **mechanical** — no semantics changed.  Every public
symbol still exists; only its dotted path may have moved.  Public
attribute names are unchanged.

## The seven lanes

| Lane            | Module                | Purpose                                                  |
| --------------- | --------------------- | -------------------------------------------------------- |
| `device_sdk`    | `pytvt.device_sdk`    | TVT device-level SDKs (NetSDK ctypes, HTTP bridge, sdk_local) |
| `platform_sdk`  | `pytvt.platform_sdk`  | NVMS / PlatformSDK management server                     |
| `web_api`       | `pytvt.web_api`       | TVT Web API (LAPI / HTTP API)                            |
| `xml_api`       | `pytvt.xml_api`       | NVR XML CGI client (session auth)                        |
| `capabilities`  | `pytvt.capabilities`  | Cross-lane discovery & capability probing                |
| `strategy`      | `pytvt.strategy`      | Lane routing (`Lane`, `OperationKind`, `plan_for`)       |
| `workflows`     | `pytvt.workflows`     | Technician-facing orchestrations                         |

`tools/` is a CLI lane (entry points), not a public library lane.

## Import-path migration table

### Sub-package renames

| Old (≤ 0.8)                | New (1.0)                  |
| -------------------------- | -------------------------- |
| `pytvt.management.*`       | `pytvt.platform_sdk.*`     |
| `pytvt.netsdk.*`           | `pytvt.device_sdk.*`       |
| `pytvt.webapi.*`           | `pytvt.web_api.*`          |

### Single-file moves

| Old (≤ 0.8)                          | New (1.0)                                  |
| ------------------------------------ | ------------------------------------------ |
| `pytvt.nvr_api`                      | `pytvt.xml_api`                            |
| `pytvt.device_manager`               | `pytvt.device_sdk.manager`                 |
| `pytvt.sdk_http`                     | `pytvt.device_sdk.sdk_http`                |
| `pytvt.sdk_http_client`              | `pytvt.device_sdk.http_client`             |
| `pytvt.sdk_local`                    | `pytvt.device_sdk.sdk_local`               |
| `pytvt.platform_alarms`              | `pytvt.platform_sdk.alarms`                |
| `pytvt.platform_capabilities`        | `pytvt.platform_sdk.capabilities`          |
| `pytvt.platform_classification`      | `pytvt.platform_sdk.classification`        |
| `pytvt.platform_health`              | `pytvt.platform_sdk.health`                |
| `pytvt.platform_inventory`           | `pytvt.platform_sdk.inventory`             |
| `pytvt.platform_topology`            | `pytvt.platform_sdk.topology`              |
| `pytvt.management.platform_sdk`      | `pytvt.platform_sdk.platform_backend`      |
| `pytvt.cli`                          | `pytvt.tools.cli`                          |
| `pytvt.workflow_cli`                 | `pytvt.tools.workflow_cli`                 |
| `pytvt.snapshot`                     | `pytvt.tools.snapshot`                     |

### Files that stayed at top level

The following modules are library utilities and remain at `pytvt.X`:

* `pytvt.models` (scanner + NVR API dataclasses)
* `pytvt.constants` (`BackendFamily`, `IntegrationMode`, `CompositeStrategy`, …)
* `pytvt.exceptions` (`PytvtError`, `BackendError`, `RegistryError`)
* `pytvt.protocol` (TVT binary protocol scan client)
* `pytvt.registry` (backend dispatch registry)
* `pytvt.diagnostics` (`diagnostics()` runtime probe)
* `pytvt.scanner`, `pytvt.discovery`, `pytvt.config`,
  `pytvt.connection_pool`, `pytvt.diff`, `pytvt.output`
* `pytvt.workflows` (unchanged)

### Console scripts

All entry points moved with `cli.py`:

| Script               | Old target                         | New target                              |
| -------------------- | ---------------------------------- | --------------------------------------- |
| `pytvt`              | `pytvt.cli:main`                   | `pytvt.tools.cli:main`                  |
| `pytvt-scan`         | `pytvt.cli:scan_nvr_cli`           | `pytvt.tools.cli:scan_nvr_cli`          |
| `pytvt-discover`     | `pytvt.cli:discover`               | `pytvt.tools.cli:discover`              |
| `pytvt-api`          | `pytvt.cli:api`                    | `pytvt.tools.cli:api`                   |
| `pytvt-snapshot`     | `pytvt.cli:snapshot`               | `pytvt.tools.cli:snapshot`              |
| `pytvt-diff`         | `pytvt.cli:diff_cli`               | `pytvt.tools.cli:diff_cli`              |
| `pytvt-workflow`     | `pytvt.workflow_cli:workflow_cli`  | `pytvt.tools.workflow_cli:workflow_cli` |

If you call entry points via name (`pytvt-scan ...`), nothing changes.
If you call them via dotted path (`python -m pytvt.cli scan ...`), update
to `python -m pytvt.tools.cli scan ...`.

## Public surface

The top-level `pytvt` package keeps the same re-export list as 0.8 —
`from pytvt import NvrClient, ManagementClient, DeviceManager, …` all
still work unchanged.  You only need to update imports if you reach
into a sub-package directly.

## New in 1.0

### `pytvt.capabilities`

```python
from pytvt.capabilities import probe_capabilities, TvtCapabilities

caps = probe_capabilities("10.0.0.42", username="admin", password="...")
caps.xml_api      # True if NVR CGI responded
caps.web_api      # True if LAPI HTTP responded
caps.to_dict()    # JSON-serialisable snapshot
```

`detect_platform_capabilities` is re-exported from
`pytvt.platform_sdk.capabilities` for symmetry.

### `pytvt.strategy`

```python
from pytvt.strategy import Lane, OperationKind, plan_for

plan = plan_for(OperationKind.HEALTH)
plan.primary       # Lane.PLATFORM_SDK
plan.fallbacks     # (Lane.XML_API,)
```

The strategy lane is the seam at which higher-level orchestrators
decide which lane handles each operation.

## Step-by-step migration for downstream consumers

1. Run a project-wide search-and-replace using the **Single-file moves**
   and **Sub-package renames** tables above.
2. Re-run your tests.  Most consumers should see zero failures because
   the broad `from pytvt import X` surface is unchanged.
3. If you depend on console-script dotted paths (rare), update them as
   listed in the **Console scripts** table.
4. Optionally, adopt `pytvt.capabilities` and `pytvt.strategy` for new
   lane-aware code.

## Verifying 1.0

```bash
pip install -e ./vendor/pytvt
python -c "import pytvt; print(pytvt.__version__)"   # 1.0.0
python -m pytest vendor/pytvt/tests/ -q              # 774 passed
```
