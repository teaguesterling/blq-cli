# blq MCP Tools - Agent Usage Guide

This guide explains how AI agents should use blq's MCP tools for build log capture and analysis. The blq tools provide significant advantages over raw Bash commands for build/test workflows.

## Why Use blq Tools Instead of Bash?

### 1. Structured Output vs Raw Text

**Bash approach:**
```
$ make build 2>&1
src/main.c:42:15: error: expected ';' before '}' token
src/utils.c:10:1: error: undefined reference to 'foo'
make: *** [Makefile:12: build] Error 1
```
You get raw text that requires manual parsing to understand.

**blq approach:**
```python
blq.run(command="build")
# Returns structured data:
{
  "status": "FAIL",
  "exit_code": 1,
  "summary": {"errors": 2, "warnings": 0},
  "errors": [
    {"ref": "build:1:1", "ref_file": "src/main.c", "ref_line": 42, "message": "expected ';'..."},
    {"ref": "build:1:2", "ref_file": "src/utils.c", "ref_line": 10, "message": "undefined reference..."}
  ]
}
```

### 2. Automatic Format Detection

blq uses duck_hunt to automatically detect and parse 60+ log formats:
- **GCC/Clang** - C/C++ compiler errors with column info
- **Rust/Cargo** - Error codes (E0425), warnings, notes
- **Python** - mypy, pytest, ruff, flake8
- **JavaScript/TypeScript** - Various linter formats
- **Go, Java, and many more**

No need to write custom regex or parsing logic.

### 3. Persistent History and Comparison

Every command execution is stored with full context:
- Git commit/branch at time of run
- Environment variables
- Hostname, platform, architecture
- Timestamps and duration

Compare runs to find regressions:
```python
blq.diff(run1=5, run2=6)  # What errors are new? What got fixed?
```

### 4. Drill-Down Capability

Start broad, then drill into specifics:
```python
blq.status()              # Overview of all sources
blq.errors(limit=10)      # Recent errors
blq.event(ref="build:3:2")  # Full details on specific error
blq.context(ref="build:3:2")  # Surrounding log lines
```

## Recommended Workflow

### Step 1: Register Your Commands

Instead of running ad-hoc commands, register them for reuse:

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

**Why register?**
- Cleaner refs: registered commands use the name as the tag (e.g., `"build:1:3"`),
  while ad-hoc commands use the full command string as the tag (ugly and long)
- Consistent naming across runs
- Automatic format detection based on command
- Can set timeouts and capture preferences

### Step 2: Run Commands

```python
# Registered command (preferred)
blq.run(command="build")

# Ad-hoc command (when exploring)
blq.exec(command="python setup.py check")
```

### Step 3: Analyze Results

```python
# Check status
blq.status()

# Get errors from latest run
blq.errors()

# Compare with previous run
blq.diff(run1=1, run2=2)

# Deep dive into specific error
blq.event(ref="build:2:1")
```

### Step 4: Iterate

After fixing issues:
```python
blq.run(command="build")
blq.diff(run1=2, run2=3)  # Did fixes work? Any regressions?
```

## Reference Format

blq uses a human-friendly reference scheme:

| Format | Example | Meaning |
|--------|---------|---------|
| `tag:serial` | `build:3` | Run #3 (globally), tagged "build" |
| `tag:serial:event` | `build:3:2` | Event #2 in run #3 |
| `serial:event` | `5:2` | Event #2 in run #5 (no tag) |

The **serial** is a global sequence number across all runs (1, 2, 3...).
The **tag** comes from the registered command name (or full command for ad-hoc runs).

Use these refs with `event()` and `context()` for drill-down.

## Tool Reference

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `run` | Run registered command | Building/testing with named commands |
| `exec` | Run ad-hoc command | One-off commands |
| `status` | Quick overview | Starting analysis, checking health |
| `history` | Run history | Finding past runs to compare |
| `errors` | Get errors | After failed build/test |
| `warnings` | Get warnings | Code quality analysis |
| `event` | Error details | Understanding specific issue |
| `context` | Surrounding lines | Seeing error in context |
| `diff` | Compare runs | Finding regressions |
| `query` | SQL queries | Advanced analysis |
| `register_command` | Save command | Setting up new project |
| `list_commands` | Show registered | Discovering available commands |
| `reset` | Clear data | Starting fresh |

## Best Practices

### Do:
- Register commands you'll run repeatedly
- Use `diff()` to verify fixes don't introduce regressions
- Drill down with `event()` and `context()` for unclear errors
- Check `status()` before and after changes

### Don't:
- Use Bash to run builds when blq tools are available
- Ignore warnings - they often indicate future errors
- Skip the diff step after fixes
- Manually parse build output when blq does it automatically

## Resetting State

If you need to start fresh:

```python
# Clear data only (keep commands)
blq.reset(mode="data", confirm=True)

# Recreate database schema
blq.reset(mode="schema", confirm=True)

# Full reinitialize (loses commands too)
blq.reset(mode="full", confirm=True)
```

## Example Session

```python
# 1. Check what commands are available
blq.list_commands()

# 2. Run the build
blq.run(command="build")
# Returns: status=FAIL, 3 errors

# 3. See the errors
blq.errors()
# Returns structured list with refs

# 4. Investigate first error
blq.event(ref="build:1:1")
# Returns: file, line, message, context

# 5. Fix the code...

# 6. Rebuild and compare
blq.run(command="build")
blq.diff(run1=1, run2=2)
# Shows: 2 fixed, 0 new - success!
```

## Summary

blq MCP tools transform build/test output from unstructured text into queryable, comparable data. This enables:

1. **Faster diagnosis** - Structured errors with file:line refs
2. **Regression detection** - Compare runs to catch new issues
3. **Historical context** - See what changed between failures
4. **Consistent workflow** - Same tools work across all projects

Use blq tools instead of Bash for any build, test, or lint command.
