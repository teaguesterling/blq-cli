"""Tests for sandbox integration with command registration."""
from __future__ import annotations

import subprocess
import sys

import pytest

from blq.commands.core import RegisteredCommand


class TestSandboxOnRegister:
    def test_sandbox_stored_in_extra(self):
        cmd = RegisteredCommand(name="test", cmd="pytest")
        cmd._extra["sandbox"] = "test"
        d = cmd.to_dict()
        assert d["sandbox"] == "test"

    def test_sandbox_preset_name(self):
        cmd = RegisteredCommand(name="test", cmd="pytest")
        cmd._extra["sandbox"] = "build"
        d = cmd.to_dict()
        assert d["sandbox"] == "build"


class TestDefaultSandboxPresets:
    def test_test_gets_test_preset(self):
        from blq.commands.init_cmd import _DEFAULT_SANDBOX_PRESETS

        assert _DEFAULT_SANDBOX_PRESETS.get("test") == "test"

    def test_build_gets_build_preset(self):
        from blq.commands.init_cmd import _DEFAULT_SANDBOX_PRESETS

        assert _DEFAULT_SANDBOX_PRESETS.get("build") == "build"

    def test_lint_gets_readonly_preset(self):
        from blq.commands.init_cmd import _DEFAULT_SANDBOX_PRESETS

        assert _DEFAULT_SANDBOX_PRESETS.get("lint") == "readonly"

    def test_unknown_has_no_preset(self):
        from blq.commands.init_cmd import _DEFAULT_SANDBOX_PRESETS

        assert _DEFAULT_SANDBOX_PRESETS.get("docker-build") is None


class TestRegisterCLI:
    def test_register_with_sandbox_flag(self, initialized_project):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "blq",
                "commands",
                "register",
                "mytest",
                "pytest",
                "-S",
                "test",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "sandbox: test" in result.stdout.lower() or "test" in result.stdout

    def test_register_without_sandbox(self, initialized_project):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "blq",
                "commands",
                "register",
                "mytest",
                "echo",
                "hi",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
