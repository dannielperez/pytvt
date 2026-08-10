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
import time
from datetime import datetime, timezone

import pytest

from pytvt import AlarmServer, NvrPlateSearchIncompleteError
from pytvt.alarm_protocol import TVT_ALARM_CODES
from pytvt.alarm_server import AlarmServerCapacityError
from pytvt.models import FaceEvent, FaceSearchApiStatus, NvrApiError, parse_face_event_timestamp
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


class TestFaceDatabase:
    def test_query_persons_empty_errorcode_is_zero(self):
        # 536870942/536870947 mean "0 people", not a failure.
        client = _client()
        client._post = lambda path, body: (
            '<response cmdUrl="queryFacePersonnalInfoList"><status>fail</status>'
            "<errorCode>536870942</errorCode></response>"
        )
        assert client.query_face_persons("{G}") == []

    def test_query_persons_parses_items(self):
        client = _client()
        client._post = lambda path, body: (
            '<response cmdUrl="queryFacePersonnalInfoList"><status>success</status>'
            '<content type="list" total="1">'
            '<item id="{P1}"><name>Ada</name><gender>2</gender></item>'
            "</content></response>"
        )
        ps = client.query_face_persons("{G}")
        assert len(ps) == 1
        assert ps[0].person_id == "{P1}" and ps[0].name == "Ada" and ps[0].group_id == "{G}"
        assert ps[0].extra.get("gender") == "2"

    def test_create_group_payload(self):
        sent = {}
        client = _client()
        client._post = lambda path, body: (
            sent.update(path=path, body=body) or "<response><status>success</status></response>"
        )
        client.create_face_group("VIPs", group_type="allow")
        assert sent["path"] == "createFacePersonnalInfoGroup"
        assert "<name><![CDATA[VIPs]]></name>" in sent["body"]
        assert '<property type="property">allow</property>' in sent["body"]

    def test_delete_groups_payload(self):
        sent = {}
        client = _client()
        client._post = lambda path, body: (
            sent.update(path=path, body=body) or "<response><status>success</status></response>"
        )
        client.delete_face_groups(["{G1}", "{G2}"])
        assert sent["path"] == "delFacePersonnalInfoGroups"
        assert '<item id="{G1}"></item><item id="{G2}"></item>' in sent["body"]

    def test_get_person_image_decodes_cdata(self):
        import base64 as _b64

        jpeg = b"\xff\xd8\xff\xe0PIC"
        client = _client()
        sent = {}
        client._post = lambda path, body: (
            sent.update(path=path, body=body)
            or f"<response><status>success</status><content><![CDATA[{_b64.b64encode(jpeg).decode()}]]></content></response>"
        )
        out = client.get_face_person_image("{P1}", index=0)
        assert out == jpeg
        assert sent["path"] == "requestFacePersonnalInfoImage"
        assert "<id>{P1}</id>" in sent["body"] and "<index>0</index>" in sent["body"]


class TestSearchFaceEvents:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "garbage",
            "2026-07-23 04:46:43",
            "2026-07-23 04:46:43:123",
            "2026-07-23 04:46:43:10000000",
        ],
    )
    def test_face_event_timestamp_parser_rejects_unrecognized_values(self, raw):
        assert parse_face_event_timestamp(raw) is None

    def test_face_event_timestamp_parser_returns_aware_utc_time(self):
        assert parse_face_event_timestamp(
            "2026-07-23 04:46:43:3365630",
        ) == datetime(2026, 7, 23, 4, 46, 43, 336563, tzinfo=timezone.utc)

    def test_face_event_preserves_existing_positional_constructor(self):
        event = FaceEvent(
            "{CHANNEL}",
            9,
            "2026-07-23 04:46:43:3365630",
            9425,
            "2026-07-23 04:46:43:3365630",
            True,
            "allow",
            "Ada",
            0.95,
            b"face",
            b"background",
        )

        assert event.img_id == 9425
        assert event.frame_time == "2026-07-23 04:46:43:3365630"
        assert event.background == b"background"
        assert event.occurred_at is None

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
        assert evs[0].occurred_at == datetime.fromtimestamp(
            0x6A619CB3,
            tz=timezone.utc,
        ).replace(microsecond=336563)
        assert evs[0].occurred_at.tzinfo is timezone.utc
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

    def test_empty_window_code_returns_no_events(self):
        client = _client()
        client._post = lambda path, body: "<response><status>fail</status><errorCode>536870942</errorCode></response>"

        events = client.search_face_events(
            9,
            "2026-07-28 09:00:00",
            "2026-07-28 09:00:01",
        )

        assert events == []


