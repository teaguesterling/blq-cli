"""Executor that runs commands through Slurm `srun`, or locally per the route."""

from __future__ import annotations

import logging
import math
import os
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

# srun 23.11 prints exactly this one line when the controller is unreachable (measured):
# the command never started. The whole output must match, so a started command that
# prints the line itself is not rerun.
_UNABLE_TO_CONTACT = re.compile(
    r"srun: error: Unable to allocate resources: Unable to contact slurm controller.*"
)
# srun prints this line at any position in the merged output (measured), and a parser
# loses a multi-line record that the line splits. So it moves to the end.
_TASK_STATUS = re.compile(r"srun: error: \S+: task \d+: .*\n?")
_SCANCEL_LIMIT_S = 5
_PROBE_LIMIT_S = 5


def _status_lines_last(text: str) -> str:
    """Move srun's task status lines to the end. No line is dropped."""
    lines = text.splitlines(keepends=True)
    status = [line for line in lines if _TASK_STATUS.fullmatch(line)]
    if not status:
        return text
    body = [line for line in lines if not _TASK_STATUS.fullmatch(line)]
    if body and not body[-1].endswith("\n"):
        body[-1] += "\n"
    return "".join(body + status)


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
        f"-c {shlex.quote(str(res.cpus))}",
        f"--mem={shlex.quote(res.mem)}",
        f"-p {shlex.quote(res.partition)}",
        f"-J {shlex.quote('blq-' + job_name)}",
        f"--chdir={shlex.quote(str(cwd))}",
    ]
    if timeout is not None:
        parts.append(f"-t {math.ceil(timeout / 60) + 1}")
    # srun reorders the task's stdout and stderr (measured), so the task merges them first.
    parts += ["/bin/sh", "-c", shlex.quote(f"exec 2>&1; {command}")]
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
        if (
            not result.timeout
            and result.exit_code > 0
            and _UNABLE_TO_CONTACT.fullmatch(result.output.strip())
        ):
            return self._execute_local(
                spec, "[blq] Slurm unavailable, running locally: controller unreachable"
            )
        if self._live_output_path:
            # Rewrite what LocalExecutor wrote, so its [TIMEOUT ...] marker survives.
            live = self._live_output_path.read_text()
            self._live_output_path.write_text(_status_lines_last(live))
        return replace(result, output=_status_lines_last(result.output))

    def _execute_local(self, spec: CommandSpec, warning: str | None) -> ExecutionResult:
        if warning is not None:
            print(warning, file=sys.stderr, flush=True)
        result = self._local.execute(replace(spec, command=self._memory_capped(spec.command)))
        if warning is None:
            return result
        result = replace(result, output=warning + "\n" + result.output)
        if self._live_output_path:
            # Prepend to what LocalExecutor wrote, so its [TIMEOUT ...] marker survives.
            live = self._live_output_path.read_text()
            self._live_output_path.write_text(warning + "\n" + live)
        return result

    def _memory_capped(self, command: str) -> str:
        limit = self._policy.local_memory_max if self._policy else None
        if not limit or os.environ.get("SLURM_JOB_ID"):  # a wrap would leave the job's cgroup
            return command
        if shutil.which("systemd-run") is None or not self._systemd_run_works():
            return command
        return (
            f"systemd-run --user --scope -q -p MemoryMax={shlex.quote(limit)} "
            f"/bin/sh -c {shlex.quote(command)}"
        )

    @staticmethod
    def _systemd_run_works() -> bool:
        try:
            proc = subprocess.run(
                ["systemd-run", "--user", "--scope", "-q", "true"],
                capture_output=True,
                text=True,
                timeout=_PROBE_LIMIT_S,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("systemd-run probe failed, running without a memory cap: %s", e)
            return False
        if proc.returncode != 0:
            logger.warning(
                "systemd-run is unusable, running without a memory cap: %s", proc.stderr.strip()
            )
            return False
        return True

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
