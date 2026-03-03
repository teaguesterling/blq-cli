"""Tests for well-known exit code reasons and status_reason field."""

from __future__ import annotations

import json

from blq.commands.core import WELL_KNOWN_EXIT_CODES, RunResult, _get_exit_code_reason
from blq.commands.execution import _compute_status_reason, _make_synthetic_exit_event

# ============================================================================
# _get_exit_code_reason tests
# ============================================================================


class TestGetExitCodeReason:
    """Tests for exit code map lookup."""

    def test_exact_match_pytest_5(self):
        assert _get_exit_code_reason("pytest", 5) == "No tests collected"

    def test_exact_match_pytest_1(self):
        assert _get_exit_code_reason("pytest", 1) == "Tests failed"

    def test_exact_match_ruff(self):
        assert _get_exit_code_reason("ruff", 1) == "Lint violations found"

    def test_exact_match_mypy(self):
        assert _get_exit_code_reason("mypy", 1) == "Type errors found"

    def test_exact_match_cargo(self):
        assert _get_exit_code_reason("cargo", 101) == "Build/test failed"

    def test_prefix_match(self):
        """Source names like 'pytest-unit' should match 'pytest' entry."""
        assert _get_exit_code_reason("pytest-unit", 5) == "No tests collected"

    def test_prefix_match_ruff_check(self):
        assert _get_exit_code_reason("ruff-check", 1) == "Lint violations found"

    def test_unknown_tool(self):
        assert _get_exit_code_reason("unknown-tool", 1) is None

    def test_unknown_exit_code(self):
        """Known tool but unrecognized exit code returns None."""
        assert _get_exit_code_reason("pytest", 99) is None

    def test_exit_code_zero_not_in_map(self):
        """Exit code 0 is not in any map (it means success)."""
        assert _get_exit_code_reason("pytest", 0) is None

    def test_all_tools_in_map(self):
        """Verify the map has all expected tools."""
        expected_tools = {
            "pytest",
            "ruff",
            "mypy",
            "cargo",
            "make",
            "go",
            "npm",
            "tsc",
            "eslint",
            "black",
            "flake8",
            "gcc",
            "rustc",
        }
        assert set(WELL_KNOWN_EXIT_CODES.keys()) == expected_tools


# ============================================================================
# _compute_status_reason tests
# ============================================================================


class TestComputeStatusReason:
    """Tests for status reason computation."""

    def test_timeout(self):
        reason = _compute_status_reason("TIMEOUT", -1, 0, 0, "pytest", timed_out=True)
        assert reason == "Command timed out"

    def test_fail_with_errors_returns_none(self):
        """When there are errors, the errors speak for themselves."""
        reason = _compute_status_reason("FAIL", 1, 5, 0, "pytest", timed_out=False)
        assert reason is None

    def test_warn_returns_none(self):
        reason = _compute_status_reason("WARN", 0, 0, 3, "pytest", timed_out=False)
        assert reason is None

    def test_ok_returns_none(self):
        reason = _compute_status_reason("OK", 0, 0, 0, "pytest", timed_out=False)
        assert reason is None

    def test_fail_no_errors_known_tool(self):
        """FAIL with 0 errors from known tool gets well-known reason."""
        reason = _compute_status_reason("FAIL", 5, 0, 0, "pytest", timed_out=False)
        assert reason == "No tests collected"

    def test_fail_no_errors_unknown_tool(self):
        """FAIL with 0 errors from unknown tool gets generic reason."""
        reason = _compute_status_reason("FAIL", 42, 0, 0, "my-script", timed_out=False)
        assert reason == "Non-zero exit code (42) with no errors detected"

    def test_fail_with_warnings_only_returns_none(self):
        """FAIL with warnings but no errors - warnings are present, so no reason needed."""
        # This shouldn't actually happen (warnings make status=WARN not FAIL),
        # but if it did, having warnings means reason is None
        reason = _compute_status_reason("FAIL", 1, 0, 3, "pytest", timed_out=False)
        assert reason is None


