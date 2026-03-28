# Extension System + blq-sandbox

*Lightweight execution pipeline for sandbox, environment, and platform concerns.*

## Context

blq's execution path is simple: `subprocess.Popen(cmd, shell=True)` with streaming, timeout, and signal management. Three categories of concern want to modify *how* commands execute without changing *what* blq captures:

1. **Sandbox** — bound effects (network, filesystem, memory, processes)
2. **Environment** — set up prerequisites (venvs, env vars, source scripts)
3. **Platform** — choose where the command runs (local, Docker, SLURM, K8s)

These don't belong in blq core. They're optional, composable, and platform-specific. An extension system lets them plug in without bloating the core.

Additionally, duck_hunt (log parsing) is currently hardcoded in the execution path. The extension model should accommodate migrating it to an extension in a future round.

### What we're building this round

1. **Extension protocol** in blq core — `CommandSpec`, `Extension`, `Executor`, `Collector` types, entry point discovery, pipeline orchestration
2. **`blq-sandbox`** — generic sandbox extension owning `SandboxSpec`, presets, grade computation, engine dispatch
3. **`blq-sandbox-systemd`** — first sandbox engine using `systemd-run --scope` for resource limits and monitoring
4. **Refactor Phase 1 commit** — move `SandboxSpec` from `blq/sandbox.py` to `blq_sandbox/spec.py`, decouple core

### What we're deferring

- duck_hunt migration to extension (next round — protocol proven first)
- bwrap engine (second engine — adds namespace isolation)
- nsjail engine (third — full stack)
- Environment and platform extensions
- Extension `store()` implementation (stub only this round)

## Architecture

### Pipeline flow

```
RegisteredCommand + TOML config sections
    → build CommandSpec
    → extension_1.prepare(spec)     # e.g., env: modify env vars
    → extension_2.prepare(spec)     # e.g., sandbox: wrap command, register collectors
    → executor.execute(spec)        # e.g., LocalExecutor (default)
    → collector_2.collect(result)   # reverse order
    → collector_1.collect(result)   # reverse order
    → extension_1.store(...)        # forward order (stubbed this round)
    → extension_2.store(...)
    → core writes RunResult to BIRD
```

### Package layout

```
src/blq/                            # blq-cli (core)
    ext/                            # extension protocol + discovery
        __init__.py                 # CommandSpec, Extension, Executor, Collector
        discovery.py                # entry point loading
        pipeline.py                 # orchestration: prepare → execute → collect → store
        local_executor.py           # default LocalExecutor (extracted from execution.py)
src/blq_sandbox/                    # blq-sandbox extension
    __init__.py                     # SandboxExtension class
    spec.py                         # SandboxSpec, presets, grade computation
    engines.py                      # SandboxEngine protocol, engine discovery, composition
src/blq_sandbox_systemd/            # blq-sandbox-systemd engine
    __init__.py                     # SystemdEngine class
    scope.py                        # systemd-run scope management, cgroup stat reading
```

Three separate packages in the monorepo, each with its own pyproject.toml. Entry point discovery:

```toml
# blq-sandbox
[project.entry-points."blq.extensions"]
sandbox = "blq_sandbox:SandboxExtension"

# blq-sandbox-systemd
[project.entry-points."blq.sandbox.engines"]
systemd = "blq_sandbox_systemd:SystemdEngine"
```

## Core types

### CommandSpec

Structured execution request that flows through the pipeline. Replaces the current pattern of passing a command string through functions.

```python
@dataclass
class CommandSpec:
    """Structured execution request flowing through the extension pipeline."""

    # What to run
    command: str                     # shell command (mutable — extensions modify)
    original_command: str            # before any wrapping (immutable — for audit)

    # Identity
    command_name: str                # registered name (e.g., "test")
    attempt_id: str                  # UUID

    # Context
    workspace: Path                  # project root
    cwd: Path                       # working directory
    live_dir: Path                  # .lq/live/{attempt_id}/

    # Environment
    env: dict[str, str]             # env vars (extensions can add/modify)

    # Resource requirements
    timeout: int | None = None

    # Extension data — namespaced by extension name
    extension_data: dict[str, Any] = field(default_factory=dict)

    # Collectors — registered during prepare(), run post-execution in reverse
    collectors: list[Collector] = field(default_factory=list)
```

### ExecutionResult

What the executor returns after running the command.

```python
@dataclass
class ExecutionResult:
    """Result from an executor."""
    exit_code: int
    output: str                      # captured stdout+stderr
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    signal: int | None = None
    timeout: bool = False
    pid: int | None = None

    # Collector contributions — accumulated post-execution
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)
```

