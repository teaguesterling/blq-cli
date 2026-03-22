# Extension System

*Lightweight execution wrappers for sandbox, environment, and platform concerns.*

## Motivation

blq's core is capture and query: run a command, capture its output, parse events, store everything in DuckDB. The execution path is deliberately simple — `subprocess.Popen(cmd, shell=True)` with streaming, timeout handling, and signal management.

Three concerns want to modify how commands execute without changing what blq captures:

1. **Sandbox** — bound the command's effects (network, filesystem, memory, processes)
2. **Environment** — set up what the command needs (interpreters, deps, env vars, source scripts)
3. **Platform** — choose where the command runs (local, Docker, SLURM, K8s)

Each wraps the execution differently. A sandbox extension wraps with nsjail. A Docker extension wraps with `docker run`. A SLURM extension wraps with `sbatch`. But from blq's perspective, the result is the same: a process ran, produced output, exited with a code, consumed resources. blq captures all of that identically.

These concerns don't belong in blq core. They're optional, composable, and platform-specific. An extension system lets them plug in without bloating the core.

## Design principles

**blq core doesn't change.** Extensions wrap the execution path. The `_execute_with_live_output()` function in `execution.py` is the integration point, but its responsibility stays the same: manage the subprocess lifecycle, stream output, capture results.

**Extensions compose.** An environment extension sets up the shell, then a sandbox extension wraps it. Order matters and is explicit. You can use sandbox without environment, environment without platform, or any combination.

**Extensions are packages.** Each extension is a separate installable package (`blq-sandbox`, `blq-docker`, etc.) that registers itself via a Python entry point. blq core discovers installed extensions at runtime.

**Configuration lives in `commands.toml`.** Each extension reads its own section. blq core ignores sections it doesn't recognize. The command definition is the single source of truth.

**Extensions declare capabilities.** A Docker extension declares it can enforce `network`, `filesystem`, `memory`, `cpu` but not `seccomp`. blq can validate that a sandbox spec is fully enforceable on the selected platform.

## Architecture

```
commands.toml                 blq core               extensions
─────────────               ─────────               ──────────

[commands.test]         ┌─ resolve command ─┐
cmd = "pytest tests/"   │                   │
                        │  load extensions   │
[commands.test.env]     │        │          │
venv = ".venv"          │        ▼          │      ┌─────────────┐
                        │  env extension ───┼─────▶│ blq-env     │
[commands.test.sandbox] │        │          │      │ source .venv│
network = "none"        │        ▼          │      └─────────────┘
filesystem = "readonly" │  sandbox ext ─────┼─────▶┌─────────────┐
                        │        │          │      │ blq-sandbox │
[commands.test.platform]│        ▼          │      │ nsjail wrap │
default = "local"       │  platform ext ────┼─────▶└─────────────┘
                        │        │          │      ┌─────────────┐
                        │        ▼          │      │ blq-docker  │
                        │  Popen(wrapped)   │      │ docker run  │
                        │        │          │      └─────────────┘
                        │  stream output    │
                        │  capture results  │
                        │  store in DuckDB  │
                        └───────────────────┘
```

## Extension interface

### The wrapper protocol

An extension transforms a command invocation before execution and optionally collects artifacts after execution. The interface is a Python protocol:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Any

@dataclass
class ExecutionContext:
    """What blq knows about the command being run."""
    command: str                    # rendered command string
    command_name: str               # registered name (e.g. "test")
    workspace: Path                 # project root
    live_dir: Path                  # .lq/live/{attempt_id}/
    attempt_id: str                 # UUID
    run_number: int
    timeout: int | None             # seconds, from RegisteredCommand
    config: dict[str, Any]          # this extension's section from commands.toml


@dataclass
class WrapResult:
    """How the extension modifies execution."""
    command: str                    # the (possibly wrapped) command string
    env_updates: dict[str, str] = field(default_factory=dict)  # env var additions
    pre_command: str | None = None  # shell command to run before (e.g. source .venv/bin/activate)


