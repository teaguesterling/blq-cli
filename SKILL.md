# blq MCP Tools - Agent Usage Guide

blq captures, stores, and queries build/test logs. Both humans (via CLI) and agents (via MCP) share the same database, enabling collaborative debugging workflows.

**Documentation:** https://blq-cli.readthedocs.io/en/latest/

## Key Concept: Shared State

The blq database (`.lq/blq.duckdb`) is shared between CLI and MCP:

```
Human runs:     blq run build           → stored in .lq/
Agent queries:  blq.events(...)         → reads from .lq/
Agent runs:     blq.run(command="test") → stored in .lq/
Human queries:  blq errors              → reads from .lq/
```

This means:
- The user can run `blq run build` in their terminal, then ask the agent to analyze the errors
- The agent can run tests and the user can review results with `blq errors` or `blq history`
- Both see the same run history, diffs, and error references

## Getting Started

### Check if blq is initialized

```python
blq.commands()
```

If this returns commands, you're ready. If it returns an empty list or error, the project may need initialization (user should run `blq init`).

### Discover available commands

```python
blq.commands()
# Returns: {"commands": [{"name": "build", "cmd": "make -j8"}, {"name": "test", "tpl": "pytest {path}", "defaults": {"path": "tests/"}}]}
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
  "severity": "error",
  "fingerprint": "gcc_error_a1b2c3"
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

Use `inspect()` to understand errors without reading source files directly.

## Recommended Workflow

### Step 1: Check Status

```python
blq.status()    # Overview of all sources
blq.commands()  # What commands are registered
```

### Step 2: Run or Analyze

If you need fresh results:
```python
blq.run(command="build")  # Run registered command
blq.run(command="test")
```

Or analyze existing results (from user's CLI runs):
```python
blq.events(severity="error")  # Recent errors
blq.history()                  # Past runs
```

### Step 3: Drill Down

```python
# Quick view with log context around each error
blq.info(ref="build:3", context=5)
# Returns compact format:
# {
#   "run_ref": "build:3",
#   "status": "FAIL",
#   "error_count": 2,
#   "errors_by_category": {"lint": 1, "test": 1},
#   "events": [
#     {"ref": "3:1", "location": "src/main.py:42", "context": "...>>> 42 | error line..."},
#     {"ref": "3:2", "location": "tests/test_main.py:15", "context": "..."}
#   ]
# }

# Or get full event details for deeper investigation
blq.events(severity="error", limit=10)  # Get error list with all fields
blq.inspect(ref="build:3:2")            # Full details with log + source context
blq.output(run_id=3, tail=50)           # Raw output if parsing missed something
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

### Core Tools

| Tool | Purpose |
|------|---------|
| `run(command, ...)` | Run a registered command (supports batch mode with `commands` param) |
| `status()` | Quick overview of all sources |
| `commands()` | List registered commands |
| `info(ref, context)` | Detailed info for a run (omit `ref` for most recent) |
| `history(limit, source)` | Run history |

#### The `run` Tool Output

The `run` tool returns a concise response optimized for token efficiency:

```python
blq.run(command="test")
# Returns:
{
  "run_ref": "test:47",
  "cmd": "pytest tests/ -v",
  "status": "FAIL",
  "exit_code": 1,
  "summary": {"error_count": 2, "warning_count": 5},
  "errors": [...],     # Only if errors exist
  "preview": [...]     # Head + tail of output on failure (see below)
}
```

**Preview behavior:**
- **Failed**: Shows first 3 + last 3 lines with separator (head and tail of output)
- **Short output**: Shows all lines when total <= 7
- **Success**: No preview included
- Use `output(run_id=N)` to see the full log

#### The `info` Tool with `context=N`

When you pass `context=N` to `info`, you get a compact event format optimized for quick understanding:

```python
blq.info(ref="test:47", context=3)
# Returns:
{
  "run_ref": "test:47",
  "status": "FAIL",
  "error_count": 2,
  "errors_by_category": {"test": 2},
  "events": [
    {
      "ref": "47:242",                        # Short format (no tag prefix)
      "location": "tests/test_main.py:251",   # Combined file:line
      "context": "     248 | ...PASSED...\n>>>  251 | ...FAILED...\n     252 | ..."
    }
  ],
  "summary": {
    "by_fingerprint": [
      {"fingerprint": "abc123", "count": 2, "example_message": "AssertionError"}
    ],
    "by_file": [
      {"file": "tests/test_main.py", "count": 2}
    ],
    "affected_commits": [
      {"hash": "abc1234", "author": "alice@example.com", "message": "Refactor tests"}
    ]
  }
}
```

For failed runs, `info` includes an aggregated `summary` with:
- `by_fingerprint`: Error counts grouped by fingerprint (for deduplication)
- `by_file`: Error counts grouped by file
- `affected_commits`: Recent git commits that touched files with errors

This is the recommended starting point - you can see errors with their surrounding log context in one call. Use `inspect(ref)` only when you need additional details like source code context or error codes.

### Event Tools

| Tool | Purpose |
|------|---------|
| `events(severity, limit, run_id, ...)` | Get events (use `severity="error"` for errors, `severity="warning"` for warnings) |
| `inspect(ref, lines, ...)` | Full details with log/source context and optional enrichment (git, fingerprint) |
| `output(run_id, stream, tail, head, grep, context, lines, debug_formats)` | Raw stdout/stderr with search and filtering |
| `diff(run1, run2)` | Compare errors between runs |
| `query(sql, filter, limit)` | Query with SQL or filter expressions (e.g., `filter="severity=error"`) |

#### The `output` Tool

The `output` tool retrieves raw build output with optional search and filtering:

```python
# Basic usage
blq.output(run_id=3)                    # Full output
blq.output(run_id=3, tail=50)           # Last 50 lines
blq.output(run_id=3, head=20)           # First 20 lines
blq.output(run_id=3, stream="stderr")   # Only stderr

# Search with grep
blq.output(run_id=3, grep="error|warning")           # Find matches
blq.output(run_id=3, grep="FAIL", context=3)         # With 3 lines context
blq.output(run_id=3, grep="undefined", context=5)    # Find undefined refs

# Line selection (requires read_lines extension)
blq.output(run_id=3, lines="100-200")   # Lines 100-200
blq.output(run_id=3, lines="42 +/-5")   # Lines 37-47 (around line 42)

# Format debugging (shows which parsers were tried)
blq.output(run_id=3, debug_formats=True)
```

| Parameter | Description |
|-----------|-------------|
| `run_id` | Run serial number or ref (e.g., `3` or `"build:3"`) |
| `stream` | Filter by stream: `"stdout"`, `"stderr"`, or `"combined"` (default) |
| `tail` | Show only last N lines |
| `head` | Show only first N lines |
| `grep` | Regex pattern to search for in output |
| `context` | Lines of context around grep matches (default: 0) |
| `lines` | Line spec like `"100-200"` or `"42 +/-5"` (requires read_lines) |
| `debug_formats` | Show format detection diagnosis (which parsers matched)

#### Filter Syntax for `query`

The `filter` parameter supports simple expressions:
- `key=value` - exact match (`severity=error`)
- `key=v1,v2` - multiple values (`severity=error,warning`)
- `key~pattern` - contains (`ref_file~test`)
- `key!=value` - not equal (`tool_name!=mypy`)

Multiple filters are AND'd together (space or comma separated):
```python
blq.query(filter="severity=error ref_file~test")  # Errors in test files
blq.query(filter="tool_name=pytest category=test")  # pytest test failures
```

#### Event Enrichment with `inspect`

The `inspect` tool supports optional enrichment to provide deeper context:

| Parameter | Description |
|-----------|-------------|
| `include_source_context` | Source file lines around error location (default: true) |
| `include_git_context` | Git blame and recent commits for the file |
| `include_fingerprint_history` | Error occurrence history and regression detection |

