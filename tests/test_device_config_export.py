from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from pytvt.device_sdk import ConfigExportResult
from pytvt.device_sdk import bindings as sdk
from pytvt.device_sdk.client import DeviceSession, NetSdkError


@pytest.fixture
def native_lib(monkeypatch):
    lib = MagicMock()
    lib.NET_SDK_GetLastError.return_value = 27
    monkeypatch.setattr(sdk, "_lib", lib)
    return lib


@pytest.fixture
def session(native_lib):
    return DeviceSession(handle=11, client=MagicMock())


def test_export_config_atomically_publishes_verified_artifact(tmp_path, native_lib, session):
    payload = b"encrypted-vendor-configuration\x00\x01"

    def export(_handle, encoded_path):
        temporary_path = encoded_path.decode("utf-8")
        with open(temporary_path, "wb") as exported:
            exported.write(payload)
        return True

    native_lib.NET_SDK_GetConfigFile.side_effect = export
    destination = tmp_path / "nested" / "device.backup"

    result = session.export_config(destination)

    assert result == ConfigExportResult(
        path=destination,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert destination.read_bytes() == payload
    assert list(destination.parent.glob("*.partial")) == []
    native_lib.NET_SDK_GetConfigFile.assert_called_once()
    assert native_lib.NET_SDK_GetConfigFile.call_args.args[0] == 11


def test_export_config_preserves_existing_backup_when_sdk_fails(tmp_path, native_lib, session):
    destination = tmp_path / "device.backup"
    destination.write_bytes(b"known-good-backup")
    native_lib.NET_SDK_GetConfigFile.return_value = False

    with pytest.raises(NetSdkError, match="GetConfigFile"):
        session.export_config(destination)

    assert destination.read_bytes() == b"known-good-backup"
    assert list(tmp_path.glob("*.partial")) == []


def test_export_config_rejects_empty_sdk_output(tmp_path, native_lib, session):
    destination = tmp_path / "device.backup"
    native_lib.NET_SDK_GetConfigFile.return_value = True

    with pytest.raises(NetSdkError, match="empty configuration export"):
        session.export_config(destination)

    assert not destination.exists()
    assert list(tmp_path.glob("*.partial")) == []