# ============================================================================
# _make_synthetic_exit_event tests
# ============================================================================


class TestMakeSyntheticExitEvent:
    """Tests for synthetic event generation."""

    def test_event_structure(self):
        event = _make_synthetic_exit_event("pytest", 5, "No tests collected")
        assert event["severity"] == "info"
        assert event["message"] == "No tests collected"
        assert event["error_code"] == "exit_5"
        assert event["tool_name"] == "pytest"
        assert event["event_id"] == 1
        assert event["ref_file"] is None
        assert event["ref_line"] is None
        assert event["ref_column"] is None

    def test_fingerprint_is_deterministic(self):
        event1 = _make_synthetic_exit_event("pytest", 5, "No tests collected")
        event2 = _make_synthetic_exit_event("pytest", 5, "No tests collected")
        assert event1["fingerprint"] == event2["fingerprint"]

    def test_fingerprint_varies_by_tool(self):
        event1 = _make_synthetic_exit_event("pytest", 5, "reason")
        event2 = _make_synthetic_exit_event("ruff", 5, "reason")
        assert event1["fingerprint"] != event2["fingerprint"]

    def test_fingerprint_varies_by_exit_code(self):
        event1 = _make_synthetic_exit_event("pytest", 1, "reason")
        event2 = _make_synthetic_exit_event("pytest", 5, "reason")
        assert event1["fingerprint"] != event2["fingerprint"]

    def test_error_code_format(self):
        event = _make_synthetic_exit_event("cargo", 101, "Build/test failed")
        assert event["error_code"] == "exit_101"


# ============================================================================
# RunResult with status_reason tests
# ============================================================================


class TestRunResultStatusReason:
    """Tests for status_reason in RunResult output."""

    def _make_result(self, status_reason: str | None = None, **kwargs) -> RunResult:
        defaults = {
            "run_id": 1,
            "command": "pytest tests/",
            "status": "FAIL",
            "exit_code": 5,
            "started_at": "2024-01-01T00:00:00",
            "completed_at": "2024-01-01T00:00:01",
            "duration_sec": 1.0,
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
            "source_name": "pytest",
            "status_reason": status_reason,
        }
        defaults.update(kwargs)
        return RunResult(**defaults)

    def test_json_includes_status_reason(self):
        result = self._make_result(status_reason="No tests collected")
        data = json.loads(result.to_json())
        assert data["status_reason"] == "No tests collected"

    def test_json_omits_status_reason_when_none(self):
        result = self._make_result(status_reason=None)
        data = json.loads(result.to_json())
        assert "status_reason" not in data

    def test_markdown_includes_status_reason(self):
        result = self._make_result(status_reason="No tests collected")
        md = result.to_markdown()
        assert "**Reason:** No tests collected" in md

    def test_markdown_omits_status_reason_when_none(self):
        result = self._make_result(status_reason=None)
        md = result.to_markdown()
        assert "**Reason:**" not in md

    def test_well_known_exit_code_in_result(self):
        """A RunResult from pytest exit 5 should have the well-known reason."""
        result = self._make_result(
            exit_code=5,
            status="FAIL",
            status_reason="No tests collected",
        )
        data = json.loads(result.to_json())
        assert data["status"] == "FAIL"
        assert data["status_reason"] == "No tests collected"
        assert data["exit_code"] == 5

    def test_unknown_exit_code_in_result(self):
        """A RunResult from unknown tool gets generic reason."""
        result = self._make_result(
            exit_code=42,
            status="FAIL",
            source_name="my-script",
            status_reason="Non-zero exit code (42) with no errors detected",
        )
        data = json.loads(result.to_json())
        assert data["status_reason"] == "Non-zero exit code (42) with no errors detected"

    def test_ok_result_no_status_reason(self):
        result = self._make_result(
            exit_code=0,
            status="OK",
            status_reason=None,
        )
        data = json.loads(result.to_json())
        assert "status_reason" not in data
