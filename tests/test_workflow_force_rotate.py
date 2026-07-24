"""Tests for the include_online force-rotate path of rotate_nvr_channel_passwords.

The stock workflow only rotates OFFLINE channels (it assumes an online channel
is already in sync), so it cannot move an all-online site that is still on the
default password. ``include_online=True`` force-rotates online channels via
``edit_ipc_password_status``, treating errorCode 536870962 as ``already-ours``.
"""

from types import SimpleNamespace

import pytest

from pytvt.models import NvrApiError
from pytvt.workflows.exceptions import WorkflowPrecheckError
from pytvt.workflows.password_rotate import rotate_nvr_channel_passwords


def _chan(n, online=True):
    return SimpleNamespace(chl_num=n, dev_id=f"dev-{n}", ip=f"10.0.0.{100 + n}", online=online)


class FakeClient:
    def __init__(self, channels, *, statuses=None, fail=None):
        self.host = "10.0.0.250"
        self._channels = channels
        self._statuses = statuses or {}
        self._fail = set(fail or ())
        self.editpw_calls = []
        self.editdev_calls = []

    def query_channels(self):
        return list(self._channels)

    def edit_ipc_password_status(self, channel_id, *, new_password):
        self.editpw_calls.append(channel_id)
        if channel_id in self._fail:
            raise NvrApiError("editIPChlPassword failed", "536870931")
        return self._statuses.get(channel_id, "changed")

    def update_device_credentials(self, dev_ids=None, username="admin", password=None):
        self.editdev_calls.append((tuple(dev_ids or ()), password))
        return len(dev_ids or [])


PW = {"old_password": "123456", "new_password": "New@2026"}


def test_default_leaves_online_channels_untouched():
    # Regression: without include_online, all-online is a no-op (existing behavior).
    client = FakeClient([_chan(1), _chan(2)])
    res = rotate_nvr_channel_passwords(client, apply=True, **PW)
    assert res.channels_already_ok == 2
    assert res.channels_rotated == 0
    assert client.editpw_calls == []
    assert client.editdev_calls == []


def test_force_rotates_all_online():
    client = FakeClient([_chan(1), _chan(2), _chan(3)])
    res = rotate_nvr_channel_passwords(client, apply=True, include_online=True, **PW)
    assert res.success
    assert res.channels_rotated == 3
    assert res.channels_already_ok == 0
    assert sorted(client.editpw_calls) == ["dev-1", "dev-2", "dev-3"]
    # Every rotated channel's NVR-stored cred is re-synced to the new password.
    assert all(pw == "New@2026" for _, pw in client.editdev_calls)
    assert {r.status for r in res.results} == {"rotated-via-force"}


def test_already_on_target_counts_as_already_ours():
    client = FakeClient([_chan(1), _chan(2)], statuses={"dev-2": "already-set"})
    res = rotate_nvr_channel_passwords(client, apply=True, include_online=True, **PW)
    assert res.success
    assert res.channels_rotated == 1
    assert res.channels_already_ok == 1
    statuses = {r.status for r in res.results}
    assert "already-ours" in statuses and "rotated-via-force" in statuses


def test_failed_channel_is_recorded_and_skips_cred_sync():
    client = FakeClient([_chan(1), _chan(2)], fail={"dev-1"})
    res = rotate_nvr_channel_passwords(client, apply=True, include_online=True, **PW)
    assert not res.success
    assert res.channels_failed == 1
    assert res.channels_rotated == 1
    # The failed channel never has its NVR cred touched.
    assert ("dev-1",) not in [ids for ids, _ in client.editdev_calls]


def test_dry_run_makes_no_changes():
    client = FakeClient([_chan(1), _chan(2)])
    res = rotate_nvr_channel_passwords(client, apply=False, include_online=True, **PW)
    assert res.dry_run
    assert client.editpw_calls == []
    assert client.editdev_calls == []
    assert all(r.status == "skipped" for r in res.results)


def test_precheck_still_rejects_noop_password():
    client = FakeClient([_chan(1)])
    with pytest.raises(WorkflowPrecheckError):
        rotate_nvr_channel_passwords(client, apply=True, include_online=True, old_password="same", new_password="same")
