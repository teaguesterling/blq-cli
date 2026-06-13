"""Tests for the BwrapEngine class."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blq_sandbox.spec import SandboxSpec
from blq_sandbox_bwrap import BwrapEngine


@pytest.fixture
def engine():
    return BwrapEngine()


class TestBwrapEngineProtocol:
    """BwrapEngine satisfies the SandboxEngine protocol."""

    def test_has_name(self, engine):
        assert engine.name == "bwrap"

    def test_has_capabilities(self, engine):
        assert "network" in engine.capabilities
        assert "filesystem" in engine.capabilities
        assert "processes" in engine.capabilities
        assert "tmpfs" in engine.capabilities

    def test_wrap_returns_string(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert isinstance(result, str)

    def test_wrap_starts_with_bwrap(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert result.startswith("bwrap ")

    def test_wrap_ends_with_original_command(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert result.endswith("-- echo hello")

    def test_wrap_includes_die_with_parent(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert "--die-with-parent" in result

    def test_collector_returns_none(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.collector(spec, "abc-123")
        assert result is None


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bwrap not installed")
class TestBwrapEngineIntegration:
    """Integration tests that actually run bwrap."""

    def test_simple_command_in_sandbox(self, engine):
        import subprocess

        spec = SandboxSpec(network="none", filesystem="readonly", processes="isolated")
        wrapped = engine.wrap("echo sandbox-works", spec, Path("/tmp"), "test-int")
        result = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert "sandbox-works" in result.stdout

    def test_network_blocked(self, engine):
        import subprocess

        spec = SandboxSpec(network="none", filesystem="readonly")
        wrapped = engine.wrap(
            "python3 -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:1')\"",
            spec,
            Path("/tmp"),
            "test-net",
        )
        result = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0

    def test_readonly_blocks_write(self, engine, tmp_path):
        import subprocess

        target = tmp_path / "canary.txt"
        spec = SandboxSpec(network="none", filesystem="readonly")
        wrapped = engine.wrap(
            f"touch {target}",
            spec,
            Path("/tmp"),
            "test-ro",
        )
        subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)
        assert not target.exists()

    def test_workspace_only_allows_workspace_write(self, engine, tmp_path):
        import subprocess

        target = tmp_path / "output.txt"
        spec = SandboxSpec(network="none", filesystem="workspace_only")
        wrapped = engine.wrap(
            f"touch {target}",
            spec,
            tmp_path,
            "test-ws",
        )
        result = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert target.exists()

    def test_preset_test(self, engine):
        import subprocess

        spec = SandboxSpec.from_preset("test")
        wrapped = engine.wrap("echo preset-ok", spec, Path("/tmp"), "test-preset")
        result = subprocess.run(wrapped, shell=True, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert "preset-ok" in result.stdout


class TestBwrapEngineDiscovery:
    """Verify bwrap engine is found by the extension system."""

    def test_load_engines_finds_bwrap(self):
        from blq_sandbox.engines import load_engines

        engines = load_engines()
        assert "bwrap" in engines
        assert engines["bwrap"].name == "bwrap"

    def test_select_engines_picks_bwrap_for_network(self):
        from blq_sandbox.engines import load_engines, select_engines

        spec = SandboxSpec(network="none", filesystem="readonly", processes="isolated")
        engines = load_engines()
        selected = select_engines(spec, engines)
        engine_names = [e.name for e in selected]
        assert "bwrap" in engine_names

    def test_bwrap_covers_more_than_systemd(self):
        from blq_sandbox.engines import load_engines

        engines = load_engines()
        if "bwrap" in engines and "systemd" in engines:
            bwrap_caps = engines["bwrap"].capabilities
            systemd_caps = engines["systemd"].capabilities
            # bwrap covers network/filesystem/processes which systemd doesn't
            assert "network" in bwrap_caps
            assert "network" not in systemd_caps