@dataclass
class PostResult:
    """Artifacts collected after execution."""
    profiles: dict[str, Path] = field(default_factory=dict)  # name → tempfile to store as blob
    metrics: dict[str, Any] = field(default_factory=dict)     # structured data for DuckDB
    metadata: dict[str, Any] = field(default_factory=dict)    # additional metadata to log


class Extension(Protocol):
    """Protocol for blq execution extensions."""

    name: str
    """Extension identifier (e.g. 'sandbox', 'docker', 'env')."""

    config_key: str
    """Section name in commands.toml (e.g. 'sandbox', 'env', 'platform')."""

    def wrap(self, ctx: ExecutionContext) -> WrapResult:
        """Transform the command before execution.

        Called in extension order. Each extension receives the command
        as modified by previous extensions.
        """
        ...

    def post(self, ctx: ExecutionContext, exit_code: int, duration_ms: int) -> PostResult:
        """Collect artifacts after execution.

        Called after the process exits but before blq writes the OutcomeRecord.
        This is where resource profiles are read, strace outputs are collected, etc.
        """
        ...

    def validate(self, config: dict[str, Any]) -> list[str]:
        """Validate the extension's config section. Return list of warnings/errors."""
        ...
```

### How wrapping works

The `wrap()` method returns a `WrapResult` that can modify the command string, add environment variables, or prepend setup commands. Examples:

**Environment extension** — activates a virtualenv:
```python
class EnvExtension:
    name = "env"
    config_key = "env"

    def wrap(self, ctx: ExecutionContext) -> WrapResult:
        venv = ctx.config.get("venv")
        source_scripts = ctx.config.get("source", [])
        env_vars = ctx.config.get("vars", {})

        pre_parts = []
        if venv:
            pre_parts.append(f"source {venv}/bin/activate")
        for script in source_scripts:
            pre_parts.append(f"source {script}")

        return WrapResult(
            command=ctx.command,  # unchanged
            env_updates=env_vars,
            pre_command=" && ".join(pre_parts) if pre_parts else None,
        )
```

**Sandbox extension** — wraps in nsjail + systemd-run:
```python
class SandboxExtension:
    name = "sandbox"
    config_key = "sandbox"

    def wrap(self, ctx: ExecutionContext) -> WrapResult:
        spec = SandboxSpec.from_config(ctx.config)

        # Generate nsjail config
        nsjail_cfg = ctx.live_dir / "sandbox.cfg"
        nsjail_cfg.write_text(build_nsjail_config(ctx.command, spec, ctx.workspace))

        # Wrap command with systemd-run (cgroup accounting) + nsjail (isolation)
        wrapped = (
            f"systemd-run --scope --unit=blq-{ctx.attempt_id[:8]} "
            f"-p MemoryAccounting=yes -p CPUAccounting=yes "
        )
        if spec.memory:
            wrapped += f"-p MemoryMax={spec.memory} "
        if spec.timeout:
            wrapped += f"-- nsjail --config {nsjail_cfg}"
        else:
            wrapped += f"-- nsjail --config {nsjail_cfg}"

        return WrapResult(command=wrapped)

    def post(self, ctx: ExecutionContext, exit_code: int, duration_ms: int) -> PostResult:
        # Read cgroup stats before systemd cleans up the scope
        scope_name = f"blq-{ctx.attempt_id[:8]}"
        cgroup_path = f"/sys/fs/cgroup/system.slice/{scope_name}.scope"

        metrics = {}
        try:
            metrics["memory_peak_bytes"] = int(
                Path(f"{cgroup_path}/memory.peak").read_text().strip()
            )
            cpu_stat = parse_cpu_stat(
                Path(f"{cgroup_path}/cpu.stat").read_text()
            )
            metrics.update(cpu_stat)
        except FileNotFoundError:
            pass  # scope already cleaned up

        return PostResult(metrics=metrics)
