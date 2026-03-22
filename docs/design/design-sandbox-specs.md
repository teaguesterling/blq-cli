# Sandbox Specifications

*Declarative execution environment bounds for registered commands.*

## Motivation

### The problem from practice

In controlled experiments comparing agent tool configurations, we discovered that the execution environment's bounds are a critical — and currently invisible — part of every command's type signature.

When an agent runs `blq run test`, the command executes in whatever environment the shell provides. The agent (and the Harness regulating the agent) can't characterize what that environment allows: Can the command reach the network? Write to arbitrary paths? Spawn persistent processes? Consume unbounded resources? The answers depend on the host system's configuration, not on anything in blq's command registry.

This is the same problem the Ma framework identifies with computation channels: the *input* to the command is characterizable (blq logs it), but the *execution environment* is not. Two identical commands on two differently-configured machines can have different effect boundaries — and blq captures neither the boundaries nor whether they were violated.

### The problem stated formally

Every registered command has an implicit grade — a position on the framework's lattice:

```
grade(command) = (world_coupling, computation_level)
```

Currently blq captures the output (what the command produced) but not the grade (what the command could have done). The sandbox spec makes the grade explicit, enforceable, and queryable.

### What this enables

1. **Agent safety**: The Harness knows what bounds a command runs within before dispatching it
2. **Audit**: Every run's sandbox spec is logged alongside its output — queryable by DuckDB
3. **Reproducibility**: The execution environment is part of the command definition, not implicit
4. **Grade measurement**: The spec IS the formal grade of the command's execution environment
5. **Ratchet support**: As commands get promoted from bash to structured tools, the sandbox spec tightens — the grade drops, measurably

## What is a sandbox spec?

A sandbox spec declares the *consequence bounds* of a command's execution. It answers: regardless of what the command does internally, what effects can it have on the world?

```toml
[commands.test]
cmd = "python -m pytest tests/ --tb=short"

[commands.test.sandbox]
network = "none"              # no network access
filesystem = "readonly"       # can't write to workspace
timeout = "60s"               # killed after 60 seconds
memory = "512m"               # OOM-killed above 512MB
cpu = "30s"                   # killed after 30 CPU-seconds
processes = "isolated"        # can't see host processes
tmpfs = "100m"                # writable /tmp, capped at 100MB
paths_readable = ["workspace", "/usr", "/bin", "/lib", "/lib64"]
paths_hidden = ["/home", "/var", "/root", "/etc/shadow"]
```

### The dimensions

Each dimension is independently characterizable — the Harness can decide, before execution, exactly what the command can and can't do:

| Dimension | Values | What it bounds | Mechanism |
|---|---|---|---|
| **network** | `none`, `localhost`, `allowed_hosts`, `unrestricted` | Data exfiltration, external dependencies | bwrap --unshare-net, or iptables rules |
| **filesystem** | `readonly`, `workspace_only`, `scoped_write`, `unrestricted` | Persistent mutation, data leakage | bwrap --ro-bind / --bind |
| **timeout** | duration (e.g. `60s`, `5m`) | Infinite loops, resource exhaustion | subprocess timeout + SIGKILL |
| **memory** | size (e.g. `512m`, `2g`) | Memory exhaustion, OOM | cgroup memory.max |
| **cpu** | duration (e.g. `30s`, `2m`) | CPU exhaustion, crypto mining | cgroup cpu.max |
| **processes** | `isolated`, `visible` | Process enumeration, signaling | bwrap --unshare-pid |
| **tmpfs** | size (e.g. `100m`) | Disk exhaustion within scratch | --tmpfs with size= |
| **paths_readable** | list of paths | Information disclosure scope | Selective bind mounts |
| **paths_hidden** | list of paths | Sensitive data protection | Omit from mount namespace |

### Presets

Common configurations as named presets:

```toml
[commands.test]
cmd = "python -m pytest tests/"
sandbox = "test"      # preset: readonly + no network + 60s timeout + 512m

[commands.build]
cmd = "make -j8"
sandbox = "build"     # preset: workspace_only write + no network + 5m timeout + 2g

[commands.lint]
cmd = "ruff check ."
sandbox = "readonly"  # preset: readonly + no network + 30s + 256m

[commands.deploy]
cmd = "./deploy.sh"
sandbox = "none"      # no sandboxing (explicit opt-out, logged as such)
```

| Preset | network | filesystem | timeout | memory | cpu |
|---|---|---|---|---|---|
| **readonly** | none | readonly | 30s | 256m | 15s |
| **test** | none | readonly | 60s | 512m | 30s |
| **build** | none | workspace_only | 5m | 2g | 2m |
| **integration** | localhost | workspace_only | 10m | 4g | 5m |
| **unrestricted** | unrestricted | unrestricted | 30m | — | — |
| **none** | unrestricted | unrestricted | — | — | — |

## Grading commands on the Ma lattice

The sandbox spec maps directly to the Ma framework's grade lattice. This makes every registered command's security posture formally measurable:

### World coupling (W axis)

