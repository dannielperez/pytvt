"""Tests for DeviceManager — backend resolution, method dispatch, fallbacks."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pytvt.device_manager import (
    Backend,
    DeviceManager,
    NoBackendAvailable,
    _docker_running,
    _docker_tvt_api_available,
    _netsdk_available,
    available_backends,
)
from pytvt.sdk_http_client import (
    CommandResult,
    DeviceInfoResult,
    DeviceTimeResult,
    RtspUrlResult,
)

CREDS = dict(ip="10.0.0.1", username="admin", password="pass123")


# ── Backend detection ────────────────────────────────────────────────


class TestNativeDetection:
    def test_netsdk_not_available_on_non_linux(self) -> None:
        with patch("pytvt.netsdk.loader.platform") as mock_plat:
            mock_plat.system.return_value = "Darwin"
            from pytvt.netsdk.loader import is_netsdk_available
            assert is_netsdk_available() is False

    def test_netsdk_available_checks_file(self) -> None:
        with patch("pytvt.netsdk.loader.platform") as mock_plat, \
             patch("pytvt.netsdk.loader.Path") as mock_path:
            mock_plat.system.return_value = "Linux"
            mock_plat.machine.return_value = "x86_64"
            # Make _find_lib return a path that "exists"
            mock_path_inst = MagicMock()
            mock_path_inst.is_absolute.return_value = True
            mock_path_inst.exists.return_value = True
            mock_path.return_value = mock_path_inst
            # But the explicit test path avoids system lookup,
            # so _find_lib returns "libdvrnetsdk.so" (bare)
            # We need to mock _find_lib directly for a clean test
            with patch("pytvt.netsdk.loader._find_lib", return_value="/fake/libdvrnetsdk.so"):
                from pytvt.netsdk.loader import is_netsdk_available
                # Path("/fake/libdvrnetsdk.so").is_absolute() and .exists()
                mock_path.reset_mock()
                p = MagicMock()
                p.is_absolute.return_value = True
                p.exists.return_value = True
                mock_path.return_value = p
                assert is_netsdk_available() is True


class TestDockerDetection:
    def test_tvt_api_reachable(self) -> None:
        with patch("pytvt.device_manager.SdkHttpClient") as mock_cls:
            mock_cls.return_value.health.return_value = True
            assert _docker_tvt_api_available("http://test:3000") is True

    def test_tvt_api_unreachable(self) -> None:
        with patch("pytvt.device_manager.SdkHttpClient") as mock_cls:
            mock_cls.return_value.health.return_value = False
            assert _docker_tvt_api_available("http://test:3000") is False

    def test_tvt_api_exception(self) -> None:
        with patch("pytvt.device_manager.SdkHttpClient") as mock_cls:
            mock_cls.return_value.health.side_effect = ConnectionRefusedError
            assert _docker_tvt_api_available("http://test:3000") is False

    def test_docker_running_no_docker_binary(self) -> None:
        with patch("pytvt.device_manager.shutil.which", return_value=None):
            assert _docker_running() is False

    def test_docker_running_daemon_ok(self) -> None:
        with patch("pytvt.device_manager.shutil.which", return_value="/usr/bin/docker"), \
             patch("pytvt.device_manager.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            assert _docker_running() is True


class TestAvailableBackends:
    def test_both_available(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=True), \
             patch("pytvt.device_manager._docker_tvt_api_available", return_value=True):
            result = available_backends()
            assert result == [Backend.NETSDK, Backend.SDK_HTTP]

    def test_sdk_path_passed_to_native_probe(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=True) as mock_native, \
             patch("pytvt.device_manager._docker_tvt_api_available", return_value=False):
            result = available_backends(sdk_path="/opt/tvt-sdk")
            assert result == [Backend.NETSDK]
            mock_native.assert_called_once_with("/opt/tvt-sdk")

    def test_only_http(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=False), \
             patch("pytvt.device_manager._docker_tvt_api_available", return_value=True):
            result = available_backends()
            assert result == [Backend.SDK_HTTP]

    def test_none_available(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=False), \
             patch("pytvt.device_manager._docker_tvt_api_available", return_value=False):
            result = available_backends()
            assert result == []


# ── DeviceManager construction ───────────────────────────────────────


class TestDeviceManagerInit:
    def test_auto_detect_netsdk(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=True):
            mgr = DeviceManager(**CREDS)
            assert mgr.backend == Backend.NETSDK
            mgr.close()

    def test_auto_detect_netsdk_with_sdk_path(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=True) as mock_native:
            mgr = DeviceManager(**CREDS, sdk_path="/opt/tvt-sdk")
            assert mgr.backend == Backend.NETSDK
            mock_native.assert_called_once_with("/opt/tvt-sdk")
            mgr.close()

    def test_auto_detect_http_fallback(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=False), \
             patch("pytvt.device_manager._docker_tvt_api_available", return_value=True):
            mgr = DeviceManager(**CREDS)
            assert mgr.backend == Backend.SDK_HTTP
            mgr.close()

    def test_auto_detect_none_raises(self) -> None:
        with patch("pytvt.device_manager._netsdk_available", return_value=False), \
             patch("pytvt.device_manager._docker_tvt_api_available", return_value=False), \
             pytest.raises(NoBackendAvailable):
            DeviceManager(**CREDS)

    def test_force_backend(self) -> None:
        mgr = DeviceManager(**CREDS, backend=Backend.SDK_HTTP)
        assert mgr.backend == Backend.SDK_HTTP
        mgr.close()

    def test_force_backend_string(self) -> None:
        mgr = DeviceManager(**CREDS, backend="sdk_http")
        assert mgr.backend == Backend.SDK_HTTP
        mgr.close()

    def test_repr(self) -> None:
        mgr = DeviceManager(**CREDS, backend=Backend.SDK_HTTP)
        assert "10.0.0.1" in repr(mgr)
        assert "sdk_http" in repr(mgr)
        mgr.close()

    def test_context_manager(self) -> None:
        with DeviceManager(**CREDS, backend=Backend.SDK_HTTP) as mgr:
            assert mgr.backend == Backend.SDK_HTTP


# ── HTTP backend dispatch ────────────────────────────────────────────


class TestHttpDispatch:
    @pytest.fixture
    def mgr(self) -> DeviceManager:
        m = DeviceManager(**CREDS, backend=Backend.SDK_HTTP)
        return m

    def test_device_info(self, mgr: DeviceManager) -> None:
        expected = DeviceInfoResult(success=True, device_name="NVR-01", serial_number="SN1")
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.device_info.return_value = expected
            result = mgr.device_info()
        assert result.success is True
        assert result.device_name == "NVR-01"

    def test_device_time_get(self, mgr: DeviceManager) -> None:
        expected = DeviceTimeResult(success=True, action="get", device_time="2025-01-01T00:00:00")
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.device_time.return_value = expected
            result = mgr.device_time()
        assert result.action == "get"

    def test_device_time_set(self, mgr: DeviceManager) -> None:
        expected = DeviceTimeResult(success=True, action="set", timestamp=1700000000)
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.device_time.return_value = expected
            result = mgr.device_time(set_timestamp=1700000000)
        assert result.action == "set"

    def test_snapshot(self, mgr: DeviceManager) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 50
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.snapshot.return_value = jpeg
            result = mgr.snapshot(channel=0)
        assert result is not None
        assert result[:4] == b"\xff\xd8\xff\xe0"

    def test_rtsp_url(self, mgr: DeviceManager) -> None:
        expected = RtspUrlResult(success=True, rtsp_url="rtsp://10.0.0.1:554/chID=0&streamType=main")
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.rtsp_url.return_value = expected
            result = mgr.rtsp_url(channel=0)
        assert "rtsp://" in (result.rtsp_url or "")

    def test_ptz(self, mgr: DeviceManager) -> None:
        expected = CommandResult(success=True)
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.ptz.return_value = expected
            result = mgr.ptz(command=12, speed=6)
        assert result.success is True

    def test_ptz_preset(self, mgr: DeviceManager) -> None:
        expected = CommandResult(success=True)
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.ptz_preset.return_value = expected
            result = mgr.ptz_preset(command=16, preset_index=3)
        assert result.success is True

    def test_reboot(self, mgr: DeviceManager) -> None:
        expected = CommandResult(success=True)
        with patch.object(mgr, "_get_http") as mock_http:
            mock_http.return_value.reboot.return_value = expected
            result = mgr.reboot()
        assert result.success is True


# ── netsdk backend dispatch (mocked) ────────────────────────────────


class TestNetsdkDispatch:
    @pytest.fixture
    def mgr(self) -> DeviceManager:
        m = DeviceManager(**CREDS, backend=Backend.NETSDK)
        return m

    def test_device_info(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mock_info = MagicMock()
        mock_info.device_name = "NVR-02"
        mock_info.product = "ED9516"
        mock_info.serial_number = "SN2"
        mock_info.firmware = "v4.0"
        mock_info.hardware_version = "HW2"
        mock_info.kernel_version = "K2"
        mock_info.video_inputs = 16
        mock_info.audio_inputs = 1
        mock_info.sensor_inputs = 4
        mock_info.sensor_outputs = 2
        mock_info.device_type = 3
        mock_session.device_info.return_value = mock_info
        mgr._netsdk_session = mock_session

        result = mgr.device_info()
        assert result.success is True
        assert result.device_name == "NVR-02"
        assert result.serial_number == "SN2"

    def test_device_info_error(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mock_session.device_info.side_effect = RuntimeError("Connection refused")
        mgr._netsdk_session = mock_session

        result = mgr.device_info()
        assert result.success is False
        assert "Connection refused" in (result.error or "")

    def test_snapshot(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mock_session.capture_jpeg.return_value = b"\xff\xd8" + b"\x00" * 10
        mgr._netsdk_session = mock_session

        result = mgr.snapshot(channel=1)
        assert result is not None
        mock_session.capture_jpeg.assert_called_once_with(1)

    def test_snapshot_error(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mock_session.capture_jpeg.side_effect = RuntimeError("fail")
        mgr._netsdk_session = mock_session

        result = mgr.snapshot()
        assert result is None

    def test_reboot(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mgr._netsdk_session = mock_session

        result = mgr.reboot()
        assert result.success is True
        mock_session.reboot.assert_called_once()

    def test_reboot_error(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mock_session.reboot.side_effect = RuntimeError("timeout")
        mgr._netsdk_session = mock_session

        result = mgr.reboot()
        assert result.success is False

    def test_device_time_get(self, mgr: DeviceManager) -> None:
        from datetime import datetime
        mock_session = MagicMock()
        mock_session.device_time.return_value = datetime(2025, 1, 15, 10, 30, 0)
        mgr._netsdk_session = mock_session

        result = mgr.device_time()
        assert result.success is True
        assert result.action == "get"
        assert "2025-01-15" in (result.device_time or "")

    def test_device_time_set(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mgr._netsdk_session = mock_session

        result = mgr.device_time(set_timestamp=1700000000)
        assert result.success is True
        assert result.action == "set"
        mock_session.sync_time.assert_called_once_with(1700000000)

    def test_rtsp_url(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mock_session.rtsp_url.return_value = "rtsp://10.0.0.1:554/chID=0&streamType=main"
        mgr._netsdk_session = mock_session

        result = mgr.rtsp_url(channel=0)
        assert result.success is True
        assert "rtsp://" in (result.rtsp_url or "")

    def test_ptz(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mgr._netsdk_session = mock_session

        result = mgr.ptz(command=0, speed=4)
        assert result.success is True

    def test_ptz_preset(self, mgr: DeviceManager) -> None:
        mock_session = MagicMock()
        mgr._netsdk_session = mock_session

        result = mgr.ptz_preset(command=16, preset_index=1)
        assert result.success is True

    def test_netsdk_client_receives_sdk_path(self) -> None:
        mgr = DeviceManager(**CREDS, backend=Backend.NETSDK, sdk_path="/opt/tvt-sdk")
        with patch("pytvt.netsdk.client.NetSdkClient") as mock_cls:
            mock_session = MagicMock()
            mock_cls.return_value.login.return_value = mock_session
            session = mgr._get_netsdk_session()
            assert session is mock_session
            mock_cls.assert_called_once_with(sdk_path="/opt/tvt-sdk")
        mgr.close()


# ── Backend enum ─────────────────────────────────────────────────────


class TestBackendEnum:
    def test_values(self) -> None:
        assert Backend.NETSDK == "netsdk"
        assert Backend.SDK_HTTP == "sdk_http"

    def test_str(self) -> None:
        assert str(Backend.NETSDK) == "netsdk"

    def test_from_string(self) -> None:
        assert Backend("netsdk") == Backend.NETSDK
        assert Backend("sdk_http") == Backend.SDK_HTTP


# ── Loader updates ───────────────────────────────────────────────────


class TestLoaderUpdates:
    def test_arch_dir_x86(self) -> None:
        with patch("pytvt.netsdk.loader.platform") as mock_plat:
            mock_plat.machine.return_value = "x86_64"
            from pytvt.netsdk.loader import _arch_dir
            assert _arch_dir() == "linux"

    def test_arch_dir_arm64(self) -> None:
        with patch("pytvt.netsdk.loader.platform") as mock_plat:
            mock_plat.machine.return_value = "aarch64"
            from pytvt.netsdk.loader import _arch_dir
            assert _arch_dir() == "linux-arm64"

    def test_find_lib_env_override(self, tmp_path) -> None:
        lib_path = tmp_path / "libdvrnetsdk.so"
        lib_path.write_text("", encoding="utf-8")

        with patch.dict("os.environ", {"TVT_SDK_PATH": str(lib_path)}):
            from pytvt.netsdk.loader import _find_lib
            assert _find_lib() == str(lib_path)

    def test_find_lib_legacy_env_override(self, tmp_path) -> None:
        lib_path = tmp_path / "legacy-libdvrnetsdk.so"
        lib_path.write_text("", encoding="utf-8")

        with patch.dict("os.environ", {"PYTVT_NETSDK_LIB": str(lib_path)}, clear=True):
            from pytvt.netsdk.loader import _find_lib
            assert _find_lib() == str(lib_path)

    def test_find_lib_sdk_root_directory(self, tmp_path) -> None:
        sdk_root = tmp_path / "vendor-sdk"
        lib_path = sdk_root / "bin" / "linux" / "libdvrnetsdk.so"
        lib_path.parent.mkdir(parents=True)
        lib_path.write_text("", encoding="utf-8")

        from pytvt.netsdk.loader import _find_lib

        assert _find_lib(str(sdk_root)) == str(lib_path)
