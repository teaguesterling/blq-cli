"""Tests for SandboxExtension and engine dispatch."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from blq.ext import CommandSpec
from blq_sandbox import SandboxExtension
from blq_sandbox.engines import LogEngine, select_engines
from blq_sandbox.spec import SandboxSpec


def _make_spec(**overrides: Any) -> CommandSpec:
    defaults = dict(
        command="pytest tests/",
        original_command="pytest tests/",
        command_name="test",
        attempt_id="test-001",
        workspace=Path("/project"),
        cwd=Path("/project"),
        live_dir=Path("/project/.lq/live/test-001"),
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


class TestLogEngine:
    def test_passthrough(self) -> None:
        engine = LogEngine()
        result = engine.wrap("pytest", SandboxSpec(), Path("/p"), "abc")
        assert result == "pytest"

    def test_no_collector(self) -> None:
        engine = LogEngine()
        assert engine.collector(SandboxSpec(), "abc") is None

    def test_empty_capabilities(self) -> None:
        engine = LogEngine()
        assert engine.capabilities == set()


class TestActiveDimensions:
    def test_all_defaults_empty(self) -> None:
        spec = SandboxSpec()
        assert spec.active_dimensions() == set()

    def test_network(self) -> None:
        spec = SandboxSpec(network="none")
        assert "network" in spec.active_dimensions()

    def test_filesystem(self) -> None:
        spec = SandboxSpec(filesystem="readonly")
        assert "filesystem" in spec.active_dimensions()

    def test_memory(self) -> None:
        spec = SandboxSpec(memory=1024)
        assert "memory" in spec.active_dimensions()

    def test_cpu(self) -> None:
        spec = SandboxSpec(cpu=30)
        assert "cpu" in spec.active_dimensions()

    def test_processes(self) -> None:
        spec = SandboxSpec(processes="isolated")
        assert "processes" in spec.active_dimensions()

    def test_tmpfs(self) -> None:
        spec = SandboxSpec(tmpfs=1024)
        assert "tmpfs" in spec.active_dimensions()

    def test_paths_readable(self) -> None:
        spec = SandboxSpec(paths_readable=["/usr"])
        assert "paths_readable" in spec.active_dimensions()

    def test_paths_hidden(self) -> None:
        spec = SandboxSpec(paths_hidden=["/root"])
        assert "paths_hidden" in spec.active_dimensions()

    def test_test_preset_dimensions(self) -> None:
        spec = SandboxSpec.from_preset("test")
        dims = spec.active_dimensions()
        assert "network" in dims
        assert "filesystem" in dims
        assert "processes" in dims
        assert "memory" in dims
        assert "cpu" in dims


class TestSelectEngines:
    def test_no_active_dimensions_returns_log(self) -> None:
        spec = SandboxSpec()  # all defaults
        engines = {"log": LogEngine()}
        selected = select_engines(spec, engines)
        assert len(selected) == 1
        assert selected[0].name == "log"

    def test_active_dimensions_with_no_real_engines_returns_log(self) -> None:
        spec = SandboxSpec(network="none")
        engines = {"log": LogEngine()}
        selected = select_engines(spec, engines)
        assert len(selected) == 1
        assert selected[0].name == "log"


class TestSandboxExtension:
    def test_prepare_with_preset(self) -> None:
        spec = _make_spec(extension_data={"sandbox": "test"})
        ext = SandboxExtension()
        result = ext.prepare(spec)
        assert result.extension_data["sandbox_grade_w"] == "pinhole"
        assert result.extension_data["sandbox_effects_ceiling"] == 2

    def test_prepare_with_dict_config(self) -> None:
        spec = _make_spec(extension_data={
            "sandbox": {"network": "none", "filesystem": "workspace_only", "memory": "2g"}
        })
        ext = SandboxExtension()
        result = ext.prepare(spec)
        assert result.extension_data["sandbox_grade_w"] == "scoped"
        assert result.extension_data["sandbox_effects_ceiling"] == 7

    def test_prepare_without_config(self) -> None:
        spec = _make_spec()  # no sandbox in extension_data
        ext = SandboxExtension()
        result = ext.prepare(spec)
        assert "sandbox_grade_w" not in result.extension_data

    def test_prepare_with_none_config(self) -> None:
        spec = _make_spec(extension_data={"sandbox": None})
        ext = SandboxExtension()
        result = ext.prepare(spec)
        assert "sandbox_grade_w" not in result.extension_data

    def test_validate_valid_config(self) -> None:
        ext = SandboxExtension()
        warnings = ext.validate({"network": "none"})
        assert warnings == []

    def test_validate_invalid_config(self) -> None:
        ext = SandboxExtension()
        warnings = ext.validate({"network": "invalid"})
        assert len(warnings) > 0
