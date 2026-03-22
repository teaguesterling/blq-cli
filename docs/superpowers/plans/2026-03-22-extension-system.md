# Extension System + blq-sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build blq's extension pipeline with CommandSpec, Extension/Executor/Collector protocols, and blq-sandbox as the first extension with systemd-run enforcement.

**Architecture:** Extensions are separate packages discovered via Python entry points. A `CommandSpec` flows through a pipeline of extensions (prepare), an executor (execute), and collectors (collect). `blq-sandbox` owns `SandboxSpec` and dispatches to sandbox engines. The current `_execute_with_live_output()` is extracted into a `LocalExecutor`.

**Tech Stack:** Python 3.10+, dataclasses, `importlib.metadata` entry points, `typing.Protocol`, DuckDB (BIRD storage), systemd-run (cgroup enforcement)

**Spec:** `docs/superpowers/specs/2026-03-22-extension-system-design.md`

**Test runner:** `/home/teague/.local/share/venv/bin/python -m pytest`

---

## File Structure

### New files — blq core (`src/blq/ext/`)
| File | Responsibility |
|---|---|
| `src/blq/ext/__init__.py` | CommandSpec, ExecutionResult, Extension, Executor, Collector protocols |
| `src/blq/ext/discovery.py` | Entry point loading, extension ordering |
| `src/blq/ext/pipeline.py` | Pipeline orchestration: prepare → execute → collect |
| `src/blq/ext/local_executor.py` | Default LocalExecutor (extracted from execution.py) |

### New files — blq-sandbox (`src/blq_sandbox/`)
| File | Responsibility |
|---|---|
| `src/blq_sandbox/__init__.py` | SandboxExtension class |
| `src/blq_sandbox/spec.py` | SandboxSpec, presets, parsing, grade computation (moved from blq/sandbox.py) |
| `src/blq_sandbox/engines.py` | SandboxEngine protocol, engine discovery, LogEngine built-in |
| `pyproject.sandbox.toml` | Package config for blq-sandbox |

### New files — blq-sandbox-systemd (`src/blq_sandbox_systemd/`)
| File | Responsibility |
|---|---|
| `src/blq_sandbox_systemd/__init__.py` | SystemdEngine class, SystemdCollector |
| `pyproject.sandbox-systemd.toml` | Package config for blq-sandbox-systemd |

### New test files
| File | Tests for |
|---|---|
| `tests/test_ext_types.py` | CommandSpec, ExecutionResult construction |
| `tests/test_ext_discovery.py` | Extension discovery, ordering |
| `tests/test_ext_pipeline.py` | Pipeline orchestration, error handling |
| `tests/test_ext_local_executor.py` | LocalExecutor subprocess management |
| `tests/test_sandbox_ext.py` | SandboxExtension prepare/validate, engine dispatch |
| `tests/test_sandbox_systemd.py` | SystemdEngine wrapping, SystemdCollector (mocked) |

### Modified files
| File | Change |
|---|---|
| `src/blq/commands/core.py` | Remove `sandbox` field from RegisteredCommand, add `_extra` dict for config passthrough |
| `src/blq/commands/execution.py` | Replace `sandbox` param with pipeline integration, extract subprocess logic to LocalExecutor |
| `src/blq/bird.py` | Rename `sandbox` to `extension_data` on AttemptRecord/InvocationRecord |
| `src/blq/bird_schema.sql` | Rename column, update macros, schema version 2.4.0 |
| `src/blq/serve.py` | Remove sandbox-specific code, pass through extension data generically |
| `src/blq/sandbox.py` | Delete (moved to blq_sandbox/spec.py) |
| `tests/test_sandbox.py` | Update imports to point to blq_sandbox |

---

## Task 1: Core protocol types (`src/blq/ext/`)

**Files:**
- Create: `src/blq/ext/__init__.py`
- Test: `tests/test_ext_types.py`

- [ ] **Step 1: Write failing tests for CommandSpec and ExecutionResult**

