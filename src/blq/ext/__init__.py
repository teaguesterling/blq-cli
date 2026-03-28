"""blq extension protocol types.

Defines the structured execution pipeline: CommandSpec flows through
Extension.prepare() → Executor.execute() → Collector.collect().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


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
