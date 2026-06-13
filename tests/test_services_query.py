"""Tests for src/blq/services/query.py."""

from __future__ import annotations

import sys

from blq.services.query import (
    _build_run_ref,
    _compute_status,
    query_diff,
    query_events,
    query_history,
    query_status,
)
from blq.storage import BlqStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exec_echo(message="hello"):
    """Run a quick ad-hoc command to generate a run record."""
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "blq", "exec", "--quiet", "echo", message],
        check=False,
    )


def _open_storage():
    return BlqStorage.open()


# ---------------------------------------------------------------------------
# Unit tests for private helpers
# ---------------------------------------------------------------------------


class TestComputeStatus:
    def test_ok(self):
        assert _compute_status(0, 0, 0, "completed") == "OK"

    def test_fail_errors(self):
        assert _compute_status(3, 0, 0, "completed") == "FAIL"

    def test_fail_exit_code(self):
        assert _compute_status(0, 0, 1, "completed") == "FAIL"

    def test_warn(self):
        assert _compute_status(0, 2, 0, "completed") == "WARN"

    def test_running(self):
        assert _compute_status(0, 0, None, "pending") == "RUNNING"

    def test_orphaned(self):
        assert _compute_status(0, 0, None, "orphaned") == "ORPHANED"


class TestBuildRunRef:
    def test_tag_and_serial(self):
        assert _build_run_ref("build", "build", 1) == "build:1"

    def test_source_name_fallback(self):
        assert _build_run_ref(None, "test", 3) == "test:3"

    def test_serial_only(self):
        assert _build_run_ref(None, None, 5) == "5"

    def test_both_none(self):
        assert _build_run_ref(None, None, None) == ""


# ---------------------------------------------------------------------------
# Integration tests — require initialized_project fixture
# ---------------------------------------------------------------------------


class TestQueryStatus:
    def test_returns_list(self, initialized_project):
        storage = _open_storage()
        result = query_status(storage)
        assert isinstance(result, list)

    def test_empty_project_returns_empty(self, initialized_project):
        storage = _open_storage()
        result = query_status(storage)
        # Fresh project has no runs -> empty list
        assert result == []

    def test_has_source_after_run(self, initialized_project):
        _exec_echo("status_test")
        storage = _open_storage()
        result = query_status(storage)
        assert len(result) >= 1

    def test_result_has_required_keys(self, initialized_project):
        _exec_echo("key_check")
        storage = _open_storage()
        result = query_status(storage)
        assert len(result) >= 1
        entry = result[0]
        for key in (
            "name",
            "status",
            "error_count",
            "warning_count",
            "last_run",
            "run_ref",
            "run_serial",
        ):  # noqa: E501
            assert key in entry, f"Missing key: {key}"

    def test_status_values_are_valid(self, initialized_project):
        _exec_echo("status_values")
        storage = _open_storage()
        result = query_status(storage)
        valid_statuses = {"OK", "FAIL", "WARN", "RUNNING", "ORPHANED"}
        for entry in result:
            assert entry["status"] in valid_statuses, f"Unexpected status: {entry['status']}"


class TestQueryHistory:
    def test_returns_list(self, initialized_project):
        storage = _open_storage()
        result = query_history(storage)
        assert isinstance(result, list)

    def test_empty_project_returns_empty(self, initialized_project):
        storage = _open_storage()
        result = query_history(storage)
        assert result == []

    def test_has_run_after_exec(self, initialized_project):
        _exec_echo("history_test")
        storage = _open_storage()
        result = query_history(storage)
        assert len(result) >= 1

    def test_result_has_required_keys(self, initialized_project):
        _exec_echo("history_keys")
        storage = _open_storage()
        result = query_history(storage)
        assert len(result) >= 1
        entry = result[0]
        for key in (
            "run_ref",
            "run_serial",
            "source_name",
            "status",
            "error_count",
            "warning_count",
            "started_at",
            "exit_code",
            "command",
            "git_commit",
            "git_branch",
            "git_dirty",
        ):
            assert key in entry, f"Missing key: {key}"

    def test_limit_respected(self, initialized_project):
        for i in range(5):
            _exec_echo(f"limit_test_{i}")
        storage = _open_storage()
        result = query_history(storage, limit=3)
        assert len(result) <= 3

    def test_source_filter_works(self, initialized_project):
        _exec_echo("source_filter")
        storage = _open_storage()
        all_result = query_history(storage)
        assert len(all_result) >= 1
        source = all_result[0]["source_name"]
        filtered = query_history(storage, source=source)
        assert all(r["source_name"] == source for r in filtered)

    def test_no_match_source_filter(self, initialized_project):
        _exec_echo("no_match")
        storage = _open_storage()
        result = query_history(storage, source="this_source_does_not_exist_xyz")
        assert result == []


class TestQueryEvents:
    def test_returns_dict(self, initialized_project):
        storage = _open_storage()
        result = query_events(storage)
        assert isinstance(result, dict)

    def test_result_has_required_keys(self, initialized_project):
        storage = _open_storage()
        result = query_events(storage)
        assert "events" in result
        assert "total_count" in result

    def test_empty_project_returns_zeros(self, initialized_project):
        storage = _open_storage()
        result = query_events(storage)
        assert result["total_count"] == 0
        assert result["events"] == []

    def test_events_list_type(self, initialized_project):
        _exec_echo("events_list_check")
        storage = _open_storage()
        result = query_events(storage)
        assert isinstance(result["events"], list)
        assert isinstance(result["total_count"], int)

    def test_severity_filter(self, initialized_project):
        _exec_echo("severity_filter")
        storage = _open_storage()
        result = query_events(storage, severity="error")
        assert isinstance(result, dict)
        for event in result["events"]:
            assert event.get("severity") == "error"

    def test_limit_respected(self, initialized_project):
        _exec_echo("limit_events")
        storage = _open_storage()
        result = query_events(storage, limit=1)
        assert len(result["events"]) <= 1


class TestQueryDiff:
    def test_returns_dict(self, initialized_project):
        _exec_echo("diff_a")
        _exec_echo("diff_b")
        storage = _open_storage()
        result = query_diff(storage, 1, 2)
        assert isinstance(result, dict)

    def test_result_has_required_keys(self, initialized_project):
        _exec_echo("diff_keys_a")
        _exec_echo("diff_keys_b")
        storage = _open_storage()
        result = query_diff(storage, 1, 2)
        assert "summary" in result
        assert "fixed" in result
        assert "new" in result

    def test_summary_has_expected_fields(self, initialized_project):
        _exec_echo("diff_summary_a")
        _exec_echo("diff_summary_b")
        storage = _open_storage()
        result = query_diff(storage, 1, 2)
        summary = result["summary"]
        for key in ("run1_errors", "run2_errors", "fixed", "new", "unchanged"):
            assert key in summary, f"Missing summary key: {key}"

    def test_no_errors_gives_zero_counts(self, initialized_project):
        _exec_echo("zero_a")
        _exec_echo("zero_b")
        storage = _open_storage()
        result = query_diff(storage, 1, 2)
        assert result["summary"]["run1_errors"] == 0
        assert result["summary"]["run2_errors"] == 0
        assert result["fixed"] == []
        assert result["new"] == []

    def test_invalid_run_returns_empty(self, initialized_project):
        storage = _open_storage()
        result = query_diff(storage, 9999, 9998)
        assert result["summary"]["run1_errors"] == 0
        assert result["summary"]["run2_errors"] == 0