```python
# tests/test_ext_types.py
"""Tests for extension protocol types."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from blq.ext import CommandSpec, ExecutionResult


class TestCommandSpec:
    def test_construction(self) -> None:
        spec = CommandSpec(
            command="pytest tests/",
            original_command="pytest tests/",
            command_name="test",
            attempt_id="abc-123",
            workspace=Path("/project"),
            cwd=Path("/project"),
            live_dir=Path("/project/.lq/live/abc-123"),
            env={"PATH": "/usr/bin"},
        )
        assert spec.command == "pytest tests/"
        assert spec.original_command == "pytest tests/"
        assert spec.extension_data == {}
        assert spec.collectors == []

    def test_extension_data_namespacing(self) -> None:
        spec = CommandSpec(
            command="pytest",
            original_command="pytest",
            command_name="test",
            attempt_id="abc",
            workspace=Path("/p"),
            cwd=Path("/p"),
            live_dir=Path("/p/.lq/live/abc"),
            env={},
        )
        spec.extension_data["sandbox"] = {"network": "none"}
        spec.extension_data["env"] = {"venv": ".venv"}
        assert spec.extension_data["sandbox"]["network"] == "none"
        assert "env" in spec.extension_data

    def test_command_is_mutable(self) -> None:
        spec = CommandSpec(
            command="pytest",
            original_command="pytest",
            command_name="test",
            attempt_id="abc",
            workspace=Path("/p"),
            cwd=Path("/p"),
            live_dir=Path("/p/.lq/live/abc"),
            env={},
        )
        spec.command = "bwrap -- pytest"
        assert spec.command == "bwrap -- pytest"
        assert spec.original_command == "pytest"


class TestExecutionResult:
    def test_construction(self) -> None:
        now = datetime.now()
        result = ExecutionResult(
            exit_code=0,
            output="PASSED",
            started_at=now,
            completed_at=now,
            duration_ms=1000,
        )
        assert result.exit_code == 0
        assert result.metrics == {}
        assert result.artifacts == {}
        assert result.signal is None
        assert result.timeout is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq.ext'`

- [ ] **Step 3: Implement core protocol types**

```python
# src/blq/ext/__init__.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/blq/ext/__init__.py tests/test_ext_types.py
git commit -m "feat: add extension protocol types (CommandSpec, Extension, Executor, Collector)"
```

---

## Task 2: Extension discovery (`src/blq/ext/discovery.py`)

**Files:**
- Create: `src/blq/ext/discovery.py`
- Test: `tests/test_ext_discovery.py`

- [ ] **Step 1: Write failing tests for extension discovery**

```python
# tests/test_ext_discovery.py
"""Tests for extension discovery and ordering."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch, MagicMock

from blq.ext import CommandSpec, ExecutionResult
from blq.ext.discovery import load_extensions, order_extensions


class FakeExtension:
    def __init__(self, name: str, config_key: str):
        self.name = name
        self.config_key = config_key

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        return []

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass


class TestLoadExtensions:
    def test_empty_when_no_extensions(self) -> None:
        with patch("blq.ext.discovery.entry_points", return_value=[]):
            result = load_extensions()
            assert result == {}

    def test_loads_extension_by_config_key(self) -> None:
        ep = MagicMock()
        ep.load.return_value = lambda: FakeExtension("sandbox", "sandbox")
        with patch("blq.ext.discovery.entry_points", return_value=[ep]):
            result = load_extensions()
            assert "sandbox" in result
            assert result["sandbox"].name == "sandbox"


class TestOrderExtensions:
    def test_default_order(self) -> None:
        exts = {
            "sandbox": FakeExtension("sandbox", "sandbox"),
            "env": FakeExtension("env", "env"),
        }
        ordered = order_extensions(exts)
        keys = [e.config_key for e in ordered]
        assert keys.index("env") < keys.index("sandbox")

    def test_custom_order(self) -> None:
        exts = {
            "sandbox": FakeExtension("sandbox", "sandbox"),
            "env": FakeExtension("env", "env"),
        }
        ordered = order_extensions(exts, order=["sandbox", "env"])
        keys = [e.config_key for e in ordered]
        assert keys.index("sandbox") < keys.index("env")

    def test_unlisted_extensions_go_last(self) -> None:
        exts = {
            "sandbox": FakeExtension("sandbox", "sandbox"),
            "custom": FakeExtension("custom", "custom"),
        }
        ordered = order_extensions(exts, order=["sandbox"])
        keys = [e.config_key for e in ordered]
        assert keys == ["sandbox", "custom"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq.ext.discovery'`

- [ ] **Step 3: Implement discovery module**

```python
# src/blq/ext/discovery.py
"""Extension discovery via Python entry points."""
from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from blq.ext import Extension

logger = logging.getLogger("blq-ext")

DEFAULT_ORDER = ["env", "sandbox", "platform"]


def load_extensions() -> dict[str, Extension]:
    """Discover installed extensions via entry points."""
    extensions: dict[str, Extension] = {}
    for ep in entry_points(group="blq.extensions"):
        try:
            ext_factory = ep.load()
            ext = ext_factory()
            extensions[ext.config_key] = ext
        except Exception as e:
            logger.warning(f"Failed to load extension {ep.name}: {e}")
    return extensions


def order_extensions(
    extensions: dict[str, Extension],
    order: list[str] | None = None,
) -> list[Extension]:
    """Order extensions by priority. Unlisted extensions go last."""
    priority = order or DEFAULT_ORDER

    def sort_key(ext: Extension) -> tuple[int, str]:
        try:
            idx = priority.index(ext.config_key)
        except ValueError:
            idx = len(priority)
        return (idx, ext.config_key)

    return sorted(extensions.values(), key=sort_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_discovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/blq/ext/discovery.py tests/test_ext_discovery.py
git commit -m "feat: add extension discovery via entry points"
```