class TestSearchPlateEvents:
    def test_decodes_plate_metadata_from_two_stage_search(self):
        client = _client()
        sent = []

        def fake_post(path, body):
            sent.append((path, body))
            if path == "searchSmartTarget":
                return (
                    "<response><status>success</status><content>"
                    "<i>6a619cb3,335afe,24d1,7,6a619cb1,6a619cb8,{PATH},19,576,2c,10,14,0,2</i>"
                    "</content></response>"
                )
            return (
                "<response><status>success</status><eventType>plateDetection</eventType>"
                "<targetType>plate</targetType><plateNumber>ABC123</plateNumber>"
                "<content><![CDATA[aGVsbG8=]]></content></response>"
            )

        client._post = fake_post
        events = client.search_plate_events(
            [1, 7],
            "2026-07-23 04:00:00",
            "2026-07-24 03:59:59",
            fetch_snapshots=True,
        )

        assert len(events) == 1
        assert events[0].channel == 7
        assert events[0].plate == "ABC123"
        assert events[0].event_type == "plateDetection"
        assert events[0].target_type == "plate"
        assert events[0].direction == "2"
        assert events[0].snapshot == b"hello"
        assert events[0].occurred_at == datetime.fromtimestamp(0x6A619CB3, tz=timezone.utc).replace(microsecond=336563)
        assert sent[0][0] == "searchSmartTarget"
        assert '<item id="{00000007-0000-0000-0000-000000000000}">' in sent[0][1]
        assert "<item>plateDetection</item>" in sent[0][1]
        assert sent[1][0] == "requestSmartTargetSnapImage"
        assert "<pathGUID>{PATH}</pathGUID>" in sent[1][1]
        assert "<blockNo>44</blockNo>" in sent[1][1]

    def test_ignores_non_plate_details_and_does_not_decode_image_by_default(self):
        client = _client()
        responses = iter(
            [
                "<response><status>success</status><content>"
                "<i>6a619cb3,1,1,1,1,1,{PATH},1,1,1,1,1</i>"
                "</content></response>",
                "<response><status>success</status><targetType>car</targetType>"
                "<plateNumber>SHOULD-NOT-RETURN</plateNumber>"
                "<content><![CDATA[not-base64]]></content></response>",
            ]
        )
        client._post = lambda path, body: next(responses)

        assert client.search_plate_events([1], "start", "end") == []

    @pytest.mark.parametrize("error_code", ["2871", "536871033"])
    def test_skips_deleted_snapshots(self, error_code):
        client = _client()
        responses = iter(
            [
                "<response><status>success</status><content>"
                "<i>6a619cb3,1,1,1,1,1,{PATH},1,1,1,1,1</i>"
                "</content></response>",
                f"<response><status>fail</status><errorCode>{error_code}</errorCode></response>",
            ]
        )
        client._post = lambda path, body: next(responses)

        assert client.search_plate_events([1], "start", "end") == []

    @pytest.mark.parametrize("limit", [0, 101])
    def test_rejects_unbounded_result_limits(self, limit):
        client = _client()

        with pytest.raises(ValueError, match="between 1 and 100"):
            client.search_plate_events([1], "start", "end", result_limit=limit)

    def test_requires_at_least_one_valid_channel(self):
        client = _client()

        with pytest.raises(ValueError, match="must not be empty"):
            client.search_plate_events([], "start", "end")
        with pytest.raises(ValueError, match="positive integers"):
            client.search_plate_events([0], "start", "end")

    def test_full_page_raises_instead_of_silently_truncating(self):
        client = _client()
        record = "6a619cb3,1,1,1,1,1,{PATH},1,1,1,1,1"
        client._post = lambda path, body: (
            f"<response><status>success</status><content><i>{record}</i><i>{record}</i></content></response>"
        )

        with pytest.raises(NvrPlateSearchIncompleteError, match="result limit"):
            client.search_plate_events([1], "start", "end", result_limit=2)

    def test_aggregate_deadline_stops_detail_fanout(self, monkeypatch):
        client = _client()
        record = "6a619cb3,1,1,1,1,1,{PATH},1,1,1,1,1"
        client._post = lambda path, body: (
            f"<response><status>success</status><content><i>{record}</i></content></response>"
        )
        times = iter([0.0, 1.0])
        monkeypatch.setattr("pytvt.xml_api.time.monotonic", lambda: next(times))

        with pytest.raises(NvrPlateSearchIncompleteError, match="deadline"):
            client.search_plate_events(
                [1],
                "start",
                "end",
                max_duration_seconds=1,
            )

    @pytest.mark.parametrize("duration", [0, 301])
    def test_rejects_unbounded_aggregate_deadline(self, duration):
        client = _client()

        with pytest.raises(ValueError, match="between 1 and 300"):
            client.search_plate_events(
                [1],
                "start",
                "end",
                max_duration_seconds=duration,
            )


