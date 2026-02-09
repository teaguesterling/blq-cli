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
| `run(command)` | Run a registered command |
| `exec(command)` | Run an ad-hoc shell command |
| `status()` | Quick overview of all sources |
| `history(limit, source)` | Run history |
| `errors(limit, run_id, source)` | Get error events |
| `warnings(limit, run_id, source)` | Get warning events |
| `event(ref)` | Full details for one event |
| `context(ref, lines)` | Log lines around an event |
| `diff(run1, run2)` | Compare errors between runs |
| `query(sql, limit)` | Run SQL against the database |
| `register_command(name, cmd, ...)` | Register a new command |
| `unregister_command(name)` | Remove a command |
| `list_commands()` | List registered commands |
| `reset(mode, confirm)` | Clear data or reinitialize |

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

**Benefits of registration:**
- Clean refs (`build:1:3` vs the full command string)
- Automatic format detection based on command
- Reusable across sessions
- Visible to both agent and user

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

## Summary

blq provides structured access to build/test results that both humans and agents can query. Use the MCP tools to:

1. **Query existing results** - The user may have already run builds
2. **Run commands** - Execute registered build/test commands
3. **Drill down** - Get details on specific errors without needing source access
4. **Compare runs** - Detect regressions and verify fixes

Always prefer blq tools over Bash for build/test/lint operations.