---

## Task 3: Pipeline orchestration (`src/blq/ext/pipeline.py`)

**Files:**
- Create: `src/blq/ext/pipeline.py`
- Test: `tests/test_ext_pipeline.py`

- [ ] **Step 1: Write failing tests for pipeline**

```python
# tests/test_ext_pipeline.py
"""Tests for extension pipeline orchestration."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from blq.ext import CommandSpec, ExecutionResult, Collector
from blq.ext.pipeline import run_pipeline


def _make_spec(**overrides: Any) -> CommandSpec:
    defaults = dict(
        command="echo hello",
        original_command="echo hello",
        command_name="test",
        attempt_id="abc-123",
        workspace=Path("/project"),
        cwd=Path("/project"),
        live_dir=Path("/project/.lq/live/abc-123"),
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


class RecordingExtension:
    def __init__(self, name: str, prefix: str = ""):
        self.name = name
        self.config_key = name
        self.prepared = False
        self.prefix = prefix

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        self.prepared = True
        if self.prefix:
            spec.command = f"{self.prefix} {spec.command}"
        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        return []

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass


class RecordingExecutor:
    def __init__(self) -> None:
        self.name = "recording"
        self.executed_command: str | None = None

    def execute(self, spec: CommandSpec) -> ExecutionResult:
        self.executed_command = spec.command
        now = datetime.now()
        return ExecutionResult(
            exit_code=0, output="ok", started_at=now,
            completed_at=now, duration_ms=100,
        )


class RecordingCollector:
    def __init__(self, key: str, value: Any):
        self.key = key
        self.value = value

    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        result.metrics[self.key] = self.value


class FailingCollector:
    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        raise RuntimeError("collector failed")


class TestRunPipeline:
    def test_basic_flow(self) -> None:
        spec = _make_spec()
        executor = RecordingExecutor()
        result = run_pipeline(spec, [], executor)
        assert result.exit_code == 0
        assert executor.executed_command == "echo hello"

    def test_extension_prepare_modifies_command(self) -> None:
        spec = _make_spec()
        spec.extension_data["wrapper"] = {}
        ext = RecordingExtension("wrapper", prefix="sudo")
        executor = RecordingExecutor()
        result = run_pipeline(spec, [ext], executor)
        assert executor.executed_command == "sudo echo hello"

    def test_only_active_extensions_called(self) -> None:
        spec = _make_spec()
        spec.extension_data["active"] = {}
        # "inactive" not in extension_data
        active = RecordingExtension("active")
        inactive = RecordingExtension("inactive")
        executor = RecordingExecutor()
        run_pipeline(spec, [active, inactive], executor)
        assert active.prepared
        assert not inactive.prepared

    def test_collectors_run_in_reverse(self) -> None:
        spec = _make_spec()
        order: list[str] = []

        class OrderCollector:
            def __init__(self, label: str):
                self.label = label
            def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
                order.append(self.label)

        spec.collectors = [OrderCollector("first"), OrderCollector("second")]
        executor = RecordingExecutor()
        run_pipeline(spec, [], executor)
        assert order == ["second", "first"]

    def test_collector_failure_is_logged_not_raised(self) -> None:
        spec = _make_spec()
        spec.collectors = [FailingCollector(), RecordingCollector("key", "val")]
        executor = RecordingExecutor()
        result = run_pipeline(spec, [], executor)
        # Second collector still ran despite first failing
        assert result.metrics["key"] == "val"

    def test_prepare_failure_aborts(self) -> None:
        class FailingExtension(RecordingExtension):
            def prepare(self, spec: CommandSpec) -> CommandSpec:
                raise ValueError("bad config")

        spec = _make_spec()
        spec.extension_data["failing"] = {}
        ext = FailingExtension("failing")
        executor = RecordingExecutor()
        with pytest.raises(ValueError, match="bad config"):
            run_pipeline(spec, [ext], executor)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq.ext.pipeline'`

- [ ] **Step 3: Implement pipeline orchestration**

```python
# src/blq/ext/pipeline.py
"""Extension pipeline orchestration."""
from __future__ import annotations

import logging
from typing import Any

from blq.ext import CommandSpec, ExecutionResult, Extension, Executor

logger = logging.getLogger("blq-ext")


def run_pipeline(
    spec: CommandSpec,
    extensions: list[Extension],
    executor: Executor,
) -> ExecutionResult:
    """Run the full extension pipeline.

    1. prepare() — forward order, only active extensions
    2. execute() — the executor runs the command
    3. collect() — reverse order of registered collectors
    """
    # 1. Prepare (forward order, only active extensions)
    for ext in extensions:
        if ext.config_key in spec.extension_data:
            spec = ext.prepare(spec)

    # 2. Execute
    result = executor.execute(spec)

    # 3. Collect (reverse order)
    for collector in reversed(spec.collectors):
        try:
            collector.collect(spec, result)
        except Exception as e:
            logger.warning(
                f"Collector {type(collector).__name__} failed: {e}"
            )

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add src/blq/ext/pipeline.py tests/test_ext_pipeline.py
git commit -m "feat: add extension pipeline orchestration"
```

