# AGENTS.md - Guide for AI Agents Using blq

This document provides guidance for AI agents (Claude, GPT, etc.) on effectively using blq to help users with build failures, test errors, and log analysis.

## Overview

**blq** (Build Log Query) captures, stores, and queries build/test logs using DuckDB. It's designed for agent integration with:
- Structured JSON output for easy parsing
- Event references for drill-down workflows
- Simple filter syntax for quick queries
- SQL access for complex analysis

## When to Use blq

Use blq when the user:
- Has a build or test failure to investigate
- Wants to analyze log files
- Needs to find patterns across multiple runs
- Asks about errors, warnings, or test failures

## Preferred Way to Run Commands

**Always use `blq run` instead of running commands directly.** This provides:
- Structured error/warning parsing
- Persistent log storage for later analysis
- Cross-run comparison and regression detection
- Consistent JSON output for agents

```bash
# PREFERRED: Use blq run
blq run pytest
blq run make -j8

# AVOID: Direct command execution
pytest          # No parsing, no storage
make -j8        # Errors not captured
```

### Using Registered Commands

Projects can register commonly-used commands. Check what's available:

```bash
blq commands                    # List registered commands
blq run test                    # Run by name (uses registered config)
blq run build                   # Registered commands have timeouts, descriptions
```

If a command isn't registered, `blq run` will execute it as a shell command.

## How blq Builds a Repository

blq maintains a **local repository** of all captured logs in `.lq/logs/`. Each action adds to this repository:

```
Action                      → Result
─────────────────────────────────────────────────────
blq run make                 → Creates run_id=1 with parsed events
blq run make (again)         → Creates run_id=2 with new events
blq import build.log         → Creates run_id=3 from file
blq run pytest               → Creates run_id=4 with test results
```

### Storage Structure

```
.lq/
├── logs/                          # All runs stored here
│   └── date=2024-01-15/
│       └── source=build/
│           ├── 001_make_103000.parquet    # run_id=1
│           ├── 002_make_110000.parquet    # run_id=2
│           └── 003_pytest_140000.parquet  # run_id=4
├── raw/                           # Optional raw logs (--keep-raw)
└── commands.yaml                  # Registered commands
```

### Run Metadata

Each `blq run` automatically captures execution context:

| Field | Description |
|-------|-------------|
| `hostname` | Machine name |
| `platform` | OS (Linux, Darwin, Windows) |
| `arch` | Architecture (x86_64, arm64) |
| `git_commit` | Current commit SHA |
| `git_branch` | Current branch |
| `git_dirty` | Uncommitted changes present |
| `cwd` | Working directory |
| `ci` | CI provider info (auto-detected) |

This metadata helps correlate errors across different machines, branches, or CI runs.

### Querying the Repository

**Query a single file (not stored):**
```bash
blq q build.log                    # Parses file directly, not stored
```

**Query stored runs:**
```bash
blq errors                         # Recent errors from ALL runs
blq q -f "severity='error'"        # All stored errors (no file = query repository)
blq history                        # List all runs
blq status                         # Summary of repository
```

### Cross-Run Analysis

The repository enables powerful cross-run queries:

```bash
# Errors from the latest run
blq q -f "run_id = (SELECT MAX(run_id) FROM lq_events)"

# Compare latest run to previous
blq sql "SELECT 'new' as status, message FROM lq_events
        WHERE run_id = (SELECT MAX(run_id) FROM lq_events)
          AND error_fingerprint NOT IN (
              SELECT error_fingerprint FROM lq_events
              WHERE run_id < (SELECT MAX(run_id) FROM lq_events))"

# Error frequency over time
blq sql "SELECT date, COUNT(*) as errors FROM lq_events
        WHERE severity='error' GROUP BY date ORDER BY date"

# Most common errors across all runs
blq sql "SELECT error_fingerprint, COUNT(*) as occurrences,
               ANY_VALUE(message) as example
        FROM lq_events WHERE severity='error'
        GROUP BY error_fingerprint ORDER BY occurrences DESC LIMIT 10"
```

### Repository Commands

| Command | Description |
|---------|-------------|
| `blq status` | Overview of repository (runs, errors, date range) |
| `blq history` | List all runs with timestamps and status |
| `blq errors` | Recent errors across all runs |
| `blq prune --older-than 30` | Remove runs older than 30 days |

### Key Insight for Agents

- **File queries** (`blq q file.log`) are one-shot, not stored
- **Run captures** (`blq run cmd`) are stored with a `run_id`
- **Repository queries** (`blq errors`, `blq q` without file) search all stored runs
- Use `run_id` to correlate events to specific builds/tests

## Quick Reference

