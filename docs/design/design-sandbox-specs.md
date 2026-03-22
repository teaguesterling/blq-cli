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

### Computation level (D axis derivative)

The computation level depends on what the command CAN do, which is bounded by the sandbox:

| What the sandbox allows | Max reachable level | Why |
|---|---|---|
| readonly, no network | Level 2 (read + compute) | Can read and process but can't mutate or reach out |
| workspace write, no network | Level 3 (mutation) | Can modify files, future reads see changes |
| workspace write, localhost | Level 5+ (environment modification, service interaction) | Can install packages, talk to local services |
| unrestricted | Level 7+ (subprocess spawning, network, persistence) | Unbounded — can spawn processes, open ports, exfiltrate |

### Grading our experiment conditions

This is where it gets precise. Our experimental conditions, graded by their actual sandbox specs:

```
Condition I (file_edit + run_tests):
  file_edit:  (scoped, level 0)    — structured write to workspace
  run_tests:  (scoped-ro, level 2*) — executes code but read-only + no network
  Composite:  (scoped, level 2*)    — the join
  * level 2 because effects are bounded to read + compute, even though
    the computation is Turing-complete. The sandbox constrains the level.

Condition D (bash_sandboxed):
  bash:       (scoped-rw, level 4+) — read-write workspace, no network
  But actually: can the agent write a script that spawns a background process?
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
    def max_computation_level(self) -> int:
        """Compute maximum reachable computation level from spec."""
        if self.processes == "visible" and self.network != "none":
            return 8  # can modify controller, reach external services
        if self.processes == "visible":
            return 7  # can spawn persistent subprocesses
        if self.filesystem not in ("readonly",):
            return 4  # can write + execute (computation amplification)
        return 2      # read + compute only
```

### Phase 2: Enforcement via bwrap

When `blq run` executes a command with a sandbox spec, wrap it in bwrap:

```python
def build_bwrap_command(cmd: str, spec: SandboxSpec, workspace: Path) -> list[str]:
    args = ["bwrap"]

    # Filesystem
    if spec.filesystem == "readonly":
        args.extend(["--ro-bind", str(workspace), str(workspace)])
    elif spec.filesystem == "workspace_only":
        args.extend(["--bind", str(workspace), str(workspace)])

    # System libraries (always read-only)
    for sys_path in ["/usr", "/bin", "/lib", "/lib64"]:
        args.extend(["--ro-bind", sys_path, sys_path])

    # Selective /etc mounting
    for etc_file in ["resolv.conf", "alternatives", "ld.so.cache", "ssl"]:
        etc_path = f"/etc/{etc_file}"
        if Path(etc_path).exists():
            args.extend(["--ro-bind", etc_path, etc_path])

    # Hidden paths (explicitly not mounted)
    # paths_hidden items are simply not bind-mounted — invisible by default

    # Network
    if spec.network == "none":
        args.append("--unshare-net")

    # Process isolation
    if spec.processes == "isolated":
        args.append("--unshare-pid")

    # Tmpfs
    if spec.tmpfs:
        args.extend(["--tmpfs", "/tmp", f"--size={spec.tmpfs}"])
    else:
        args.extend(["--tmpfs", "/tmp"])

    args.extend(["--proc", "/proc", "--dev", "/dev", "--new-session"])
    args.extend(["--chdir", str(workspace)])
    args.extend(["bash", "-c", cmd])

    return args
```

### Phase 3: Enforcement via cgroups

For CPU and memory limits, create a cgroup per run:

```python
def setup_cgroup(run_id: str, spec: SandboxSpec) -> str:
    cgroup_path = f"/sys/fs/cgroup/blq-{run_id}"
    os.makedirs(cgroup_path, exist_ok=True)

    if spec.memory:
        with open(f"{cgroup_path}/memory.max", "w") as f:
            f.write(str(spec.memory))

    if spec.cpu:
        # cpu.max format: "quota period" in microseconds
        # For 30 CPU-seconds over 60 wall-seconds: quota=500000, period=1000000
        with open(f"{cgroup_path}/cpu.max", "w") as f:
            f.write(f"{spec.cpu * 1000000 // 60} 1000000")

    return cgroup_path
```

### Phase 4: Queryable sandbox events

Log sandbox spec and violations as structured events:

```sql
-- What sandbox specs are in use?
SELECT command, sandbox_network, sandbox_filesystem, sandbox_timeout,
       grade_w, max_computation_level
FROM blq_commands
WHERE sandbox IS NOT NULL;

-- Were any bounds hit?
SELECT run_id, command, violation_type, violation_detail
FROM blq_sandbox_violations
ORDER BY timestamp DESC;

-- Grade distribution across all commands
SELECT max_computation_level, count(*) as commands,
       count(*) FILTER (WHERE sandbox_network = 'none') as network_isolated
FROM blq_commands
GROUP BY max_computation_level
ORDER BY max_computation_level;
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
        "grade": {"w": spec.grade_w, "max_level": spec.max_computation_level},
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

1. **Spec definition + logging** — no enforcement, just declaration. Ships first because it's useful for audit and documentation even without enforcement.
2. **bwrap enforcement** — network isolation, filesystem bounds, process isolation. The highest-value security bounds.
3. **cgroup enforcement** — CPU and memory limits. Important for resource exhaustion prevention.
4. **Presets** — named configurations for common patterns (test, build, lint).
5. **MCP integration** — agents can query their sandbox. Transparency principle.
6. **Grade computation** — auto-compute the Ma grade from the spec. Queryable.
7. **Ratchet integration** — observe resource usage, suggest spec tightening. The sandbox ratchet.
