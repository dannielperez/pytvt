"""Lifecycle tests for native smart-subscription plate streams."""

from __future__ import annotations

import ctypes as ct
import threading
from unittest.mock import MagicMock, patch

import pytest

from pytvt.device_sdk import bindings as sdk
from pytvt.device_sdk.client import DeviceSession, NetSdkClient, NetSdkError
from pytvt.device_sdk.constants import SmartEventType
from pytvt.device_sdk.plate_events import PlateSource
from pytvt.device_sdk.types import NET_DVR_SUBSCRIBE_REPLY


@pytest.fixture
def native_lib():
    lib = MagicMock()
    lib.NET_SDK_Init.return_value = True
    lib.NET_SDK_Cleanup.return_value = True
    lib.NET_SDK_SetConnectTime.return_value = True
    lib.NET_SDK_SetReconnect.return_value = True
    lib.NET_SDK_GetSDKVersion.return_value = (1 << 24) | (3 << 16) | 2
    lib.NET_SDK_GetSDKBuildVersion.return_value = 20260116
    lib.NET_SDK_GetLastError.return_value = 27
    lib.NET_SDK_Logout.return_value = True
    lib.NET_SDK_SetSubscribCallBack_V2.return_value = True
    lib.NET_SDK_UnSmartSubscrib.return_value = True
    return lib


@pytest.fixture
def native_client(native_lib):
    with patch("pytvt.device_sdk.client.load_sdk", return_value=native_lib):
        client = NetSdkClient()
        yield client
        client.cleanup()


@pytest.fixture
def native_session(native_lib, native_client):
    sdk.bind(native_lib)
    return DeviceSession(handle=11, client=native_client)


def _fill_reply(reply_pointer, token: bytes) -> None:
    reply = ct.cast(reply_pointer, ct.POINTER(NET_DVR_SUBSCRIBE_REPLY)).contents
    reply.serverAddress = token
    reply.currentTime = 100
    reply.terminationTime = 200


def test_plate_subscription_registers_all_targets_and_closes_in_reverse(native_lib, native_session):
    def subscribe(user_id, command, channel_id, reply_pointer):
        _fill_reply(reply_pointer, f"token-{command}-{channel_id}".encode())
        return True

    native_lib.NET_SDK_SmartSubscrib.side_effect = subscribe
    stream = native_session.subscribe_plate_events(
        [2, 3],
        commands=[SmartEventType.NVR_VEHICLE],
        experimental=True,
    )

    assert [(info.source, info.channel_id) for info in stream.subscriptions] == [
        (PlateSource.NVR, 2),
        (PlateSource.NVR, 3),
    ]
    assert native_lib.NET_SDK_SmartSubscrib.call_count == 2

    stream.close()

    assert stream.closed is True
    assert native_lib.NET_SDK_UnSmartSubscrib.call_count == 2
    calls = native_lib.NET_SDK_UnSmartSubscrib.call_args_list
    assert calls[0].args[2] == 3
    assert calls[1].args[2] == 2
    assert native_session._plate_stream is None


def test_plate_subscription_rolls_back_when_a_later_target_fails(native_lib, native_session):
    attempts = 0

    def subscribe(user_id, command, channel_id, reply_pointer):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return False
        _fill_reply(reply_pointer, b"first-token")
        return True

    native_lib.NET_SDK_SmartSubscrib.side_effect = subscribe

    with pytest.raises(NetSdkError, match="SmartSubscrib"):
        native_session.subscribe_plate_events(
            [2, 3],
            commands=[SmartEventType.NVR_VEHICLE],
            experimental=True,
        )

    native_lib.NET_SDK_UnSmartSubscrib.assert_called_once()
    assert native_session._plate_stream is None


