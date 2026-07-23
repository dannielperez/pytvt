"""Tests for the NVR face/AI HTTP surface and the alarm-server listener.

Covers the additive face-recognition support: channel-GUID formatting, the
``queryAIResourceDetail`` / ``queryBackFaceMatch`` / ``queryFacePersonnalInfoGroupList``
parsers (canned responses shaped after live NVR replies), the AI/face alarm
codes, and an end-to-end :class:`~pytvt.alarm_server.AlarmServer` round-trip.
"""

from __future__ import annotations

import json
import re
import socket
import threading

import pytest

from pytvt import AlarmServer
from pytvt.alarm_protocol import TVT_ALARM_CODES
from pytvt.xml_api import NvrClient


def _client() -> NvrClient:
    client = NvrClient("10.0.0.1", "admin", "pass")
    client._logged_in = True
    return client


class TestChannelGuid:
    def test_format(self):
        assert NvrClient.channel_guid(9) == "{00000009-0000-0000-0000-000000000000}"
        assert NvrClient.channel_guid(1) == "{00000001-0000-0000-0000-000000000000}"

    def test_rejects_non_positive(self):
        with pytest.raises(ValueError):
            NvrClient.channel_guid(0)


class TestQueryAiResource:
    def test_parses_pool_and_channel(self):
        client = _client()
        client._post = lambda path, body: (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response cmdUrl="queryAIResourceDetail"><status>success</status>'
            "<types><eventType>"
            "<enum>faceMatch</enum><enum>faceDetect</enum>"
            "<enum>tripwire</enum><enum>perimeter</enum>"
            "</eventType></types>"
            "<content>"
            "<totalResourceOccupancy>50.00</totalResourceOccupancy>"
            '<item id="{00000009-0000-0000-0000-000000000000}">'
            "<name>Cara Visitante</name>"
            "<connectState>true</connectState>"
            "<eventType>faceMatch</eventType>"
            "<resource>25.00</resource>"
            "</item>"
            "</content></response>"
        )
        res = client.query_ai_resource()
        assert res.supported_event_types == ["faceMatch", "faceDetect", "tripwire", "perimeter"]
        assert res.total_occupancy == 50.0
        assert len(res.channels) == 1
        chl = res.channels[0]
        assert chl.chl_id == "{00000009-0000-0000-0000-000000000000}"
        assert chl.name == "Cara Visitante"
        assert chl.event_types == ["faceMatch"]
        assert chl.connect_state == "true"
        assert chl.resource == 25  # float "25.00" coerced to int units


class TestQueryNvrFaceDetection:
    def test_parses_switch_and_schedule_without_status_tag(self):
        # queryBackFaceMatch omits <status> on success and returns <content> directly.
        client = _client()
        client._post = lambda path, body: (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<response cmdUrl="queryBackFaceMatch"><content><param><chls>'
            '<item guid="{00000009-0000-0000-0000-000000000000}" '
            'scheduleGuid="{7C21E998-8B89-4D19-8669-457C26181F76}">'
            '<switch>true</switch><mutexList type="list"/>'
            "</item></chls></param></content></response>"
        )
        cfg = client.query_nvr_face_detection(9)
        assert cfg.enabled is True
        assert cfg.chl_id == "{00000009-0000-0000-0000-000000000000}"
        assert cfg.schedule_id == "{7C21E998-8B89-4D19-8669-457C26181F76}"

    def test_explicit_fail_raises(self):
        from pytvt.models import NvrApiError

        client = _client()
        client._post = lambda path, body: (
            '<response cmdUrl="queryBackFaceMatch"><status>fail</status><errorCode>536870923</errorCode></response>'
        )
        with pytest.raises(NvrApiError):
            client.query_nvr_face_detection(9)


