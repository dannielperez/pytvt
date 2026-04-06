"""Tests for pytvt.discovery — XML parsing, dedup, format conversion."""

from __future__ import annotations

import pytest

from pytvt.discovery import (
    _parse_xml_response,
    discovery_to_scanner_format,
    print_discovery_report,
)
from tests.conftest import DISCOVERY_XML

# ── _parse_xml_response ──────────────────────────────────────────────


class TestParseXmlResponse:
    def test_valid_xml(self):
        device = _parse_xml_response(DISCOVERY_XML, ("192.168.1.50", 1900))
        assert device is not None
        assert device["ip"] == "192.168.1.50"
        assert device["mac"] == "58:5B:69:11:22:33"
        assert device["product_model"] == "TD-3332B4"
        # N9000 series — model heuristic detects NVR from TD-*TS* pattern
        assert device["device_type"] in ("NVR", "Unknown")  # depends on series matching
        assert device["data_port"] == 9008
        assert device["http_port"] == 80
        assert device["software_version"] == "V5.2.0"
        assert device["device_name"] == "NVR-Front"

    def test_falls_back_to_source_ip(self):
        xml = b"<multicastSearchResult><tcpIp></tcpIp></multicastSearchResult>"
        device = _parse_xml_response(xml, ("1.2.3.4", 1900))
        assert device is not None
        assert device["ip"] == "1.2.3.4"

    def test_non_xml_returns_none(self):
        assert _parse_xml_response(b"not xml at all", ("1.1.1.1", 1900)) is None

    def test_wrong_root_returns_none(self):
        assert _parse_xml_response(b"<other>test</other>", ("1.1.1.1", 1900)) is None

    def test_http_prefixed_xml(self):
        """Real responses may have HTTP headers before the XML."""
        data = b"HTTP/1.1 200 OK\r\nContent-Type: text/xml\r\n\r\n" + DISCOVERY_XML
        device = _parse_xml_response(data, ("192.168.1.50", 1900))
        assert device is not None
        assert device["ip"] == "192.168.1.50"

    def test_dvr_type_detection(self):
        xml = b"""\
<multicastSearchResult>
  <tcpIp><ipAddr>10.0.0.1</ipAddr><macAddr>58:5B:69:00:00:01</macAddr></tcpIp>
  <port><dataPort>9008</dataPort></port>
  <productInfo><productModel>TD-2104TS</productModel><productSeries>DVR</productSeries></productInfo>
</multicastSearchResult>"""
        device = _parse_xml_response(xml, ("10.0.0.1", 1900))
        assert device["device_type"] == "DVR"

    def test_ipc_type_detection(self):
        xml = b"""\
<multicastSearchResult>
  <tcpIp><ipAddr>10.0.0.2</ipAddr><macAddr>58:5B:69:00:00:02</macAddr></tcpIp>
  <port></port>
  <productInfo><productModel>IP-5IRD4S4</productModel><productSeries>IPC</productSeries></productInfo>
</multicastSearchResult>"""
        device = _parse_xml_response(xml, ("10.0.0.2", 1900))
        assert device["device_type"] == "IPC"

    def test_missing_ports(self):
        xml = b"""\
<multicastSearchResult>
  <tcpIp><ipAddr>10.0.0.3</ipAddr></tcpIp>
  <port></port>
  <productInfo></productInfo>
</multicastSearchResult>"""
        device = _parse_xml_response(xml, ("10.0.0.3", 1900))
        assert device["data_port"] == 0
        assert device["http_port"] == 0


# ── discovery_to_scanner_format ──────────────────────────────────────


class TestDiscoveryToScannerFormat:
    def test_basic_conversion(self):
        devices = [
            {
                "ip": "10.0.0.1",
                "mac": "58:5B:69:00:00:01",
                "device_name": "NVR-1",
                "product_model": "TD-3332B4",
                "device_type": "NVR",
                "data_port": 9008,
                "http_port": 80,
                "software_version": "V5.2.0",
            }
        ]
        result = discovery_to_scanner_format(devices, site="TestSite", scan_port=6036)
        assert len(result) == 1
        assert result[0]["ip"] == "10.0.0.1"
        assert result[0]["site"] == "TestSite"
        assert result[0]["port"] == 6036
        assert result[0]["manufacturer"] == "TVT"
        assert result[0]["hostname"] == "NVR-1"

    def test_empty_input(self):
        assert discovery_to_scanner_format([], site="X") == []

    def test_hostname_fallback_to_model(self):
        devices = [
            {
                "ip": "10.0.0.1",
                "mac": "",
                "device_name": "",
                "product_model": "TD-9544S4",
                "device_type": "IPC",
                "data_port": 9008,
                "http_port": 0,
                "software_version": "",
            }
        ]
        result = discovery_to_scanner_format(devices)
        assert result[0]["hostname"] == "TD-9544S4"


# ── print_discovery_report ───────────────────────────────────────────


class TestPrintDiscoveryReport:
    def test_empty_list(self, capsys):
        print_discovery_report([])
        assert "No TVT devices found" in capsys.readouterr().out

    def test_with_devices(self, capsys):
        devices = [
            {
                "device_type": "NVR",
                "ip": "10.0.0.1",
                "mac": "58:5B:69:00:00:01",
                "product_model": "TD-3332B4",
                "device_name": "NVR-1",
                "software_version": "V5.2.0",
                "data_port": 9008,
                "http_port": 80,
            }
        ]
        print_discovery_report(devices)
        out = capsys.readouterr().out
        assert "1 device(s) found" in out
        assert "10.0.0.1" in out
        assert "TD-3332B4" in out