def test_session_logout_closes_plate_subscription_before_logout(native_lib, native_session):
    native_lib.NET_SDK_SmartSubscrib.side_effect = lambda uid, command, channel, reply: (
        _fill_reply(reply, b"token") or True
    )
    stream = native_session.subscribe_plate_events(
        [0],
        commands=[SmartEventType.VEHICLE],
        experimental=True,
    )

    native_session.logout()

    assert stream.closed is True
    native_lib.NET_SDK_UnSmartSubscrib.assert_called_once()
    native_lib.NET_SDK_Logout.assert_called_once_with(11)


def test_session_logout_preserves_handle_when_unsubscribe_needs_retry(native_lib, native_session):
    native_lib.NET_SDK_SmartSubscrib.side_effect = lambda uid, command, channel, reply: (
        _fill_reply(reply, b"token") or True
    )
    stream = native_session.subscribe_plate_events(
        [0],
        commands=[SmartEventType.VEHICLE],
        experimental=True,
    )
    native_lib.NET_SDK_UnSmartSubscrib.return_value = False

    with pytest.raises(NetSdkError, match="UnSmartSubscrib"):
        native_session.logout()

    handle_after_failure = native_session.handle
    logout_calls_after_failure = native_lib.NET_SDK_Logout.call_count
    stream_closed_after_failure = stream.closed

    native_lib.NET_SDK_UnSmartSubscrib.return_value = True
    native_session.logout()

    assert handle_after_failure == 11
    assert logout_calls_after_failure == 0
    assert stream_closed_after_failure is False
    assert stream.closed is True
    native_lib.NET_SDK_Logout.assert_called_once_with(11)


def test_plate_subscription_setup_has_an_aggregate_deadline(native_lib, native_session, monkeypatch):
    class Clock:
        now = 100.0

        def monotonic(self):
            return self.now

    clock = Clock()

    def delayed_subscribe(user_id, command, channel_id, reply_pointer):
        clock.now += 1.0
        _fill_reply(reply_pointer, f"token-{channel_id}".encode())
        return True

    native_lib.NET_SDK_SmartSubscrib.side_effect = delayed_subscribe
    monkeypatch.setattr("pytvt.device_sdk.client.time", clock)

    with pytest.raises(NetSdkError, match="setup deadline"):
        native_session.subscribe_plate_events(
            [0, 1, 2],
            commands=[SmartEventType.VEHICLE],
            setup_timeout=1.5,
            experimental=True,
        )

    assert native_lib.NET_SDK_SmartSubscrib.call_count == 2
    assert native_lib.NET_SDK_UnSmartSubscrib.call_count == 2
    assert native_session._plate_stream is None


@pytest.mark.parametrize("setup_timeout", [0, -1, float("nan"), float("inf"), 301])
def test_plate_subscription_rejects_unbounded_setup_timeout(native_session, setup_timeout):
    with pytest.raises(ValueError, match="setup_timeout"):
        native_session.subscribe_plate_events(
            [0],
            commands=[SmartEventType.VEHICLE],
            setup_timeout=setup_timeout,
            experimental=True,
        )


def test_subscribe_v2_rejects_oversized_payload_before_copy(native_lib, native_client):
    callback = None

    def register(cb, user):
        nonlocal callback
        callback = cb
        return True

    native_lib.NET_SDK_SetSubscribCallBack_V2.side_effect = register
    rejected = []
    native_client.subscribe_v2(
        lambda *args: pytest.fail("oversized callback was dispatched"),
        max_payload_bytes=4,
        on_rejected=rejected.append,
    )

    assert callback is not None
    payload = (ct.c_char * 5).from_buffer_copy(b"12345")
    callback(1, 2, 20, ct.cast(payload, ct.c_void_p), 5, None)
    assert rejected == ["subscription payload length 5 exceeds limit 4"]