class TestQueryFaceDbGroups:
    def test_parses_groups(self):
        client = _client()
        client._post = lambda path, body: (
            '<response cmdUrl="queryFacePersonnalInfoGroupList"><status>success</status>'
            "<types><property><enum>allow</enum><enum>reject</enum><enum>limited</enum></property></types>"
            '<content type="list">'
            '<item id="{group-1}"><name>VIP</name><property>allow</property><faceNum>3</faceNum></item>'
            '<item id="{group-2}"><name>Blocklist</name><property>reject</property><faceNum>7</faceNum></item>'
            "</content></response>"
        )
        groups = client.query_face_db_groups()
        assert [g.name for g in groups] == ["VIP", "Blocklist"]
        assert groups[0].group_type == "allow" and groups[0].face_count == 3
        assert groups[1].group_type == "reject" and groups[1].face_count == 7


class TestSearchFaceEvents:
    def test_decodes_records(self):
        client = _client()
        # searchImageByImageV2 compact <i> records: _,calTimeS,calTimeNS,imgId,channel,... (hex)
        client._post = lambda path, body: (
            '<response cmdUrl="searchImageByImageV2"><status>success</status>'
            '<content type="list" total="2">'
            "<i>0,6a619cb3,335afe,24d1,9,0,6a619cb1,6a619cb8,{G},19,576,2c,0,1</i>"
            "<i>0,6a6198fc,000001,24be,9,0,6a6198fa,6a619901,{G},19,576,24,0,1</i>"
            "</content></response>"
        )
        evs = client.search_face_events(9, "2026-07-23 04:00:00", "2026-07-24 03:59:59")
        assert len(evs) == 2
        assert evs[0].img_id == 0x24D1 == 9425
        assert evs[0].channel == 9
        # frame time = "YYYY-MM-DD HH:MM:SS:NNNNNNN" (7-digit sub-second from calTimeNS)
        assert evs[0].frame_time.endswith(":3365630")  # 0x335afe
        assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{7}", evs[0].frame_time)
        assert evs[1].frame_time.endswith(":0000001")

    def test_sends_searchimagebyimagev2_payload(self):
        sent = {}
        client = _client()

        def fake_post(path, body):
            sent["path"] = path
            sent["body"] = body
            return '<response><status>success</status><content type="list" total="0"></content></response>'

        client._post = fake_post
        client.search_face_events(9, "2026-07-23 04:00:00", "2026-07-24 03:59:59", similarity=80)
        assert sent["path"] == "searchImageByImageV2"
        assert "<resultLimit>" in sent["body"]
        assert '<item id="{00000009-0000-0000-0000-000000000000}">' in sent["body"]
        assert "<eventType>byAll</eventType>" in sent["body"]
        assert "<similarity>80</similarity>" in sent["body"]


class TestGetFaceSnapshot:
    def test_decodes_cdata_jpeg(self):
        import base64 as _b64

        jpeg = b"\xff\xd8\xff\xe0FAKEJPEG"
        client = _client()
        sent = {}

        def fake_post(path, body):
            sent["path"] = path
            sent["body"] = body
            return f"<response><status>success</status><content><![CDATA[{_b64.b64encode(jpeg).decode()}]]></content><grade>63</grade></response>"

        client._post = fake_post
        out = client.get_face_snapshot(9, 9425, "2026-07-23 04:46:43:3365630")
        assert out == jpeg
        assert sent["path"] == "requestChSnapFaceImage"
        assert "<imgId>9425</imgId>" in sent["body"]
        assert "<frameTime>2026-07-23 04:46:43:3365630</frameTime>" in sent["body"]


