# Plugin Developer Guide

blq uses Python entry points for plugin discovery. There are three plugin types:

| Entry Point Group | Purpose | Protocol |
|---|---|---|
| `blq.extensions` | Modify the execution pipeline | `Extension` |
| `blq.sandbox.engines` | Enforce sandbox constraints | `SandboxEngine` |
| `blq.annotators` | Enrich stored events post-execution | `Annotator` |

All plugins are discovered at runtime via `importlib.metadata.entry_points()`. Install a package that declares the right entry point and blq picks it up automatically.

## Extension Plugins

Extensions hook into the command execution pipeline: **prepare -> execute -> collect**. Each extension can modify the `CommandSpec` before execution, validate its configuration, and store additional data after execution.

### Protocol

```python
# src/blq/ext/__init__.py

class Extension(Protocol):
    name: str
    config_key: str  # matches key in extension_data and commands.toml

    def prepare(self, spec: CommandSpec) -> CommandSpec: ...
    def validate(self, config: dict[str, Any]) -> list[str]: ...
    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None: ...
```

- **`prepare(spec)`** -- Modify the command spec before execution. Return the (possibly mutated) spec. This is where you wrap commands, adjust environment variables, or register collectors.
- **`validate(config)`** -- Validate the extension's config from `commands.toml`. Return a list of error messages (empty = valid).
- **`store(spec, result, store)`** -- Called after execution. Write extension-specific data to the database.

### Key Types

```python
@dataclass
class CommandSpec:
    command: str              # The command string to execute (mutable by extensions)
    original_command: str     # The original command before any modification
    command_name: str         # Registered command name
    attempt_id: str           # Unique ID for this execution attempt
    workspace: Path           # Project root
    cwd: Path                 # Working directory
    live_dir: Path            # Directory for live output files
    env: dict[str, str]       # Environment variables
    timeout: int | None       # Timeout in seconds
    extension_data: dict[str, Any]  # Config from commands.toml, keyed by config_key
    collectors: list[Collector]     # Post-execution collectors (append during prepare)

@dataclass
class ExecutionResult:
    exit_code: int
    output: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    signal: int | None
    timeout: bool
    pid: int | None
    metrics: dict[str, Any]   # Collector contributions
    artifacts: dict[str, Path]
```

### Registration

In your package's `pyproject.toml`:

```toml
[project.entry-points."blq.extensions"]
my_extension = "my_package:MyExtension"
```

The entry point value must be a callable (typically a class) that returns an `Extension` instance when called with no arguments.

### Configuration

Users configure your extension in `.lq/commands.toml` under a section matching your `config_key`:

```toml
[commands.test.my_extension]
option1 = "value"
option2 = true
```

This config is available during `prepare()` as `spec.extension_data["my_extension"]` and validated via `validate()`.

### Discovery and Ordering

Extensions are loaded by `load_extensions()` in `src/blq/ext/discovery.py`. They are ordered by a priority list (default: `["env", "sandbox", "platform"]`). Extensions not in the list run last, sorted alphabetically. Each extension's `prepare()` is called in order, so later extensions see modifications from earlier ones.

### Example: Minimal Extension

```python
# my_blq_ext.py
from dataclasses import dataclass, field
from typing import Any
from blq.ext import CommandSpec, ExecutionResult

class TimestampExtension:
    name = "timestamp"
    config_key = "timestamp"

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        config = spec.extension_data.get("timestamp", {})
        if config.get("enabled", True):
            # Prefix command with timestamp utility
            spec.command = f"ts '[%Y-%m-%d %H:%M:%S]' | {spec.command}"
        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if "enabled" in config and not isinstance(config["enabled"], bool):
            errors.append("timestamp.enabled must be a boolean")
        return errors

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass  # Nothing to persist
```

```toml
# pyproject.toml
[project.entry-points."blq.extensions"]
timestamp = "my_blq_ext:TimestampExtension"
```

## Sandbox Engine Plugins

Engines enforce sandbox specs by wrapping commands with isolation tooling. The sandbox extension selects engines based on which `capabilities` they advertise.

### Protocol

```python
# src/blq_sandbox/engines.py

class SandboxEngine(Protocol):
    name: str
    capabilities: set[str]  # dimensions this engine handles (e.g., "network", "filesystem")

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str) -> str: ...
    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None: ...
```

- **`wrap(command, spec, workspace, attempt_id)`** -- Return a modified command string that enforces the spec's constraints. Multiple engines can wrap the same command (they compose).
- **`collector(spec, attempt_id)`** -- Optionally return a `Collector` that gathers post-execution data (resource usage, violation logs, etc.).

### Capabilities and Selection

A `SandboxSpec` has dimensions like `network`, `filesystem`, `process`, `time`, `memory`. Each engine declares which dimensions it can enforce via `capabilities`. The engine selector (`select_engines()` in `src/blq_sandbox/engines.py`) picks the minimal set of engines to cover all active dimensions.

If no engine covers a dimension, blq logs a warning but proceeds.

Built-in engines:
- **`bwrap`** -- Linux namespace isolation (network, filesystem)
- **`systemd`** -- cgroup resource limits (time, memory, process)
- **`log`** -- No-op fallback, always available

### Registration

```toml
[project.entry-points."blq.sandbox.engines"]
my_engine = "my_package:MyEngine"
```

### Collector Protocol

Collectors gather data after execution completes:

```python
class Collector(Protocol):
    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None: ...
```