def test_subscribe_v2_keeps_callback_reference_when_clear_fails(native_lib, native_client):
    native_lib.NET_SDK_SetSubscribCallBack_V2.return_value = True
    native_client.subscribe_v2(lambda *args: None)
    retained = native_client._subscribe_callback
    native_lib.NET_SDK_SetSubscribCallBack_V2.return_value = False

    with pytest.raises(NetSdkError, match="SetSubscribCallBack_V2"):
        native_client.subscribe_v2(None)

    assert native_client._subscribe_callback is retained


def test_live_plate_subscription_requires_explicit_experimental_opt_in(native_session):
    with pytest.raises(NetSdkError, match="experimental=True"):
        native_session.subscribe_plate_events([0], commands=[SmartEventType.VEHICLE])


def test_failed_unsubscribe_keeps_stream_retryable_and_blocks_replacement(native_lib, native_session):
    native_lib.NET_SDK_SmartSubscrib.side_effect = lambda uid, command, channel, reply: (
        _fill_reply(reply, b"token") or True
    )
    stream = native_session.subscribe_plate_events(
        [0],
        commands=[SmartEventType.VEHICLE],
        experimental=True,
    )
    native_lib.NET_SDK_UnSmartSubscrib.return_value = False

    with pytest.raises(NetSdkError, match="UnSmartSubscrib"):
        stream.close()

    assert stream.closed is False
    assert native_session._plate_stream is stream
    with (
        patch("pytvt.device_sdk.client.load_sdk", return_value=native_lib),
        pytest.raises(
            NetSdkError,
            match="another NetSdkClient",
        ),
    ):
        NetSdkClient()
    with pytest.raises(NetSdkError, match="already owns"):
        native_session.subscribe_plate_events(
            [0],
            commands=[SmartEventType.VEHICLE],
            experimental=True,
        )

    native_lib.NET_SDK_UnSmartSubscrib.return_value = True
    stream.close()
    assert stream.closed is True
    assert native_session._plate_stream is None


def test_raising_rejection_hook_is_contained_inside_callback(native_lib, native_client, caplog):
    callback = None

    def register(cb, user):
        nonlocal callback
        callback = cb
        return True

    def reject(reason):
        raise RuntimeError("metrics sink unavailable")

    native_lib.NET_SDK_SetSubscribCallBack_V2.side_effect = register
    native_client.subscribe_v2(lambda *args: None, max_payload_bytes=4, on_rejected=reject)
    payload = (ct.c_char * 5).from_buffer_copy(b"12345")

    callback(1, 2, 20, ct.cast(payload, ct.c_void_p), 5, None)

    assert "callback handler raised" in caplog.text


def test_process_global_callback_rejects_second_live_client(native_lib, native_client):
    native_client.subscribe_v2(lambda *args: None)

    with (
        patch("pytvt.device_sdk.client.load_sdk", return_value=native_lib),
        pytest.raises(
            NetSdkError,
            match="another NetSdkClient",
        ),
    ):
        NetSdkClient()


def test_callback_clear_waits_for_inflight_handler(native_lib, native_client):
    callback = None
    entered = threading.Event()
    release = threading.Event()
    cleared = threading.Event()

    def register(cb, user):
        nonlocal callback
        if cb:
            callback = cb
        return True

    def handler(*args):
        entered.set()
        assert release.wait(1.0)

    native_lib.NET_SDK_SetSubscribCallBack_V2.side_effect = register
    native_client.subscribe_v2(handler)
    payload = (ct.c_char * 1).from_buffer_copy(b"x")
    callback_thread = threading.Thread(
        target=callback,
        args=(1, 2, 20, ct.cast(payload, ct.c_void_p), 1, None),
    )
    callback_thread.start()
    assert entered.wait(1.0)

    def clear_callback():
        native_client.subscribe_v2(None)
        cleared.set()

    clear_thread = threading.Thread(target=clear_callback)
    clear_thread.start()
    assert not cleared.wait(0.05)
    release.set()
    callback_thread.join(timeout=1.0)
    clear_thread.join(timeout=1.0)

    assert cleared.is_set()
    assert native_client._subscribe_callback is None