class TestAlarmServerConfig:
    def test_query_parses_config(self):
        client = _client()
        client._post = lambda path, body: (
            '<response cmdUrl="queryAlarmServerParam"><status>success</status>'
            "<types><dataFormat><enum>XML</enum></dataFormat></types>"
            "<content>"
            "<switch>false</switch><deviceId>dev1</deviceId><token>tok</token>"
            "<address>10.0.0.5</address><url>/push</url><port>9010</port>"
            "<alarmServerSchedule>{SCHED}</alarmServerSchedule>"
            "<alarmServerAlarmTypes>1,2,16</alarmServerAlarmTypes>"
            '<dataFormat type="dataFormat">XML</dataFormat>'
            "<heartbeat><switch>true</switch><interval>30</interval></heartbeat>"
            "</content></response>"
        )
        cfg = client.query_alarm_server()
        assert cfg.enabled is False
        assert cfg.address == "10.0.0.5" and cfg.port == 9010 and cfg.url == "/push"
        assert cfg.data_format == "XML"
        assert cfg.schedule_id == "{SCHED}"
        assert cfg.alarm_types == [1, 2, 16]  # 16 == face match
        assert cfg.device_id == "dev1" and cfg.token == "tok"
        assert cfg.heartbeat_enabled is True and cfg.heartbeat_interval == 30

    def test_set_builds_payload_and_omits_types_for_json(self):
        from pytvt import AlarmServerConfig

        sent = {}
        client = _client()

        def fake_post(path, body):
            sent["path"] = path
            sent["body"] = body
            return "<response><status>success</status></response>"

        client._post = fake_post
        client.set_alarm_server(
            AlarmServerConfig(
                enabled=True,
                address="10.0.0.5",
                port=9010,
                data_format="XML",
                schedule_id="{SCHED}",
                alarm_types=[1, 16],
                heartbeat_enabled=True,
                heartbeat_interval=15,
            )
        )
        assert sent["path"] == "editAlarmServerParam"
        assert "<switch>true</switch>" in sent["body"]
        assert "<port>9010</port>" in sent["body"]
        assert "<alarmServerAlarmTypes>1,16</alarmServerAlarmTypes>" in sent["body"]
        assert "<interval>15</interval>" in sent["body"]
        # JSON format omits the XML-only alarm-types element
        client.set_alarm_server(AlarmServerConfig(enabled=False, data_format="JSON", alarm_types=[1, 16]))
        assert "alarmServerAlarmTypes" not in sent["body"]


class TestSetNvrFaceDetection:
    def test_uses_editrealfacematch_with_switch(self):
        sent = {}
        client = _client()

        def fake_post(path, body):
            sent["path"] = path
            sent["body"] = body
            return "<response><status>success</status></response>"

        client._post = fake_post
        client.set_nvr_face_detection(9, True, schedule_id="{SCHED}")
        assert sent["path"] == "editRealFaceMatch"
        assert 'guid="{00000009-0000-0000-0000-000000000000}"' in sent["body"]
        assert 'scheduleGuid="{SCHED}"' in sent["body"]
        assert "<switch>true</switch>" in sent["body"]


class TestAlarmCodes:
    def test_face_and_ai_codes_present(self):
        assert TVT_ALARM_CODES[0x0C] == ("face_detect", "face")
        assert TVT_ALARM_CODES[0x10] == ("face_match", "face")
        assert TVT_ALARM_CODES[0x11] == ("face_match_ipc", "face")
        assert TVT_ALARM_CODES[0x14] == ("vehicle_plate", "lpr")
        # basic codes preserved
        assert TVT_ALARM_CODES[0x01] == ("motion", "alarm")


class TestAlarmServer:
    def test_receives_and_parses_face_push(self):
        received: list = []
        done = threading.Event()

        def on_event(event, addr):
            received.append(event)
            done.set()

        with AlarmServer("127.0.0.1", 0, on_event) as srv:
            port = srv._sock.getsockname()[1]  # ephemeral port chosen by bind
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            frame = json.dumps({"type": "alarm", "alarm_type": 0x10, "channel": 9, "device_id": "nvr-1"}).encode()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
                conn.sendall(frame)
            assert done.wait(timeout=5), "callback was not invoked"

        assert len(received) == 1
        event = received[0]
        assert event.event_code == "face_match"
        assert event.event_type == "face"
        assert event.channel == 9
