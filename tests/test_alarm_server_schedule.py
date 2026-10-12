"""set_alarm_server must send the schedule NAME, as the NVR web client does.

CAPTURED 2026-10-12: queryAlarmServerParam returns the schedule as a GUID, but
editAlarmServerParam takes the schedule's name. The shipped web client
(app/AlarmCfg/alarmServer.js) resolves the selected GUID to its text and sends
that. Sending the GUID back made the recorder fall to the null schedule
({00000000-…}, "IDCS_NULL") — and under the null schedule no alarm was pushed
(heartbeats only); restoring "24x7" by name brought alarm pushes back.
"""

from __future__ import annotations

import re

import pytest

from pytvt.models import AlarmServerConfig, NvrApiError
from pytvt.xml_api import NvrClient

SCHEDULES = (
    '<?xml version="1.0" encoding="UTF-8"?><response cmdUrl="queryScheduleList">'
    '<status>success</status><content type="list">'
    '<item id="{513900B7-C30E-4E2E-A668-9E7C99C33591}"><![CDATA[24x7]]></item>'
    '<item id="{2F0EEAE2-1FB5-4825-9BC9-E460EC3C1C76}"><![CDATA[24x5]]></item>'
    "</content></response>"
)
OK = "<response><status>success</status></response>"
NULL = "{00000000-0000-0000-0000-000000000000}"


def _client(sent):
    client = NvrClient("10.0.0.1", "admin", "pass")
    client._logged_in = True

    def fake_post(path, body):
        sent.append((path, body))
        return SCHEDULES if path == "queryScheduleList" else OK

    client._post = fake_post
    return client


def _schedule_sent(sent):
    edit = next(body for path, body in sent if path == "editAlarmServerParam")
    return re.search(r"<alarmServerSchedule>(.*?)</alarmServerSchedule>", edit).group(1)


def test_guid_is_resolved_to_the_schedule_name():
    sent = []
    _client(sent).set_alarm_server(
        AlarmServerConfig(enabled=True, schedule_id="{513900B7-C30E-4E2E-A668-9E7C99C33591}"),
    )
    assert _schedule_sent(sent) == "24x7"


def test_guid_lookup_is_case_insensitive():
    sent = []
    _client(sent).set_alarm_server(
        AlarmServerConfig(enabled=True, schedule_id="{2f0eeae2-1fb5-4825-9bc9-e460ec3c1c76}"),
    )
    assert _schedule_sent(sent) == "24x5"


def test_unknown_guid_is_refused_rather_than_silently_nulled():
    sent = []
    with pytest.raises(NvrApiError, match="schedule"):
        _client(sent).set_alarm_server(
            AlarmServerConfig(enabled=True, schedule_id="{DEADBEEF-0000-0000-0000-000000000000}"),
        )
    assert not [p for p, _ in sent if p == "editAlarmServerParam"]


def test_null_schedule_and_plain_names_are_sent_as_given():
    for value in (NULL, "24x7", ""):
        sent = []
        _client(sent).set_alarm_server(AlarmServerConfig(enabled=True, schedule_id=value))
        assert _schedule_sent(sent) == value
        assert not [p for p, _ in sent if p == "queryScheduleList"]
