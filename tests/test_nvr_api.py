"""Tests for pytvt.nvr_api."""

from __future__ import annotations

import base64

import pytvt.nvr_api as nvr_api
from pytvt.models import NvrApiError
from pytvt.nvr_api import NvrClient


class TestQueryNvrLanFreeDevices:
    def test_query_nvr_lan_free_devices_parses_results(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        client._post = lambda path, body: (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response version="1.0" cmdId="" cmdUrl="queryLanFreeDeviceList">'
            "<status>success</status>"
            '<content type="list">'
            "<item>"
            "<activateStatus>ACTIVATED</activateStatus>"
            "<ip>10.200.50.40</ip>"
            "<port>9008</port>"
            "<httpPort>80</httpPort>"
            "<mask>255.255.255.0</mask>"
            "<gateway>10.200.50.1</gateway>"
            "<mac>58:5b:69:45:fc:8c</mac>"
            "<localEthName>eth0</localEthName>"
            "<subIp>169.254.63.115</subIp>"
            "<subIpNetMask>255.255.0.0</subIpNetMask>"
            "<protocolType>TVT_IPCAMERA</protocolType>"
            "<manufacturer>TVT</manufacturer>"
            "<productModel>IP-5IRD4S4C4-28</productModel>"
            "<serialNum>58:5B:69:45:FC:8C</serialNum>"
            "<industryProductType>NORMAL</industryProductType>"
            "<devType>NORMAL</devType>"
            "</item>"
            "<item>"
            "<activateStatus>UNKNOWN</activateStatus>"
            "<ip>10.200.50.41</ip>"
            "<mask>255.255.255.0</mask>"
            "<gateway>10.200.50.1</gateway>"
            "<mac>AA:BB:CC:DD:EE:FF</mac>"
            "<protocolType>ONVIF</protocolType>"
            "<manufacturer>ONVIF</manufacturer>"
            "<productModel>Generic Cam</productModel>"
            "</item>"
            "</content>"
            "</response>"
        )

        devices = client.query_nvr_lan_free_devices()

        assert len(devices) == 2
        assert devices[0].ip == "10.200.50.40"
        assert devices[0].mac == "58:5B:69:45:FC:8C"
        assert devices[0].activated is True
        assert devices[0].protocol == "TVT_IPCAMERA"
        assert devices[0].model == "IP-5IRD4S4C4-28"
        assert devices[1].http_port == 80
        assert devices[1].activated is None

    def test_query_lan_free_devices_alias_delegates(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client.query_nvr_lan_free_devices = lambda: ["ok"]  # type: ignore[method-assign]

        assert client.query_lan_free_devices() == ["ok"]


class TestNvrLanNetworkEdit:
    def test_decrypt_session_key_uses_vendor_aes_shape(self, monkeypatch):
        calls: list[tuple[bytes, str, bool]] = []

        def fake_aes(data: bytes, key: str, *, decrypt: bool) -> bytes:
            calls.append((data, key, decrypt))
            return b"session-key\x00\x00"

        monkeypatch.setattr(nvr_api, "aes_ecb_zeropad", fake_aes)

        out = NvrClient._decrypt_session_key(base64.b64encode(b"cipher").decode(), "MD5HEX")

        assert out == "session-key"
        assert calls == [(b"cipher", "MD5HEX", True)]

    def test_edit_nvr_lan_device_network_builds_expected_request(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        client._session_key = "session-key"
        client._security_ver = "1"
        posted: list[tuple[str, str]] = []

        client._encrypt_for_session = lambda plaintext, session_key: "ENC"  # type: ignore[method-assign]
        client._post = lambda path, body: posted.append((path, body)) or (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response version="1.0" cmdId="" cmdUrl="editDevNetworkList">'
            "<status>success</status>"
            "<content><item><errorCode>0</errorCode></item></content>"
            "</response>"
        )

        client.edit_nvr_lan_device_network(
            old_ip="192.168.110.135",
            new_ip="192.168.110.200",
            netmask="255.255.255.0",
            gateway="192.168.110.1",
            username="admin",
            password="Secret123!",
        )

        assert posted[0][0] == "editDevNetworkList"
        assert "<oldIP>192.168.110.135</oldIP>" in posted[0][1]
        assert "<newIP>192.168.110.200</newIP>" in posted[0][1]
        assert '<password securityVer="1"><![CDATA[ENC]]></password>' in posted[0][1]

    def test_edit_nvr_lan_device_network_raises_on_item_error(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        client._session_key = "session-key"
        client._encrypt_for_session = lambda plaintext, session_key: "ENC"  # type: ignore[method-assign]
        client._post = lambda path, body: (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response version="1.0" cmdId="" cmdUrl="editDevNetworkList">'
            "<status>success</status>"
            "<content><item><errorCode>536871001</errorCode></item></content>"
            "</response>"
        )

        try:
            client.edit_nvr_lan_device_network(
                old_ip="192.168.110.135",
                new_ip="192.168.110.200",
                netmask="255.255.255.0",
                gateway="192.168.110.1",
                username="admin",
                password="Secret123!",
            )
        except NvrApiError as exc:
            assert exc.error_code == "536871001"
        else:
            raise AssertionError("Expected NvrApiError")


class TestDeleteNvrDevices:
    def test_query_channels_parses_delete_relevant_fields(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        responses = {
            "queryDevList": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<response cmdId="" cmdUrl="queryDevList">'
                "<status>success</status>"
                '<content type="list" total="1">'
                '<item id="{00000001-0000-0000-0000-000000000000}">'
                "<chlNum>1</chlNum>"
                "<name>IP Camera</name>"
                "<ip>10.0.0.31</ip>"
                "<port>9008</port>"
                '<productModel factoryName="EAST">IP-5IRD4S4C4-28</productModel>'
                "<manufacturer>TVT</manufacturer>"
                "<protocolType>TVT_IPCAMERA</protocolType>"
                "<AccessType>0</AccessType>"
                "<addType>normalOccupyPoe</addType>"
                "<poeIndex>1</poeIndex>"
                "<chlType>digital</chlType>"
                "<autoReportID>--</autoReportID>"
                "</item>"
                "</content>"
                "</response>"
            ),
            "queryOnlineChlList": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<response><status>success</status>"
                '<content><item id="{00000001-0000-0000-0000-000000000000}"/></content>'
                "</response>"
            ),
        }
        client._post = lambda path, body: responses[path]

        channels = client.query_channels()

        assert channels[0].add_type == "normalOccupyPoe"
        assert channels[0].poe_index == 1
        assert channels[0].chl_type == "digital"
        assert channels[0].access_type == "0"

    def test_delete_nvr_devices_builds_expected_request(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        posted: list[tuple[str, str]] = []
        client._post = lambda path, body: posted.append((path, body)) or (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response version="1.0" cmdId="" cmdUrl="delDevList">'
            "<status>success</status>"
            "</response>"
        )

        deleted = client.delete_nvr_devices(
            ["dev-1", "dev-2"],
            poe_indexes_by_id={"dev-1": 3},
        )

        assert deleted == 2
        assert posted[0][0] == "delDevList"
        assert '<item id="dev-1"><poeIndex>3</poeIndex></item>' in posted[0][1]
        assert '<item id="dev-2"></item>' in posted[0][1]


class TestEditNvrIpcPasswords:
    def test_edit_nvr_ipc_passwords_builds_expected_requests(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        client._session_key = "session-key"
        client._security_ver = "1"
        posted: list[tuple[str, str]] = []

        client._encrypt_for_session = lambda plaintext, session_key: "ENC"  # type: ignore[method-assign]
        client._post = lambda path, body: posted.append((path, body)) or (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response version="1.0" cmdId="" cmdUrl="editIPChlPassword">'
            "<status>success</status>"
            "</response>"
        )

        updated = client.edit_nvr_ipc_passwords(["dev-1", "dev-2"], new_password="TestPass123!")

        assert updated == 2
        assert posted[0][0] == "editIPChlPassword"
        assert "<chl id='dev-1'>" in posted[0][1]
        assert '<password securityVer="1"><![CDATA[ENC]]></password>' in posted[0][1]

    def test_edit_nvr_ipc_passwords_raises_on_failures(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        client._logged_in = True
        client._session_key = "session-key"
        client._encrypt_for_session = lambda plaintext, session_key: "ENC"  # type: ignore[method-assign]

        def fake_post(path, body):
            if "dev-2" in body:
                return '<?xml version="1.0" encoding="UTF-8"?><response><status>fail</status></response>'
            return '<?xml version="1.0" encoding="UTF-8"?><response><status>success</status></response>'

        client._post = fake_post

        try:
            client.edit_nvr_ipc_passwords(["dev-1", "dev-2"], new_password="TestPass123!")
        except NvrApiError as exc:
            assert "dev-2" in str(exc)
        else:
            raise AssertionError("Expected NvrApiError")


class TestEditNvrChannelCredentials:
    def test_edit_nvr_channel_credentials_delegates(self):
        client = NvrClient("10.0.0.1", "admin", "pass")
        calls: list[tuple[list[str] | None, str, str | None]] = []

        def fake_update(dev_ids=None, username="admin", password=None):
            calls.append((dev_ids, username, password))
            return 3

        client.update_device_credentials = fake_update  # type: ignore[method-assign]

        updated = client.edit_nvr_channel_credentials(
            ["dev-1", "dev-2"],
            username="admin",
            password="TestPass123!",
        )

        assert updated == 3
        assert calls == [(["dev-1", "dev-2"], "admin", "TestPass123!")]
