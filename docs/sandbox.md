# Sandbox Guide

blq can enforce execution boundaries on registered commands using Linux namespace isolation. This prevents commands from accessing the network, writing outside the workspace, or spawning unconstrained processes.

## Quick Start

```bash
# Register a command with a sandbox preset
blq commands register test "pytest" --sandbox test

# Or add sandbox to an existing command in .lq/commands.toml:
# [commands.test.sandbox]
# network = "none"
# filesystem = "readonly"

# Run — sandbox is automatically enforced
blq run test

# See what sandbox specs are in use
blq sandbox list
```

## How It Works

When a command has a sandbox spec, blq wraps it in [bubblewrap](https://github.com/containers/bubblewrap) (bwrap), which creates Linux namespaces to isolate the command:

| Dimension | What it controls | Enforcement |
|-----------|-----------------|-------------|
| `network` | Network access | `--unshare-net` (full isolation) |
| `filesystem` | File writes | `--ro-bind` / `--bind` mount strategy |
| `processes` | Process visibility | `--unshare-pid` |
| `tmpfs` | Scratch space | `--tmpfs` with `--size` limit |
| `timeout` | Wall-clock time | Subprocess timeout |
| `memory` | Peak memory | Cgroup limit (systemd engine) |
| `cpu` | CPU time | Cgroup limit (systemd engine) |

Safety flags `--die-with-parent` and `--new-session` are always applied.

## Presets

Named presets cover common use cases:

| Preset | network | filesystem | timeout | memory | processes |
|--------|---------|------------|---------|--------|-----------|
| `readonly` | none | readonly | 30s | 256m | isolated |
| `test` | none | readonly | 60s | 512m | isolated |
| `build` | none | workspace_only | 5m | 2g | isolated |
| `integration` | localhost | workspace_only | 10m | 4g | visible |
| `unrestricted` | unrestricted | unrestricted | 30m | - | visible |
| `none` | unrestricted | unrestricted | - | - | visible |

```bash
# Use a preset
blq commands register test "pytest" --sandbox test

# Or in commands.toml
[commands.test]
cmd = "pytest"
sandbox = "test"
```

## Custom Specs

For fine-grained control, use a `[commands.NAME.sandbox]` section:

```toml
[commands.test]
cmd = "pytest tests/"

[commands.test.sandbox]
network = "none"
filesystem = "readonly"
timeout = "120s"
memory = "1g"
processes = "isolated"
tmpfs = "200m"
paths_hidden = ["/home", "/root"]
```

## Grading

Each sandbox spec maps to a formal grade on the Ma framework's lattice:

**World coupling (grade_w):**
- `sealed` — no network, no reads beyond /usr
- `pinhole` — no network, readonly workspace
- `scoped` — no network, workspace writes only
- `broad` — localhost network access
- `open` — unrestricted

**Effects ceiling:**
- Level 2 — readonly, no network (can read + compute only)
- Level 4 — workspace writes, no network (can mutate files)
- Level 7 — workspace writes + visible processes (can spawn daemons)
- Level 8 — network access (can reach external services)

```bash
blq sandbox inspect test
# Grade W: pinhole
# Effects Ceiling: 2
```

## Discovery Workflow

The recommended workflow for adding sandbox specs to a project:

### 1. Profile

Discover what a command actually accesses:

```bash
blq sandbox profile test
```

This wraps the command in `strace` (one-time, 2-10x overhead) and reports files read/written, network connections, and subprocess spawns.

### 2. Suggest

Combine strace profile with observed resource metrics:

```bash
blq sandbox suggest test
```

This queries past run data for memory peak and CPU usage, then suggests a spec with headroom (2x memory, 3x timeout).

### 3. Declare

Add the suggested spec to `commands.toml`:

```toml
[commands.test.sandbox]
network = "none"
filesystem = "readonly"
timeout = "2m"
memory = "1g"
```

### 4. Enforce

Run normally — the sandbox is automatically applied:

```bash
blq run test
```

If the command fails due to sandbox restrictions, blq generates a structured info event with the sandbox context, queryable via `blq events`.

### 5. Tighten

After accumulating runs, auto-narrow the spec based on observed resource usage:

```bash
blq sandbox tighten test
# Tightening sandbox spec for 'test' (from 15 runs):
#   memory: 512m -> 256m
#   timeout: 1m -> 30s
#   cpu: 30s -> 15s
# Updated commands.toml
```

Use `--dry-run` to preview changes without writing:

```bash
blq sandbox tighten test --dry-run
```

Tightening only reduces bounds — it never loosens them. It applies headroom (2x memory, 2x CPU, 3x timeout) to observed maximums. Requires at least 3 runs for reliable data.

### 6. Query

Check sandbox status across all commands:

```bash
blq sandbox list          # overview of all specs and grades
blq sandbox inspect test  # detailed spec for one command
```

## Auto-Detection

When using `blq init --detect`, detected commands get default sandbox presets:

| Command type | Default sandbox |
|-------------|----------------|
| test | `test` (readonly, no network) |
| build | `build` (workspace writes, no network) |
| lint | `readonly` (readonly, no network) |
| clean | `build` (needs to delete files) |
| format | `build` (modifies source files) |

Commands without a matching type (e.g., `docker-build`, `configure`) get no sandbox by default.

## MCP Integration

AI agents can query and manage sandbox specs:

```json
// Query sandbox info for one command
{"tool": "sandbox_info", "command": "test"}

// Query all commands
{"tool": "sandbox_info"}

// Register with sandbox preset
{"tool": "register_command", "name": "test", "cmd": "pytest", "sandbox": "test"}
```

The `sandbox_info` tool returns the spec, grades, and observed resource metrics (memory peak, CPU usage, average duration) when monitoring data is available.

## Annotators

Annotators are plugins that enrich stored events with additional context. They run after events are written to the database and add structured annotations to the `metadata` JSON column.

Each annotation has:
- **type** — what kind of enrichment (source, provenance, diagnostic)
- **display** — when to show it: `inline` (always), `detail` (inspect only), `hidden` (queryable only)
- **data** — annotator-specific payload

Annotators declare whether they're **eager** (run during `blq run`) or **deferred** (run on demand). Eager annotators execute in Window 2 alongside event storage. Deferred annotators run when explicitly requested.

Annotators are discovered via Python entry points (`blq.annotators` group).

## Requirements

- **bwrap** (bubblewrap) for namespace isolation: `sudo apt install bubblewrap`
- **strace** for profiling (optional): `sudo apt install strace`
- **systemd** for cgroup resource limits (optional, for memory/CPU enforcement)

## Engines

blq uses multiple enforcement engines that compose together:

| Engine | Dimensions | Install |
|--------|-----------|---------|
| bwrap | network, filesystem, processes, tmpfs | `apt install bubblewrap` |
| systemd | memory, cpu | Built-in on systemd systems |

Engines are discovered via Python entry points and selected based on which spec dimensions need enforcement.
