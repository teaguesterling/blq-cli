"""Tests for extension pipeline orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from blq.ext import CommandSpec, ExecutionResult
from blq.ext.pipeline import run_pipeline


def _make_spec(**overrides: Any) -> CommandSpec:
    defaults = dict(
        command="echo hello",
        original_command="echo hello",
        command_name="test",
        attempt_id="abc-123",
        workspace=Path("/project"),
        cwd=Path("/project"),
        live_dir=Path("/project/.lq/live/abc-123"),
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


class RecordingExtension:
    def __init__(self, name: str, prefix: str = ""):
        self.name = name
        self.config_key = name
        self.prepared = False
        self.prefix = prefix

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        self.prepared = True
        if self.prefix:
            spec.command = f"{self.prefix} {spec.command}"
        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        return []

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass


class RecordingExecutor:
    def __init__(self) -> None:
        self.name = "recording"
        self.executed_command: str | None = None

    def execute(self, spec: CommandSpec) -> ExecutionResult:
        self.executed_command = spec.command
        now = datetime.now()
        return ExecutionResult(
            exit_code=0,
            output="ok",
            started_at=now,
            completed_at=now,
            duration_ms=100,
        )


class RecordingCollector:
    def __init__(self, key: str, value: Any):
        self.key = key
        self.value = value

    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        result.metrics[self.key] = self.value


class FailingCollector:
    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        raise RuntimeError("collector failed")


class TestRunPipeline:
    def test_basic_flow(self) -> None:
        spec = _make_spec()
        executor = RecordingExecutor()
        result = run_pipeline(spec, [], executor)
        assert result.exit_code == 0
        assert executor.executed_command == "echo hello"

    def test_extension_prepare_modifies_command(self) -> None:
        spec = _make_spec()
        spec.extension_data["wrapper"] = {}
        ext = RecordingExtension("wrapper", prefix="sudo")
        executor = RecordingExecutor()
        run_pipeline(spec, [ext], executor)
        assert executor.executed_command == "sudo echo hello"

    def test_only_active_extensions_called(self) -> None:
        spec = _make_spec()
        spec.extension_data["active"] = {}
        active = RecordingExtension("active")
        inactive = RecordingExtension("inactive")
        executor = RecordingExecutor()
        run_pipeline(spec, [active, inactive], executor)
        assert active.prepared
        assert not inactive.prepared

    def test_collectors_run_in_reverse(self) -> None:
        spec = _make_spec()
        order: list[str] = []

        class OrderCollector:
            def __init__(self, label: str):
                self.label = label

            def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
                order.append(self.label)

        spec.collectors = [OrderCollector("first"), OrderCollector("second")]
        executor = RecordingExecutor()
        run_pipeline(spec, [], executor)
        assert order == ["second", "first"]

    def test_collector_failure_is_logged_not_raised(self) -> None:
        spec = _make_spec()
        spec.collectors = [FailingCollector(), RecordingCollector("key", "val")]
        executor = RecordingExecutor()
        result = run_pipeline(spec, [], executor)
        assert result.metrics["key"] == "val"

    def test_prepare_failure_aborts(self) -> None:
        class FailingExtension(RecordingExtension):
            def prepare(self, spec: CommandSpec) -> CommandSpec:
                raise ValueError("bad config")

        spec = _make_spec()
        spec.extension_data["failing"] = {}
        ext = FailingExtension("failing")
        executor = RecordingExecutor()
        with pytest.raises(ValueError, match="bad config"):
            run_pipeline(spec, [ext], executor)
