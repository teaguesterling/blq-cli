"""Tests for JSON null field filtering and comma-separated format handling (Issue #24)."""

import json
import logging

from blq.output import _filter_nulls, format_json


class TestFilterNulls:
    """Tests for _filter_nulls helper."""

    def test_removes_none_values(self):
        d = {"a": 1, "b": None, "c": "hello"}
        assert _filter_nulls(d) == {"a": 1, "c": "hello"}

    def test_removes_empty_string_values(self):
        d = {"a": 1, "b": "", "c": "hello"}
        assert _filter_nulls(d) == {"a": 1, "c": "hello"}

    def test_keeps_zero_and_false(self):
        d = {"a": 0, "b": False, "c": None}
        assert _filter_nulls(d) == {"a": 0, "b": False}

    def test_keeps_empty_list_and_dict(self):
        d = {"a": [], "b": {}, "c": None}
        assert _filter_nulls(d) == {"a": [], "b": {}}

    def test_handles_list_of_dicts(self):
        data = [
            {"a": 1, "b": None},
            {"a": 2, "c": ""},
            {"a": 3, "d": "ok"},
        ]
        result = _filter_nulls(data)
        assert result == [{"a": 1}, {"a": 2}, {"a": 3, "d": "ok"}]

    def test_passes_through_non_dict_non_list(self):
        assert _filter_nulls(42) == 42
        assert _filter_nulls("hello") == "hello"

    def test_empty_dict_stays_empty(self):
        assert _filter_nulls({}) == {}

    def test_all_null_dict(self):
        assert _filter_nulls({"a": None, "b": ""}) == {}


class TestFormatJsonFiltering:
    """Tests that format_json applies null filtering."""

    def test_omits_null_fields(self):
        data = {"message": "error", "ref_line": None, "ref_column": None}
        result = json.loads(format_json(data))
        assert result == {"message": "error"}
        assert "ref_line" not in result
        assert "ref_column" not in result

    def test_omits_empty_string_fields(self):
        data = {"message": "error", "error_code": "", "context": ""}
        result = json.loads(format_json(data))
        assert result == {"message": "error"}

    def test_list_of_events_filtered(self):
        data = [
            {
                "severity": "error",
                "message": "bad",
                "ref_line": 10,
                "ref_column": None,
                "error_code": None,
                "context": "",
                "metadata": None,
            },
            {
                "severity": "warning",
                "message": "maybe bad",
                "ref_line": None,
                "ref_column": None,
                "error_code": "W001",
                "context": None,
                "metadata": None,
            },
        ]
        result = json.loads(format_json(data))
        assert result[0] == {"severity": "error", "message": "bad", "ref_line": 10}
        assert result[1] == {
            "severity": "warning",
            "message": "maybe bad",
            "error_code": "W001",
        }

    def test_preserves_valid_values(self):
        data = {"a": 0, "b": False, "c": [], "d": "text"}
        result = json.loads(format_json(data))
        assert result == data


class TestCommaFormats:
    """Tests for comma-separated format handling in parse_log_content."""

    def test_single_format_works(self):
        """Single format (no comma) still works as before."""
        from blq.cli import parse_log_content

        content = "src/main.c:15:5: error: undefined variable 'foo'"
        events = parse_log_content(content, "auto")
        assert len(events) >= 1
        assert events[0]["severity"] == "error"

    def test_comma_separated_first_succeeds(self):
        """Comma-separated formats: first match wins."""
        from blq.cli import parse_log_content

        # GCC-style error should be parsed by auto
        content = "src/main.c:15:5: error: undefined variable 'foo'"
        events = parse_log_content(content, "gcc_text,auto")
        assert len(events) >= 1
        assert events[0]["severity"] == "error"

    def test_comma_separated_fallback_to_later_format(self):
        """If first format fails, try the next one."""
        from blq.cli import parse_log_content

        content = "src/main.c:15:5: error: undefined variable 'foo'"
        # "nonexistent_format_xyz" should fail, "auto" should work
        events = parse_log_content(content, "nonexistent_format_xyz,auto")
        assert len(events) >= 1

    def test_all_formats_fail_falls_back_to_auto(self, caplog):
        """If all comma-separated formats fail, fall back to auto with warning."""
        from blq.cli import parse_log_content

        content = "src/main.c:15:5: error: undefined variable 'foo'"
        with caplog.at_level(logging.WARNING, logger="blq-cli"):
            events = parse_log_content(content, "nonexistent_format_xyz,another_bad_format")
        # Should fall back to auto and still find events
        assert len(events) >= 1
        # Should have logged a warning about all formats failing
        assert any("failed to parse" in r.message for r in caplog.records)

    def test_single_bad_format_falls_back_to_auto(self, caplog):
        """Single bad format falls back to auto with warning."""
        from blq.cli import parse_log_content

        content = "src/main.c:15:5: error: undefined variable 'foo'"
        with caplog.at_level(logging.WARNING, logger="blq-cli"):
            events = parse_log_content(content, "nonexistent_format_xyz")
        assert len(events) >= 1
        assert any("failed to parse" in r.message for r in caplog.records)

    def test_whitespace_in_comma_formats_handled(self):
        """Whitespace around commas is stripped."""
        from blq.cli import parse_log_content

        content = "src/main.c:15:5: error: undefined variable 'foo'"
        events = parse_log_content(content, " auto , gcc_text ")
        assert len(events) >= 1

    def test_empty_format_treated_as_auto(self):
        """Empty format string falls back to auto."""
        from blq.cli import parse_log_content

        content = "src/main.c:15:5: error: undefined variable 'foo'"
        events = parse_log_content(content, "")
        assert len(events) >= 1
