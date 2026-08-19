"""Shared Platform Access parsing and the NetSDK ``query_platform_access``."""

from __future__ import annotations

from pytvt.models import PlatformAccessConfig
from pytvt.platform_access import parse_platform_access_config, response_status

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response version="1.0" cmdUrl="queryPlatformCfg">
  <status>success</status>
  <content type="list" current="NVMS5000">
    <item id="OTHER"><switch>false</switch><serverAddr>x</serverAddr>
      <port>1</port><reportId>1</reportId></item>
    <item id="NVMS5000"><switch>true</switch><serverAddr>nvms.example.net</serverAddr>
      <port>2009</port><reportId>100234</reportId></item>
  </content>
</response>"""


def test_parse_prefers_nvms5000_item():
    cfg = parse_platform_access_config(_XML)
    assert cfg == PlatformAccessConfig(enabled=True, server_address="nvms.example.net", port=2009, report_id="100234")


def test_parse_defaults_when_fields_missing_or_bad_port():
    cfg = parse_platform_access_config("<content><item id='NVMS5000'><port>x</port></item></content>")
    assert cfg == PlatformAccessConfig(enabled=False, server_address="", port=2009, report_id="")


def test_response_status_extracts_status_and_code():
    assert response_status(_XML) == ("success", None)
    assert response_status("<status>fail</status><errorCode>536870943</errorCode>") == (
        "fail",
        "536870943",
    )
    assert response_status("<x/>") == (None, None)
