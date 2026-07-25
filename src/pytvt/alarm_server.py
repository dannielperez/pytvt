"""Minimal receiver for TVT NVR "Alarm Server" pushes.

The NVR's *Alarm Server* trigger action (Function Panel → Event Notification →
Alarm Server) opens a TCP connection to a host:port you run and pushes alarm
frames — including AI/face events when a face detection/recognition rule links
the Alarm Server action. This module binds that port and hands each raw frame to
:func:`~pytvt.alarm_protocol.parse_alarm_frame`, then delivers a typed
:class:`~pytvt.alarm_protocol.ParsedAlarmFrame` to your callback.

The wire *parsing* lives in :mod:`pytvt.alarm_protocol` (transport-agnostic by
design); this module owns only the *transport* — a small bounded threaded TCP
server. Rate-limiting, auth, dedup and persistence remain the caller's concern.

Example::

    from pytvt.alarm_server import AlarmServer

    def on_event(event, addr):
        if event.event_type == "face":
            print("face event from", addr, "chl", event.channel)

    with AlarmServer("0.0.0.0", 9010, on_event) as srv:
        srv.serve_forever()
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Callable

from .alarm_protocol import MAX_FRAME_SIZE, ParsedAlarmFrame, parse_alarm_frame

__all__ = [
    "DEFAULT_MAX_CONNECTIONS",
    "AlarmServer",
    "AlarmServerCapacityError",
    "AlarmCallback",
]

# (parsed frame, source (host, port)) -> None
AlarmCallback = Callable[[ParsedAlarmFrame, tuple[str, int]], None]

# One NVR push is a single frame; cap the per-connection read so a wedged or
# hostile peer can't stream unbounded bytes into memory.
_MAX_CONN_BYTES = MAX_FRAME_SIZE * 4
DEFAULT_MAX_CONNECTIONS = 32


class AlarmServerCapacityError(RuntimeError):
    """Raised through ``on_error`` when the listener is at its connection cap."""


class AlarmServer:
    """A bounded threaded TCP listener for TVT alarm-server pushes.

    Each accepted connection is read (up to a hard cap), parsed with
    :func:`parse_alarm_frame`, and the result passed to ``callback``. Callback
    exceptions are swallowed per-connection so one bad handler can't take the
    server down; pass ``on_error`` to observe them.
    """

    def __init__(
        self,
        host: str,
        port: int,
        callback: AlarmCallback,
        *,
        backlog: int = 32,
        recv_timeout: float = 10.0,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        on_error: Callable[[BaseException, tuple[str, int] | None], None] | None = None,
    ) -> None:
        if isinstance(max_connections, bool) or not isinstance(max_connections, int) or max_connections <= 0:
            raise ValueError("max_connections must be a positive integer")
        self.host = host
        self.port = port
        self._callback = callback
        self._backlog = backlog
        self._recv_timeout = recv_timeout
        self._on_error = on_error
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self._max_connections = max_connections
        self._threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> AlarmServer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        """Bind and listen (does not block)."""
        if self._sock is not None:
            raise RuntimeError("AlarmServer already started")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(self._backlog)
        sock.settimeout(0.5)  # so serve_forever can observe the stop flag
        self._sock = sock

    def serve_forever(self) -> None:
        """Accept and dispatch connections until :meth:`close` is called."""
        if self._sock is None:
            self.start()
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not self._connection_slots.acquire(blocking=False):
                with contextlib.suppress(OSError):
                    conn.close()
                self._report_error(
                    AlarmServerCapacityError(
                        f"AlarmServer connection limit reached ({self._max_connections})",
                    ),
                    addr,
                )
                continue
            t = threading.Thread(target=self._handle, args=(conn, addr), daemon=True)
            try:
                # Publish and start atomically with respect to close(): joining a
                # registered-but-not-yet-started Thread raises RuntimeError.
                with self._threads_lock:
                    self._threads.add(t)
                    try:
                        t.start()
                    except BaseException:
                        self._threads.discard(t)
                        raise
            except BaseException as exc:
                self._connection_slots.release()
                with contextlib.suppress(OSError):
                    conn.close()
                self._report_error(exc, addr)

    def close(self) -> None:
        """Stop accepting and release the socket."""
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        with self._threads_lock:
            threads = tuple(self._threads)
        deadline = time.monotonic() + 1.0
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

    # -- per-connection ----------------------------------------------------

    def _handle(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        try:
            conn.settimeout(self._recv_timeout)
            chunks: list[bytes] = []
            total = 0
            while total < _MAX_CONN_BYTES:
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            data = b"".join(chunks)
            if not data:
                return
            event = parse_alarm_frame(data)
            try:
                self._callback(event, addr)
            except BaseException as exc:
                self._report_error(exc, addr)
        except BaseException as exc:
            self._report_error(exc, addr)
        finally:
            with contextlib.suppress(OSError):
                conn.close()
            with self._threads_lock:
                self._threads.discard(threading.current_thread())
            self._connection_slots.release()

    def _report_error(
        self,
        exc: BaseException,
        addr: tuple[str, int] | None,
    ) -> None:
        if self._on_error is None:
            return
        with contextlib.suppress(BaseException):
            self._on_error(exc, addr)