### Extension protocol

```python
class Extension(Protocol):
    """Modifies execution context. Composable — multiple can be active."""

    name: str
    config_key: str                  # TOML section (e.g., "sandbox")

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        """Transform the spec before execution.
        Called in extension order. Register collectors here."""
        ...

    def validate(self, config: dict[str, Any]) -> list[str]:
        """Validate this extension's config section. Return warnings/errors."""
        ...

    def store(self, spec: CommandSpec, result: ExecutionResult, store: BirdStore) -> None:
        """Write extension-specific artifacts to storage.
        Called after collectors, with an open DB connection.
        Stubbed this round — default no-op.
        BirdStore type imported from blq.bird."""
        ...
```

### Executor protocol

```python
class Executor(Protocol):
    """Runs the command. Terminal — only one active per invocation."""

    name: str

    def execute(self, spec: CommandSpec) -> ExecutionResult:
        """Run the command. Responsible for full lifecycle:
        start process, stream output, handle signals, wait, return result."""
        ...
```

### Collector protocol

```python
class Collector(Protocol):
    """Gathers artifacts post-execution. Registered by extensions during prepare()."""

    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        """Called after execution, in reverse registration order.
        Mutates result.metrics and result.artifacts in place."""
        ...
```

## Extension discovery

Extensions register via Python entry points and are discovered at startup:

```python
from importlib.metadata import entry_points

def load_extensions() -> dict[str, Extension]:
    extensions = {}
    for ep in entry_points(group="blq.extensions"):
        ext_class = ep.load()
        ext = ext_class()
        extensions[ext.config_key] = ext
    return extensions
```

The discovery dict is keyed by `config_key` — this is how TOML sections are matched to extensions. `name` and `config_key` should be the same by convention (both `"sandbox"` for the sandbox extension). The extension ordering config (`[extensions] order = [...]`) also uses `config_key` values.

An extension is only active for a command if the command has a matching config section in `commands.toml`:

```toml
# sandbox extension active (has [commands.test.sandbox] section)
[commands.test]
cmd = "pytest tests/"

[commands.test.sandbox]
network = "none"
filesystem = "readonly"

# no extensions active
[commands.build]
cmd = "make -j8"
```

### Config passthrough

blq core must preserve unknown TOML sections on load and save. Currently `_save_commands_impl()` drops everything except known `RegisteredCommand` fields.

**Implementation**: `RegisteredCommand` gains a `_extra: dict[str, Any]` field (default empty dict) that holds all TOML keys not consumed by known fields. On load, `_load_commands_impl()` extracts known fields and stores the rest in `_extra`. On save, `to_dict()` merges `_extra` back into the output dict. Extension config sections (e.g., `[commands.test.sandbox]`) appear as nested dicts in TOML and are preserved in `_extra["sandbox"]`.

### Pipeline orchestration: `run_pipeline()`

The pipeline builder populates `CommandSpec.extension_data` before calling `prepare()`:

```python
def run_pipeline(spec: CommandSpec, extensions: dict[str, Extension],
                 registered_command: RegisteredCommand, executor: Executor) -> ExecutionResult:
    # 1. Populate extension_data from command config
    for config_key, ext in extensions.items():
        if config_key in registered_command._extra:
            spec.extension_data[config_key] = registered_command._extra[config_key]

    # 2. Prepare (forward order) — only active extensions
    active = [ext for key, ext in extensions.items() if key in spec.extension_data]
    for ext in active:
        spec = ext.prepare(spec)

    # 3. Execute
    result = executor.execute(spec)

    # 4. Collect (reverse order)
    for collector in reversed(spec.collectors):
        try:
            collector.collect(spec, result)
        except Exception as e:
            logger.warning(f"Collector {type(collector).__name__} failed: {e}")

    # 5. Store (forward order, stubbed this round)
    # for ext in active:
    #     ext.store(spec, result, store)

    return result
```

### CommandSpec.env population

`CommandSpec.env` is initialized from `capture_environment(capture_env_vars)` — the same captured subset the current execution path uses. It is **not** a full copy of `os.environ`. Extensions that need to add env vars (e.g., a future `blq-env`) append to this dict during `prepare()`. The `LocalExecutor` passes `spec.env` as supplemental environment to `subprocess.Popen`.

### Error handling

