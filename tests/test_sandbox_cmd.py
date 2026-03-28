"""Tests for blq sandbox CLI commands."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from blq.commands.sandbox_cmd import cmd_sandbox_inspect, cmd_sandbox_list
from blq.config_format import load_toml, save_toml


def _add_command_with_sandbox(lq_dir: Path, name: str, cmd: str, sandbox: str | dict) -> None:
    """Helper to add a command with a sandbox spec to commands.toml."""
    commands_path = lq_dir / "commands.toml"
    if commands_path.exists():
        data = load_toml(commands_path)
    else:
        data = {}
    data.setdefault("commands", {})[name] = {
        "cmd": cmd,
        "sandbox": sandbox,
    }
    save_toml(commands_path, data)


class TestSandboxList:
    def test_list_shows_header(self, initialized_project, capsys):
        args = argparse.Namespace(json=False)
        cmd_sandbox_list(args)
        output = capsys.readouterr().out
        assert "Command" in output
        assert "Sandbox" in output

    def test_list_shows_commands_without_sandbox(self, initialized_project, capsys):
        subprocess.run(
            [sys.executable, "-m", "blq", "commands", "register", "echo-test", "echo", "hi"],
            capture_output=True,
        )
        args = argparse.Namespace(json=False)
        cmd_sandbox_list(args)
        output = capsys.readouterr().out
        assert "echo-test" in output
        assert "none" in output

    def test_list_json_format(self, initialized_project, capsys):
        args = argparse.Namespace(json=True)
        cmd_sandbox_list(args)
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)

    def test_list_json_with_commands(self, initialized_project, capsys):
        subprocess.run(
            [sys.executable, "-m", "blq", "commands", "register", "echo-test", "echo", "hi"],
            capture_output=True,
        )
        args = argparse.Namespace(json=True)
        cmd_sandbox_list(args)
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) >= 1
        entry = next(e for e in data if e["command"] == "echo-test")
        assert entry["sandbox"] == "none"

    def test_list_with_sandbox_preset(self, initialized_project, capsys):
        """Command with sandbox preset shows preset name."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.ensure()
        _add_command_with_sandbox(config.lq_dir, "sandboxed-cmd", "echo hi", "test")

        args = argparse.Namespace(json=False)
        cmd_sandbox_list(args)
        output = capsys.readouterr().out
        assert "sandboxed-cmd" in output
        assert "test" in output


class TestSandboxInspect:
    def test_inspect_unknown_command(self, initialized_project):
        args = argparse.Namespace(command="nonexistent", json=False)
        with pytest.raises(SystemExit):
            cmd_sandbox_inspect(args)

    def test_inspect_no_sandbox(self, initialized_project, capsys):
        subprocess.run(
            [sys.executable, "-m", "blq", "commands", "register", "echo-test", "echo", "hi"],
            capture_output=True,
        )
        args = argparse.Namespace(command="echo-test", json=False)
        cmd_sandbox_inspect(args)
        output = capsys.readouterr().out
        assert "no sandbox" in output.lower()

    def test_inspect_with_sandbox_preset(self, initialized_project, capsys):
        """Inspect a command with a sandbox preset."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.ensure()
        _add_command_with_sandbox(config.lq_dir, "sandboxed-cmd", "echo hi", "build")

        args = argparse.Namespace(command="sandboxed-cmd", json=False)
        cmd_sandbox_inspect(args)
        output = capsys.readouterr().out
        assert "build" in output
        assert "Grade W" in output

    def test_inspect_json_output(self, initialized_project, capsys):
        """Inspect with JSON output."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.ensure()
        _add_command_with_sandbox(config.lq_dir, "sandboxed-cmd", "echo hi", "test")

        args = argparse.Namespace(command="sandboxed-cmd", json=True)
        cmd_sandbox_inspect(args)
        output = capsys.readouterr().out
        data = json.loads(output)
        assert data["command"] == "sandboxed-cmd"
        assert data["preset"] == "test"
        assert "grade_w" in data
        assert "effects_ceiling" in data
        assert "active_dimensions" in data

    def test_inspect_custom_sandbox(self, initialized_project, capsys):
        """Inspect a command with custom sandbox dict."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.ensure()
        _add_command_with_sandbox(
            config.lq_dir,
            "custom-cmd",
            "echo hi",
            {"network": "none", "filesystem": "readonly", "timeout": "45s"},
        )

        args = argparse.Namespace(command="custom-cmd", json=False)
        cmd_sandbox_inspect(args)
        output = capsys.readouterr().out
        assert "custom" in output
        assert "Grade W" in output
        assert "network" in output
