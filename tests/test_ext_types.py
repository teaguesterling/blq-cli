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


class TestConfigPassthrough:
    def test_extra_sections_preserved_on_roundtrip(self, tmp_path) -> None:
        from pathlib import Path

        from blq.commands.core import RegisteredCommand, _load_commands_impl
        from blq.config_format import save_toml

        lq_dir = Path(str(tmp_path))
        commands_path = lq_dir / "commands.toml"

        data = {
            "commands": {
                "test": {
                    "cmd": "pytest tests/",
                    "description": "Run tests",
                    "sandbox": {
                        "network": "none",
                        "filesystem": "readonly",
                    },
                    "env": {
                        "venv": ".venv",
                    },
                }
            }
        }
        save_toml(commands_path, data)

        loaded = _load_commands_impl(lq_dir)
        cmd = loaded["test"]
        assert cmd._extra["sandbox"] == {"network": "none", "filesystem": "readonly"}
        assert cmd._extra["env"] == {"venv": ".venv"}
        assert cmd.cmd == "pytest tests/"

    def test_extra_sections_survive_save(self, tmp_path) -> None:
        from pathlib import Path

        from blq.commands.core import RegisteredCommand, _load_commands_impl, _save_commands_impl

        lq_dir = Path(str(tmp_path))

        cmd = RegisteredCommand(
            name="test",
            cmd="pytest tests/",
            _extra={"sandbox": {"network": "none"}, "env": {"venv": ".venv"}},
        )
        _save_commands_impl(lq_dir, {"test": cmd})

        reloaded = _load_commands_impl(lq_dir)
        assert reloaded["test"]._extra["sandbox"] == {"network": "none"}
        assert reloaded["test"]._extra["env"] == {"venv": ".venv"}