**`prepare()` failures**: If an extension's `prepare()` raises, execution is aborted and the error is reported to the user. Extensions should use `validate()` for early config checks — `validate()` is called at command registration time and on first run. `prepare()` failures are unexpected and indicate a bug or missing dependency.

**Collector failures**: Collector exceptions are caught and logged (`logger.warning`). The pipeline continues with remaining collectors. `result.metrics` may be partially populated — this is acceptable since metrics are advisory, not correctness-critical.

### Extension ordering

Default order: `["env", "sandbox", "platform"]`

Configurable in `.lq/config.toml`:

```toml
[extensions]
order = ["env", "sandbox", "platform"]
```

## blq-sandbox extension

### Responsibilities

- Owns the `[commands.*.sandbox]` config section
- Parses `SandboxSpec` from config (presets, individual fields)
- Computes `grade_w` and `effects_ceiling`
- Discovers installed sandbox engines via `blq.sandbox.engines` entry points
- Maps spec dimensions to engine capabilities
- Composes engines to cover required dimensions
- Registers engine collectors for post-execution metric gathering
- Exposes sandbox data through the pipeline (stored in BIRD by core)

### SandboxSpec

Moved from `blq/sandbox.py` to `blq_sandbox/spec.py`. Same dataclass, same presets, same grade computation. The only change is location.

### Engine protocol

```python
class SandboxEngine(Protocol):
    """A sandbox enforcement backend."""

    name: str
    capabilities: set[str]           # dimensions this engine can enforce

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path) -> str:
        """Wrap the command string for enforcement."""
        ...

    def collector(self, spec: SandboxSpec) -> Collector | None:
        """Return a collector for post-execution metrics, or None."""
        ...
```

### Engine composition

The sandbox extension's `prepare()` method:

1. Parse `SandboxSpec` from `spec.extension_data["sandbox"]`
2. Identify non-default dimensions that need enforcement
3. Load installed engines (via `blq.sandbox.engines` entry points)
4. Apply engine preference (from config)
5. Match dimensions to engines, warn about uncovered dimensions
6. Compose: call each needed engine's `wrap()` in order
7. Register each engine's `collector()` on the spec

### Engine selection

Three levels of control:

```toml
# 1. Auto (default) — blq-sandbox picks based on what's installed

# 2. Project preference — .lq/config.toml
[sandbox]
engines = ["systemd", "bwrap"]       # priority order, restricts available engines

# 3. Per-command override — commands.toml
[commands.test.sandbox]
network = "none"
memory = "512m"
engines = ["nsjail"]                 # this command uses only nsjail
```

Resolution: per-command overrides project preference overrides auto-detection.

### Dimension-to-engine mapping

| Dimension | systemd | bwrap | nsjail (future) |
|---|---|---|---|
| `memory` | cgroup memory.max | - | cgroup_mem_max |
| `cpu` | cgroup cpu.max | - | rlimit_cpu |
| `timeout` | core (not engine) | core | time_limit |
| `network` | - | --unshare-net | clone_newnet |
| `filesystem` | - | --ro-bind/--bind | mount config |
| `processes` | cgroup pids.max | --unshare-pid | clone_newpid + pids cgroup |
| `tmpfs` | - | --tmpfs | mount tmpfs |

`timeout` is handled by core (or the executor), not by sandbox engines — it's already implemented in the execution path.

### Built-in "log" engine

`blq-sandbox` ships with a trivial built-in engine:

```python
class LogEngine:
    """Declaration-only engine. No enforcement, just logging."""
    name = "log"
    capabilities: set[str] = set()   # enforces nothing

    def wrap(self, command, spec, workspace):
        return command                # pass-through

    def collector(self, spec):
        return None                   # no metrics
```

This is what Phase 1 does today — declare the spec, log it, compute grades, no enforcement. If no real engines are installed, `blq-sandbox` falls back to `log`.

## blq-sandbox-systemd engine

First real enforcement engine. Uses `systemd-run --scope` for cgroup-based resource limits and monitoring.

### Capabilities

```python
class SystemdEngine:
    name = "systemd"
    capabilities = {"memory", "cpu", "pids"}
```

### Wrapping

```python
def wrap(self, command: str, spec: SandboxSpec, workspace: Path,
         attempt_id: str) -> str:
    parts = [
        "systemd-run", "--scope", "--quiet",
        f"--unit=blq-{attempt_id[:8]}",
        "-p", "MemoryAccounting=yes",
        "-p", "CPUAccounting=yes",
    ]
    if spec.memory:
        parts.extend(["-p", f"MemoryMax={spec.memory}"])
    # Note: CPU budget (spec.cpu in cpu-seconds) is enforced via
    # cgroup cpu.max, not CPUQuota. CPUQuota is a rate limit (e.g., 50%
    # means half a core continuously). cpu.max is set directly via
    # the cgroup filesystem after scope creation — see collector setup.
    parts.append("--")
    parts.append(command)
    return " ".join(parts)
```