Register collectors during `prepare()` by appending to `spec.collectors`. They run in **reverse** order after execution -- last registered runs first. Engines return collectors from their `collector()` method; the sandbox extension appends them automatically.

Write results into `result.metrics` or `result.artifacts`.

### Example: Docker Engine

```python
# my_docker_engine.py
from pathlib import Path
from blq_sandbox.spec import SandboxSpec
from blq.ext import Collector

class DockerEngine:
    name = "docker"
    capabilities = {"network", "filesystem", "process"}

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str) -> str:
        parts = ["docker", "run", "--rm"]
        if spec.network == "none":
            parts.append("--network=none")
        if spec.filesystem == "readonly":
            parts.extend(["-v", f"{workspace}:{workspace}:ro"])
        else:
            parts.extend(["-v", f"{workspace}:{workspace}"])
        parts.extend(["-w", str(workspace), "ubuntu:latest", "bash", "-c", command])
        return " ".join(parts)

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        return None
```

```toml
[project.entry-points."blq.sandbox.engines"]
docker = "my_docker_engine:DockerEngine"
```

## Annotator Plugins

Annotators enrich stored events after execution. They run against the database and attach structured annotations to individual events.

### Protocol

```python
# src/blq/ext/annotator.py

class Annotator(Protocol):
    name: str
    eager: bool  # True = runs during `blq run`, False = runs on demand

    def should_annotate(self, context: RunContext) -> bool: ...
    def annotate(self, context: RunContext) -> None: ...
```

- **`eager`** -- Eager annotators run automatically after every `blq run`. Non-eager annotators run only when explicitly requested (e.g., via `blq inspect --full`).
- **`should_annotate(context)`** -- Return `True` if this annotator applies to the given run. Check `context.events`, `context.exit_code`, or `context.extension_data`.
- **`annotate(context)`** -- Do the work. Use `context.add_annotation()` to attach data to events.

### RunContext

`RunContext` provides lazy, DB-backed access to a completed run:

```python
class RunContext:
    conn: duckdb.DuckDBPyConnection   # Direct DB access
    invocation_id: str                 # Run ID
    source_root: Path                  # Project root for resolving file paths
    events: list[dict[str, Any]]       # Parsed events (lazy-loaded)
    metadata: dict[str, Any]           # source_name, cmd, cwd, extension_data, timestamp
    extension_data: dict[str, Any]     # Shortcut for metadata["extension_data"]
    exit_code: int | None              # Process exit code
    duration_ms: int | None            # Execution duration

    def add_annotation(self, event_id: str, annotation: Annotation) -> None: ...
```

### Annotation

```python
@dataclass
class Annotation:
    annotator: str   # Name of the annotator that created this
    type: str        # Category (e.g., "source_context", "suggestion", "link")
    display: str     # "inline" | "detail" | "hidden"
    data: dict[str, Any]  # Arbitrary structured data
```

Display modes:
- **`inline`** -- Shown alongside the event in default output
- **`detail`** -- Shown only in detailed views (`blq inspect`)
- **`hidden`** -- Stored but not displayed (for programmatic consumers)

### Registration

```toml
[project.entry-points."blq.annotators"]
my_annotator = "my_package:MyAnnotator"
```

### Dispatch

Annotators are loaded by `load_annotators()` and run by `run_annotators()` in `src/blq/ext/annotator.py`. Failures in one annotator are logged but do not prevent others from running. Annotators run in discovery order.

### Example: Complete Annotator Package

This annotator adds a "complexity" annotation to events in files with high cyclomatic complexity.

**`complexity_annotator.py`**:

```python
from pathlib import Path
from blq.ext.annotator import Annotation, RunContext

class ComplexityAnnotator:
    name = "complexity"
    eager = False  # Only run on demand, not every build

    def should_annotate(self, context: RunContext) -> bool:
        # Only annotate runs that produced errors with file references
        return any(e.get("ref_file") for e in context.events)

    def annotate(self, context: RunContext) -> None:
        for event in context.events:
            ref_file = event.get("ref_file")
            if not ref_file:
                continue
            path = context.source_root / ref_file
            if not path.exists():
                continue

            score = self._complexity(path)
            if score > 10:
                context.add_annotation(
                    event["id"],
                    Annotation(
                        annotator=self.name,
                        type="complexity",
                        display="detail",
                        data={"score": score, "file": ref_file},
                    ),
                )

    def _complexity(self, path: Path) -> int:
        # Placeholder -- use radon, lizard, etc.
        lines = path.read_text().splitlines()
        return sum(1 for l in lines if l.strip().startswith(("if ", "elif ", "for ", "while ")))
```

**`pyproject.toml`**:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "blq-complexity-annotator"
version = "0.1.0"
dependencies = ["blq-cli"]

[project.entry-points."blq.annotators"]
complexity = "complexity_annotator:ComplexityAnnotator"
```

Install and test:

```bash
pip install -e .
blq run test
blq inspect test:1:1 --full  # Triggers non-eager annotators
```

## Source Reference

| File | Contents |
|------|----------|
| `src/blq/ext/__init__.py` | `CommandSpec`, `ExecutionResult`, `Collector`, `Extension`, `Executor` protocols |
| `src/blq/ext/discovery.py` | Extension discovery and ordering |
| `src/blq/ext/annotator.py` | `Annotation`, `RunContext`, `Annotator` protocol, dispatch |
| `src/blq_sandbox/engines.py` | `SandboxEngine` protocol, engine discovery and selection |
| `src/blq_sandbox/spec.py` | `SandboxSpec` dataclass and presets |