class TestProbeFaceSearch:
    @staticmethod
    def _response(path):
        responses = {
            "queryNetPortCfg": (
                "<response><status>success</status><content>"
                "<httpPort>80</httpPort><httpsPort>443</httpsPort>"
                "<netPort>6036</netPort><rtspPort>8554</rtspPort>"
                "<posPort>9036</posPort><autoReportPort>2009</autoReportPort>"
                "</content></response>"
            ),
            "queryRTSPServer": (
                "<response><status>success</status><content>"
                "<rtspServerSwitch>true</rtspServerSwitch><rtspPort>8554</rtspPort>"
                "<rtspAuthType>Digest</rtspAuthType><anonymousAccess>false</anonymousAccess>"
                "</content></response>"
            ),
            "queryApiServer": (
                "<response><status>success</status><content>"
                "<apiserverSwitch>true</apiserverSwitch>"
                "<authenticationType>Digest</authenticationType>"
                "</content></response>"
            ),
        }
        return responses[path]

    def test_supported_result_includes_port_rtsp_and_api_state(self):
        client = _client()
        paths = []

        def fake_post(path, body):
            paths.append(path)
            if path == "searchImageByImageV2":
                assert "<resultLimit>1</resultLimit>" in body
                return '<response><status>success</status><content type="list" total="0"/></response>'
            return self._response(path)

        client._post = fake_post

        result = client.probe_face_search(
            9,
            "2026-07-28 09:00:00",
            "2026-07-28 09:00:01",
        )

        assert result.status is FaceSearchApiStatus.SUPPORTED
        assert result.error_code is None
        assert result.port_config.server_port == 6036
        assert result.port_config.rtsp_port == 8554
        assert result.rtsp_server.enabled is True
        assert result.rtsp_server.port == 8554
        assert result.api_server.enabled is True
        assert paths == [
            "queryNetPortCfg",
            "queryRTSPServer",
            "queryApiServer",
            "searchImageByImageV2",
        ]

    def test_field_proven_rejection_is_typed_unsupported(self):
        client = _client()

        def fake_post(path, body):
            if path == "searchImageByImageV2":
                return "<response><status>fail</status><errorCode>536870934</errorCode></response>"
            return self._response(path)

        client._post = fake_post

        result = client.probe_face_search(
            9,
            "2026-07-28 09:00:00",
            "2026-07-28 09:00:01",
        )

        assert result.status is FaceSearchApiStatus.UNSUPPORTED
        assert result.error_code == "536870934"

    def test_empty_window_is_still_supported(self):
        client = _client()

        def fake_post(path, body):
            if path == "searchImageByImageV2":
                return "<response><status>fail</status><errorCode>536870942</errorCode></response>"
            return self._response(path)

        client._post = fake_post

        result = client.probe_face_search(
            9,
            "2026-07-28 09:00:00",
            "2026-07-28 09:00:01",
        )

        assert result.status is FaceSearchApiStatus.SUPPORTED
        assert result.error_code is None

    def test_unknown_rejection_is_not_misclassified(self):
        client = _client()

        def fake_post(path, body):
            if path == "searchImageByImageV2":
                return "<response><status>fail</status><errorCode>536870999</errorCode></response>"
            return self._response(path)

        client._post = fake_post

        with pytest.raises(NvrApiError) as exc_info:
            client.probe_face_search(
                9,
                "2026-07-28 09:00:00",
                "2026-07-28 09:00:01",
            )

        assert exc_info.value.error_code == "536870999"


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
    @pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
    def test_rejects_invalid_connection_cap(self, value):
        with pytest.raises(ValueError, match="max_connections"):
            AlarmServer(
                "127.0.0.1",
                0,
                lambda _event, _addr: None,
                max_connections=value,
            )

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

    def test_rejects_connections_above_hard_concurrency_cap(self):
        rejected = threading.Event()
        errors: list[BaseException] = []

        def on_error(exc, _addr):
            errors.append(exc)
            rejected.set()

        with AlarmServer(
            "127.0.0.1",
            0,
            lambda _event, _addr: None,
            max_connections=1,
            recv_timeout=5,
            on_error=on_error,
        ) as srv:
            port = srv._sock.getsockname()[1]
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            first = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                deadline = time.monotonic() + 2
                while not srv._threads and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert srv._threads
                with socket.create_connection(("127.0.0.1", port), timeout=5):
                    assert rejected.wait(timeout=2)
            finally:
                first.close()

        assert len(errors) == 1
        assert isinstance(errors[0], AlarmServerCapacityError)