---

## Task 4: Config passthrough in RegisteredCommand

**Files:**
- Modify: `src/blq/commands/core.py:1055-1172` (RegisteredCommand), `src/blq/commands/core.py:1341-1394` (_load/_save)
- Test: `tests/test_sandbox.py` (update existing), `tests/test_ext_types.py` (add config round-trip test)

- [ ] **Step 1: Write failing test for config passthrough**

Add to `tests/test_ext_types.py`:

```python
class TestConfigPassthrough:
    def test_extra_sections_preserved_on_roundtrip(self, tmp_path: object) -> None:
        from pathlib import Path
        from blq.commands.core import RegisteredCommand, _load_commands_impl
        from blq.config_format import save_toml

        lq_dir = Path(str(tmp_path))
        commands_path = lq_dir / "commands.toml"

        # Write TOML with extension sections
        data = {
            "commands": {
                "test": {
                    "cmd": "pytest tests/",
                    "description": "Run tests",
                    "sandbox": {
                        "network": "none",
                        "filesystem": "readonly",
                    },
                    "env": {
                        "venv": ".venv",
                    },
                }
            }
        }
        save_toml(commands_path, data)

        # Load — extension sections should be in _extra
        loaded = _load_commands_impl(lq_dir)
        cmd = loaded["test"]
        assert cmd._extra["sandbox"] == {"network": "none", "filesystem": "readonly"}
        assert cmd._extra["env"] == {"venv": ".venv"}
        assert cmd.cmd == "pytest tests/"

    def test_extra_sections_survive_save(self, tmp_path: object) -> None:
        from pathlib import Path
        from blq.commands.core import RegisteredCommand, _load_commands_impl, _save_commands_impl

        lq_dir = Path(str(tmp_path))
        commands_path = lq_dir / "commands.toml"

        cmd = RegisteredCommand(
            name="test",
            cmd="pytest tests/",
            _extra={"sandbox": {"network": "none"}, "env": {"venv": ".venv"}},
        )
        _save_commands_impl(lq_dir, {"test": cmd})

        # Reload and verify
        reloaded = _load_commands_impl(lq_dir)
        assert reloaded["test"]._extra["sandbox"] == {"network": "none"}
        assert reloaded["test"]._extra["env"] == {"venv": ".venv"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_types.py::TestConfigPassthrough -v`
Expected: FAIL — `TypeError: RegisteredCommand.__init__() got an unexpected keyword argument '_extra'`

- [ ] **Step 3: Add `_extra` field to RegisteredCommand, update load/save**

In `src/blq/commands/core.py`:

1. Remove `sandbox` import (line 29): delete `from blq.sandbox import SandboxSpec, resolve_sandbox`
2. Remove `sandbox` field from RegisteredCommand (line 1070): delete `sandbox: SandboxSpec | None = None`
3. Add `_extra` field: `_extra: dict[str, Any] = field(default_factory=dict)`
4. Update `to_dict()` (lines 1166-1171): remove sandbox serialization, add `d.update(self._extra)`
5. Update `_load_commands_impl()` (lines 1341-1387): collect unknown keys into `_extra`, remove sandbox parsing
6. `_save_commands_impl()` needs no change — `to_dict()` now includes `_extra`

Known fields to extract from TOML:
```python
_KNOWN_COMMAND_KEYS = {
    "cmd", "tpl", "defaults", "description", "timeout",
    "format", "capture", "capture_env", "suppress", "lines",
}
```

In the load function, after extracting known fields:
```python
extra = {k: v for k, v in config.items() if k not in _KNOWN_COMMAND_KEYS}
```

- [ ] **Step 4: Update test_sandbox.py imports**

The `TestRegisteredCommandIntegration` tests in `tests/test_sandbox.py` reference `RegisteredCommand.sandbox`. These tests now test the extension integration, not core. Update them to verify sandbox config lands in `_extra["sandbox"]` instead of `RegisteredCommand.sandbox`.

- [ ] **Step 5: Run tests to verify**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_types.py::TestConfigPassthrough tests/test_sandbox.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```
git add src/blq/commands/core.py tests/test_ext_types.py tests/test_sandbox.py
git commit -m "feat: add config passthrough (_extra) to RegisteredCommand, remove sandbox from core"
```

---

## Task 5: Move SandboxSpec to blq_sandbox package

