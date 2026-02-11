# blq MCP Tools - Agent Usage Guide

blq captures, stores, and queries build/test logs. Both humans (via CLI) and agents (via MCP) share the same database, enabling collaborative debugging workflows.

**Documentation:** https://blq-cli.readthedocs.io/en/latest/

## Key Concept: Shared State

The blq database (`.lq/blq.duckdb`) is shared between CLI and MCP:

```
Human runs:     blq run build          → stored in .lq/
Agent queries:  blq.errors()           → reads from .lq/
Agent runs:     blq.run(command="test") → stored in .lq/
Human queries:  blq errors             → reads from .lq/
```

This means:
- The user can run `blq run build` in their terminal, then ask the agent to analyze the errors
- The agent can run tests and the user can review results with `blq errors` or `blq history`
- Both see the same run history, diffs, and error references

## Getting Started

### Check if blq is initialized

```python
blq.list_commands()
```

If this returns commands, you're ready. If it returns an empty list or error, the project may need initialization (user should run `blq init`).

### Discover available commands

```python
blq.list_commands()
# Returns: [{"name": "build", "cmd": "make -j8"}, {"name": "test", "cmd": "pytest"}]
```

### Check current status

```python
blq.status()
# Returns status of each registered command's last run
```

## Why Use blq Tools Instead of Bash?

### 1. Structured Output

**Bash gives you raw text:**
```
src/main.c:42:15: error: expected ';' before '}' token
```

**blq gives you structured data:**
```python
{
  "ref": "build:1:1",
  "ref_file": "src/main.c",
  "ref_line": 42,
  "ref_column": 15,
  "message": "expected ';' before '}' token",
  "severity": "error"
}
```

### 2. Automatic Format Detection

blq parses 60+ log formats automatically:
- **C/C++**: GCC, Clang, MSVC
- **Rust**: cargo, rustc with error codes
- **Python**: pytest, mypy, ruff, flake8, pylint
- **JavaScript**: ESLint, TypeScript
- **Go, Java, Ruby, and more**

### 3. History and Comparison

Every run is stored with git context (commit, branch, dirty state). Compare runs to find regressions:

```python
blq.diff(run1=5, run2=6)
# Returns: {"fixed": [...], "new": [...], "unchanged": [...]}
```

### 4. No Source Code Required

You may not have access to the project's source files. blq stores:
- Error locations (file:line:column)
- Error messages and codes
- Log context around errors
- Git metadata

Use `event()` and `context()` to understand errors without reading source files directly.

## Recommended Workflow

### Step 1: Check Status

```python
blq.status()        # Overview of all sources
blq.list_commands() # What commands are registered
```

### Step 2: Run or Analyze

If you need fresh results:
```python
blq.run(command="build")  # Run registered command
blq.run(command="test")
```

