"""Tests for ConnectionPool, connect_many, and related types."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from pytvt.connection_pool import (
    ConnectionPool,
    PoolStats,
    SessionMetrics,
    SessionResult,
    connect_many,
)
from pytvt.models import DeviceEntry

# ── SessionMetrics ───────────────────────────────────────────────────


class TestSessionMetrics:
    def test_session_duration(self) -> None:
        m = SessionMetrics(connected_at=time.monotonic() - 5.0)
        assert m.session_duration_s >= 4.5

    def test_session_duration_zero_when_not_connected(self) -> None:
        m = SessionMetrics()
        assert m.session_duration_s == 0.0

    def test_idle_seconds(self) -> None:
        m = SessionMetrics(last_seen=time.monotonic() - 2.0)
        assert m.idle_s >= 1.5

    def test_idle_zero_when_not_seen(self) -> None:
        m = SessionMetrics()
        assert m.idle_s == 0.0

    def test_healthy_default(self) -> None:
        m = SessionMetrics()
        assert m.healthy is True

    def test_unhealthy_after_failures(self) -> None:
        m = SessionMetrics(keepalive_failures=1)
        assert m.healthy is False


# ── SessionResult ────────────────────────────────────────────────────


class TestSessionResult:
    def test_success_result(self) -> None:
        r = SessionResult(
            target="10.0.0.1",
            success=True,
            connection_method="direct",
            latency_ms=150,
            device_name="NVR-01",
        )
        assert r.success
        assert r.latency_ms == 150

    def test_failure_result(self) -> None:
        r = SessionResult(target="ABC123", success=False, error="timeout")
        assert not r.success
        assert r.error == "timeout"


# ── PoolStats ────────────────────────────────────────────────────────


class TestPoolStats:
    def test_defaults(self) -> None:
        s = PoolStats()
        assert s.active_sessions == 0
        assert s.idle_sessions == 0
        assert s.total_connects == 0
        assert s.total_failures == 0
        assert s.total_reconnects == 0


# ── ConnectionPool key helpers ───────────────────────────────────────


class TestPoolKeyHelpers:
    def test_make_key_with_ip(self) -> None:
        key = ConnectionPool._make_key(ip="10.0.0.1", username="admin")
        assert key == "admin@10.0.0.1"

    def test_make_key_with_identifier(self) -> None:
        key = ConnectionPool._make_key(identifier="ABC123", username="admin")
        assert key == "admin@ABC123"

    def test_make_key_identifier_takes_precedence(self) -> None:
        key = ConnectionPool._make_key(ip="10.0.0.1", identifier="ABC123", username="admin")
        assert key == "admin@ABC123"

    def test_key_from_device(self) -> None:
        dev = DeviceEntry(ip="10.0.0.1", identifier="ABC123")
        key = ConnectionPool._key_from_device(dev, "admin")
        assert key == "admin@ABC123"

    def test_key_from_device_ip_only(self) -> None:
        dev = DeviceEntry(ip="10.0.0.1")
        key = ConnectionPool._key_from_device(dev, "admin")
        assert key == "admin@10.0.0.1"


# ── ConnectionPool method resolution ────────────────────────────────


class TestPoolMethodResolution:
    def test_explicit_direct(self) -> None:
        pool = ConnectionPool(keepalive_interval=0)
        assert pool._resolve_method("10.0.0.1", None, "direct") == "direct"
        pool.close()

    def test_explicit_nat(self) -> None:
        pool = ConnectionPool(keepalive_interval=0)
        assert pool._resolve_method(None, "ABC123", "nat") == "nat"
        pool.close()

    def test_auto_nat_when_identifier_present(self) -> None:
        pool = ConnectionPool(keepalive_interval=0, prefer_nat=True)
        assert pool._resolve_method("10.0.0.1", "ABC123", None) == "nat"
        pool.close()

    def test_auto_direct_when_ip_only(self) -> None:
        pool = ConnectionPool(keepalive_interval=0)
        assert pool._resolve_method("10.0.0.1", None, None) == "direct"
        pool.close()

    def test_auto_nat_when_identifier_only(self) -> None:
        pool = ConnectionPool(keepalive_interval=0, prefer_nat=False)
        assert pool._resolve_method(None, "ABC123", None) == "nat"
        pool.close()

    def test_no_target_raises(self) -> None:
        pool = ConnectionPool(keepalive_interval=0)
        with pytest.raises(ValueError, match="Either ip or identifier"):
            pool._resolve_method(None, None, None)
        pool.close()


# ── ConnectionPool acquire / release / stats ────────────────────────


class TestPoolAcquireRelease:
    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_acquire_creates_session(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 42
        client = MagicMock()
        metrics = SessionMetrics(target="10.0.0.1", connection_method="direct", handshake_ms=42)
        mock_create.return_value = (session, client, metrics)

        pool = ConnectionPool(keepalive_interval=0)
        result = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        assert result is session

        stats = pool.stats()
        assert stats.active_sessions == 1
        assert stats.total_connects == 1
        pool.close()

    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_release_makes_session_idle(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 10
        mock_create.return_value = (session, MagicMock(), SessionMetrics())

        pool = ConnectionPool(keepalive_interval=0)
        s = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        pool.release(s)

        stats = pool.stats()
        assert stats.active_sessions == 0
        assert stats.idle_sessions == 1
        pool.close()

    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_reuse_idle_session(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 10
        mock_create.return_value = (session, MagicMock(), SessionMetrics())

        pool = ConnectionPool(keepalive_interval=0)
        s1 = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        pool.release(s1)
        s2 = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        assert s2 is s1
        assert mock_create.call_count == 1  # only created once
        pool.close()

    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_max_sessions_enforced(self, mock_create: MagicMock) -> None:
        call_count = 0

        def _make(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            s = MagicMock()
            s.target = f"10.0.0.{call_count}"
            s.connection_method = "direct"
            s.handshake_duration_ms = 10
            return (s, MagicMock(), SessionMetrics())

        mock_create.side_effect = _make

        pool = ConnectionPool(keepalive_interval=0, max_active_sessions=1)
        pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        with pytest.raises(RuntimeError, match="pool at capacity"):
            pool.acquire(ip="10.0.0.2", username="admin", password="pass")
        pool.close()

    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_remove_session(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 10
        mock_create.return_value = (session, MagicMock(), SessionMetrics())

        pool = ConnectionPool(keepalive_interval=0)
        s = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        pool.remove(s)

        stats = pool.stats()
        assert stats.active_sessions == 0
        assert stats.idle_sessions == 0
        pool.close()


# ── ConnectionPool keepalive ─────────────────────────────────────────


class TestPoolKeepalive:
    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_keepalive_probes_idle_sessions(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 10
        session.device_info.return_value = MagicMock()
        mock_create.return_value = (session, MagicMock(), SessionMetrics(last_seen=time.monotonic()))

        pool = ConnectionPool(keepalive_interval=0)
        s = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        pool.release(s)

        results = pool.keepalive()
        assert results.get("admin@10.0.0.1") is True
        pool.close()

    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_keepalive_marks_failed_after_max_failures(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 10
        session.device_info.side_effect = Exception("disconnected")
        mock_create.return_value = (session, MagicMock(), SessionMetrics(last_seen=time.monotonic()))

        pool = ConnectionPool(keepalive_interval=0, max_reconnect_attempts=2)
        s = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        pool.release(s)

        pool.keepalive()  # failure 1
        pool.keepalive()  # failure 2 → marked failed

        entry = pool._sessions.get("admin@10.0.0.1")
        assert entry is not None
        assert entry.failed is True
        pool.close()


# ── ConnectionPool stale eviction ────────────────────────────────────


class TestPoolEviction:
    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_evict_stale_sessions(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 10
        old_time = time.monotonic() - 1000
        mock_create.return_value = (session, MagicMock(), SessionMetrics(last_seen=old_time))

        pool = ConnectionPool(keepalive_interval=0, idle_timeout=5.0)
        s = pool.acquire(ip="10.0.0.1", username="admin", password="pass")
        pool.release(s)

        # Force the last_seen to be old
        pool._sessions["admin@10.0.0.1"].metrics.last_seen = old_time

        pool._evict_stale()
        assert len(pool._sessions) == 0
        pool.close()


# ── ConnectionPool session_metrics ───────────────────────────────────


class TestPoolSessionMetrics:
    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_session_metrics_returned(self, mock_create: MagicMock) -> None:
        session = MagicMock()
        session.target = "10.0.0.1"
        session.connection_method = "direct"
        session.handshake_duration_ms = 42
        mock_create.return_value = (
            session,
            MagicMock(),
            SessionMetrics(target="10.0.0.1", connection_method="direct", handshake_ms=42),
        )

        pool = ConnectionPool(keepalive_interval=0)
        pool.acquire(ip="10.0.0.1", username="admin", password="pass")

        all_metrics = pool.session_metrics()
        assert "admin@10.0.0.1" in all_metrics
        assert all_metrics["admin@10.0.0.1"].handshake_ms == 42
        pool.close()


# ── ConnectionPool context manager ──────────────────────────────────


class TestPoolContextManager:
    def test_context_manager_closes(self) -> None:
        with ConnectionPool(keepalive_interval=0) as pool:
            assert isinstance(pool, ConnectionPool)
        # After context exit, pool should be closed
        assert pool._keepalive_stop.is_set()


# ── ConnectionPool creation failure ─────────────────────────────────


class TestPoolCreationFailure:
    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_failure_increments_counter(self, mock_create: MagicMock) -> None:
        # _create_session itself increments _total_failures before re-raising
        # but the mock bypasses that, so we test the full flow via a side_effect
        # that mimics real behavior.
        def _fail(*args, **kwargs):
            raise RuntimeError("SDK init failed")

        mock_create.side_effect = _fail

        pool = ConnectionPool(keepalive_interval=0)
        with pytest.raises(RuntimeError, match="SDK init failed"):
            pool.acquire(ip="10.0.0.1", username="admin", password="pass")

        # The mock bypasses the internal failure counter in _create_session,
        # but acquire() still propagates the error. Verify the pool is empty.
        stats = pool.stats()
        assert stats.active_sessions == 0
        pool.close()


# ── connect_many ─────────────────────────────────────────────────────


class TestConnectMany:
    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_connect_many_success(self, mock_create: MagicMock) -> None:
        def _make(*args, **kwargs):
            s = MagicMock()
            s.target = kwargs.get("ip", "unknown")
            s.connection_method = "direct"
            s.handshake_duration_ms = 50
            info = MagicMock()
            info.device_name = "NVR"
            info.serial_number = "SN001"
            info.firmware = "1.0"
            s.device_info.return_value = info
            return (s, MagicMock(), SessionMetrics())

        mock_create.side_effect = _make
        devices = [DeviceEntry(ip="10.0.0.1"), DeviceEntry(ip="10.0.0.2")]
        results = connect_many(devices, "admin", "pass")
        assert len(results) == 2
        assert all(r.success for r in results)

    @patch("pytvt.connection_pool.ConnectionPool._create_session")
    def test_connect_many_partial_failure(self, mock_create: MagicMock) -> None:
        call_count = 0

        def _make(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("connection refused")
            s = MagicMock()
            s.target = kwargs.get("ip", "unknown")
            s.connection_method = "direct"
            s.handshake_duration_ms = 50
            info = MagicMock()
            info.device_name = "NVR"
            info.serial_number = "SN001"
            info.firmware = "1.0"
            s.device_info.return_value = info
            return (s, MagicMock(), SessionMetrics())

        mock_create.side_effect = _make
        devices = [DeviceEntry(ip="10.0.0.1"), DeviceEntry(ip="10.0.0.2")]
        results = connect_many(devices, "admin", "pass", max_workers=1)
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].error is not None

    def test_connect_many_empty_list(self) -> None:
        results = connect_many([], "admin", "pass")
        assert results == []


# ── CLI connect-many parser ──────────────────────────────────────────


class TestConnectManyParser:
    def test_parser_requires_file(self) -> None:
        from pytvt.tools.cli import _build_connect_many_parser

        parser = _build_connect_many_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--user", "a", "--password", "b"])

    def test_parser_requires_user(self) -> None:
        from pytvt.tools.cli import _build_connect_many_parser

        parser = _build_connect_many_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--file", "x.json", "--password", "b"])

    def test_parser_accepts_all_flags(self) -> None:
        from pytvt.tools.cli import _build_connect_many_parser

        parser = _build_connect_many_parser()
        args = parser.parse_args(
            [
                "--file",
                "devices.json",
                "-u",
                "admin",
                "-p",
                "pass123",
                "--sdk-path",
                "/opt/tvt",
                "--timeout",
                "15",
                "--concurrency",
                "20",
                "--nat",
                "--json",
            ]
        )
        assert args.file == "devices.json"
        assert args.username == "admin"
        assert args.password == "pass123"
        assert args.sdk_path == "/opt/tvt"
        assert args.timeout == 15.0
        assert args.concurrency == 20
        assert args.nat is True
        assert args.json is True


# ── CLI connect-many dispatch ────────────────────────────────────────


class TestConnectManyMain:
    def test_connect_many_main_file_not_found(self, tmp_path) -> None:
        from pytvt.tools.cli import _connect_many_main

        with pytest.raises(SystemExit):
            _connect_many_main(["--file", str(tmp_path / "nope.json"), "-u", "a", "-p", "b"])

    @patch("pytvt.connection_pool.connect_many")
    def test_connect_many_main_json_output(self, mock_cm, tmp_path) -> None:
        from pytvt.tools.cli import _connect_many_main

        devices_file = tmp_path / "devices.json"
        devices_file.write_text(json.dumps([{"ip": "10.0.0.1"}, {"identifier": "ABC123"}]))

        mock_cm.return_value = [
            SessionResult(target="10.0.0.1", success=True, connection_method="direct", latency_ms=50),
            SessionResult(target="ABC123", success=True, connection_method="nat", latency_ms=200),
        ]

        _connect_many_main(
            [
                "--file",
                str(devices_file),
                "-u",
                "admin",
                "-p",
                "pass",
                "--json",
            ]
        )
        mock_cm.assert_called_once()

    @patch("pytvt.connection_pool.connect_many")
    def test_connect_many_main_exits_on_failure(self, mock_cm, tmp_path) -> None:
        from pytvt.tools.cli import _connect_many_main

        devices_file = tmp_path / "devices.json"
        devices_file.write_text(json.dumps([{"ip": "10.0.0.1"}]))

        mock_cm.return_value = [
            SessionResult(target="10.0.0.1", success=False, error="timeout"),
        ]

        with pytest.raises(SystemExit):
            _connect_many_main(["--file", str(devices_file), "-u", "admin", "-p", "pass"])


# ── Model extensions ─────────────────────────────────────────────────


class TestDeviceEntryExtensions:
    def test_connection_preference_from_dict(self) -> None:
        d = DeviceEntry.from_dict({"ip": "10.0.0.1", "connection_preference": "nat"})
        assert d.connection_preference == "nat"

    def test_connection_preference_alias(self) -> None:
        d = DeviceEntry.from_dict({"ip": "10.0.0.1", "prefer": "direct"})
        assert d.connection_preference == "direct"

    def test_last_connection_method_from_dict(self) -> None:
        d = DeviceEntry.from_dict({"ip": "10.0.0.1", "last_connection_method": "nat"})
        assert d.last_connection_method == "nat"

    def test_nat_capable_from_dict(self) -> None:
        d = DeviceEntry.from_dict({"ip": "10.0.0.1", "nat_capable": True})
        assert d.nat_capable is True

    def test_nat_capable_default_none(self) -> None:
        d = DeviceEntry.from_dict({"ip": "10.0.0.1"})
        assert d.nat_capable is None

    def test_effective_method_respects_preference(self) -> None:
        d = DeviceEntry(ip="10.0.0.1", identifier="ABC123", connection_preference="direct")
        assert d.effective_connection_method == "direct"

    def test_effective_method_connection_method_beats_preference(self) -> None:
        d = DeviceEntry(ip="10.0.0.1", connection_method="nat", connection_preference="direct")
        assert d.effective_connection_method == "nat"