```bash
# Query a log file directly
blq q build.log                              # all events
blq q -s file_path,line_number,message build.log  # select columns
blq q --json build.log                       # JSON output

# Filter with simple syntax
blq f severity=error build.log               # errors only
blq f severity=error,warning build.log       # errors OR warnings
blq f file_path~main build.log               # file contains "main"
blq f -c severity=error build.log            # count errors

# Run and capture commands
blq run make                                 # run and capture
blq run --json --quiet make                  # structured output, no streaming

# View stored events
blq errors                                   # recent errors
blq event 1:3                                # specific event details
blq context 1:3                              # surrounding log lines
```

## Workflows

### Build Failure Investigation

When a user reports a build failure:

```bash
# Step 1: Run the build with structured output
blq run --json --quiet make

# Step 2: If the JSON shows errors, get the summary
blq errors

# Step 3: For each error ref (e.g., "1:3"), get details
blq event 1:3

# Step 4: If you need more context (surrounding lines)
blq context 1:3 --lines 5
```

**Agent response pattern:**
1. Run the build, capture JSON output
2. Parse the errors array from JSON
3. Present errors to user with file:line locations
4. Offer to investigate specific errors in detail

### Test Failure Analysis

```bash
# Run tests with JSON output
blq run --json pytest -v

# Filter for failed tests
blq f severity=error test_output.log

# Get details on a specific failure
blq event 1:5
```

### Log File Exploration

When the user has an existing log file:

```bash
# Quick overview - count by severity
blq f -c severity=error build.log
blq f -c severity=warning build.log

# List errors with locations
blq q -s file_path,line_number,message -f "severity='error'" build.log

# Find errors in specific files
blq f severity=error file_path~main.c build.log
```

### Finding Patterns Across Runs

```bash
# Errors that appear in multiple runs
blq sql "SELECT error_fingerprint, COUNT(*) as runs, ANY_VALUE(message)
        FROM lq_events
        WHERE severity='error'
        GROUP BY error_fingerprint
        HAVING COUNT(DISTINCT run_id) > 1"

# New errors (in latest run but not previous)
blq sql "SELECT message, file_path, line_number
        FROM lq_events
        WHERE run_id = (SELECT MAX(run_id) FROM lq_events)
          AND severity = 'error'
          AND error_fingerprint NOT IN (
              SELECT error_fingerprint FROM lq_events
              WHERE run_id < (SELECT MAX(run_id) FROM lq_events)
          )"
```

## Output Formats

### When to Use Each Format

| Format | Use When |
|--------|----------|
| `--json` | Parsing output programmatically, storing results |
| `--csv` | User wants to export to spreadsheet |
| `--markdown` | Creating reports, PR comments, documentation |
| (default table) | Displaying to user in conversation |

### JSON Output Structure

```bash
blq run --json make
```

```json
{
  "run_id": 1,
  "command": "make",
  "status": "FAIL",           // "OK", "FAIL", or "WARN"
  "exit_code": 2,
  "duration_sec": 12.5,
  "summary": {
    "total_events": 5,
    "errors": 2,
    "warnings": 3
  },
  "errors": [
    {
      "ref": "1:1",            // Use this for drill-down
      "severity": "error",
      "file_path": "src/main.c",
      "line_number": 15,
      "column_number": 5,
      "message": "undefined variable 'foo'"
    }
  ]
}
```

### Parsing JSON Output

When parsing blq JSON output:
1. Check `status` field: "OK" means success, "FAIL" means errors
2. Use `errors` array for error details
3. Use `ref` field (e.g., "1:1") for drill-down with `blq event` and `blq context`

## Event References

Event references follow the format `run_id:event_id` (e.g., `1:3` means run 1, event 3).

```bash
# Get full event details
blq event 1:3

# Get surrounding log context
blq context 1:3
blq context 1:3 --lines 10  # more context
```

**Best practice:** When presenting errors to users, include the ref so they can ask for more details:

> Error at `src/main.c:15`: undefined variable 'foo' [ref: 1:1]

## Query vs Filter

| Task | Use `blq filter` | Use `blq query` |
|------|-----------------|----------------|
| Simple exact match | `blq f severity=error` | |
| Multiple values (OR) | `blq f severity=error,warning` | |
| Contains/LIKE | `blq f file_path~main` | |
| Select specific columns | | `blq q -s file,message` |
| Complex WHERE | | `blq q -f "line > 100"` |
| ORDER BY | | `blq q -o line_number` |
| Aggregations | | `blq sql "SELECT ..."` |

## MCP Server Integration

bblq provides a full MCP (Model Context Protocol) server for AI agent integration. Start it with:

```bash
blq serve                    # stdio transport (for Claude Desktop, etc.)
blq serve --transport sse    # SSE transport for HTTP clients
```

### MCP Tools

All tools are namespaced by the server name `blq`, so they appear as `run`, `query`, etc. in MCP clients.