**Files:**
- Create: `src/blq_sandbox/__init__.py`, `src/blq_sandbox/spec.py`, `src/blq_sandbox/engines.py`
- Create: `pyproject.sandbox.toml`
- Delete: `src/blq/sandbox.py`
- Modify: `tests/test_sandbox.py` (update imports)

- [ ] **Step 1: Create blq_sandbox package structure**

```bash
mkdir -p src/blq_sandbox
```

- [ ] **Step 2: Move sandbox.py to blq_sandbox/spec.py**

```bash
cp src/blq/sandbox.py src/blq_sandbox/spec.py
```

No content changes needed — the module is self-contained.

- [ ] **Step 3: Create engines module with LogEngine and SandboxEngine protocol**

```python
# src/blq_sandbox/engines.py
"""Sandbox engine protocol and discovery."""
from __future__ import annotations

import logging
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

from blq.ext import Collector
from blq_sandbox.spec import SandboxSpec

logger = logging.getLogger("blq-sandbox")


class SandboxEngine(Protocol):
    """A sandbox enforcement backend."""

    name: str
    capabilities: set[str]

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path,
             attempt_id: str) -> str: ...

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None: ...


class LogEngine:
    """Declaration-only engine. No enforcement, just logging."""

    name = "log"
    capabilities: set[str] = set()

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path,
             attempt_id: str) -> str:
        return command

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        return None


def load_engines() -> dict[str, SandboxEngine]:
    """Discover installed sandbox engines via entry points."""
    engines: dict[str, SandboxEngine] = {"log": LogEngine()}
    for ep in entry_points(group="blq.sandbox.engines"):
        try:
            engine_factory = ep.load()
            engine = engine_factory()
            engines[engine.name] = engine
        except Exception as e:
            logger.warning(f"Failed to load sandbox engine {ep.name}: {e}")
    return engines


# Dimensions that each engine can enforce
SANDBOX_DIMENSIONS = {
    "network", "filesystem", "memory", "cpu",
    "processes", "tmpfs", "paths_readable", "paths_hidden",
}


def select_engines(
    spec: SandboxSpec,
    available: dict[str, SandboxEngine],
    preferred: list[str] | None = None,
) -> list[SandboxEngine]:
    """Select engines to cover the spec's non-default dimensions.

    Returns engines needed, warns about uncovered dimensions.
    """
    needed = spec.active_dimensions()
    if not needed:
        return [available["log"]]

    # Filter to preferred engines if specified
    candidates = available
    if preferred:
        candidates = {k: v for k, v in available.items() if k in preferred}
        if not candidates:
            logger.warning(
                f"No preferred engines ({preferred}) are installed. "
                f"Falling back to all available engines."
            )
            candidates = available

    selected: list[SandboxEngine] = []
    covered: set[str] = set()
    for name, engine in candidates.items():
        if name == "log":
            continue
        relevant = engine.capabilities & needed
        if relevant - covered:
            selected.append(engine)
            covered |= relevant

    uncovered = needed - covered
    if uncovered:
        logger.warning(
            f"Sandbox dimensions not enforced (no capable engine installed): "
            f"{', '.join(sorted(uncovered))}"
        )

    return selected if selected else [available["log"]]
```

- [ ] **Step 4: Add `active_dimensions()` to SandboxSpec in spec.py**

Add this method to the `SandboxSpec` class in `src/blq_sandbox/spec.py`:

```python
def active_dimensions(self) -> set[str]:
    """Return the set of dimensions that differ from unrestricted defaults."""
    dims: set[str] = set()
    if self.network != "unrestricted":
        dims.add("network")
    if self.filesystem != "unrestricted":
        dims.add("filesystem")
    if self.memory is not None:
        dims.add("memory")
    if self.cpu is not None:
        dims.add("cpu")
    if self.processes != "visible":
        dims.add("processes")
    if self.tmpfs is not None:
        dims.add("tmpfs")
    if self.paths_readable:
        dims.add("paths_readable")
    if self.paths_hidden:
        dims.add("paths_hidden")
    return dims
```

- [ ] **Step 5: Create SandboxExtension**