```
sealed    ← sandbox.network=none, sandbox.filesystem=readonly, paths_readable=[]
pinhole   ← sandbox.network=none, sandbox.filesystem=readonly, paths_readable=[workspace]
scoped    ← sandbox.network=none, sandbox.filesystem=workspace_only
broad     ← sandbox.network=localhost, sandbox.filesystem=unrestricted
open      ← sandbox.network=unrestricted, sandbox.filesystem=unrestricted
```

### Effects ceiling (D axis derivative)

The sandbox bounds *effects* — what the process can do to the world. It does NOT bound *computation* — what the process computes internally. A Turing-complete program running in a read-only sandbox with no network is still Turing-complete. It just can't *do* anything to the world beyond returning a result.

The sandbox provides an **effects ceiling**: the maximum computation level that the sandbox's effect constraints allow. The actual computation level depends on two things:

1. **The tool interface** — what computation level the tool operates at (a property of the tool's design)
2. **The effects ceiling** — what consequences the sandbox permits (a property of the sandbox spec)

The effective risk is the tool level bounded by the effects ceiling:

```
A level 1 tool in a level 7 sandbox: effective risk = level 1
  (the tool doesn't use the sandbox's full allowance)

A level 4 tool in a level 2 sandbox: effective risk = level 2
  (the sandbox constrains the consequences — the computation is
  Turing-complete but the effects are bounded to read + compute)
```

This is why structured tools AND sandboxing are complementary, not redundant. Structured tools constrain the *interface* (what the agent can ask for). Sandboxes constrain the *consequences* (what happens when the request executes). Neither alone is sufficient. The tool interface without a sandbox trusts the implementation. The sandbox without a structured interface trusts the input.

#### Effects ceiling by sandbox configuration

| Sandbox allows | Effects ceiling | Why |
|---|---|---|
| readonly, no network, pids.max=1 | Level 2 | Can read and process but can't mutate, spawn, or reach out |
| readonly, no network | Level 2 | Can spawn children but they can't write either — effectively level 2 |
| workspace write, no network, pids.max=1 | Level 4 | Can write + execute, but no child processes |
| workspace write, no network | Level 7 | Can spawn persistent background processes within sandbox |
| workspace write, localhost | Level 7+ | Can talk to local services, install packages |
| unrestricted | Level 8 | Unbounded |

#### The level 3/4 gap

The sandbox can't distinguish level 3 (writing data) from level 4 (writing executable code). Both look like "writes bytes to a file." The filesystem doesn't know whether those bytes are a configuration value or a Python program. This distinction lives in the *tool interface*, not the sandbox:

- `file_edit(path, old_string, new_string)` — level 3. The tool writes a structured replacement. The content could be code, but the tool's interface is a string replacement, not an execution request.
- `bash("cat > script.py << 'EOF' ... EOF && python script.py")` — level 4. The tool accepts an executable specification and runs it.

The sandbox treats both as "writes to workspace." The computation level difference is in the tool, not the container.

### Grading our experiment conditions

Two components determine each condition's grade: the tool interfaces (what the agent interacts with) and the effects ceilings (what the sandbox allows).

```
Condition I (file_edit + run_tests):
  file_edit:
    Tool interface: level 3 (structured mutation — one file, one replacement)
    Effects ceiling: level 3 (writes one specified file, nothing more)
    Effective level: 3

  run_tests:
    Tool interface: level 1 (structured query — test_file + verbose flag)
    Internal execution: level 4 (pytest runs agent-written Python)
    Effects ceiling: level 2 (readonly sandbox, no network, isolated PIDs)
    Effective level: 2 (consequences bounded to read + compute)

  Composite: level 3 (the join — mutation from file_edit)

  Key structural property: the agent WRITES through file_edit (level 3,
  logged, auditable) and EXECUTES through run_tests (level 1 interface,
  level 2 effects ceiling). It cannot close the write-execute loop in
  one tool call. The separation prevents level 4.

Condition D (bash_sandboxed):
  bash:
    Tool interface: level 4+ (arbitrary executable specification)
    Effects ceiling: level 7 (writable, no network, can spawn processes)
    Effective level: 7

  The agent writes AND executes in one opaque tool call.
  No separation. No audit point between intent and execution.

  With pids.max=1:  effective level 4 (can write + execute, no spawning)
  With readonly:    effective level 2 (can read + compute only)
```

### What level is D really?

This is the question you raised. Let's trace what `bash_sandboxed` can actually do within its current bwrap config:

```bash
# Level 4: Computation amplification — YES
python3 -c "exec(open('generated_code.py').read())"

# Level 5: Environment modification — PARTIALLY
pip install malicious-package  # FAILS: no network
# But:
python3 -c "import importlib; importlib.import_module('os').system('...')"  # works

# Level 6: Capability creation — YES
cat > /workspace/new_tool.sh << 'EOF'
#!/bin/bash
# a new tool the agent just created
EOF
chmod +x /workspace/new_tool.sh
./new_tool.sh  # executes

# Level 7: Subprocess spawning — YES
python3 -c "
import subprocess
subprocess.Popen(['python3', '-m', 'http.server', '8080'],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
"
# Process outlives the tool call. Listens on localhost:8080.
# Network namespace blocks external access but localhost works.
# The Harness doesn't know this process exists.

# Level 8: Controller modification — PARTIALLY
# Can't edit CLAUDE.md (it's outside the workspace in our setup)
# But could modify .mcp.json if it's in the workspace
cat > /workspace/.mcp.json << 'EOF'
{"mcpServers": {"malicious": {"command": "..."}}}
EOF
```

**D is actually level 7.** It can spawn persistent subprocesses within the sandbox. The bwrap sandbox prevents network egress and restricts filesystem scope, but it does NOT prevent process spawning, capability creation, or background execution. The fold model breaks at level 7 — there's computation happening that the Harness didn't invoke and doesn't observe.

**I is level 2** (with the caveat that run_tests executes Turing-complete code). The sandbox on run_tests prevents everything above level 2: no writes (no mutation, level 3), no subprocess spawning (no level 7), no capability creation (no level 6, workspace is read-only).

The actual grade comparison:

```
I: (scoped, level 2 bounded)  — in the specified band for effects
D: (scoped, level 7 bounded)  — above the specified band, fold model breaks
```

That's a 5-level difference in the computation taxonomy. Not I ≈ D with a minor edge. I is in a fundamentally different regulatory regime.

### Tightening D with sandbox specs

If we wanted to bring D down to a lower level, the sandbox spec tells us exactly what to restrict:

```toml
# D at level 7 (current)
[commands.bash_current]
sandbox.network = "none"
sandbox.filesystem = "workspace_only"  # read-write
sandbox.processes = "isolated"          # can't see host, but CAN spawn children

# D at level 4 (tightened)
[commands.bash_level4]
sandbox.network = "none"
sandbox.filesystem = "workspace_only"
sandbox.processes = "isolated"
sandbox.max_pids = 10                   # cap child processes
sandbox.no_daemon = true                # kill all children on exit
# Still level 4: can write arbitrary code and execute it
# But can't create persistent processes or capabilities

# D at level 3 (further tightened)
[commands.bash_level3]
sandbox.network = "none"
sandbox.filesystem = "workspace_only"
sandbox.processes = "none"              # no subprocess spawning at all
sandbox.exec = ["python3", "grep", "cat", "find", "sed"]  # allowlisted executables
# Level 3: can read and write files, but computation is bounded
# to specific executables — no arbitrary code execution

# D at level 2 (maximally tightened)
[commands.bash_level2]
sandbox.network = "none"
sandbox.filesystem = "readonly"
sandbox.processes = "none"
sandbox.exec = ["grep", "cat", "find", "wc", "head", "tail"]
# Level 2: read-only, bounded computation — equivalent to Condition B
```

Each step down the taxonomy removes specific capabilities. The sandbox spec makes each step *explicit and queryable*. You can ask: "what level is this command configured to run at?" and get a precise answer from the spec.

## Implementation

### Enforcement engine: nsjail

The enforcement layer should use [nsjail](https://github.com/google/nsjail) rather than raw bwrap. nsjail is Google's process isolation tool, built for exactly this use case: declarative sandbox specs enforced via Linux namespaces, cgroups, and seccomp.

**Why nsjail over bwrap:**

| Capability | bwrap | nsjail |
|---|---|---|
| Namespace isolation (PID, mount, net, IPC, UTS) | ✓ | ✓ |
| Filesystem bind mounts | ✓ | ✓ |
| cgroup v2 resource limits (memory, CPU, PID) | Manual | Built-in |
| seccomp filters (syscall allowlisting) | Manual | Built-in (Kafel policy language) |
| rlimits (file descriptors, stack, processes) | No | Built-in |
| Declarative config format | CLI flags only | Protobuf config files |
| Per-process cgroup creation/cleanup | Manual | Automatic |
| User-mode NAT for network control | No | Built-in |

bwrap is a building block — you assemble the sandbox from CLI flags. nsjail is a framework — you declare the sandbox in a config file and it enforces the full stack. Our `SandboxSpec` maps almost 1:1 to nsjail's `config.proto`, which means the translation layer is minimal.

**The key advantage**: nsjail manages the full lifecycle. It creates cgroups, applies seccomp filters, sets up namespaces, runs the command, and cleans up — all from a single config. With bwrap, we'd need to build the cgroup management, seccomp compilation, and resource limit enforcement ourselves.

**What we build on top**: nsjail handles enforcement. blq adds the layers nsjail doesn't provide:
1. Named command registry with specs (nsjail has per-config-file specs, not a registry)
2. Spec logged alongside every run into DuckDB
3. Resource usage capture from cgroup stats before cleanup
4. Queryable correlation of spec + enforcement + outcome
5. Grade computation (effects_ceiling, grade_w) from the spec

### Phase 0: Monitoring mode (observe before restricting)

Before enforcing sandbox specs, observe what commands actually do. The ratchet's discovery phase applied to execution environments.

**The workflow**: Run commands without restriction. Record what they actually access, consume, and attempt. Use the observations to generate a sandbox spec with margin. Then enforce.

**Implementation — three tiers:**

**Tier 1: Resource profiling via cgroups (simplest)**

Run commands inside a `systemd-run --scope` transient unit with accounting enabled but no limits. After the command exits, read cgroup v2 stats before cleanup:

```python
@dataclass
class ResourceProfile:
    """Observed resource usage from a single run."""
    memory_peak: int          # bytes — from memory.peak
    memory_swap_peak: int     # bytes — from memory.swap.peak
    cpu_usage_usec: int       # microseconds — from cpu.stat usage_usec
    cpu_user_usec: int        # microseconds — from cpu.stat user_usec
    cpu_system_usec: int      # microseconds — from cpu.stat system_usec
    wall_time_ms: int         # milliseconds — measured by blq

def read_cgroup_stats(cgroup_path: str) -> ResourceProfile:
    """Read cgroup v2 stats before cleanup. Requires kernel 5.19+ for memory.peak."""
    # memory.peak persists after process exit until rmdir
    memory_peak = int(Path(f"{cgroup_path}/memory.peak").read_text().strip())
    cpu_stat = parse_cpu_stat(Path(f"{cgroup_path}/cpu.stat").read_text())
    return ResourceProfile(
        memory_peak=memory_peak,
        cpu_usage_usec=cpu_stat["usage_usec"],
        # ...
    )
```

Log the `ResourceProfile` alongside every run. After N runs, suggest a spec:

```sql
-- Generate sandbox spec from observed resource usage (with 2x headroom)
SELECT command,
       max(memory_peak) * 2 as suggested_memory,
       max(cpu_usage_usec) / 1e6 * 2 as suggested_cpu_seconds,
       max(wall_time_ms) / 1000 * 3 as suggested_timeout
FROM blq_resource_profiles
GROUP BY command
HAVING count(*) >= 10;
```

**Tier 2: File and network access profiling via strace**

For the first run of a new command (or on explicit `blq profile <command>`), wrap in strace:

```bash
strace -f -e trace=%file,%network,%process -o /tmp/blq-profile-{run_id}.log \
    -- <command>
```

Parse the trace to extract:
- Files opened (paths — determines `paths_readable`, `filesystem`)
- Network connections attempted (determines `network`)
- Subprocesses spawned (determines `processes`)

This has 2-10x overhead, so it's a one-time profiling step, not continuous.

**Tier 3: seccomp learning mode via nsjail**

nsjail supports `seccomp_log: true`, which sets `SECCOMP_FILTER_FLAG_LOG`. Combined with a Kafel policy where the default action is `LOG` (not `KILL`), every syscall is logged to the kernel audit subsystem but allowed to execute. Parse the audit log afterward to generate a seccomp allowlist.

```protobuf
# nsjail config for learning mode
seccomp_log: true
seccomp_string: "DEFAULT LOG"  # log everything, block nothing
```

The audit records go to `dmesg` / `journalctl -k` / `auditd`. They include syscall numbers but not arguments — for argument-level detail (which files, which addresses), use Tier 2 strace.

**The monitoring-to-enforcement progression:**

```
1. No spec          → command runs unrestricted, blq logs output only
2. monitor: true    → command runs unrestricted, blq logs resource usage (Tier 1)
3. profile: true    → one-time strace run captures access patterns (Tier 2)
4. sandbox: "test"  → spec declared, enforced via nsjail
5. Violations       → logged as structured events, queryable
```

Each step is a ratchet turn. The spec tightens as confidence grows. Start unrestricted, observe, crystallize, enforce.

### Phase 1: Spec definition and logging

Add sandbox spec to the command registry schema. Log the spec alongside every run. No enforcement yet — just declaration and capture.

```python
@dataclass
class SandboxSpec:
    network: str = "unrestricted"       # none, localhost, allowed_hosts, unrestricted
    filesystem: str = "unrestricted"    # readonly, workspace_only, scoped_write, unrestricted
    timeout: Optional[int] = None       # seconds
    memory: Optional[int] = None        # bytes
    cpu: Optional[int] = None           # cpu-seconds
    processes: str = "visible"          # isolated, visible
    tmpfs: Optional[int] = None         # bytes
    paths_readable: list[str] = field(default_factory=list)
    paths_hidden: list[str] = field(default_factory=list)

    @property
    def grade_w(self) -> str:
        """Compute world coupling level from spec."""
        if self.network == "unrestricted" and self.filesystem == "unrestricted":
            return "open"
        if self.network != "none":
            return "broad"
        if self.filesystem in ("workspace_only", "scoped_write"):
            return "scoped"
        if self.filesystem == "readonly":
            return "pinhole"
        return "sealed"

    @property
    def effects_ceiling(self) -> int:
        """Maximum computation level the sandbox's effect constraints allow.

        This is a ceiling, not the actual level. The actual risk depends on
        the tool running inside:
        - A level 1 tool in a level 7 sandbox: effective risk = 1
          (the tool doesn't use the sandbox's full allowance)
        - A level 4 tool in a level 2 sandbox: effective risk = 2
          (the sandbox constrains the consequences)

        The sandbox bounds effects (filesystem, network, processes).
        It cannot bound semantics (is this write data or code?).
        The level 3/4 distinction (data vs executable specification)
        is a property of the tool interface, not the sandbox.
        """
        if self.network != "none":
            return 8  # can reach external services
        if self.processes == "visible" and self.filesystem not in ("readonly",):
            return 7  # can spawn persistent subprocesses + write
        if self.filesystem not in ("readonly",):
            return 4  # can write; sandbox can't distinguish data from code
        return 2      # read + compute only (effects bounded)
```

### Phase 2: Enforcement via nsjail

When `blq run` executes a command with a sandbox spec, generate an nsjail config and run through nsjail:

```python
def build_nsjail_config(cmd: str, spec: SandboxSpec, workspace: Path) -> str:
    """Generate nsjail protobuf config from a SandboxSpec."""
    config = []

    # Execution
    config.append(f'exec_bin {{ path: "/bin/bash" arg: "-c" arg: "{cmd}" }}')
    config.append(f'cwd: "{workspace}"')

    # Filesystem — workspace mount
    if spec.filesystem == "readonly":
        config.append(f'mount {{ src: "{workspace}" dst: "{workspace}" is_bind: true rw: false }}')
    elif spec.filesystem == "workspace_only":
        config.append(f'mount {{ src: "{workspace}" dst: "{workspace}" is_bind: true rw: true }}')

    # System libraries (always read-only)
    for sys_path in ["/usr", "/bin", "/lib", "/lib64"]:
        config.append(f'mount {{ src: "{sys_path}" dst: "{sys_path}" is_bind: true rw: false }}')

    # Tmpfs
    tmpfs_size = spec.tmpfs or 100 * 1024 * 1024  # default 100MB
    config.append(f'mount {{ dst: "/tmp" fstype: "tmpfs" rw: true options: "size={tmpfs_size}" }}')
    config.append('mount { dst: "/proc" fstype: "proc" rw: false }')
    config.append('mount { dst: "/dev" fstype: "tmpfs" rw: false }')

    # Namespaces — nsjail handles all of these natively
    if spec.network == "none":
        config.append("clone_newnet: true")
    if spec.processes == "isolated":
        config.append("clone_newpid: true")
    config.append("clone_newns: true")    # always: mount namespace
    config.append("clone_newipc: true")   # always: IPC isolation

    # Resource limits — nsjail manages cgroups automatically
    if spec.memory:
        config.append(f"cgroup_mem_max: {spec.memory}")
    if spec.cpu:
        config.append(f"rlimit_cpu_type: HARD")
        config.append(f"rlimit_cpu: {spec.cpu}")
    if spec.timeout:
        config.append(f"time_limit: {spec.timeout}")

    # Seccomp — optional, from Kafel policy file
    # config.append('seccomp_policy_file: "policies/{command}.policy"')

    return "\n".join(config)


def run_sandboxed(cmd: str, spec: SandboxSpec, workspace: Path, run_id: str) -> subprocess.CompletedProcess:
    """Execute a command inside an nsjail sandbox, capture resource usage."""
    config_path = f"/tmp/blq-nsjail-{run_id}.cfg"
    config = build_nsjail_config(cmd, spec, workspace)
    Path(config_path).write_text(config)

    # nsjail creates and manages cgroups automatically
    # Use --cgroup_mem_mount and --cgroup_pids_mount for cgroup v2
    result = subprocess.run(
        ["nsjail", "--config", config_path, "--cgroup_mem_mount", "/sys/fs/cgroup"],
        capture_output=True, text=True,
    )

    # Read resource usage from cgroup before nsjail cleans up
    # (may require patching nsjail or using --keep_env to delay cleanup)
    # See Phase 0 for the systemd-run alternative for resource profiling

    Path(config_path).unlink()
    return result
```

nsjail handles namespace creation, cgroup setup, resource enforcement, and cleanup in one tool. The `SandboxSpec` → nsjail config translation is the only glue code blq needs.

### Phase 4: Queryable sandbox events

Log sandbox spec and violations as structured events:

```sql
-- What sandbox specs are in use?
SELECT command, sandbox_network, sandbox_filesystem, sandbox_timeout,
       grade_w, effects_ceiling
FROM blq_commands
WHERE sandbox IS NOT NULL;

-- Were any bounds hit?
SELECT run_id, command, violation_type, violation_detail
FROM blq_sandbox_violations
ORDER BY timestamp DESC;

-- Grade distribution across all commands
SELECT effects_ceiling, count(*) as commands,
       count(*) FILTER (WHERE sandbox_network = 'none') as network_isolated
FROM blq_commands
GROUP BY effects_ceiling
ORDER BY effects_ceiling;
```

### Phase 5: MCP integration

Expose sandbox specs through MCP tools so agents can query their own execution environment:

```python
@server.tool()
def sandbox_info(command: str) -> str:
    """Show the sandbox specification for a registered command.
    Returns the bounds the command runs within."""
    spec = registry.get_sandbox_spec(command)
    return {
        "network": spec.network,
        "filesystem": spec.filesystem,
        "timeout": spec.timeout,
        "memory": spec.memory,
        "grade": {"w": spec.grade_w, "max_level": spec.effects_ceiling},
    }
```

This is the transparency principle from post 8 — project the constraints into the agent's scope so it can reason about what's possible instead of discovering limits empirically.

## Connection to the ratchet

The sandbox spec is a ratchet artifact for execution environments:

1. **Discovery**: Run commands without sandboxing. Observe actual resource usage (blq already captures timing; add resource metrics).
2. **Capture**: Aggregate resource usage across runs. What's the max memory? Max CPU? Does it access the network? Write to disk?
3. **Crystallize**: Define a sandbox spec that bounds the observed usage with margin. The spec is the crystallized understanding of what the command actually needs.
4. **Teach**: Document why each bound exists. "This test suite never exceeds 200MB, so the 512MB limit provides 2.5x headroom."
5. **Deploy**: Enforce the spec. If a future run exceeds the bounds, that's a signal — either the command changed or the bounds need updating.

The sandbox spec tightens over time as confidence grows. Start with `sandbox = "unrestricted"` (no bounds). After 100 successful runs that never exceed 300MB, tighten to `memory = "512m"`. After 1000 runs, tighten to `memory = "400m"`. Each tightening is a ratchet turn — the grade drops, the characterizability improves, the regulatory cost decreases.

## Priorities

1. **Monitoring mode** — resource profiling via cgroups (Phase 0, Tier 1). Log `memory.peak`, `cpu.stat`, and wall time for every run. No enforcement, no new dependencies. Ships first because it produces the data that informs everything else.
2. **Spec definition + logging** — add `SandboxSpec` to the command registry schema. Log the spec alongside every run. Still no enforcement — declaration and capture.
3. **nsjail enforcement** — translate `SandboxSpec` → nsjail config, enforce the full stack (namespaces, cgroups, seccomp). This replaces the earlier bwrap plan — nsjail handles the entire lifecycle.
4. **Presets** — named configurations for common patterns (test, build, lint, readonly).
5. **Profile command** — `blq profile <command>` runs strace-based access profiling (Phase 0, Tier 2). One-time operation that generates a suggested spec from observed behavior.
6. **MCP integration** — agents can query their sandbox via `sandbox_info()`. Transparency principle.
7. **Grade computation** — auto-compute `effects_ceiling` and `grade_w` from the spec. Queryable.
8. **Spec suggestion** — after N monitored runs, suggest a sandbox spec with headroom. The monitoring-to-enforcement ratchet.

## Extension framework note

Sandbox enforcement, execution environments, and execution platforms should be blq extensions, not core features. blq's core is capture and query. The execution path between command resolution and output capture is the extension point.

Three concerns, three potential extension packages:

| Concern | Question it answers | Example package |
|---|---|---|
| **Sandbox spec** | What effects are allowed? | `blq-sandbox` (nsjail + systemd-run) |
| **Execution environment** | What's available? (interpreters, deps, env vars, source scripts) | `blq-env` or per-platform |
| **Execution platform** | Where does it run? (local, Docker, SLURM, K8s) | `blq-docker`, `blq-slurm`, `blq-k8s` |

Each extension wraps the command execution. blq core captures output and resource profiles regardless of which extensions are active. The sandbox spec is platform-independent — `network = "none"` means the same thing whether enforced by nsjail (local), `--network=none` (Docker), or NetworkPolicy (K8s).

Platforms have different enforcement capabilities. An extension should declare what dimensions it can enforce, so blq can validate that a spec is fully enforceable on the selected platform — or warn about unenforced dimensions.

This is analogous to Flyte's task-level container/resource declarations and Bazel's execution platform constraints, but with the sandbox spec and Ma grading layered on top. The extension framework keeps blq from becoming an orchestration platform while allowing the ecosystem to grow.

## Prior art

The combination of declarative spec + enforcement + logging + queryable results has no direct prior art as a shipped product. The closest tools by dimension:

| What we need | Closest tool | Gap |
|---|---|---|
| Declarative sandbox config | **nsjail** (protobuf config) | No command registry, no audit query layer |
| Per-command profiles | **Firejail** (.profile files) | No structured logging, no SQL |
| Enforcement + logging | **systemd-run + journald** | No spec-per-run, not SQL-queryable |
| Audit + query | **AgentLens** (SQLite, tamper-evident) | No sandbox enforcement |
| Supply chain attestation | **SLSA provenance** | Records what happened, not what was allowed |
| Agent sandboxing | **Anthropic sandbox-runtime** | Limited dimensions, no query layer |
| ML workflow isolation | **Flyte** (K8s pod specs + provenance) | Per-task resources but K8s-level, not Linux-primitive |

Build systems (Bazel, Nix) sandbox per-action/per-derivation but don't log enforcement specs alongside results in a queryable store. CI systems (Tekton) allow per-step resource declarations via K8s pod specs but lack fine-grained Linux-primitive sandboxing.

The genuine novelty: spec-per-command in a named registry, logged alongside every run into the same DuckDB store as the output, queryable with SQL, and graded on the Ma lattice. The enforcement primitives are solved problems (nsjail). The contribution is making the spec a first-class queryable artifact.

## Addendum: Overhead analysis and integration design

### Overhead budget

The monitoring and enforcement stack adds overhead at three levels. The key constraint: monitoring must be cheap enough to run on every invocation; profiling can be expensive because it's one-time.

#### systemd-run --scope (cgroup accounting + enforcement)

cgroup v2 memory and CPU accounting are kernel counters that increment during normal page fault and scheduler operations. `MemoryAccounting=yes` makes the stats readable — it doesn't add new computation. Enforcement (`MemoryMax`, `CPUQuota`) adds a comparison on each allocation or scheduling decision.

| Component | Cost | When |
|---|---|---|
| Scope creation (D-Bus call to systemd) | ~5-10ms | Once per run, startup |
| cgroup directory creation | ~1ms | Once per run, startup |
| Memory accounting | ~0 | Piggybacks on existing page fault path |
| CPU accounting | ~0 | Piggybacks on existing scheduler tick |
| Enforcement checks | ~0 | Comparison against limit per allocation |
| Reading stats after exit | ~1ms | Once per run, after process exit |

**Total: ~10ms startup, negligible runtime.** The scope persists as long as blq's wrapper process is alive, so there is no race between process exit and stat collection — blq reads the cgroup files before exiting the scope.

#### nsjail (namespace + seccomp enforcement)

Namespace creation involves real kernel work. The costs scale with the isolation dimensions requested:

| Namespace | Cost | What happens |
|---|---|---|
| Mount (clone_newns) | ~1-5ms | Clones the mount table; scales with number of mounts (typical system: 30-100) |
| PID (clone_newpid) | ~0.1ms | New PID table |
| Network (clone_newnet) | ~1-2ms | New network stack |
| IPC (clone_newipc) | ~0.1ms | New IPC namespace |
| Bind mount setup | ~0.5ms per mount | One per entry in the nsjail config |
| seccomp BPF compilation | <1ms | Kernel JIT-compiles the Kafel policy (typical: 50-100 rules) |
| seccomp per-syscall check | ~50-100ns | BPF filter runs on every syscall after installation |

The per-syscall seccomp overhead is the only runtime cost. A Python process makes millions of syscalls during a test run — mostly `read`, `write`, `mmap`, `fstat`. At ~100ns each, a pytest run making 5M syscalls adds ~500ms. For a 5-second test run, that's ~10%. For a 60-second build, it's <1%.

**Total: ~10-20ms startup, ~1-5% runtime** depending on syscall volume.

#### strace (one-time profiling — `blq profile`)

strace uses `ptrace`, which interposes on every syscall with two context switches (entry + exit). Each traced syscall:

1. Target process stops (trap to kernel)
2. Context switch to strace process
3. strace reads syscall number + arguments
4. strace resumes target
5. Syscall executes normally
6. Target stops again (syscall exit)
7. strace reads return value
8. strace resumes target

Two full context switches per syscall. Python's import machinery alone generates thousands of `openat`/`fstat`/`mmap` calls.

**Total: 2-10x slowdown.** A 5-second test run becomes 10-50 seconds. This is acceptable for a one-time `blq profile test` but not for every-run monitoring.

#### Combined overhead for the normal execution path

The normal path (every `blq run` with monitoring + enforcement):

| Layer | Startup | Runtime | Total on 5s command |
|---|---|---|---|
| systemd-run scope | ~10ms | ~0 | ~10ms |
| nsjail (namespaces + seccomp) | ~15ms | ~1-5% | ~65-265ms |
| cgroup stat read at exit | ~1ms | — | ~1ms |
| **Total** | **~25ms** | **~1-5%** | **~75-275ms (~1.5-5.5%)** |

For a `blq profile test` run (one-time, adds strace):

| Layer | Total on 5s command |
|---|---|
| Normal path overhead | ~75-275ms |
| strace ptrace overhead | ~5-45s |
| **Total** | **~10-50s (2-10x)** |

The cost structure matches the workflow: monitoring is cheap enough to always run, profiling is expensive but one-time, enforcement adds near-nothing on top of monitoring.

### Integration with blq's execution path

#### Artifact storage

Three artifact types, three storage strategies:

| Artifact | Storage | Why |
|---|---|---|
| Resource profiles (cgroup stats) | DuckDB table (`resource_profiles`) | Small structured data, always queryable |
| strace profiles | Content-addressed blob (`.lq/blobs/`) | Large, same dedup/storage as stdout |
| nsjail logs | Content-addressed blob (`.lq/blobs/`) | Moderate size, queryable via `blq output` |
| Kafel policies | `.lq/policies/{command}.kafel` | Reusable across runs, version-controllable |
| nsjail configs | `.lq/sandbox/{command}.cfg` | Generated from SandboxSpec, regenerated on spec change |

Strace profiles and nsjail logs are stored through the existing blob pipeline — same content-addressed storage, same dedup via BLAKE3 hash, same `outputs` table with an `output_kind` discriminator. duck_hunt's strace parser can read them from blob storage.

#### Profiling file lifecycle

strace requires a file path at invocation time, but the content hash isn't known until the command finishes:

1. strace writes to `.lq/live/{attempt_id}/profile.strace` (alongside existing `combined` stdout file)
2. Command exits
3. blq reads the tempfile, hashes it (BLAKE3)
4. Stores through existing blob pipeline (dedup check → content-addressed path → `blob_registry`)
5. Links in `outputs` table with `output_kind = 'profile'`
6. Cleans up `.lq/live/{attempt_id}/`

Same lifecycle as stdout capture — live file during execution, blob after completion.

#### Execution flow with monitoring

Current path in `execution.py`:

```
write AttemptRecord → Popen(cmd) → stream output → wait → parse → write OutcomeRecord
```

With monitoring + enforcement:

```
write AttemptRecord
  → generate nsjail config from SandboxSpec  (if sandbox spec exists)
  → start systemd-run scope with cgroup accounting
    → start nsjail with namespace/seccomp enforcement  (if sandbox spec exists)
      → if profiling: wrap in strace -o .lq/live/{id}/profile.strace
      → Popen(cmd)
      → stream output to .lq/live/{id}/combined
      → wait for process exit
    ← nsjail exits
  ← scope still alive (blq wrapper is still in it)
  → read cgroup stats: memory.peak, cpu.stat
  ← exit scope (systemd cleans up cgroup)
  → parse stdout output
  → if profiling: hash + store strace blob, parse with duck_hunt
  → write OutcomeRecord + ResourceProfile + output blobs
```

The critical property: blq's wrapper process stays in the systemd scope after the command exits, keeping the cgroup alive for stat collection. No race condition.

#### Resource profiles schema

```sql
CREATE TABLE resource_profiles (
    attempt_id UUID REFERENCES attempts(id),
    memory_peak_bytes BIGINT,       -- from memory.peak
    memory_swap_peak_bytes BIGINT,  -- from memory.swap.peak (if swap enabled)
    cpu_usage_usec BIGINT,          -- from cpu.stat: usage_usec
    cpu_user_usec BIGINT,           -- from cpu.stat: user_usec
    cpu_system_usec BIGINT,         -- from cpu.stat: system_usec
    wall_time_ms INTEGER            -- measured by blq (already captured in outcomes)
);
```

#### Output kind extension

```sql
-- Extend outputs table to distinguish artifact types
-- output_kind: 'stdout' (default), 'stderr', 'profile', 'sandbox_log'
ALTER TABLE outputs ADD COLUMN output_kind TEXT DEFAULT 'stdout';
```

Queryable through existing `blq output` interface:

```
blq output run_id=42 kind=profile grep="connect"
blq output run_id=42 kind=sandbox_log grep="VIOLATION"
```

#### Monitoring-to-spec suggestion query

After accumulating resource profiles across runs:

```sql
-- Suggest sandbox spec from observed resource usage (2x headroom)
SELECT a.source_name as command,
       count(*) as runs,
       max(r.memory_peak_bytes) as observed_max_memory,
       max(r.memory_peak_bytes) * 2 as suggested_memory_limit,
       max(r.cpu_usage_usec) / 1e6 as observed_max_cpu_sec,
       max(r.cpu_usage_usec) / 1e6 * 2 as suggested_cpu_limit,
       max(r.wall_time_ms) / 1000 as observed_max_wall_sec,
       max(r.wall_time_ms) / 1000 * 3 as suggested_timeout
FROM resource_profiles r
JOIN attempts a ON r.attempt_id = a.id
JOIN outcomes o ON o.attempt_id = a.id
WHERE o.exit_code = 0  -- only successful runs
GROUP BY a.source_name
HAVING count(*) >= 10;
```

#### File layout

```
.lq/
├── commands.toml                    # command registry (+ sandbox specs)
├── config.toml                      # project config
├── blq.duckdb                       # DuckDB (attempts, outcomes, events,
│                                    #          outputs, resource_profiles)
├── blobs/content/{hash}.bin         # content-addressed storage
│                                    #   (stdout, strace profiles, nsjail logs)
├── policies/                        # seccomp policies (reusable)
│   ├── test.kafel
│   └── build.kafel
├── sandbox/                         # nsjail configs (generated from spec)
│   ├── test.cfg
│   └── build.cfg
└── live/{attempt_id}/               # transient, during execution
    ├── combined                     # stdout stream
    └── profile.strace               # strace output (if profiling)
```
