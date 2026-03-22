"""Tests for extension protocol types."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from blq.ext import CommandSpec, ExecutionResult


class TestCommandSpec:
    def test_construction(self) -> None:
        spec = CommandSpec(
            command="pytest tests/",
            original_command="pytest tests/",
            command_name="test",
            attempt_id="abc-123",
            workspace=Path("/project"),
            cwd=Path("/project"),
            live_dir=Path("/project/.lq/live/abc-123"),
            env={"PATH": "/usr/bin"},
        )
        assert spec.command == "pytest tests/"
        assert spec.original_command == "pytest tests/"
        assert spec.extension_data == {}
        assert spec.collectors == []

    def test_extension_data_namespacing(self) -> None:
        spec = CommandSpec(
            command="pytest",
            original_command="pytest",
            command_name="test",
            attempt_id="abc",
            workspace=Path("/p"),
            cwd=Path("/p"),
            live_dir=Path("/p/.lq/live/abc"),
            env={},
        )
        spec.extension_data["sandbox"] = {"network": "none"}
        spec.extension_data["env"] = {"venv": ".venv"}
        assert spec.extension_data["sandbox"]["network"] == "none"
        assert "env" in spec.extension_data

    def test_command_is_mutable(self) -> None:
        spec = CommandSpec(
            command="pytest",
            original_command="pytest",
            command_name="test",
            attempt_id="abc",
            workspace=Path("/p"),
            cwd=Path("/p"),
            live_dir=Path("/p/.lq/live/abc"),
            env={},
        )
        spec.command = "bwrap -- pytest"
        assert spec.command == "bwrap -- pytest"
        assert spec.original_command == "pytest"


class TestExecutionResult:
    def test_construction(self) -> None:
        now = datetime.now()
        result = ExecutionResult(
            exit_code=0,
            output="PASSED",
            started_at=now,
            completed_at=now,
            duration_ms=1000,
        )
        assert result.exit_code == 0
        assert result.metrics == {}
        assert result.artifacts == {}
        assert result.signal is None
        assert result.timeout is False