```python
# Basic inspect (log + source context)
blq.inspect(ref="build:1:3")

# With git context (who last modified, recent commits)
blq.inspect(ref="build:1:3", include_git_context=True)

# With fingerprint history (is this error new or recurring?)
blq.inspect(ref="build:1:3", include_fingerprint_history=True)

# Full enrichment
blq.inspect(
    ref="build:1:3",
    include_source_context=True,
    include_git_context=True,
    include_fingerprint_history=True
)

# Batch mode with enrichment
blq.inspect(
    ref="build:1:1",
    refs=["build:1:1", "build:1:2", "build:1:3"],
    include_git_context=True
)
```

**Git context** shows who last modified the error location and recent file changes:
```json
{
  "git_context": {
    "file": "src/main.py",
    "line": 42,
    "blame": {"author": "alice@example.com", "commit": "abc1234"},
    "recent_commits": [{"hash": "abc1234", "message": "Refactor data processing"}]
  }
}
```

**Fingerprint history** tracks error occurrences and detects regressions:
```json
{
  "fingerprint_history": {
    "fingerprint": "7f3a2b1c4d5e...",
    "first_seen": {"run_ref": "build:1"},
    "occurrences": 4,
    "is_regression": true
  }
}
```

### Command Management

| Tool | Purpose |
|------|---------|
| `register_command(name, cmd, run_now)` | Register and optionally run a command |
| `unregister_command(name)` | Remove a command |
| `clean(mode, confirm, days)` | Database cleanup (data, prune, schema, full) |

### Batch Mode

Several tools support batch operations via additional parameters:

```python
# Run multiple commands in sequence
blq.run(command="ignored", commands=["build", "test"])

# Get events from multiple runs
blq.events(run_ids=[1, 2, 3], severity="error")

# Inspect multiple events at once
blq.inspect(ref="build:1:1", refs=["build:1:1", "build:1:2", "build:1:3"])
```

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

### Idempotent Registration

`register_command` is idempotent for same name + same command:

```python
# First call: registers the command
blq.register_command(name="build", cmd="make -j8")
# → Registers, auto-detects format (e.g., "gcc")

# Second call: detects identical command, returns existing (no error)
blq.register_command(name="build", cmd="make -j8")
# → Returns existing command

# Different name, same command: returns error (use force=True to override)
blq.register_command(name="compile", cmd="make -j8")
# → Error: "Command already registered as 'build'"
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

### Template Commands

Some commands use templates with `{param}` placeholders instead of fixed commands. These appear in `commands()` output with `tpl` instead of `cmd`:

```python
blq.commands()
# Returns:
{
  "commands": [
    {"name": "build", "cmd": "make -j8"},
    {"name": "test", "tpl": "pytest {path} {flags}", "defaults": {"path": "tests/", "flags": "-v"}}
  ]
}
```

**Running template commands:**

Use the `args` parameter to provide values for template placeholders:

```python
# Run with defaults (pytest tests/ -v)
blq.run(command="test")

# Override a parameter
blq.run(command="test", args={"path": "tests/unit/"})
# → pytest tests/unit/ -v

# Override multiple parameters
blq.run(command="test", args={"path": "tests/integration/", "flags": "-vvs --tb=short"})
# → pytest tests/integration/ -vvs --tb=short
```

**Required parameters:**

If a template has parameters without defaults, they must be provided:

```python
# Given: {"name": "test-file", "tpl": "pytest {file} -v"}
blq.run(command="test-file")
# → Error: Missing required param 'file'

