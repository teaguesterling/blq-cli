"""Tests for src/blq/services/execution.py."""

from __future__ import annotations

import pytest

from blq.services.execution import run_result_to_concise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_full_result(**overrides: object) -> dict:
    """Return a minimal valid full RunResult dict."""
    base: dict = {
        "run_id": 42,
        "source_name": "build",
        "command": "make -j8",
        "status": "OK",
        "exit_code": 0,
        "duration_sec": 3.456,
        "summary": {"total_events": 0, "errors": 0, "warnings": 0},
        "output_stats": {"lines": 100, "bytes": 2048, "head": [], "tail": []},
        "errors": [],
        "warnings": [],
        "infos": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunResultToConcise:
    def test_basic_success(self):
        full = _make_full_result()
        result = run_result_to_concise(full, source_name="build")

        assert result["run_ref"] == "build:42"
        assert result["status"] == "OK"
        assert result["exit_code"] == 0
        assert result["duration_sec"] == 3.5  # rounded to 1 decimal
        assert result["cmd"] == "make -j8"
        assert result["output_stats"] == {"lines": 100, "bytes": 2048}
        assert "errors" not in result
        assert "warnings" not in result

    def test_failure_with_errors(self):
        errors = [{"message": f"error {i}", "severity": "error"} for i in range(5)]
        full = _make_full_result(
            exit_code=1,
            status="FAIL",
            errors=errors,
            summary={"total_events": 5, "errors": 5, "warnings": 0},
        )
        result = run_result_to_concise(full, source_name="build")

        assert result["status"] == "FAIL"
        assert result["exit_code"] == 1
        assert "errors" in result
        assert len(result["errors"]) == 5

    def test_no_run_id_gives_none_run_ref(self):
        full = _make_full_result(run_id=None)
        result = run_result_to_concise(full, source_name="build")

        assert result["run_ref"] is None

    def test_status_reason_included_when_present(self):
        full = _make_full_result(
            exit_code=1,
            status="FAIL",
            status_reason="timeout",
        )
        result = run_result_to_concise(full, source_name="test")

        assert result["status_reason"] == "timeout"

    def test_status_reason_absent_when_not_present(self):
        full = _make_full_result()
        result = run_result_to_concise(full, source_name="test")

        assert "status_reason" not in result

    def test_errors_capped_at_10(self):
        errors = [{"message": f"error {i}", "severity": "error"} for i in range(20)]
        full = _make_full_result(
            exit_code=1,
            status="FAIL",
            errors=errors,
            summary={"total_events": 20, "errors": 20, "warnings": 0},
        )
        result = run_result_to_concise(full, source_name="build")

        assert len(result["errors"]) == 10
        # First 10 are preserved in order
        assert result["errors"][0]["message"] == "error 0"
        assert result["errors"][9]["message"] == "error 9"

    def test_warnings_capped_at_5(self):
        warnings = [{"message": f"warn {i}", "severity": "warning"} for i in range(12)]
        full = _make_full_result(
            warnings=warnings,
            summary={"total_events": 12, "errors": 0, "warnings": 12},
        )
        result = run_result_to_concise(full, source_name="build")

        assert len(result["warnings"]) == 5
        assert result["warnings"][0]["message"] == "warn 0"
        assert result["warnings"][4]["message"] == "warn 4"
