import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pytvt.platform_sdk.exceptions import ProtocolError
from pytvt.platform_sdk.web_backend import WebManagementBackend
from pytvt.platform_sdk.web_session import WebHttpResponse, WebSession


def backend(items=None, offline=None, total="2"):
    b = WebManagementBackend("example.invalid")
    b._session = Mock()
    b._session.request_xml.return_value = SimpleNamespace(
        ok=True,
        content_attributes={"total": total},
        items=items
        if items is not None
        else [
            {"id": "{AAA}", "name": "Gate", "password": "must-not-leak"},
            {"id": "{BBB}", "name": "Exit"},
        ],
    )
    b._session.request.return_value = SimpleNamespace(
        ok=True,
        content_attributes={},
        items=offline if offline is not None else [{"id": "{BBB}"}],
    )
    return b


def test_complete_inventory_and_offline_set_without_inventing_parent():
    rows = backend().list_channels()
    assert [(x.channel_id, x.status) for x in rows] == [("{AAA}", "ONLINE"), ("{BBB}", "OFFLINE")]
    assert all(x.device_id == "" for x in rows)
    assert all("password" not in x.raw_data for x in rows)


@pytest.mark.parametrize("total", ["", "3", "0", "-2", "100001"])
def test_incomplete_or_unbounded_inventory_rejected(total):
    with pytest.raises(ProtocolError):
        backend(total=total).list_channels()


@pytest.mark.parametrize("items", [[{"id": "AAA"}, {"id": "{aaa}"}], [{"id": "AAA"}, {}]])
def test_duplicate_or_missing_inventory_identity_rejected(items):
    with pytest.raises(ProtocolError):
        backend(items=items).list_channels()


@pytest.mark.parametrize("offline", [[{"id": "unknown"}], [{}], [{"id": "BBB"}, {"id": "{bbb}"}]])
def test_inconsistent_offline_snapshot_rejected(offline):
    with pytest.raises(ProtocolError):
        backend(offline=offline).list_channels()


def test_failed_offline_read_does_not_invent_all_online():
    b = backend(offline=[])
    b._session.request.return_value.ok = False
    with pytest.raises(ProtocolError):
        b.list_channels()


def test_xml_retry_refreshes_token_without_mutating_callers_payload():
    s = WebSession("example.invalid", "user", "password")
    s._token = "old"
    s.login = Mock(side_effect=lambda: setattr(s, "_token", "new"))
    s._post = Mock(side_effect=[WebHttpResponse(408, (), b""), WebHttpResponse(200, (), b"")])
    s._parse_http = Mock(return_value="parsed")
    request = ET.fromstring("<request><nodeType>chls</nodeType></request>")
    assert s.request_xml("/service/Common/getNodeList", request) == "parsed"
    tokens = [ET.fromstring(c.args[1]["requestXml"]).findtext("token") for c in s._post.call_args_list]
    assert tokens == ["old", "new"]
    assert request.find("token") is None


def test_truncated_offline_count_is_not_all_online():
    b = backend(offline=[])
    b._session.request.return_value.content_attributes = {"total": "3"}
    with pytest.raises(ProtocolError, match="count incomplete"):
        b.list_channels()
