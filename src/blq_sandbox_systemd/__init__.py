"""blq-sandbox-systemd: systemd-run cgroup enforcement engine."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from blq.ext import Collector, CommandSpec, ExecutionResult
from blq_sandbox.spec import SandboxSpec

logger = logging.getLogger("blq-sandbox-systemd")


class SystemdCollector:
    """Reads cgroup stats after execution."""

    CGROUP_BASE: Path = Path("/sys/fs/cgroup/system.slice")

    def __init__(self, scope_name: str) -> None:
        self._scope_name = scope_name

    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        cgroup_path = self.CGROUP_BASE / f"{self._scope_name}.scope"
        if not cgroup_path.exists():
            return

        try:
            memory_peak = cgroup_path / "memory.peak"
            if memory_peak.exists():
                result.metrics["memory_peak_bytes"] = int(
                    memory_peak.read_text().strip()
                )

            cpu_stat = cgroup_path / "cpu.stat"
            if cpu_stat.exists():
                for line in cpu_stat.read_text().strip().splitlines():
                    key, _, val = line.partition(" ")
                    if key in ("usage_usec", "user_usec", "system_usec"):
                        result.metrics[f"cpu_{key}"] = int(val)
        except (OSError, ValueError) as e:
            logger.warning(f"Failed to read cgroup stats: {e}")


class SystemdEngine:
    """systemd-run --scope engine for cgroup resource limits and monitoring."""

    name = "systemd"
    capabilities = {"memory", "cpu", "pids"}

    def wrap(
        self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str
    ) -> str:
        scope_name = f"blq-{attempt_id[:8]}"
        parts = [
            "systemd-run",
            "--scope",
            "--quiet",
            f"--unit={scope_name}",
            "-p",
            "MemoryAccounting=yes",
            "-p",
            "CPUAccounting=yes",
        ]
        if spec.memory is not None:
            parts.extend(["-p", f"MemoryMax={spec.memory}"])
        parts.append("--")
        parts.append(command)
        return " ".join(parts)

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        scope_name = f"blq-{attempt_id[:8]}"
        return SystemdCollector(scope_name)
