"""NAT-aware connection pool for managing many concurrent device sessions.

Provides :class:`ConnectionPool` — a thread-safe pool that:

* Reuses active NAT / direct sessions when possible
* Enforces ``max_active_sessions``
* Monitors session health via periodic keepalive probes
* Reconnects dropped sessions with configurable backoff
* Tracks per-session metrics (latency, success rate, duration)

Bulk helper :func:`connect_many` wraps the pool with a
:class:`~concurrent.futures.ThreadPoolExecutor` and returns structured
:class:`SessionResult` objects.

Usage::

    from pytvt.connection_pool import ConnectionPool, connect_many

    pool = ConnectionPool(sdk_path="/opt/tvt")

    session = pool.acquire("10.0.0.1", "admin", "pass")
    info = session.device_info()
    pool.release(session)

    results = connect_many(devices, "admin", "pass", sdk_path="/opt/tvt")
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from .models import DeviceEntry

logger = logging.getLogger(__name__)


# ── Metrics ─────────────────────────────────────────────────────────


@dataclass
class SessionMetrics:
    """Per-session observable metrics."""

    target: str = ""
    connection_method: str = ""
    handshake_ms: int = 0
    connected_at: float = 0.0
    last_seen: float = 0.0
    keepalive_count: int = 0
    keepalive_failures: int = 0
    reconnect_count: int = 0

    @property
    def session_duration_s(self) -> float:
        """Seconds since connection was established."""
        if not self.connected_at:
            return 0.0
        return time.monotonic() - self.connected_at

    @property
    def idle_s(self) -> float:
        """Seconds since last successful interaction."""
        if not self.last_seen:
            return 0.0
        return time.monotonic() - self.last_seen

    @property
    def healthy(self) -> bool:
        """True when last keepalive succeeded."""
        return self.keepalive_failures == 0


@dataclass
class SessionResult:
    """Outcome of connecting to a single device in a bulk operation."""

    target: str
    success: bool
    connection_method: str = ""
    latency_ms: int = 0
    error: str | None = None
    device_name: str = ""
    serial_number: str = ""
    firmware: str = ""


@dataclass
class PoolStats:
    """Aggregate pool statistics."""

    active_sessions: int = 0
    idle_sessions: int = 0
    total_connects: int = 0
    total_failures: int = 0
    total_reconnects: int = 0


# ── Managed session entry (internal) ────────────────────────────────


@dataclass
class _PoolEntry:
    """Internal state for one pooled session."""

    key: str
    session: object  # DeviceSession
    client: object  # NetSdkClient
    metrics: SessionMetrics = field(default_factory=SessionMetrics)
    in_use: bool = False
    failed: bool = False


# ── Connection pool ─────────────────────────────────────────────────


class ConnectionPool:
    """Thread-safe pool of reusable device sessions.

    Args:
        sdk_path: Vendor SDK path forwarded to :class:`NetSdkClient`.
        max_active_sessions: Hard limit on concurrent sessions (0 = unlimited).
        idle_timeout: Seconds before an idle session is considered stale.
        reconnect_backoff: Base seconds between reconnection attempts.
        max_reconnect_attempts: Give up after this many consecutive reconnect
            failures for a single device.
        keepalive_interval: Seconds between background keepalive probes.  Set
            ``0`` to disable automatic keepalive (you can still call
            :meth:`keepalive` manually).
        connect_timeout: Per-device connection timeout in seconds.
        prefer_nat: When ``True``, devices with both IP and identifier will
            default to NAT.
    """

    def __init__(
        self,
        *,
        sdk_path: str | None = None,
        max_active_sessions: int = 0,
        idle_timeout: float = 300.0,
        reconnect_backoff: float = 5.0,
        max_reconnect_attempts: int = 3,
        keepalive_interval: float = 60.0,
        connect_timeout: float = 10.0,
        prefer_nat: bool = True,
    ) -> None:
        self._sdk_path = sdk_path
        self._max_active = max_active_sessions
        self._idle_timeout = idle_timeout
        self._reconnect_backoff = reconnect_backoff
        self._max_reconnect_attempts = max_reconnect_attempts
        self._keepalive_interval = keepalive_interval
        self._connect_timeout = connect_timeout
        self._prefer_nat = prefer_nat

        self._lock = threading.Lock()
        self._sessions: dict[str, _PoolEntry] = {}
        self._total_connects = 0
        self._total_failures = 0
        self._total_reconnects = 0

        # Keepalive background thread
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        if keepalive_interval > 0:
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop,
                daemon=True,
                name="pytvt-pool-keepalive",
            )
            self._keepalive_thread.start()

    # ── Session key helpers ──────────────────────────────────────

    @staticmethod
    def _make_key(
        *,
        ip: str | None = None,
        identifier: str | None = None,
        username: str = "",
    ) -> str:
        target = (identifier or ip or "").strip()
        return f"{username}@{target}"

    @staticmethod
    def _key_from_device(device: DeviceEntry, username: str) -> str:
        target = device.identifier or device.ip
        return f"{username}@{target}"

    # ── Resolve connection method ────────────────────────────────

    def _resolve_method(
        self,
        ip: str | None,
        identifier: str | None,
        method: str | None,
    ) -> Literal["direct", "nat"]:
        m = (method or "").strip().lower()
        if m in {"direct", "nat"}:
            return m  # type: ignore[return-value]
        if identifier and self._prefer_nat:
            return "nat"
        if ip:
            return "direct"
        if identifier:
            return "nat"
        raise ValueError("Either ip or identifier is required")

    # ── Acquire / release ────────────────────────────────────────

    def acquire(
        self,
        ip: str | None = None,
        username: str = "admin",
        password: str = "",
        *,
        port: int = 6036,
        identifier: str | None = None,
        connection_method: str | None = None,
        nat_server: str | None = None,
        nat_port: int | None = None,
        nat_type: str | int = "nat20",
    ) -> object:
        """Get or create a session.  Returns a ``DeviceSession``.

        If an idle session to the same target+user exists, it is reused.
        Otherwise a new connection is established.

        Raises:
            RuntimeError: If the pool is at capacity.
        """
        method = self._resolve_method(ip, identifier, connection_method)
        key = self._make_key(ip=ip, identifier=identifier, username=username)

        with self._lock:
            entry = self._sessions.get(key)
            if entry and not entry.in_use and not entry.failed:
                entry.in_use = True
                entry.metrics.last_seen = time.monotonic()
                logger.debug("Pool reusing session %s", key)
                return entry.session

            if self._max_active and self._active_count() >= self._max_active:
                raise RuntimeError(
                    f"Connection pool at capacity ({self._max_active}). "
                    "Release idle sessions or increase max_active_sessions."
                )

        # Create outside lock to avoid holding it during network I/O
        session, client, metrics = self._create_session(
            ip=ip,
            username=username,
            password=password,
            port=port,
            identifier=identifier,
            method=method,
            nat_server=nat_server,
            nat_port=nat_port,
            nat_type=nat_type,
        )

        entry = _PoolEntry(
            key=key,
            session=session,
            client=client,
            metrics=metrics,
            in_use=True,
        )

        with self._lock:
            old = self._sessions.get(key)
            if old:
                self._close_entry(old)
            self._sessions[key] = entry
            self._total_connects += 1

        return session

    def release(self, session: object) -> None:
        """Return a session to the pool for reuse."""
        with self._lock:
            for entry in self._sessions.values():
                if entry.session is session:
                    entry.in_use = False
                    entry.metrics.last_seen = time.monotonic()
                    logger.debug("Pool released session %s", entry.key)
                    return
        logger.warning("Released session not found in pool")

    def remove(self, session: object) -> None:
        """Remove and close a session from the pool."""
        with self._lock:
            to_remove: str | None = None
            for key, entry in self._sessions.items():
                if entry.session is session:
                    to_remove = key
                    break
            if to_remove:
                entry = self._sessions.pop(to_remove)
                self._close_entry(entry)

    # ── Internal session creation ────────────────────────────────

    def _create_session(
        self,
        *,
        ip: str | None,
        username: str,
        password: str,
        port: int,
        identifier: str | None,
        method: str,
        nat_server: str | None,
        nat_port: int | None,
        nat_type: str | int,
    ) -> tuple[object, object, SessionMetrics]:
        """Create a new SDK session.  Returns (session, client, metrics)."""
        from .netsdk.client import NetSdkClient

        client = NetSdkClient(sdk_path=self._sdk_path)
        try:
            session = client.connect(
                method=method,  # type: ignore[arg-type]
                username=username,
                password=password,
                host=ip,
                port=port,
                identifier=identifier,
                timeout=self._connect_timeout,
                nat_server=nat_server,
                nat_port=nat_port,
                connect_type=nat_type,
                fallback_to_direct=bool(ip),
            )
        except Exception:
            client.cleanup()
            with self._lock:
                self._total_failures += 1
            raise

        now = time.monotonic()
        metrics = SessionMetrics(
            target=session.target,
            connection_method=session.connection_method,
            handshake_ms=session.handshake_duration_ms or 0,
            connected_at=now,
            last_seen=now,
        )
        return session, client, metrics

    # ── Keepalive ────────────────────────────────────────────────

    def keepalive(self) -> dict[str, bool]:
        """Probe all idle sessions.  Returns ``{key: healthy}``."""
        results: dict[str, bool] = {}
        with self._lock:
            targets = [
                (key, entry) for key, entry in self._sessions.items() if not entry.in_use and not entry.failed
            ]

        for key, entry in targets:
            ok = self._probe_session(entry)
            results[key] = ok
            with self._lock:
                entry.metrics.keepalive_count += 1
                if ok:
                    entry.metrics.keepalive_failures = 0
                    entry.metrics.last_seen = time.monotonic()
                else:
                    entry.metrics.keepalive_failures += 1
                    if entry.metrics.keepalive_failures >= self._max_reconnect_attempts:
                        entry.failed = True
                        logger.warning(
                            "Session %s marked failed after %d keepalive failures",
                            key,
                            entry.metrics.keepalive_failures,
                        )
        return results

    def _probe_session(self, entry: _PoolEntry) -> bool:
        """Send a lightweight probe to verify the session is alive."""
        try:
            entry.session.device_info()  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    def _keepalive_loop(self) -> None:
        """Background thread that periodically probes idle sessions."""
        while not self._keepalive_stop.wait(self._keepalive_interval):
            try:
                self.keepalive()
                self._evict_stale()
            except Exception:
                logger.debug("Keepalive loop error", exc_info=True)

    # ── Eviction / reconnection ──────────────────────────────────

    def _evict_stale(self) -> None:
        """Remove sessions that have been idle longer than *idle_timeout*."""
        now = time.monotonic()
        with self._lock:
            stale = [
                key
                for key, entry in self._sessions.items()
                if not entry.in_use and (now - entry.metrics.last_seen) > self._idle_timeout
            ]
            for key in stale:
                entry = self._sessions.pop(key)
                self._close_entry(entry)
                logger.info("Evicted stale session %s (idle %.0fs)", key, now - entry.metrics.last_seen)

    def reconnect(self, key: str, username: str, password: str, **kwargs: object) -> object | None:
        """Attempt to reconnect a failed/stale session.

        Returns the new ``DeviceSession`` on success, ``None`` on failure.
        """
        with self._lock:
            entry = self._sessions.get(key)
            if entry:
                self._close_entry(entry)
                del self._sessions[key]

        for attempt in range(1, self._max_reconnect_attempts + 1):
            try:
                session = self.acquire(username=username, password=password, **kwargs)  # type: ignore[arg-type]
                with self._lock:
                    self._total_reconnects += 1
                    new_entry = self._sessions.get(key)
                    if new_entry:
                        new_entry.metrics.reconnect_count += 1
                logger.info("Reconnected %s on attempt %d", key, attempt)
                return session
            except Exception:
                if attempt < self._max_reconnect_attempts:
                    time.sleep(self._reconnect_backoff * attempt)
        return None

    # ── Cleanup helpers ──────────────────────────────────────────

    @staticmethod
    def _close_entry(entry: _PoolEntry) -> None:
        with suppress(Exception):
            entry.session.logout()  # type: ignore[attr-defined]
        with suppress(Exception):
            entry.client.cleanup()  # type: ignore[attr-defined]

    def _active_count(self) -> int:
        return sum(1 for e in self._sessions.values() if not e.failed)

    # ── Pool stats ───────────────────────────────────────────────

    def stats(self) -> PoolStats:
        """Return current pool statistics."""
        with self._lock:
            active = sum(1 for e in self._sessions.values() if e.in_use)
            idle = sum(1 for e in self._sessions.values() if not e.in_use and not e.failed)
            return PoolStats(
                active_sessions=active,
                idle_sessions=idle,
                total_connects=self._total_connects,
                total_failures=self._total_failures,
                total_reconnects=self._total_reconnects,
            )

    def session_metrics(self) -> dict[str, SessionMetrics]:
        """Return metrics for all tracked sessions."""
        with self._lock:
            return {key: entry.metrics for key, entry in self._sessions.items()}

    # ── Lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        """Shut down the pool, closing all sessions."""
        self._keepalive_stop.set()
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=5)

        with self._lock:
            for entry in self._sessions.values():
                self._close_entry(entry)
            self._sessions.clear()

        logger.info("Connection pool closed")

    def __enter__(self) -> ConnectionPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ── Bulk connection helper ──────────────────────────────────────────


def connect_many(
    devices: list[DeviceEntry],
    username: str,
    password: str,
    *,
    sdk_path: str | None = None,
    max_workers: int = 10,
    timeout: float = 10.0,
    prefer_nat: bool = True,
) -> list[SessionResult]:
    """Connect to multiple devices concurrently and return structured results.

    Each device is connected in a separate thread.  On success the pool entry
    is created but the session is immediately released back to idle.

    Args:
        devices: Device entries to connect to.
        username: Login username for all devices.
        password: Login password for all devices.
        sdk_path: Vendor SDK path.
        max_workers: Thread-pool concurrency.
        timeout: Per-device connection timeout in seconds.
        prefer_nat: Prefer NAT when a device has an identifier.

    Returns:
        One :class:`SessionResult` per device, in the same order as *devices*.
    """
    pool = ConnectionPool(
        sdk_path=sdk_path,
        connect_timeout=timeout,
        prefer_nat=prefer_nat,
        keepalive_interval=0,  # no keepalive for one-shot bulk
    )
    results: dict[int, SessionResult] = {}

    def _connect_one(idx: int, device: DeviceEntry) -> SessionResult:
        target = device.connect_target
        method = device.effective_connection_method
        started = time.perf_counter()
        try:
            session = pool.acquire(
                ip=device.ip or None,
                username=username,
                password=password,
                port=device.port or 6036,
                identifier=device.identifier or None,
                connection_method=method,
                nat_server=device.nat_server or None,
                nat_port=device.nat_port or None,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)

            # Fetch basic info
            try:
                info = session.device_info()  # type: ignore[attr-defined]
                result = SessionResult(
                    target=target,
                    success=True,
                    connection_method=session.connection_method,  # type: ignore[attr-defined]
                    latency_ms=elapsed_ms,
                    device_name=info.device_name,
                    serial_number=info.serial_number,
                    firmware=info.firmware,
                )
            except Exception:
                result = SessionResult(
                    target=target,
                    success=True,
                    connection_method=session.connection_method,  # type: ignore[attr-defined]
                    latency_ms=elapsed_ms,
                )

            pool.release(session)
            return result
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return SessionResult(
                target=target,
                success=False,
                connection_method=method,
                latency_ms=elapsed_ms,
                error=str(exc),
            )

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_connect_one, i, d): i for i, d in enumerate(devices)}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
    finally:
        pool.close()

    return [results[i] for i in range(len(devices))]
