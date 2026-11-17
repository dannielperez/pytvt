"""Tests for the read-only IPC HTTP POST config inspection (GetHttpPostConfig).

Fixtures are SYNTHETIC, built from the OEM reference response example
(videos.cctvcamerapros.com .../alarm/http-post-webhook-config/), not captured
from an installed camera. Hosts are RFC 5737 documentation addresses.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pytvt.models import NvrApiResponseShapeError
from pytvt.web_api.client import WebApiClient
from pytvt.web_api.errors import WebApiError
from pytvt.web_api.http_post import (
    MAX_CONFIG_BYTES,
    HttpPostMatchStatus,
    http_post_target_matches,
    parse_http_post_config,
)
from pytvt.web_api.models import IpcHttpPostConfig, IpcHttpPostTarget

NS = 'version="1.7" xmlns="http://www.ipc.com/ver10"'


def _v2_item(
    url_id: int,
    *,
    switch: str = "true",
    protocol: str = "http",
    domain: str = "192.0.2.10",
    port: str = "80",
    path: str = "/API",
    extra_url: str = "",
    extra_item: str = "",
    data_tag: str = "subscribeDateType",
) -> str:
    return (
        "<item>"
        f"<urlId>{url_id}</urlId>"
        f"<switch>{switch}</switch>"
        "<url>"
        f"<protocol>{protocol}</protocol>"
        f"<domain><![CDATA[{domain}]]></domain>"
        f"<port>{port}</port>"
        f"<path><![CDATA[{path}]]></path>"
        "<authentication>none</authentication>"
        f"{extra_url}"
        "</url>"
        "<heatBeatSwitch>true</heatBeatSwitch>"
        "<keepaliveTimeval>90</keepaliveTimeval>"
        f'<{data_tag} type="list" count="2"><item>alarmStatus</item><item>smartData</item></{data_tag}>'
        '<subscriptionEvents type="list" count="1"><item>VEHICLE</item></subscriptionEvents>'
        f"{extra_item}"
        "</item>"
    )


def _doc(*items: str, v1: str = "", count: int | None = None, root_extra: str = "") -> bytes:
    n = len(items) if count is None else count
    v2 = (
        "<httpPostV2><postUrlConf>"
        f'<urlList type="list" count="{n}" maxCount="3">{"".join(items)}</urlList>'
        "</postUrlConf></httpPostV2>"
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><config {NS}>{v1}{v2}{root_extra}</config>'.encode()


V1 = (
    "<httpPost>"
    '<bSwitch type="boolean">true</bSwitch>'
    "<protocolType><![CDATA[API]]></protocolType>"
    "<serverIp><![CDATA[192.0.2.10]]></serverIp>"
    "<serverPort>80</serverPort>"
    "<keepaliveTimeval>90</keepaliveTimeval>"
    '<onlineStatus type="boolean">true</onlineStatus>'
    "<URL><![CDATA[/API]]></URL>"
    "</httpPost>"
)

# The OEM reference response example, verbatim apart from the documentation-range host.
OEM_EXAMPLE = f"""<?xml version="1.0" encoding="UTF-8"?>
<config {NS}>
  {V1}
  <httpPostV2>
    <postUrlConf>
      <urlList type="list" count="1" maxCount="3">
        <item>
          <urlId>1</urlId>
          <switch>true</switch>
          <url>
            <protocol>http</protocol>
            <domain><![CDATA[192.0.2.10]]></domain>
            <port>80</port>
            <path><![CDATA[/API]]></path>
            <authentication>none</authentication>
          </url>
          <heatBeatSwitch>true</heatBeatSwitch>
          <keepaliveTimeval>90</keepaliveTimeval>
          <subscribeDateType type="list" count="1">
            <item>traject</item>
          </subscribeDateType>
          <subscriptionEvents type="list" count="1">
            <item>PERIMETER</item>
          </subscriptionEvents>
        </item>
      </urlList>
    </postUrlConf>
  </httpPostV2>
