"""Tests for LocalExecutor."""
from __future__ import annotations

from pathlib import Path

from blq.ext import CommandSpec
from blq.ext.local_executor import LocalExecutor


def _make_spec(command: str = "echo hello", **overrides) -> CommandSpec:
    defaults = dict(
        command=command,
        original_command=command,
        command_name="test",
        attempt_id="local-test-001",
        workspace=Path("/project"),
        cwd=Path("/project"),
        live_dir=Path("/tmp/blq-test-live"),
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


class TestLocalExecutor:
    def test_simple_command(self) -> None:
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("echo hello")
        result = executor.execute(spec)
        assert result.exit_code == 0
        assert "hello" in result.output
        assert result.pid is not None
        assert result.timeout is False

    def test_failing_command(self) -> None:
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("exit 42")
        result = executor.execute(spec)
        assert result.exit_code == 42

    def test_timeout(self) -> None:
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("sleep 60", timeout=1)
        result = executor.execute(spec)
        assert result.timeout is True
        assert result.exit_code == -1

    def test_output_capture(self) -> None:
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("echo line1 && echo line2 && echo line3")
        result = executor.execute(spec)
        assert "line1" in result.output
        assert "line2" in result.output
        assert "line3" in result.output

    def test_live_output_file(self, tmp_path: Path) -> None:
        live_path = tmp_path / "live" / "output.log"
        executor = LocalExecutor(quiet=True, live_output_path=live_path)
        spec = _make_spec("echo hello_live")
        result = executor.execute(spec)
        assert result.exit_code == 0
        assert live_path.exists()
        content = live_path.read_text()
        assert "hello_live" in content

    def test_duration_tracked(self) -> None:
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("sleep 0.1")
        result = executor.execute(spec)
        assert result.duration_ms >= 50  # at least ~100ms
        assert result.started_at <= result.completed_at

    def test_multiline_output(self) -> None:
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("printf 'a\\nb\\nc\\n'")
        result = executor.execute(spec)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 3

    def test_stderr_captured(self) -> None:
        """stderr should be merged into stdout via STDOUT redirect."""
        executor = LocalExecutor(quiet=True)
        spec = _make_spec("echo stdout_msg && echo stderr_msg >&2")
        result = executor.execute(spec)
        assert "stdout_msg" in result.output
        assert "stderr_msg" in result.output