| Tool | Parameters | Description |
|------|------------|-------------|
| `run` | `command`, `args?`, `timeout?` | Run a command and capture output |
| `query` | `sql`, `limit?` | Query stored events with SQL |
| `errors` | `limit?`, `run_id?`, `source?`, `file_pattern?` | Get recent errors |
| `warnings` | `limit?`, `run_id?`, `source?` | Get recent warnings |
| `event` | `ref` | Get full details for a specific event |
| `context` | `ref`, `lines?` | Get log context around an event |
| `status` | (none) | Get status summary of all sources |
| `history` | `limit?`, `source?` | Get run history |
| `diff` | `run1`, `run2` | Compare errors between two runs |
| `register_command` | `name`, `cmd`, `description?`, `timeout?`, `capture?`, `force?` | Register a new command |
| `unregister_command` | `name` | Remove a registered command |
| `list_commands` | (none) | List all registered commands |

### MCP Resources

Resources provide data that can be embedded in prompts or read directly:

| Resource URI | Description |
|--------------|-------------|
| `blq://status` | Current status of all sources (JSON) |
| `blq://runs` | List of all runs (JSON) |
| `blq://events` | All stored events (JSON) |
| `blq://event/{ref}` | Single event details by ref (JSON) |
| `blq://commands` | Registered commands (JSON) |

### MCP Prompts

Pre-built prompts that guide agents through common workflows:

| Prompt | Parameters | Description |
|--------|------------|-------------|
| `fix-errors` | `run_id?`, `file_pattern?` | Guide through fixing build errors systematically |
| `analyze-regression` | `good_run?`, `bad_run?` | Identify why a build started failing |
| `summarize-run` | `run_id?`, `format?` | Generate concise summary for PR comments |
| `investigate-flaky` | `test_pattern?`, `lookback?` | Investigate intermittently failing tests |

### MCP Tool Return Values

**`run` returns:**
```json
{
  "run_id": 1,
  "status": "FAIL",
  "exit_code": 2,
  "error_count": 3,
  "warning_count": 5,
  "errors": [...]
}
```

**`errors` / `warnings` return:**
```json
{
  "errors": [
    {
      "ref": "1:3",
      "file_path": "src/main.c",
      "line_number": 15,
      "column_number": 5,
      "message": "undefined variable 'foo'",
      "tool_name": "gcc",
      "category": "semantic"
    }
  ],
  "total_count": 42
}
```

**`event` returns:**
```json
{
  "ref": "1:3",
  "run_id": 1,
  "event_id": 3,
  "severity": "error",
  "file_path": "src/main.c",
  "line_number": 15,
  "message": "undefined variable 'foo'",
  "raw_text": "src/main.c:15:5: error: use of undeclared identifier 'foo'",
  "error_fingerprint": "abc123...",
  "cwd": "/home/user/project",
  "hostname": "dev-machine",
  "platform": "Linux",
  "arch": "x86_64",
  "git_commit": "abc1234",
  "git_branch": "main",
  "git_dirty": false,
  "ci": null
}
```

**`context` returns:**
```json
{
  "ref": "1:3",
  "context_lines": [
    {"line": 13, "text": "int main() {", "is_event": false},
    {"line": 14, "text": "    int bar = 10;", "is_event": false},
    {"line": 15, "text": "    printf(\"%d\", foo);", "is_event": true},
    {"line": 16, "text": "    return 0;", "is_event": false}
  ]
}
```

**`status` returns:**
```json
{
  "sources": [
    {
      "name": "make",
      "status": "FAIL",
      "error_count": 3,
      "warning_count": 5,
      "last_run": "2024-01-15T10:30:00",
      "run_id": 1
    }
  ]
}
```

**`history` returns run metadata:**
```json
{
  "runs": [
    {
      "run_id": 1,
      "source_name": "make",
      "status": "FAIL",
      "hostname": "dev-machine",
      "platform": "Linux",
      "arch": "x86_64",
      "git_commit": "abc1234",
      "git_branch": "main",
      "git_dirty": false,
      "ci": null
    }
  ]
}
```

**`diff` returns:**
```json
{
  "summary": {
    "run1_errors": 5,
    "run2_errors": 3,
    "fixed": 3,
    "new": 1,
    "unchanged": 2
  },
  "fixed": [
    {"file_path": "src/old.c", "message": "fixed error"}
  ],
  "new": [
    {"ref": "2:5", "file_path": "src/new.c", "line_number": 10, "message": "new error"}
  ]
}
```

**`list_commands` returns:**
```json
{
  "commands": [
    {"name": "build", "cmd": "make -j8", "description": "Build the project", "timeout": 300, "capture": true},
    {"name": "test", "cmd": "pytest", "description": "Run tests", "timeout": 300, "capture": true}
  ]
}
```

**`register_command` returns:**
```json
{
  "success": true,
  "message": "Registered command 'build': make -j8",
  "command": {"name": "build", "cmd": "make -j8", "description": "Build the project", "timeout": 300, "capture": true}
}
```

