"""blq extension protocol types.

Defines the structured execution pipeline: CommandSpec flows through
Extension.prepare() → Executor.execute() → Collector.collect().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import os


@dataclass
class CommandSpec:
    """Structured execution request flowing through the extension pipeline."""

    # What to run
    command: str
    original_command: str

    # Identity
    command_name: str
    attempt_id: str

    # Context
    workspace: Path
    cwd: Path
    live_dir: Path

    # Environment
    env: dict[str, str]

    # Resource requirements
    timeout: int | None = None

    # Extension data — namespaced by config_key
    extension_data: dict[str, Any] = field(default_factory=dict)

    # Collectors — registered during prepare(), run post-execution in reverse
    collectors: list[Collector] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result from an executor."""

    exit_code: int
    output: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    signal: int | None = None
    timeout: bool = False
    pid: int | None = None

    # Collector contributions
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)


class Collector(Protocol):
    """Gathers artifacts post-execution."""

    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None: ...


class Extension(Protocol):
    """Modifies execution context. Composable."""

    name: str
    config_key: str

    def prepare(self, spec: CommandSpec) -> CommandSpec: ...
    def validate(self, config: dict[str, Any]) -> list[str]: ...
    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None: ...


class Executor(Protocol):
    """Runs the command. Terminal — only one active."""

    name: str

    def execute(self, spec: CommandSpec) -> ExecutionResult: ...


#: Terminal width presented to captured child processes. Wide enough that the
#: tools we parse stop abbreviating; not so wide that a tool wrapping to it
#: produces unreadable raw logs.
CAPTURE_COLUMNS = 200


def capture_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a captured child process.

    Anything that formats for a terminal consults COLUMNS (or an ioctl) and
    falls back to 80 columns when there is no TTY — which is always, under a
    capturing runner. `pytest -q` abbreviates its short-summary line on that
    basis, and since blq parses that line, the stored message loses everything
    past roughly 80 columns minus the length of the test name:

        73-char test name  ->   7 characters survive ('asse...')
        35-char test name  ->  45 characters survive, nothing is lost

    So how diagnosable a failure was depended on how long someone had named
    the test. Presenting a wide COLUMNS fixes the whole class at the point of
    capture — no parser can recover text the tool never printed.

    An explicit COLUMNS from the caller wins: someone who set it meant it.
    """
    env = dict(os.environ if base is None else base)
    env.setdefault("COLUMNS", str(CAPTURE_COLUMNS))
    return env