```python
# src/blq_sandbox/__init__.py
"""blq-sandbox: Sandbox specification extension for blq."""
from __future__ import annotations

import logging
from typing import Any

from blq.ext import CommandSpec, ExecutionResult, Extension
from blq_sandbox.engines import load_engines, select_engines
from blq_sandbox.spec import SandboxSpec, resolve_sandbox

logger = logging.getLogger("blq-sandbox")


class SandboxExtension:
    """Sandbox extension — declares and enforces execution bounds."""

    name = "sandbox"
    config_key = "sandbox"

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        config = spec.extension_data.get("sandbox", {})

        # Resolve spec from config (preset string or dict)
        sandbox_spec = resolve_sandbox(config)
        if sandbox_spec is None:
            return spec

        # Store parsed spec for later use
        spec.extension_data["sandbox"] = sandbox_spec.to_dict()
        spec.extension_data["sandbox_grade_w"] = sandbox_spec.grade_w
        spec.extension_data["sandbox_effects_ceiling"] = sandbox_spec.effects_ceiling

        # Load and select engines
        engines = load_engines()
        preferred = config.get("engines") if isinstance(config, dict) else None
        selected = select_engines(sandbox_spec, engines, preferred)

        # Wrap command through each engine
        for engine in selected:
            spec.command = engine.wrap(
                spec.command, sandbox_spec, spec.workspace, spec.attempt_id
            )
            collector = engine.collector(sandbox_spec, spec.attempt_id)
            if collector is not None:
                spec.collectors.append(collector)

        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        try:
            resolve_sandbox(config)
        except (ValueError, TypeError) as e:
            warnings.append(str(e))
        return warnings

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass  # Stubbed this round
```

- [ ] **Step 6: Create pyproject.sandbox.toml**

```toml
# pyproject.sandbox.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "blq-sandbox"
version = "0.1.0"
description = "Sandbox specification extension for blq"
requires-python = ">=3.10"
dependencies = ["blq-cli>=0.9.16"]

[project.entry-points."blq.extensions"]
sandbox = "blq_sandbox:SandboxExtension"

[tool.hatch.build.targets.wheel]
packages = ["src/blq_sandbox"]
```

- [ ] **Step 7: Update test_sandbox.py imports**

Change all `from blq.sandbox import ...` to `from blq_sandbox.spec import ...` in `tests/test_sandbox.py`. Update `TestRegisteredCommandIntegration` to test config passthrough via `_extra` instead of `RegisteredCommand.sandbox`.

- [ ] **Step 8: Delete src/blq/sandbox.py**

```bash
rm src/blq/sandbox.py
```

- [ ] **Step 9: Run tests**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_sandbox.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```
git add src/blq_sandbox/ pyproject.sandbox.toml tests/test_sandbox.py
git rm src/blq/sandbox.py
git commit -m "feat: move SandboxSpec to blq-sandbox extension package"
```

---

## Task 6: blq-sandbox-systemd engine

**Files:**
- Create: `src/blq_sandbox_systemd/__init__.py`, `pyproject.sandbox-systemd.toml`
- Test: `tests/test_sandbox_systemd.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_systemd.py
"""Tests for systemd sandbox engine."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch, mock_open

from blq.ext import CommandSpec, ExecutionResult
from blq_sandbox.spec import SandboxSpec
from blq_sandbox_systemd import SystemdEngine


class TestSystemdEngine:
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
        spec = SandboxSpec(memory=512 * 1024**2)
        result = engine.wrap("pytest", spec, Path("/p"), "abc-12345678")
        assert f"MemoryMax={512 * 1024**2}" in result

    def test_wrap_no_limits(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec()  # all defaults — no limits
        result = engine.wrap("echo hi", spec, Path("/p"), "abc-12345678")
        assert "MemoryAccounting=yes" in result
        assert "MemoryMax" not in result

    def test_collector_reads_cgroup_stats(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec(memory=512 * 1024**2)
        collector = engine.collector(spec, "abc-12345678")
        assert collector is not None

        # Mock cgroup file reads
        now = datetime.now()
        result = ExecutionResult(
            exit_code=0, output="", started_at=now,
            completed_at=now, duration_ms=1000,
        )
        cmd_spec = CommandSpec(
            command="pytest", original_command="pytest",
            command_name="test", attempt_id="abc-12345678",
            workspace=Path("/p"), cwd=Path("/p"),
            live_dir=Path("/p/.lq/live/abc"), env={},
        )

        with patch("builtins.open", mock_open(read_data="12345678\n")):
            with patch("pathlib.Path.exists", return_value=True):
                collector.collect(cmd_spec, result)

        assert "memory_peak_bytes" in result.metrics

    def test_collector_handles_missing_cgroup(self) -> None:
        engine = SystemdEngine()
        spec = SandboxSpec(memory=512 * 1024**2)
        collector = engine.collector(spec, "abc-12345678")
        assert collector is not None

        now = datetime.now()
        result = ExecutionResult(
            exit_code=0, output="", started_at=now,
            completed_at=now, duration_ms=1000,
        )
        cmd_spec = CommandSpec(
            command="pytest", original_command="pytest",
            command_name="test", attempt_id="abc-12345678",
            workspace=Path("/p"), cwd=Path("/p"),
            live_dir=Path("/p/.lq/live/abc"), env={},
        )

        # cgroup doesn't exist — should not raise
        with patch("pathlib.Path.exists", return_value=False):
            collector.collect(cmd_spec, result)

        assert result.metrics == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_sandbox_systemd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq_sandbox_systemd'`