### Collector

Reads cgroup stats after execution:

```python
class SystemdCollector:
    def collect(self, spec: CommandSpec, result: ExecutionResult) -> None:
        scope = f"blq-{spec.attempt_id[:8]}.scope"
        cgroup_path = f"/sys/fs/cgroup/system.slice/{scope}"
        try:
            result.metrics["memory_peak_bytes"] = int(
                Path(f"{cgroup_path}/memory.peak").read_text().strip()
            )
            # ... cpu.stat parsing
        except FileNotFoundError:
            pass  # scope already cleaned up
```

## Refactoring the Phase 1 commit

### What moves out of core

| Current location | New location | What |
|---|---|---|
| `src/blq/sandbox.py` | `src/blq_sandbox/spec.py` | SandboxSpec, presets, parsing, grade computation |
| `RegisteredCommand.sandbox` field | removed | core doesn't parse sandbox config |
| `resolve_sandbox()` in `core.py` | `blq_sandbox` | extension parses its own config |
| sandbox in `serve.py` MCP tools | `blq_sandbox` provides data via pipeline | core passes extension data through |

### What stays in core

| What | Why |
|---|---|
| `blq_sandbox_summary()` SQL macro | queries over stored data — no sandbox-specific code |
| Schema migration 2.2→2.3 | already shipped |

### BIRD schema changes

The Phase 1 `sandbox` JSON column on `attempts` and `invocations` is replaced by a generic `extension_data` JSON column. This stores all extension data as a namespaced dict (e.g., `{"sandbox": {...}, "env": {...}}`).

Schema migration 2.3→2.4:
- Rename `sandbox` column to `extension_data` on both `attempts` and `invocations` tables
- Existing `sandbox` data is migrated: `UPDATE attempts SET extension_data = json_object('sandbox', sandbox) WHERE sandbox IS NOT NULL`
- Update `blq_sandbox_summary()` macro to read from `extension_data->>'sandbox'` instead of `sandbox`

`AttemptRecord` and `InvocationRecord` gain `extension_data: dict[str, Any] | None = None` replacing the current `sandbox` field.

### What's new in core

| What | Where |
|---|---|
| `blq.ext` package | Extension protocol types |
| `CommandSpec`, `ExecutionResult` | `blq/ext/__init__.py` |
| `Extension`, `Executor`, `Collector` | `blq/ext/__init__.py` |
| `load_extensions()` | `blq/ext/discovery.py` |
| `run_pipeline()` | `blq/ext/pipeline.py` |
| `LocalExecutor` | `blq/ext/local_executor.py` |
| Config passthrough in `_load_commands_impl` | preserve unknown TOML sections |

### MCP integration

The sandbox extension exposes its data through `extension_data` on the `CommandSpec` and `AttemptRecord`. Core's MCP tools pass through whatever extensions put there:

- `commands()` — includes each command's extension config sections as-is
- `info()` — includes `extension_data` from the stored attempt/invocation
- `register_command()` — accepts arbitrary config sections, passes to extensions for validation

No sandbox-specific code in the MCP server.

## Verification

1. **Unit tests**: `CommandSpec`, pipeline orchestration, extension discovery
2. **blq-sandbox tests**: `SandboxSpec` (migrated from existing test_sandbox.py), engine dispatch, dimension mapping
3. **blq-sandbox-systemd tests**: command wrapping, cgroup stat parsing (mocked)
4. **Integration**: Register a command with `sandbox = "test"`, run it, verify sandbox data appears in BIRD and MCP output
5. **Fallback**: With no engines installed, verify `blq-sandbox` falls back to log engine
6. **Config round-trip**: Save/load commands.toml with extension sections, verify preservation
7. **Full test suite**: `pytest tests/` — no regressions

## Priorities

1. Extension protocol + discovery + pipeline in core
2. LocalExecutor extracted from current execution path
3. Config passthrough (preserve unknown TOML sections)
4. `blq-sandbox` with SandboxSpec + engine dispatch + log engine
5. `blq-sandbox-systemd` engine
6. Refactor Phase 1 commit (remove sandbox from core)
7. Tests throughout