**`unregister_command` returns:**
```json
{
  "success": true,
  "message": "Unregistered command 'build'"
}
```

### MCP Workflow Examples

**Build Failure Investigation:**
```
1. User: "The build is failing"
2. Agent: calls run(command="make")
3. Agent: parses errors from response
4. Agent: presents summary to user with refs
5. User: "What's error 1:3 about?"
6. Agent: calls event(ref="1:3")
7. Agent: calls context(ref="1:3", lines=5)
8. Agent: explains the error with full context
```

**Regression Analysis:**
```
1. User: "The build was passing yesterday, now it fails"
2. Agent: calls history(limit=10) to find runs
3. Agent: identifies last passing run (run_id=5) and failing run (run_id=6)
4. Agent: calls diff(run1=5, run2=6)
5. Agent: presents new errors that appeared
6. Agent: uses event() and context() to explain root cause
```

**Using Prompts:**
```
1. User: "Help me fix these build errors"
2. Agent: calls get_prompt("fix-errors")
3. Prompt provides structured guidance with current status and error list
4. Agent follows the instructions in the prompt systematically
```

**Command Management:**
```
1. Agent: calls list_commands() to see available commands
2. If no build command exists:
   Agent: calls register_command(name="build", cmd="make -j8", description="Build the project")
3. Agent: calls run(command="build") to run the registered command
4. Later, if command needs updating:
   Agent: calls register_command(name="build", cmd="cmake --build .", force=true)
```

### Quick Setup

Initialize a project with MCP support:

```bash
blq init --mcp
```

This creates:
- `.lq/` directory with schema and storage
- `.mcp.json` file for MCP server discovery

The `.mcp.json` file enables automatic MCP server discovery by compatible clients.

### Claude Desktop Configuration

For Claude Desktop, add to your MCP settings (`~/.config/claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "blq": {
      "command": "blq",
      "args": ["serve"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

Or use the project's `.mcp.json` directly if your client supports project-local MCP configuration.

### Programmatic MCP Client Example

```python
from fastmcp import Client
from blq.serve import mcp

async with Client(mcp) as client:
    # Run a build
    result = await client.call_tool("run", {"command": "make"})

    # Get errors
    errors = await client.call_tool("errors", {"limit": 10})

    # Drill down
    for err in errors.data["errors"]:
        detail = await client.call_tool("event", {"ref": err["ref"]})
        context = await client.call_tool("context", {"ref": err["ref"]})
```

## Best Practices

### For Build/Test Runs

1. **Always use `--json --quiet`** for programmatic parsing:
   ```bash
   blq run --json --quiet make
   ```

2. **Check exit code** - blq preserves the command's exit code

3. **Use event refs for drill-down** - don't try to re-parse output

### For Log Analysis

1. **Start with counts** to understand the scope:
   ```bash
   blq f -c severity=error build.log
   ```

2. **Select only needed columns** for cleaner output:
   ```bash
   blq q -s file_path,line_number,message build.log
   ```

3. **Use `--json` when you'll parse the output**

### For Users

1. **Show file:line locations** - users can jump to code
2. **Include refs** - users can ask for more details
3. **Summarize first, detail on request** - don't overwhelm

### Error Handling

If blq commands fail:
- Check if `.lq/` is initialized: `blq init`
- Check if duck_hunt is installed: `blq init` will install it
- For file queries, verify the file exists

## Common Fields

| Field | Description | Example |
|-------|-------------|---------|
| `severity` | error, warning, info, note | `error` |
| `file_path` | Source file | `src/main.c` |
| `line_number` | Line in source | `15` |
| `column_number` | Column in source | `5` |
| `message` | Error/warning text | `undefined variable` |
| `error_fingerprint` | Unique hash for dedup | `abc123...` |
| `ref` | Event reference | `1:3` |
| `run_id` | Run identifier | `1` |

## Example Agent Interaction

**User:** "My build is failing, can you help?"

**Agent actions:**
```bash
# Run the build
blq run --json --quiet make 2>&1
```

**Agent response:**
> I ran the build and found 2 errors:
>
> 1. `src/main.c:15:5` - undefined variable 'foo' [ref: 1:1]
> 2. `src/utils.c:42:1` - expected ';' before '}' [ref: 1:2]
>
> Would you like me to look at either of these in detail?

**User:** "Tell me more about the first one"

**Agent actions:**
```bash
blq context 1:1 --lines 5
```

**Agent response:**
> Here's the context around that error:
> ```c
> 13: int main() {
> 14:     int bar = 10;
> 15:     printf("%d", foo);  // ERROR: 'foo' undefined
> 16:     return 0;
> 17: }
> ```
> It looks like you're using `foo` but only declared `bar`. Did you mean to use `bar` instead?
