"""Tests for pytvt.webapi.xml — XML building and parsing helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from pytvt.webapi.xml import (
    build_set_request,
    extract_status,
    find_all_text,
    find_bool,
    find_int,
    find_text,
    parse_response,
    wrap_request,
)


class TestWrapRequest:
    def test_empty_returns_empty(self):
        assert wrap_request() == ""
        assert wrap_request("") == ""

    def test_wraps_with_declaration(self):
        result = wrap_request("<Foo>bar</Foo>")
        assert result.startswith('<?xml version="1.0" encoding="utf-8"?>')
        assert "<Foo>bar</Foo>" in result


class TestBuildSetRequest:
    def test_simple_fields(self):
        result = build_set_request("DateAndTime", {"dateTimeMode": "NTP"})
        assert '<?xml version="1.0" encoding="utf-8"?>' in result
        assert "<DateAndTime>" in result
        assert "<dateTimeMode>NTP</dateTimeMode>" in result

    def test_multiple_fields(self):
        result = build_set_request("Test", {"a": "1", "b": "2"})
        root = ET.fromstring(result)
        assert root.tag == "Test"
        assert root.find("a").text == "1"
        assert root.find("b").text == "2"

    def test_none_values_skipped(self):
        result = build_set_request("Test", {"a": "1", "b": None, "c": "3"})
        root = ET.fromstring(result)
        assert root.find("a") is not None
        assert root.find("b") is None
        assert root.find("c") is not None

    def test_int_values_converted(self):
        result = build_set_request("Test", {"channelID": 5})
        root = ET.fromstring(result)
        assert root.find("channelID").text == "5"


class TestParseResponse:
    def test_parses_valid_xml(self):
        root = parse_response(b"<Root><child>text</child></Root>")
        assert root.tag == "Root"
        assert root.find("child").text == "text"

    def test_raises_on_invalid_xml(self):
        with pytest.raises(ET.ParseError):
            parse_response(b"not xml at all")


class TestFindText:
    @pytest.fixture()
    def root(self):
        return ET.fromstring(
            b"<Root>"
            b"  <deviceName>  My Camera  </deviceName>"
            b"  <empty></empty>"
            b"  <nested><inner>deep</inner></nested>"
            b"</Root>"
        )

    def test_finds_direct_child(self, root):
        assert find_text(root, "deviceName") == "My Camera"

    def test_strips_whitespace(self, root):
        assert find_text(root, "deviceName") == "My Camera"

    def test_default_on_missing(self, root):
        assert find_text(root, "missing") == ""
        assert find_text(root, "missing", "N/A") == "N/A"

    def test_empty_element(self, root):
        assert find_text(root, "empty") == ""

    def test_nested_path(self, root):
        assert find_text(root, "nested/inner") == "deep"


class TestFindInt:
    @pytest.fixture()
    def root(self):
        return ET.fromstring(b"<Root>  <count>42</count>  <bad>abc</bad>  <empty></empty></Root>")

    def test_parses_integer(self, root):
        assert find_int(root, "count") == 42

    def test_default_on_missing(self, root):
        assert find_int(root, "missing") == 0
        assert find_int(root, "missing", 99) == 99

    def test_default_on_non_numeric(self, root):
        assert find_int(root, "bad") == 0
        assert find_int(root, "bad", -1) == -1

    def test_default_on_empty(self, root):
        assert find_int(root, "empty") == 0


class TestFindBool:
    @pytest.fixture()
    def root(self):
        return ET.fromstring(
            b"<Root>"
            b"  <enabled>true</enabled>"
            b"  <disabled>false</disabled>"
            b"  <upper>True</upper>"
            b"  <junk>maybe</junk>"
            b"</Root>"
        )

    def test_true(self, root):
        assert find_bool(root, "enabled") is True

    def test_false(self, root):
        assert find_bool(root, "disabled") is False

    def test_case_insensitive(self, root):
        assert find_bool(root, "upper") is True

    def test_default_on_junk(self, root):
        assert find_bool(root, "junk") is False
        assert find_bool(root, "junk", True) is True

    def test_default_on_missing(self, root):
        assert find_bool(root, "missing") is False


class TestFindAllText:
    def test_collects_multiple(self):
        root = ET.fromstring(b"<Root>  <item>a</item>  <item>b</item>  <item>c</item></Root>")
        assert find_all_text(root, "item") == ["a", "b", "c"]

    def test_empty_when_none(self):
        root = ET.fromstring(b"<Root></Root>")
        assert find_all_text(root, "item") == []


class TestExtractStatus:
    def test_success_response(self):
        root = ET.fromstring(
            b"<Response>"
            b"  <ResponseStatus>"
            b"    <statusCode>200</statusCode>"
            b"    <subStatusCode>0</subStatusCode>"
            b"    <statusString>OK</statusString>"
            b"  </ResponseStatus>"
            b"</Response>"
        )
        code, sub, string = extract_status(root)
        assert code == 200
        assert sub == 0
        assert string == "OK"

    def test_error_response(self):
        root = ET.fromstring(
            b"<Response>"
            b"  <ResponseStatus>"
            b"    <statusCode>401</statusCode>"
            b"    <subStatusCode>0</subStatusCode>"
            b"    <statusString>Unauthorized</statusString>"
            b"  </ResponseStatus>"
            b"</Response>"
        )
        code, sub, string = extract_status(root)
        assert code == 401
        assert sub == 0
        assert string == "Unauthorized"

    def test_status_at_root_level(self):
        root = ET.fromstring(
            b"<ResponseStatus>"
            b"  <statusCode>400</statusCode>"
            b"  <subStatusCode>12</subStatusCode>"
            b"  <statusString>Unsupported</statusString>"
            b"</ResponseStatus>"
        )
        code, sub, string = extract_status(root)
        assert code == 400
        assert sub == 12
        assert string == "Unsupported"

    def test_defaults_when_missing(self):
        root = ET.fromstring(b"<Empty></Empty>")
        code, sub, string = extract_status(root)
        assert code == 200
        assert sub == 0
        assert string == ""