```

**Docker extension** — wraps in `docker run`:
```python
class DockerExtension:
    name = "docker"
    config_key = "platform.docker"

    def wrap(self, ctx: ExecutionContext) -> WrapResult:
        image = ctx.config["image"]
        mount = ctx.config.get("mount_workspace", True)

        wrapped = f"docker run --rm"
        if mount:
            wrapped += f" -v {ctx.workspace}:{ctx.workspace} -w {ctx.workspace}"

        # Map sandbox spec to Docker flags if sandbox config also present
        sandbox = ctx.config.get("_sandbox")  # passed from sandbox extension
        if sandbox:
            if sandbox.get("network") == "none":
                wrapped += " --network=none"
            if sandbox.get("memory"):
                wrapped += f" --memory={sandbox['memory']}"

        wrapped += f" {image} {ctx.command}"
        return WrapResult(command=wrapped)
```

**Profiling extension** — wraps in strace (one-time, via `blq profile`):
```python
class ProfileExtension:
    name = "profile"
    config_key = "profile"

    def wrap(self, ctx: ExecutionContext) -> WrapResult:
        self._strace_path = ctx.live_dir / "profile.strace"
        wrapped = (
            f"strace -f -e trace=%file,%network,%process "
            f"-o {self._strace_path} -- {ctx.command}"
        )
        return WrapResult(command=wrapped)

    def post(self, ctx: ExecutionContext, exit_code: int, duration_ms: int) -> PostResult:
        return PostResult(
            profiles={"strace": self._strace_path},
        )
```

### Extension composition

Extensions are applied in a defined order. The order matters because each `wrap()` receives the command as modified by previous extensions:

```python
# Default order (configurable)
EXTENSION_ORDER = ["env", "sandbox", "profile", "platform"]
```

The composition for `blq run test` with env + sandbox:

```
1. resolve: "pytest tests/ --tb=short"
2. env.wrap():     pre_command="source .venv/bin/activate"
   → "source .venv/bin/activate && pytest tests/ --tb=short"
3. sandbox.wrap(): wraps in systemd-run + nsjail
   → "systemd-run --scope ... -- nsjail --config ... "
4. Popen(final_command)
5. ... process runs, output streams ...
6. process exits
7. sandbox.post(): reads cgroup stats → metrics
8. blq writes OutcomeRecord + metrics + blobs
```

Platform extensions (docker, slurm) replace the local execution model entirely, so they run last and consume the full wrapped command.

## Discovery and registration

Extensions register via Python entry points:

```toml
# In blq-sandbox's pyproject.toml
[project.entry-points."blq.extensions"]
sandbox = "blq_sandbox:SandboxExtension"

# In blq-docker's pyproject.toml
[project.entry-points."blq.extensions"]
docker = "blq_docker:DockerExtension"
```

blq core discovers extensions at startup:

```python
from importlib.metadata import entry_points

def load_extensions() -> dict[str, Extension]:
    extensions = {}
    for ep in entry_points(group="blq.extensions"):
        ext_class = ep.load()
        ext = ext_class()
        extensions[ext.name] = ext
    return extensions
```

An extension is only active for a command if the command has a matching config section:

```toml
# This command uses sandbox and env extensions
[commands.test]
cmd = "pytest tests/"

[commands.test.env]
venv = ".venv"

[commands.test.sandbox]
network = "none"
filesystem = "readonly"

# This command uses no extensions
[commands.build]
cmd = "make -j8"
```

## Configuration in commands.toml

Each extension owns its config section. blq core passes the section dict to the extension and doesn't interpret it. This means extensions can evolve their config schemas independently.

```toml
[commands.train]
cmd = "python train.py"
timeout = 3600

# Environment extension
[commands.train.env]
venv = ".venv"
source = ["setup-cuda.sh"]
vars = { CUDA_VISIBLE_DEVICES = "0,1" }

# Sandbox extension
[commands.train.sandbox]
network = "none"
filesystem = "workspace_only"
memory = "16g"
gpu = true                          # extension-specific

# Platform extension
[commands.train.platform]
default = "local"

[commands.train.platform.slurm]
partition = "gpu"
gpus = 2
time = "01:00:00"
mem = "32G"

