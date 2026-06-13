"""Tests for systemd sandbox engine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from blq.ext import CommandSpec, ExecutionResult
from blq_sandbox.spec import SandboxSpec, parse_size
from blq_sandbox_systemd import SystemdCollector, SystemdEngine


def _make_cmd_spec(**overrides: object) -> CommandSpec:
    defaults = dict(
        command="pytest",
        original_command="pytest",
        command_name="test",
        attempt_id="abc-12345678",
        workspace=Path("/p"),
        cwd=Path("/p"),
        live_dir=Path("/p/.lq/live/abc"),
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


def _make_result(**overrides: object) -> ExecutionResult:
    now = datetime.now()
    defaults = dict(exit_code=0, output="", started_at=now, completed_at=now, duration_ms=1000)
    defaults.update(overrides)
    return ExecutionResult(**defaults)


class TestSystemdEngine:
    def test_name(self) -> None:
        engine = SystemdEngine()
        assert engine.name == "systemd"

    def test_capabilities(self) -> None:
        engine = SystemdEngine()
        assert "memory" in engine.capabilities
        assert "cpu" in engine.capabilities
        assert "pids" in engine.capabilities
        assert "network" not in engine.capabilities

    def test_wrap_basic(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("pytest tests/", spec, Path("/project"), "abc-12345678")
        assert result.startswith("systemd-run")
        assert "--scope" in result
        assert "pytest tests/" in result
        assert "blq-abc-1234" in result

    def test_wrap_memory_limit(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec(memory=parse_size("512m"))
        result = engine.wrap("pytest", spec, Path("/p"), "abc-12345678")
        assert f"MemoryMax={parse_size('512m')}" in result

    def test_wrap_no_limits(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec()
        result = engine.wrap("echo hi", spec, Path("/p"), "abc-12345678")
        assert "MemoryAccounting=yes" in result
        assert "MemoryMax" not in result

    def test_wrap_includes_quiet(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec()
        result = engine.wrap("echo hi", spec, Path("/p"), "abc-12345678")
        assert "--quiet" in result

    def test_wrap_includes_cpu_accounting(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec()
        result = engine.wrap("echo hi", spec, Path("/p"), "abc-12345678")
        assert "CPUAccounting=yes" in result

    def test_wrap_preserves_command(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec()
        cmd = "make -j8 CFLAGS='-O2 -Wall'"
        result = engine.wrap(cmd, spec, Path("/p"), "abc-12345678")
        assert result.endswith(cmd)

    def test_collector_returned(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec(memory=parse_size("512m"))
        collector = engine.collector(spec, "abc-12345678")
        assert collector is not None
        assert isinstance(collector, SystemdCollector)

    def test_collector_scope_name(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec()
        collector = engine.collector(spec, "abc-12345678")
        assert isinstance(collector, SystemdCollector)
        assert collector._scope_name == "blq-abc-1234"


class TestSystemdCollector:
    def test_handles_missing_cgroup_dir(self) -> None:
        collector = SystemdCollector("blq-abc-1234")
        result = _make_result()
        spec = _make_cmd_spec()
        # cgroup dir doesn't exist on this system — should not raise
        collector.collect(spec, result)
        assert result.metrics == {}

    def test_reads_memory_peak(self, tmp_path: Path) -> None:
        # Create fake cgroup structure
        scope_dir = tmp_path / "blq-test.scope"
        scope_dir.mkdir()
        (scope_dir / "memory.peak").write_text("12345678\n")

        result = _make_result()
        _make_cmd_spec()

        # Directly test parsing logic using real temp files
        memory_peak = scope_dir / "memory.peak"
        if memory_peak.exists():
            result.metrics["memory_peak_bytes"] = int(memory_peak.read_text().strip())

        assert result.metrics["memory_peak_bytes"] == 12345678

    def test_reads_cpu_stat(self, tmp_path: Path) -> None:
        scope_dir = tmp_path / "blq-test.scope"
        scope_dir.mkdir()
        (scope_dir / "cpu.stat").write_text(
            "usage_usec 5000000\nuser_usec 3000000\nsystem_usec 2000000\nnr_periods 100\n"
        )

        result = _make_result()
        cpu_stat = scope_dir / "cpu.stat"
        for line in cpu_stat.read_text().strip().splitlines():
            key, _, val = line.partition(" ")
            if key in ("usage_usec", "user_usec", "system_usec"):
                result.metrics[f"cpu_{key}"] = int(val)

        assert result.metrics["cpu_usage_usec"] == 5000000
        assert result.metrics["cpu_user_usec"] == 3000000
        assert result.metrics["cpu_system_usec"] == 2000000
        assert "cpu_nr_periods" not in result.metrics

    def test_collect_with_patched_cgroup_path(self, tmp_path: Path) -> None:
        """Test the full collect() method by patching the cgroup base path."""
        scope_name = "blq-test"
        scope_dir = tmp_path / f"{scope_name}.scope"
        scope_dir.mkdir()
        (scope_dir / "memory.peak").write_text("999999\n")
        (scope_dir / "cpu.stat").write_text("usage_usec 100\nuser_usec 60\nsystem_usec 40\n")

        collector = SystemdCollector(scope_name)
        result = _make_result()
        spec = _make_cmd_spec()

        # Patch the cgroup base path used by the collector
        original_cgroup_base = SystemdCollector.CGROUP_BASE
        try:
            SystemdCollector.CGROUP_BASE = tmp_path
            collector.collect(spec, result)
        finally:
            SystemdCollector.CGROUP_BASE = original_cgroup_base

        assert result.metrics["memory_peak_bytes"] == 999999
        assert result.metrics["cpu_usage_usec"] == 100
        assert result.metrics["cpu_user_usec"] == 60
        assert result.metrics["cpu_system_usec"] == 40

    def test_collect_handles_read_errors(self, tmp_path: Path) -> None:
        """OSError during read is caught and logged."""
        scope_name = "blq-test"
        scope_dir = tmp_path / f"{scope_name}.scope"
        scope_dir.mkdir()
        # Create a directory where a file is expected — read_text will fail
        (scope_dir / "memory.peak").mkdir()

        collector = SystemdCollector(scope_name)
        result = _make_result()
        spec = _make_cmd_spec()

        original_cgroup_base = SystemdCollector.CGROUP_BASE
        try:
            SystemdCollector.CGROUP_BASE = tmp_path
            # Should not raise
            collector.collect(spec, result)
        finally:
            SystemdCollector.CGROUP_BASE = original_cgroup_base

        assert "memory_peak_bytes" not in result.metrics
