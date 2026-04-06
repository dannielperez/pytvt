"""Tests for pytvt.sdk_local — subprocess JSON extraction."""

from __future__ import annotations

from pytvt.sdk_local import _extract_json


class TestExtractJson:
    def test_valid_markers(self):
        stdout = 'noise noise\n___JSON_START___\n{"key": "value"}\n___JSON_END___\nmore noise'
        result = _extract_json(stdout)
        assert result == '{"key": "value"}'

    def test_no_markers(self):
        assert _extract_json("just some output") is None

    def test_only_start_marker(self):
        assert _extract_json('___JSON_START___\n{"a": 1}') is None

    def test_only_end_marker(self):
        assert _extract_json('{"a": 1}\n___JSON_END___') is None

    def test_empty_between_markers(self):
        result = _extract_json("___JSON_START___ ___JSON_END___")
        assert result == ""

    def test_multiline_json(self):
        stdout = '___JSON_START___\n{\n  "a": 1,\n  "b": 2\n}\n___JSON_END___'
        result = _extract_json(stdout)
        assert '"a": 1' in result
        assert '"b": 2' in result
