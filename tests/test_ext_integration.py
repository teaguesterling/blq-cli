"""End-to-end integration tests for the extension pipeline."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from blq.ext import CommandSpec, ExecutionResult
from blq.ext.pipeline import run_pipeline
from blq_sandbox import SandboxExtension
from blq_sandbox.spec import SandboxSpec


class FakeExecutor:
    """Records what command was executed."""
    name = "fake"
    def __init__(self):
        self.executed_command: str | None = None

    def execute(self, spec: CommandSpec) -> ExecutionResult:
        self.executed_command = spec.command
        now = datetime.now()
        return ExecutionResult(
            exit_code=0, output="PASSED", started_at=now,
            completed_at=now, duration_ms=500,
        )


def _make_spec(**overrides: Any) -> CommandSpec:
    defaults = dict(
        command="pytest tests/",
        original_command="pytest tests/",
        command_name="test",
        attempt_id="int-test-001",
        workspace=Path("/project"),
        cwd=Path("/project"),
        live_dir=Path("/project/.lq/live/int-test-001"),
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


class TestSandboxPresetIntegration:
    """Test sandbox preset flows through the full pipeline."""

    def test_preset_resolves_grade(self) -> None:
        spec = _make_spec(extension_data={"sandbox": "test"})
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)

        assert result.exit_code == 0
        assert spec.extension_data["sandbox_grade_w"] == "pinhole"
        assert spec.extension_data["sandbox_effects_ceiling"] == 2

    def test_preset_command_unchanged_with_log_engine(self) -> None:
        """With only LogEngine (no real engines), command passes through unchanged."""
        spec = _make_spec(extension_data={"sandbox": "test"})
        ext = SandboxExtension()
        executor = FakeExecutor()
        run_pipeline(spec, [ext], executor)
        assert executor.executed_command == "pytest tests/"


class TestSandboxDictIntegration:
    """Test sandbox dict config flows through the pipeline."""

    def test_dict_config_resolves_grade(self) -> None:
        spec = _make_spec(extension_data={
            "sandbox": {
                "network": "none",
                "filesystem": "workspace_only",
                "memory": "2g",
            },
        })
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)

        assert spec.extension_data["sandbox_grade_w"] == "scoped"

    def test_readonly_spec(self) -> None:
        spec = _make_spec(extension_data={
            "sandbox": {
                "network": "none",
                "filesystem": "readonly",
            },
        })
        ext = SandboxExtension()
        executor = FakeExecutor()
        run_pipeline(spec, [ext], executor)

        assert spec.extension_data["sandbox_grade_w"] == "pinhole"
        assert spec.extension_data["sandbox_effects_ceiling"] == 2


class TestNoSandboxIntegration:
    """Test commands without sandbox config."""

    def test_no_sandbox_config_is_passthrough(self) -> None:
        spec = _make_spec()  # no sandbox in extension_data
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)

        assert result.exit_code == 0
        assert "sandbox_grade_w" not in spec.extension_data

    def test_empty_extension_data(self) -> None:
        spec = _make_spec(extension_data={})
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)
        assert result.exit_code == 0


class TestMultipleExtensions:
    """Test pipeline with multiple extensions."""

    def test_sandbox_with_other_extensions(self) -> None:
        """Sandbox extension ignores other extension configs."""
        spec = _make_spec(extension_data={
            "sandbox": "readonly",
            "env": {"venv": ".venv"},  # not handled by sandbox
        })
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)

        assert spec.extension_data["sandbox_grade_w"] == "pinhole"
        # env config preserved but not processed (no env extension)
        assert spec.extension_data["env"] == {"venv": ".venv"}