[commands.train.platform.docker]
image = "pytorch/pytorch:2.2-cuda12.1"
mount_workspace = true
gpus = "all"
```

Selecting a platform at runtime: `blq run train --platform=slurm`

## Capability declarations

Extensions declare what they can enforce, so blq can validate specs:

```python
class SandboxExtension:
    name = "sandbox"
    config_key = "sandbox"

    capabilities = {
        "network", "filesystem", "memory", "cpu",
        "processes", "seccomp", "tmpfs",
    }


class DockerExtension:
    name = "docker"
    config_key = "platform.docker"

    capabilities = {
        "network", "filesystem", "memory", "cpu",
        # no seccomp, no tmpfs control, no fine-grained process isolation
    }
```

Validation at registration time or first run:

```
$ blq run test --platform=docker
⚠ sandbox spec requires 'seccomp' enforcement, but docker platform does not support it.
  Unenforced dimensions: seccomp
  Proceeding with partial enforcement.
```

## Post-execution artifacts

The `post()` method returns three types of artifacts:

| Type | Storage | Example |
|---|---|---|
| **profiles** | Content-addressed blob (`.lq/blobs/`) | strace output, nsjail logs |
| **metrics** | DuckDB table (`extension_metrics`) | cgroup stats, Docker container stats |
| **metadata** | JSON in OutcomeRecord | platform used, enforcement warnings |

Profiles go through the existing blob pipeline — hash, dedup, store, link in `outputs` table with `output_kind` set by the extension. Metrics go to a new table or extend `resource_profiles`. Metadata attaches to the outcome record.

```sql
CREATE TABLE extension_metrics (
    attempt_id UUID REFERENCES attempts(id),
    extension TEXT,             -- 'sandbox', 'docker', etc.
    key TEXT,                   -- 'memory_peak_bytes', 'cpu_usage_usec', etc.
    value_num DOUBLE,          -- numeric metrics
    value_text TEXT,            -- string metrics
);
```

Or, for the common case of resource profiles, a dedicated table as described in the sandbox spec addendum.

## blq profile command

`blq profile <command>` is syntactic sugar for running with the profile extension active:

```bash
blq profile test
# equivalent to:
# BLQ_EXTENSIONS=profile blq run test
```

After the run, blq:
1. Stores the strace blob
2. Parses it (via duck_hunt's strace parser) to extract file access, network, subprocess data
3. Suggests a sandbox spec
4. Optionally generates a Kafel seccomp policy
5. Stores suggestions in `.lq/profiles/{command}-suggested.toml`

The user reviews and copies the relevant sections into `commands.toml`.

## What blq core needs

The changes to blq core are minimal:

1. **Extension discovery** — load entry points at startup, ~20 lines
2. **Extension dispatch in execution path** — call `wrap()` before Popen, `post()` after wait, ~30 lines in `_execute_with_live_output()`
3. **Config passthrough** — when loading `commands.toml`, preserve unknown sections and pass them to extensions, ~10 lines
4. **Artifact storage** — route `PostResult.profiles` through existing blob pipeline, store `PostResult.metrics` in DuckDB, ~20 lines
5. **`output_kind` column** — add to `outputs` table for distinguishing stdout from extension artifacts, ~5 lines

No changes to the MCP server, event parsing, hooks system, or query interface. Extensions are invisible to everything except the execution path.

## Priorities

1. **Extension protocol** — define the `Extension` protocol, `ExecutionContext`, `WrapResult`, `PostResult` dataclasses. Ship in blq core.
2. **Extension discovery** — entry point loading, config passthrough. Ship in blq core.
3. **Execution path integration** — `wrap()` / `post()` dispatch in `_execute_with_live_output()`.
4. **`blq-sandbox`** — first extension package. nsjail + systemd-run, resource profiling.
5. **`blq profile`** — strace-based profiling command, suggests sandbox specs.
6. **`blq-docker`** — Docker execution platform. Maps sandbox specs to Docker flags.

Later:
- `blq-env` — virtualenv activation, source scripts, env var management
- `blq-slurm` — SLURM job submission
- `blq-k8s` — Kubernetes pod execution