- [ ] **Step 3: Implement SystemdEngine**

```python
# src/blq_sandbox_systemd/__init__.py
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

    def __init__(self, scope_name: str) -> None:
        self._scope_name = scope_name

    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        cgroup_path = Path(f"/sys/fs/cgroup/system.slice/{self._scope_name}.scope")
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

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path,
             attempt_id: str) -> str:
        scope_name = f"blq-{attempt_id[:8]}"
        parts = [
            "systemd-run", "--scope", "--quiet",
            f"--unit={scope_name}",
            "-p", "MemoryAccounting=yes",
            "-p", "CPUAccounting=yes",
        ]
        if spec.memory:
            parts.extend(["-p", f"MemoryMax={spec.memory}"])
        parts.append("--")
        parts.append(command)
        return " ".join(parts)

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        scope_name = f"blq-{attempt_id[:8]}"
        return SystemdCollector(scope_name)
```

- [ ] **Step 4: Create pyproject.sandbox-systemd.toml**

```toml
# pyproject.sandbox-systemd.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "blq-sandbox-systemd"
version = "0.1.0"
description = "systemd-run cgroup enforcement engine for blq-sandbox"
requires-python = ">=3.10"
dependencies = ["blq-sandbox>=0.1.0"]

[project.entry-points."blq.sandbox.engines"]
systemd = "blq_sandbox_systemd:SystemdEngine"

[tool.hatch.build.targets.wheel]
packages = ["src/blq_sandbox_systemd"]
```

- [ ] **Step 5: Run tests**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_sandbox_systemd.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```
git add src/blq_sandbox_systemd/ pyproject.sandbox-systemd.toml tests/test_sandbox_systemd.py
git commit -m "feat: add blq-sandbox-systemd engine (cgroup resource limits)"
```

---

## Task 7: BIRD schema migration (sandbox → extension_data)

**Files:**
- Modify: `src/blq/bird.py:162,219` (InvocationRecord, AttemptRecord)
- Modify: `src/blq/bird_schema.sql:103,171,852-886`

- [ ] **Step 1: Rename field in AttemptRecord and InvocationRecord**

In `src/blq/bird.py`:
- AttemptRecord line 219: change `sandbox: dict[str, Any] | None = None` to `extension_data: dict[str, Any] | None = None`
- InvocationRecord line 162: same change
- Update `write_attempt()` INSERT: change column name `sandbox` to `extension_data`, update serialization reference
- Update `write_invocation()` INSERT: same change
- Update `write_bird_invocation()`: change `sandbox=run_meta.get("sandbox")` to `extension_data=run_meta.get("extension_data")`

- [ ] **Step 2: Update bird_schema.sql**

- Change column name in `attempts` CREATE (line 103): `extension_data JSON` (was `sandbox JSON`)
- Change column name in `invocations` CREATE (line 171): same
- Update schema version to `2.4.0` (line 29)
- Update `blq_sandbox_summary()` macro (lines 861-886): read from `extension_data->>'sandbox'` path
- Add migration in `_apply_migrations()` in bird.py: rename column 2.3→2.4

- [ ] **Step 3: Update execution.py**

- Change `sandbox` parameter name to `extension_data` in `_execute_with_live_output()` and `_execute_command()`
- Update AttemptRecord construction: `extension_data=extension_data`
- Update RunResult construction: `sandbox=extension_data` (RunResult.sandbox stays for now — will be removed in Task 8)
- Update `cmd_run()` call site: build `extension_data = {"sandbox": reg.sandbox.to_dict()}` pattern becomes `extension_data = dict(registered_commands[cmd_name]._extra)` if the command has extension config

- [ ] **Step 4: Run full test suite**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/ -q`
Expected: All pass (except pre-existing test_basic_line_selection failure)

- [ ] **Step 5: Commit**

```
git add src/blq/bird.py src/blq/bird_schema.sql src/blq/commands/execution.py
git commit -m "feat: rename sandbox to extension_data in BIRD schema (2.4.0)"
```

---

## Task 8: Remove sandbox-specific code from serve.py and execution.py

**Files:**
- Modify: `src/blq/serve.py:2359-2362,2545-2548,3302,3335`
- Modify: `src/blq/commands/execution.py:1294-1299`
- Modify: `src/blq/commands/core.py` (RunResult.sandbox field)

- [ ] **Step 1: Remove sandbox from MCP _command_to_dict()**

In `src/blq/serve.py` lines 2359-2362, remove the sandbox-specific fields. Replace with generic extension data passthrough:

```python
if hasattr(cmd, '_extra') and cmd._extra:
    result["extensions"] = cmd._extra