blq.run(command="test-file", args={"file": "test_main.py"})
# → pytest test_main.py -v
```

**Registering template commands:**

Use `tpl` instead of `cmd`, and provide `defaults` for optional parameters:

```python
blq.register_command(
    name="test",
    tpl="pytest {path} {flags}",
    defaults={"path": "tests/", "flags": "-v"},
    description="Run tests"
)
```

Or via CLI:
```bash
blq commands register test --tpl "pytest {path} {flags}" --defaults path=tests/ --defaults flags=-v
```

Or by editing `.lq/commands.toml`:
```toml
[commands.test]
tpl = "pytest {path} {flags}"
defaults = { path = "tests/", flags = "-v" }
description = "Run tests"
```

## Filtering and Searching Output

**Do NOT use shell pipes, redirects, or command chains** in run or exec commands.
Commands like `exec(command="pytest tests/ | tail -20")` will be rejected because
commands are executed directly, not through a shell.

### Correct Workflow: Run Then Filter

```python
# Step 1: Run the command
result = blq.run(command="test")

# Step 2: Filter the captured output using output()
blq.output(run_id=47, tail=20)                      # Last 20 lines
blq.output(run_id=47, head=10)                       # First 10 lines
blq.output(run_id=47, grep="FAILED", context=3)      # Search with context
blq.output(run_id=47, grep="error|warning")           # Regex search
blq.output(run_id=47, lines="100-200")               # Specific line range
```

### Why This Works Better

- **Persistent**: Output is stored — you can search it multiple times without re-running
- **Structured**: The `output()` tool handles grep, context lines, and line selection natively
- **Token-efficient**: Request only the lines you need instead of piping full output

### Shell Escape Hatch

For advanced use cases that genuinely need shell interpretation:

```python
blq.exec(command="echo hello && echo world", shell=True)
```

This bypasses pipe detection and passes the command to a shell. Prefer the
two-step workflow above unless you have a specific reason to use shell syntax.

## Best Practices

### Do:
- Start with `status()` or `commands()` to understand current state
- Use `info(context=5)` to see errors with surrounding log context in one call
- Use `diff()` after fixes to verify no regressions
- Use `inspect()` only when you need additional details (source context, error codes)
- Register commands the user will run repeatedly
- Use `output()` with grep/tail/head to filter captured logs

### Don't:
- Use Bash to run builds when blq tools are available
- Use shell pipes or redirects in run/exec commands (`| tail`, `| grep`, `> file`)
- Assume you can read source files - use blq's stored error context
- Skip checking existing results - the user may have already run the build
- Call `events()` then `inspect()` for each error - use `info(context=N)` instead

## Cleaning Up

```python
blq.clean(mode="data", confirm=True)              # Clear runs, keep commands
blq.clean(mode="prune", days=30, confirm=True)    # Remove data older than 30 days
blq.clean(mode="schema", confirm=True)            # Recreate database
blq.clean(mode="full", confirm=True)              # Full reinitialize
```

## Example: Collaborative Debugging Session

```python
# User ran: blq run build (from terminal)
# Agent is asked to help with the errors

# 1. See what happened with context around each error
blq.info(ref="build:5", context=3)
# → {
#     "run_ref": "build:5",
#     "status": "FAIL",
#     "error_count": 3,
#     "errors_by_category": {"compile": 3},
#     "events": [
#       {"ref": "5:1", "location": "src/main.c:42", "context": "...>>> 42 | error..."},
#       ...
#     ],
#     "summary": {
#       "by_fingerprint": [...],
#       "by_file": [{"file": "src/main.c", "count": 3}],
#       "affected_commits": [{"hash": "abc1234", "message": "Refactor core"}]
#     }
#   }

# 2. If you need more details on a specific error
blq.inspect(ref="build:5:1")
# → Full error details including message, code, log_context, source_context

# 3. After user fixes the code, they run: blq run build
# Agent verifies the fix:
blq.diff(run1=5, run2=6)
# → {"fixed": 3, "new": 0} - Success!
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
2. **Run commands** - Use registered build/test commands
3. **Drill down** - Get details on specific errors without needing source access
4. **Compare runs** - Detect regressions and verify fixes

Always prefer blq tools over Bash for build/test/lint operations.
