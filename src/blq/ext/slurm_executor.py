"""Executor that runs commands through Slurm `srun`, or locally per the route."""

from __future__ import annotations

import logging
import math
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from blq.ext import CommandSpec, ExecutionResult
from blq.ext.local_executor import LocalExecutor
from blq.ext.slurm_policy import Route, SlurmPolicy, SlurmResources

logger = logging.getLogger("blq-ext")

# srun prints this when the controller cannot be reached: the command never started.
_UNABLE_TO_CONTACT = re.compile(r"^srun: error: Unable to contact", re.MULTILINE)
_SCANCEL_LIMIT_S = 5


def srun_command(
    command: str,
    cwd: Path,
    res: SlurmResources,
    job_name: str,
    timeout: int | None,
) -> str:
    """Build the `srun` command line that runs `command` as one task in `cwd`."""
    parts = [
        "srun",
        "-n 1",
        f"-c {res.cpus}",
        f"--mem={shlex.quote(res.mem)}",
        f"-p {shlex.quote(res.partition)}",
        f"-J {shlex.quote('blq-' + job_name)}",
        f"--chdir={shlex.quote(str(cwd))}",
    ]
    if timeout is not None:
        parts.append(f"-t {math.ceil(timeout / 60) + 1}")
    parts += ["/bin/sh", "-c", shlex.quote(command)]
    return " ".join(parts)


class SlurmExecutor:
    """Run a command per a `Route`: through `srun`, or locally (optionally memory-capped)."""

    name = "slurm"

    def __init__(
        self,
        route: Route,
        policy: SlurmPolicy | None,
        quiet: bool = False,
        live_output_path: Path | None = None,
    ):
        self._route = route
        self._policy = policy
        self._quiet = quiet
        self._live_output_path = live_output_path
        self._local = LocalExecutor(quiet=quiet, live_output_path=live_output_path)

    def execute(self, spec: CommandSpec) -> ExecutionResult:
        if self._route.kind == "slurm" and self._route.resources is not None:
            return self._execute_slurm(spec, self._route.resources)
        warning = None
        if self._route.warn:
            warning = f"[blq] Slurm unavailable, running locally: {self._route.reason}"
        return self._execute_local(spec, warning)

    def _execute_slurm(self, spec: CommandSpec, res: SlurmResources) -> ExecutionResult:
        wrapped = srun_command(spec.command, spec.cwd, res, spec.attempt_id, spec.timeout)
        result = self._local.execute(replace(spec, command=wrapped))
        if result.timeout or result.exit_code < 0:
            self._scancel(spec.attempt_id)
        if result.exit_code != 0 and _UNABLE_TO_CONTACT.search(result.output):
            return self._execute_local(
                spec, "[blq] Slurm unavailable, running locally: controller unreachable"
            )
        return result

    def _execute_local(self, spec: CommandSpec, warning: str | None) -> ExecutionResult:
        result = self._local.execute(replace(spec, command=self._memory_capped(spec.command)))
        if warning is None:
            return result
        print(warning, file=sys.stderr, flush=True)
        result = replace(result, output=warning + "\n" + result.output)
        if self._live_output_path:  # LocalExecutor wrote only its own lines
            self._live_output_path.write_text(result.output)
        return result

    def _memory_capped(self, command: str) -> str:
        limit = self._policy.local_memory_max if self._policy else None
        if not limit or shutil.which("systemd-run") is None:
            return command
        return (
            f"systemd-run --user --scope -q -p MemoryMax={shlex.quote(limit)} "
            f"/bin/sh -c {shlex.quote(command)}"
        )

    @staticmethod
    def _scancel(attempt_id: str) -> None:
        try:
            subprocess.run(
                ["scancel", "-n", f"blq-{attempt_id}"],
                capture_output=True,
                timeout=_SCANCEL_LIMIT_S,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("scancel failed for blq-%s: %s", attempt_id, e)
