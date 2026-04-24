"""Tests for pytvt.config — config loading, precedence, env overrides."""

from __future__ import annotations

import json

import pytest

from pytvt.config import load_config


class TestLoadConfig:
    def test_defaults_without_file(self, monkeypatch):
        # Clear any env vars that might leak from .env
        for key in (
            "TVT_USERNAME",
            "TVT_PASSWORD",
            "TVT_PORT",
            "TVT_TIMEOUT",
            "TVT_MAX_CHANNELS",
            "TVT_CONCURRENCY",
            "TVT_SDK_PATH",
            "PYTVT_NETSDK_LIB",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = load_config(None)
        assert cfg.username == "admin"
        assert cfg.password == ""
        assert cfg.port == 6036
        assert cfg.timeout == 10
        assert cfg.max_channels == 64
        assert cfg.concurrency == 4
        assert cfg.sdk_path is None
        assert cfg.scan_script is None

    def test_json_file_overrides_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"port": 9999, "timeout": 30}))
        cfg = load_config(str(cfg_file))
        assert cfg.port == 9999
        assert cfg.timeout == 30
        # Defaults preserved for unset fields
        assert cfg.concurrency == 4

    def test_env_overrides_json(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"port": 7000, "username": "file_user"}))
        monkeypatch.setenv("TVT_PORT", "8000")
        monkeypatch.setenv("TVT_USERNAME", "env_user")
        cfg = load_config(str(cfg_file))
        assert cfg.port == 8000  # env wins
        assert cfg.username == "env_user"  # env wins

    def test_env_only(self, monkeypatch):
        monkeypatch.setenv("TVT_PASSWORD", "secret")
        monkeypatch.setenv("TVT_CONCURRENCY", "16")
        monkeypatch.setenv("TVT_SDK_PATH", "/opt/tvt-sdk")
        cfg = load_config(None)
        assert cfg.password == "secret"
        assert cfg.concurrency == 16
        assert cfg.sdk_path == "/opt/tvt-sdk"
        assert cfg.scan_script is None

    def test_nonexistent_file_ignored(self):
        cfg = load_config("/nonexistent/config.json")
        assert cfg.port == 6036  # falls back to defaults

    def test_api_url_from_env(self, monkeypatch):
        monkeypatch.setenv("TVT_API_URL", "http://custom:4000")
        # Need to reimport to pick up module-level env read
        import importlib

        import pytvt.config

        importlib.reload(pytvt.config)
        cfg = pytvt.config.load_config(None)
        assert cfg.api_url == "http://custom:4000"
        # Restore
        monkeypatch.delenv("TVT_API_URL")
        importlib.reload(pytvt.config)

    def test_all_fields_from_json(self, tmp_path, monkeypatch):
        for key in (
            "TVT_USERNAME",
            "TVT_PASSWORD",
            "TVT_PORT",
            "TVT_TIMEOUT",
            "TVT_MAX_CHANNELS",
            "TVT_CONCURRENCY",
            "TVT_SDK_PATH",
            "PYTVT_NETSDK_LIB",
        ):
            monkeypatch.delenv(key, raising=False)
        data = {
            "username": "u",
            "password": "p",
            "port": 1111,
            "timeout": 2,
            "max_channels": 8,
            "concurrency": 1,
            "sdk_path": "/opt/vendor-sdk",
        }
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(data))
        cfg = load_config(str(cfg_file))
        assert cfg.username == "u"
        assert cfg.password == "p"
        assert cfg.port == 1111
        assert cfg.timeout == 2
        assert cfg.max_channels == 8
        assert cfg.concurrency == 1
        assert cfg.sdk_path == "/opt/vendor-sdk"

    def test_legacy_sdk_env_supported(self, monkeypatch):
        monkeypatch.setenv("PYTVT_NETSDK_LIB", "/legacy/path.so")
        cfg = load_config(None)
        assert cfg.sdk_path == "/legacy/path.so"

    def test_unknown_json_keys_ignored(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"port": 5000, "unknown_key": "value"}))
        cfg = load_config(str(cfg_file))
        assert cfg.port == 5000

    def test_type_casting(self, tmp_path):
        """Config string values are cast to correct types."""
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"port": "8080"}))
        cfg = load_config(str(cfg_file))
        assert cfg.port == 8080
        assert isinstance(cfg.port, int)