```

- [ ] **Step 2: Remove sandbox from _commands_impl()**

In `src/blq/serve.py` lines 2545-2548, same pattern — pass through `_extra` generically.

- [ ] **Step 3: Remove sandbox param from register_command()**

In `src/blq/serve.py`, remove `sandbox` from `register_command()` function signature (line 3302) and `_register_command_impl()` signature. Add a generic `extensions: dict[str, Any] | None = None` parameter instead that gets merged into `_extra`.

- [ ] **Step 4: Remove sandbox from RunResult**

In `src/blq/commands/core.py`, remove `sandbox` field from RunResult. Add `extension_data: dict[str, Any] | None = None` instead. Update `to_json()` accordingly.

- [ ] **Step 5: Clean up execution.py sandbox references**

Remove the sandbox-specific extraction in `cmd_run()` (lines 1294-1299). Replace with generic `_extra` passthrough to `_execute_command(extension_data=...)`.

- [ ] **Step 6: Run full test suite**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/ -q`
Expected: All pass

- [ ] **Step 7: Commit**

```
git add src/blq/serve.py src/blq/commands/core.py src/blq/commands/execution.py
git commit -m "refactor: remove sandbox-specific code from core, use generic extension_data"
```

---

## Task 9: Integration test — end-to-end pipeline

**Files:**
- Create: `tests/test_ext_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_ext_integration.py
"""End-to-end integration tests for the extension pipeline."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from blq.ext import CommandSpec, ExecutionResult
from blq.ext.pipeline import run_pipeline
from blq_sandbox import SandboxExtension
from blq_sandbox.spec import SandboxSpec


class FakeExecutor:
    name = "fake"
    def execute(self, spec: CommandSpec) -> ExecutionResult:
        now = datetime.now()
        return ExecutionResult(
            exit_code=0, output="PASSED", started_at=now,
            completed_at=now, duration_ms=500,
        )


class TestSandboxIntegration:
    def test_sandbox_preset_flows_through_pipeline(self) -> None:
        spec = CommandSpec(
            command="pytest tests/",
            original_command="pytest tests/",
            command_name="test",
            attempt_id="int-test-001",
            workspace=Path("/project"),
            cwd=Path("/project"),
            live_dir=Path("/project/.lq/live/int-test-001"),
            env={},
            extension_data={"sandbox": "test"},
        )
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)

        assert result.exit_code == 0
        # Sandbox spec was parsed and grade computed
        assert spec.extension_data["sandbox_grade_w"] == "pinhole"
        assert spec.extension_data["sandbox_effects_ceiling"] == 2

    def test_no_sandbox_config_is_passthrough(self) -> None:
        spec = CommandSpec(
            command="pytest tests/",
            original_command="pytest tests/",
            command_name="test",
            attempt_id="int-test-002",
            workspace=Path("/project"),
            cwd=Path("/project"),
            live_dir=Path("/project/.lq/live/int-test-002"),
            env={},
        )
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)
        # Extension not active (no sandbox config), command unchanged
        assert result.exit_code == 0

    def test_sandbox_dict_config(self) -> None:
        spec = CommandSpec(
            command="make build",
            original_command="make build",
            command_name="build",
            attempt_id="int-test-003",
            workspace=Path("/project"),
            cwd=Path("/project"),
            live_dir=Path("/project/.lq/live/int-test-003"),
            env={},
            extension_data={
                "sandbox": {
                    "network": "none",
                    "filesystem": "workspace_only",
                    "memory": "2g",
                },
            },
        )
        ext = SandboxExtension()
        executor = FakeExecutor()
        result = run_pipeline(spec, [ext], executor)

        assert spec.extension_data["sandbox_grade_w"] == "scoped"
        assert spec.extension_data["sandbox_effects_ceiling"] == 4
```

- [ ] **Step 2: Run integration tests**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/test_ext_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/ -q`
Expected: All pass (except pre-existing test_basic_line_selection failure)

- [ ] **Step 4: Commit**

```
git add tests/test_ext_integration.py
git commit -m "test: add end-to-end extension pipeline integration tests"
```

---

## Task 10: Final verification and cleanup

- [ ] **Step 1: Run full test suite**

Run: `/home/teague/.local/share/venv/bin/python -m pytest tests/ -q`

- [ ] **Step 2: Run type checker**

Run: `/home/teague/.local/share/venv/bin/python -m mypy src/blq/ext/ src/blq_sandbox/ src/blq_sandbox_systemd/`

- [ ] **Step 3: Run linter**

Run: `ruff check src/blq/ext/ src/blq_sandbox/ src/blq_sandbox_systemd/`

- [ ] **Step 4: Verify no sandbox imports remain in blq core**

Run: `grep -r "from blq.sandbox" src/blq/ --include="*.py"` — should return nothing
Run: `grep -r "SandboxSpec" src/blq/ --include="*.py"` — should return nothing

- [ ] **Step 5: Commit any fixes**

```
git add -A
git commit -m "chore: final cleanup for extension system"
```