Or analyze existing results (from user's CLI runs):
```python
blq.errors()        # Recent errors
blq.history()       # Past runs
```

### Step 3: Drill Down

```python
blq.errors(limit=10)           # Get error list
blq.event(ref="build:3:2")     # Full details on one error
blq.context(ref="build:3:2")   # Surrounding log lines
blq.output(run_id=3, tail=50)  # Raw output if parsing missed something
```

### Step 4: Compare (After Fixes)

```python
blq.diff(run1=3, run2=4)  # What changed between runs?
```

## Reference Format

| Format | Example | Meaning |
|--------|---------|---------|
| `tag:serial` | `build:3` | Run #3 (globally), tagged "build" |
| `tag:serial:event` | `build:3:2` | Event #2 in run #3 |
| `serial:event` | `5:2` | Event #2 in run #5 (no tag) |

- **serial**: Global sequence number across all runs (1, 2, 3...)
- **tag**: From registered command name (e.g., "build", "test")

## Tool Reference

| Tool | Purpose |
|------|---------|
| `run(command, args)` | Run a registered command (args for templates) |
| `exec(command)` | Run an ad-hoc shell command |
| `status()` | Quick overview of all sources |
| `history(limit, source)` | Run history |
| `errors(limit, run_id, source)` | Get error events |
| `warnings(limit, run_id, source)` | Get warning events |
| `event(ref)` | Full details for one event |
| `context(ref, lines)` | Log lines around an event |
| `output(run_id, stream, tail, head)` | Raw stdout/stderr for a run |
| `diff(run1, run2)` | Compare errors between runs |
| `query(sql, limit)` | Run SQL against the database |
| `register_command(name, cmd, run_now)` | Register and optionally run a command |
| `unregister_command(name)` | Remove a command |
| `list_commands()` | List registered commands |
| `reset(mode, confirm)` | Clear data or reinitialize |
| `batch_run(commands)` | Run multiple commands in sequence |
| `batch_errors(run_ids)` | Get errors from multiple runs |
| `batch_event(refs)` | Get details for multiple events |

## Registering Commands

If a project doesn't have commands registered, help the user set them up:

```python
blq.register_command(
    name="build",
    cmd="make -j8",
    description="Build the project"
)

blq.register_command(
    name="test",
    cmd="pytest tests/ -v",
    description="Run test suite"
)
```

**Important: Don't specify timeouts** unless you have a specific reason. Commands run without timeout by default, which is correct for most build/test tasks. Specifying a timeout often causes problems when builds take longer than expected.

### Parameterized Commands

Commands can be templates with `{param}` placeholders. Use `tpl` instead of `cmd`, with `defaults` for optional parameters:

```toml
# In .lq/commands.toml
[commands.test]
tpl = "pytest {path} {flags}"
defaults = { path = "tests/", flags = "-v" }
description = "Run tests"

[commands.test-file]
tpl = "pytest {file} -v --tb=short"
description = "Test a single file"
# No defaults = 'file' is required
```

Run parameterized commands with the `args` parameter:

```python
# Use defaults
blq.run(command="test")
# → pytest tests/ -v

# Override path
blq.run(command="test", args={"path": "tests/unit/"})
# → pytest tests/unit/ -v

# Override both
blq.run(command="test", args={"path": "tests/unit/", "flags": "-vvs -x"})
# → pytest tests/unit/ -vvs -x

# Required parameter
blq.run(command="test-file", args={"file": "tests/test_core.py"})
# → pytest tests/test_core.py -v --tb=short
```

Missing required parameters will raise an error with a helpful message.

### Idempotent Registration

`register_command` is idempotent - calling it multiple times is safe:

```python
# First call: registers the command
blq.register_command(name="build", cmd="make -j8")

# Second call: detects identical command, returns existing (no error)
blq.register_command(name="build", cmd="make -j8")

# Different name, same command: returns existing command
blq.register_command(name="compile", cmd="make -j8")
# → Uses existing 'build' command instead
```

### Register and Run

Use `run_now=True` to register and immediately run:

```python
# Register (if needed) and run in one call
blq.register_command(
    name="test",
    cmd="pytest tests/ -v",
    run_now=True
)
```

This is the recommended pattern for agents - it ensures clean refs while being efficient.

**Benefits of registration:**
- Clean refs (`build:1:3` vs the full command string)
- Automatic format detection based on command
- Reusable across sessions
- Visible to both agent and user
- Idempotent - safe to call multiple times

## Best Practices

### Do:
- Start with `status()` or `list_commands()` to understand current state
- Use `diff()` after fixes to verify no regressions
- Drill down with `event()` and `context()` for unclear errors
- Register commands the user will run repeatedly

### Don't:
- Use Bash to run builds when blq tools are available
- Assume you can read source files - use blq's stored error context
- Skip checking existing results - the user may have already run the build
- Specify timeouts when registering commands - let builds run to completion

## Resetting State

```python
blq.reset(mode="data", confirm=True)    # Clear runs, keep commands
blq.reset(mode="schema", confirm=True)  # Recreate database
blq.reset(mode="full", confirm=True)    # Full reinitialize
```

## Example: Collaborative Debugging Session

```python
# User ran: blq run build (from terminal)
# Agent is asked to help with the errors

# 1. See what happened
blq.status()
# → build: FAIL, 3 errors

# 2. Get the errors
blq.errors()
# → [{"ref": "build:5:1", "ref_file": "src/main.c", ...}, ...]

# 3. Understand the first error
blq.event(ref="build:5:1")
# → Full error details including message, code, context

# 4. See surrounding log context
blq.context(ref="build:5:1")
# → Lines before and after the error

# 5. After user fixes the code, they run: blq run build
# Agent verifies the fix:
blq.diff(run1=5, run2=6)
# → {"fixed": 3, "new": 0} - Success!
```

## Hook Scripts

blq can generate portable shell scripts for registered commands that work with or without blq installed.

### Generating Hooks

```bash
# Generate hook scripts (CLI)
blq hooks generate lint test

# Creates:
# .lq/hooks/lint.sh
# .lq/hooks/test.sh
```

### Hook Script Features

Generated scripts support:
- `--via=blq|standalone|auto` - Use blq for capture, or run command directly
- `--metadata=auto|none|footer` - Output metadata for CI log parsing
- `--dry-run` - Show command without executing
- `key=value` params for template commands

```bash
# Run with blq (captures logs)
.lq/hooks/test.sh --via=blq

# Run standalone (no blq needed)
.lq/hooks/test.sh --via=standalone

# Auto mode (default): uses blq if available
.lq/hooks/test.sh

# Override template parameters
.lq/hooks/test.sh path=tests/unit/
```

### CI Integration

When running standalone in CI, scripts can output metadata for later import:

```bash
.lq/hooks/test.sh --via=standalone --metadata=footer
# Output ends with:
# blq:meta {"command":"test","exit_code":0,"git_sha":"abc123",...}
```

### Installing to Git Hooks

```bash
# Install to .git/hooks/pre-commit
blq hooks install git lint format-check

# Install to different hook
blq hooks install git test --hook=pre-push
```

### Installing to CI Workflows

```bash
# GitHub Actions: .github/workflows/blq.yml
blq hooks install github lint test

# GitLab CI: .gitlab-ci.blq.yml (include in your .gitlab-ci.yml)
blq hooks install gitlab lint test

# Drone CI: .drone.blq.yml
blq hooks install drone lint test
```

### Checking Hook Status

```bash
blq hooks status
# Shows:
# - Generated hook scripts and their status (ok/stale/orphan)
# - Git hook installations (pre-commit, pre-push)
# - CI workflow installations (github, gitlab, drone)
```

### Uninstalling Hooks

```bash
blq hooks uninstall git              # Remove git pre-commit hook
blq hooks uninstall git --hook=pre-push
blq hooks uninstall github           # Remove GitHub workflow
blq hooks uninstall gitlab           # Remove GitLab CI config
blq hooks uninstall drone            # Remove Drone CI config
```

## MCP Resources

In addition to tools, blq provides read-only resources:

| Resource | Description |
|----------|-------------|
| `blq://guide` | This guide |
| `blq://status` | Current status summary (JSON) |
| `blq://errors` | Recent errors (JSON) |
| `blq://errors/{serial}` | Errors for a specific run |
| `blq://warnings` | Recent warnings (JSON) |
| `blq://warnings/{serial}` | Warnings for a specific run |
| `blq://context/{ref}` | Log context around an event |
| `blq://commands` | Registered commands (JSON) |

Resources are useful for embedding data in prompts or quick reads without calling tools.

## Claude Code Integration

If blq's Claude Code hooks are installed (via `blq hooks install claude-code`), the agent will receive suggestions when Bash commands match registered blq commands:

```
Tip: Use blq MCP tool run(command="test") instead.
Using the blq MCP run tool parses output into structured events,
reducing context usage. Query errors with events() or inspect().
```

This helps guide agents toward using blq's structured tools instead of raw Bash output.

## Summary

blq provides structured access to build/test results that both humans and agents can query. Use the MCP tools to:

1. **Query existing results** - The user may have already run builds
2. **Run commands** - Execute registered build/test commands
3. **Drill down** - Get details on specific errors without needing source access
4. **Compare runs** - Detect regressions and verify fixes

Always prefer blq tools over Bash for build/test/lint operations.