</config>""".encode()


# ── Parser ───────────────────────────────────────────────────────────


class TestParse:
    def test_oem_example(self):
        cfg = parse_http_post_config(OEM_EXAMPLE)
        assert isinstance(cfg, IpcHttpPostConfig)
        assert cfg.v1 == IpcHttpPostTarget(
            version="v1",
            url_id=None,
            enabled=True,
            protocol="",
            host="192.0.2.10",
            port=80,
            path="/API",
            authentication="",
            keepalive_seconds=90,
            heartbeat_enabled=None,
            online=True,
            protocol_type="API",
        )
        assert cfg.v2_max_targets == 3
        (t,) = cfg.v2_targets
        assert (t.version, t.url_id, t.enabled, t.protocol, t.host, t.port, t.path) == (
            "v2",
            1,
            True,
            "http",
            "192.0.2.10",
            80,
            "/API",
        )
        assert t.authentication == "none"
        assert t.heartbeat_enabled is True
        assert t.keepalive_seconds == 90
        assert t.data_types == ("traject",)
        assert t.events == ("PERIMETER",)
        assert t.has_password is False and t.has_username is False
        assert cfg.targets == (cfg.v1, t)

    def test_multiple_targets_and_https(self):
        cfg = parse_http_post_config(
            _doc(
                _v2_item(1),
                _v2_item(2, protocol="https", domain="vms.example.net", port="8443", path="/hook"),
                _v2_item(3, switch="false", domain="", port="0", path=""),
            )
        )
        assert [t.url_id for t in cfg.v2_targets] == [1, 2, 3]
        second = cfg.v2_targets[1]
        assert (second.protocol, second.host, second.port, second.path) == ("https", "vms.example.net", 8443, "/hook")
        assert cfg.v2_targets[2].enabled is False
        assert [t.url_id for t in cfg.enabled_targets] == [1, 2]
        assert cfg.v1 is None

    def test_sub_data_type_alias(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, data_tag="subDataType")))
        assert cfg.v2_targets[0].data_types == ("alarmStatus", "smartData")

    def test_all_disabled(self):
        cfg = parse_http_post_config(
            _doc(_v2_item(1, switch="false"), v1=V1.replace(">true</bSwitch>", ">false</bSwitch>"))
        )
        assert cfg.enabled_targets == ()

    def test_v1_only_without_url(self):
        body = f"<config {NS}>{V1.replace('<URL><![CDATA[/API]]></URL>', '')}</config>".encode()
        cfg = parse_http_post_config(body)
        assert cfg.v1 is not None and cfg.v1.path == ""
        assert cfg.v2_targets == ()

    def test_password_is_flagged_never_stored(self):
        secret = "Sup3rS3cret!"
        cfg = parse_http_post_config(
            _doc(
                _v2_item(
                    1,
                    extra_url=f"<userName>operator7</userName><password>{secret}</password>",
                    extra_item=f"<authKey>{secret}</authKey>",
                )
            )
        )
        (t,) = cfg.v2_targets
        assert t.has_password is True
        assert t.has_username is True
        for rendered in (repr(cfg), str(cfg), repr(t), str(t), repr(t.unknown_fields)):
            assert secret not in rendered
            assert "operator7" not in rendered
        assert all("pass" not in k.lower() and "user" not in k.lower() for k, _ in t.unknown_fields)

    def test_empty_password_element_is_not_has_password(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, extra_url="<password></password>")))
        assert cfg.v2_targets[0].has_password is False

    def test_userinfo_in_domain_is_stripped(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, domain="bob:hunter2@192.0.2.10")))
        (t,) = cfg.v2_targets
        assert t.host == "192.0.2.10"
        assert t.has_username is True and t.has_password is True
        assert "hunter2" not in repr(cfg)

    def test_query_string_is_not_kept(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, path="/hook?token=abc123")))
        (t,) = cfg.v2_targets
        assert t.path == "/hook"
        assert t.has_query is True
        assert "abc123" not in repr(cfg)

    def test_unknown_fields_preserved(self):
        cfg = parse_http_post_config(
            _doc(
                _v2_item(1, extra_url="<connectType>persistent</connectType>", extra_item="<newKnob>7</newKnob>"),
                root_extra="<futureBlock><mode>x</mode></futureBlock>",
            )
        )
        (t,) = cfg.v2_targets
        assert ("url/connectType", "persistent") in t.unknown_fields
        assert ("newKnob", "7") in t.unknown_fields
        assert ("futureBlock/mode", "x") in cfg.unknown_fields

    def test_unknown_fields_are_bounded(self):
        many = "".join(f"<extra{i}>{'v' * 600}</extra{i}>" for i in range(200))
        cfg = parse_http_post_config(_doc(_v2_item(1, extra_item=many)))
        fields = cfg.v2_targets[0].unknown_fields
        assert len(fields) <= 32
        assert all(len(v) <= 256 for _, v in fields)

    def test_no_namespace_is_accepted(self):
        cfg = parse_http_post_config(OEM_EXAMPLE.replace(b' xmlns="http://www.ipc.com/ver10"', b""))
        assert cfg.v2_targets[0].host == "192.0.2.10"


class TestParseRejects:
    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"not xml",
            f"<config {NS}></config>".encode(),  # neither httpPost nor httpPostV2
            f"<config {NS}><deviceInfo/></config>".encode(),
            b"<response><status>success</status></response>",
            f'<config {NS} status="failed" errorCode="1"/>'.encode(),
            f"<config {NS}><httpPostV2/></config>".encode(),  # V2 without urlList
            b'<config xmlns="http://example.com/other"><httpPost><bSwitch>true</bSwitch></httpPost></config>',
            _doc(_v2_item(1), count=2),  # declared count != parsed items
            _doc(_v2_item(1, switch="maybe")),
            _doc(_v2_item(1, port="99999")),
            _doc(_v2_item(1), _v2_item(1)),  # duplicate urlId
            _doc(_v2_item(1)).replace(b"<urlId>1</urlId>", b""),
            f"<config {NS}>{V1}{V1}</config>".encode(),  # ambiguous v1 block
            f"<config {NS}><httpPost><other>1</other></httpPost></config>".encode(),
        ],
    )
    def test_unrecognised_shape_raises(self, body):
        with pytest.raises(NvrApiResponseShapeError):
            parse_http_post_config(body)

    def test_too_many_targets(self):
        body = _doc(*(_v2_item(i) for i in range(1, 5)))
        with pytest.raises(NvrApiResponseShapeError):
            parse_http_post_config(body)

    def test_oversize(self):
        body = OEM_EXAMPLE.replace(b"</config>", b"<!--" + b"x" * MAX_CONFIG_BYTES + b"--></config>")
        with pytest.raises(NvrApiResponseShapeError, match="size"):
            parse_http_post_config(body)

    def test_doctype_rejected(self):
        body = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>' + OEM_EXAMPLE.split(b"?>", 1)[1]
        with pytest.raises(NvrApiResponseShapeError, match="declarations"):
            parse_http_post_config(body)

    def test_element_cap(self):
        body = _doc(_v2_item(1, extra_item="<a/>" * 5000))
        with pytest.raises(NvrApiResponseShapeError, match="element"):
            parse_http_post_config(body)


# ── Matcher ──────────────────────────────────────────────────────────

OURS = {"host": "192.0.2.10", "port": 80, "path": "/API", "protocol": "http"}


class TestMatch:
    def test_match(self):
        result = http_post_target_matches(parse_http_post_config(_doc(_v2_item(1))), **OURS)
        assert result.status is HttpPostMatchStatus.MATCH
        assert result.target is not None and result.target.url_id == 1
        assert result.differing_fields == ()
        assert result.third_party_targets == ()

    def test_host_comparison_is_case_insensitive(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, domain="VMS.Example.NET")))
        result = http_post_target_matches(cfg, host="vms.example.net", port=80, path="/API", protocol="HTTP")
        assert result.status is HttpPostMatchStatus.MATCH

    def test_differs_reports_fields(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, protocol="https", port="443", path="/other")))
        result = http_post_target_matches(cfg, **OURS)
        assert result.status is HttpPostMatchStatus.DIFFERS
        assert result.differing_fields == ("protocol", "port", "path")

    def test_disabled_slot_for_us_differs_on_enabled(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, switch="false")))
        result = http_post_target_matches(cfg, **OURS)
        assert result.status is HttpPostMatchStatus.DIFFERS
        assert result.differing_fields == ("enabled",)

    def test_not_configured(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, switch="false", domain="", port="0", path="")))
        result = http_post_target_matches(cfg, **OURS)
        assert result.status is HttpPostMatchStatus.NOT_CONFIGURED
        assert result.target is None

    def test_third_party_only(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, domain="198.51.100.7")))
        result = http_post_target_matches(cfg, **OURS)
        assert result.status is HttpPostMatchStatus.THIRD_PARTY
        assert [t.host for t in result.third_party_targets] == ["198.51.100.7"]

    def test_match_alongside_third_party_is_reported(self):
        cfg = parse_http_post_config(_doc(_v2_item(1), _v2_item(2, domain="198.51.100.7")))
        result = http_post_target_matches(cfg, **OURS)
        assert result.status is HttpPostMatchStatus.MATCH
        assert result.has_third_party is True

    def test_disabled_third_party_is_not_a_conflict(self):
        cfg = parse_http_post_config(_doc(_v2_item(1), _v2_item(2, switch="false", domain="198.51.100.7")))
        assert http_post_target_matches(cfg, **OURS).third_party_targets == ()

    def test_v1_protocol_unknown_never_proven_match(self):
        body = f"<config {NS}>{V1}</config>".encode()
        result = http_post_target_matches(parse_http_post_config(body), **OURS)
        assert result.status is HttpPostMatchStatus.DIFFERS
        assert result.differing_fields == ("protocol",)

    def test_prefers_exact_slot_over_partial(self):
        cfg = parse_http_post_config(_doc(_v2_item(1, port="8080"), _v2_item(2)))
        result = http_post_target_matches(cfg, **OURS)
        assert result.status is HttpPostMatchStatus.MATCH
        assert result.target is not None and result.target.url_id == 2

    @pytest.mark.parametrize(
        "kwargs",
        [
            {**OURS, "protocol": "ftp"},
            {**OURS, "port": 0},
            {**OURS, "port": 70000},
            {**OURS, "host": ""},
        ],
    )
    def test_invalid_arguments(self, kwargs):
        cfg = parse_http_post_config(_doc(_v2_item(1)))
        with pytest.raises(ValueError):
            http_post_target_matches(cfg, **kwargs)


# ── Client ───────────────────────────────────────────────────────────


def _resp(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.getheader.side_effect = lambda name, default="": {"Content-Type": "application/xml"}.get(name, default)
    return resp


class TestClient:
    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_query_is_read_only_get_with_timeout(self, mock_conn_cls):
        client = WebApiClient("192.0.2.50", "admin", "pw-not-printed", timeout=7)
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _resp(OEM_EXAMPLE)

        cfg = client.query_http_post_config()

        assert cfg.v2_targets[0].host == "192.0.2.10"
        assert mock_conn_cls.call_args.kwargs["timeout"] == 7
        (call,) = mock_conn.request.call_args_list
        assert call.args[:2] == ("GET", "/GetHttpPostConfig")
        assert call.kwargs["body"] is None

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_unsupported_firmware_http_400_raises(self, mock_conn_cls):
        client = WebApiClient("192.0.2.50", "admin", "x")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _resp(f'<config {NS} status="failed" errorCode="1"/>'.encode(), 400)
        with pytest.raises(WebApiError) as exc_info:
            client.query_http_post_config()
        assert exc_info.value.status_code == 400

    @patch("pytvt.web_api.client.http.client.HTTPConnection")
    def test_unrecognised_body_raises_shape_error(self, mock_conn_cls):
        client = WebApiClient("192.0.2.50", "admin", "x")
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        mock_conn.getresponse.return_value = _resp(b"<html>login</html>")
        with pytest.raises(NvrApiResponseShapeError):
            client.query_http_post_config()

    def test_no_write_method_exposed(self):
        assert not any("set_http_post" in name or "SetHttpPost" in name for name in dir(WebApiClient))
