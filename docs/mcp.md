# MCP Server Guide

blq provides an MCP (Model Context Protocol) server for AI agent integration. This allows agents to run builds, query logs, and analyze errors through a standardized interface.

## Quick Start

```bash
# Create .mcp.json for agent discovery
blq mcp install

# Start the MCP server
blq mcp serve

# Or with specific transport
blq mcp serve --transport stdio      # For Claude Desktop, etc.
blq mcp serve --transport sse --port 8080  # For HTTP clients
```

## Commands

### blq mcp install

Create or update `.mcp.json` with blq server configuration for agent discovery.

```bash
blq mcp install
```

This creates a `.mcp.json` file in the current directory:

```json
{
  "mcpServers": {
    "blq": {
      "command": "blq",
      "args": ["mcp", "serve"]
    }
  }
}
```

### blq mcp serve

Start the MCP server for agent integration.

```bash
blq mcp serve [OPTIONS]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--transport TYPE` | `-t` | Transport type: `stdio` or `sse` (default: stdio) |
| `--port PORT` | `-p` | Port for SSE transport (default: 8080) |
| `--safe-mode` | `-S` | Disable state-modifying tools (exec, clean, register/unregister) |
| `--disabled-tools LIST` | `-D` | Comma-separated list of tools to disable |

#### Safe Mode

Safe mode (`--safe-mode` or `-S`) disables tools that can modify state:
- `exec` - No arbitrary command execution
- `clean` - No data deletion
- `register_command` - No registry modification
- `unregister_command` - No registry modification

```bash
blq mcp serve --safe-mode              # Disable all state-modifying tools
blq mcp serve -D exec,clean            # Disable specific tools
blq mcp serve -S -D custom_tool        # Combine both
```

### Transport Types

**stdio (default)** - Standard I/O transport for direct integration:
- Used by Claude Desktop
- Used by command-line MCP clients
- Process communicates via stdin/stdout

**sse** - Server-Sent Events over HTTP:
- Useful for web-based integrations
- Allows multiple concurrent connections
- Runs a local HTTP server

## Overview

The blq MCP server exposes:

- **Tools** - Actions agents can perform (run commands, query logs)
- **Resources** - Data agents can read (events, runs, status)
- **Prompts** - Templates for common workflows (fix errors, analyze regressions)

All tools are namespaced under the `blq` server, so `run` becomes `blq.run` when accessed by agents.

### Available Tools (Consolidated API)

| Tool | Description |
|------|-------------|
| `run` | Run registered command(s) - supports batch mode via `commands` param. Returns concise output with conditional tail. |
| `exec` | Execute ad-hoc shell command (detects registered command prefixes) |
| `query` | Query events with SQL or filter expressions |
| `events` | Get events with severity/run filters - supports batch mode via `run_ids` param. Includes fingerprint. |
| `inspect` | Get event details with log/source context - supports batch mode via `refs` param |
| `output` | Get raw stdout/stderr for a run |
| `status` | Get status summary |
| `info` | Get detailed run info (omit `ref` for most recent, `context=N` for inline errors). Includes summary aggregations for failed runs. |
| `history` | Get run history |
| `diff` | Compare errors between runs |
| `commands` | List all registered commands |
| `register_command` | Register a new command (idempotent, with `run_now` option) |
| `unregister_command` | Remove a registered command |
| `clean` | Database cleanup (modes: data, prune, schema, full) |

---

## Tools

### run

Run a registered command and capture its output. For ad-hoc commands, use `exec`.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `command` | string | Yes | Registered command name |
| `args` | dict | No | Template parameters (for parameterized commands) |
| `extra` | string[] | No | Additional passthrough arguments |
| `timeout` | number | No | Timeout in seconds (default: 300) |

For parameterized commands (templates with `{param}` placeholders), use `args` to provide parameter values:

**Returns:**

```json
{
  "run_ref": "build:1",
  "cmd": "make -j8",
  "status": "FAIL",
  "exit_code": 2,
  "summary": {"error_count": 3, "warning_count": 5},
  "errors": [
    {
      "ref": "1:1",
      "ref_file": "src/main.c",
      "ref_line": 15,
      "ref_column": 5,
      "message": "undefined variable 'foo'",
      "tool_name": "gcc",
      "category": "error",
      "fingerprint": "gcc_error_a1b2c3"
    }
  ],
  "tail": ["line 1", "line 2"],
  "duration_sec": 12.5
}
```

**Conditional tail behavior:**
- **Failed + errors extracted**: 2 lines of tail (summary context)
- **Failed + no errors**: Full tail (fallback for debugging)
- **Success**: No tail included

Duration is only included if > 5 seconds.
```

**Examples:**

```json
// Simple command
{
  "tool": "run",
  "arguments": {
    "command": "build"
  }
}

// Parameterized command with args
{
  "tool": "run",
  "arguments": {
    "command": "test",
    "args": {"path": "tests/unit/", "flags": "-vvs"}
  }
}

// With extra passthrough args
{
  "tool": "run",
  "arguments": {
    "command": "test",
    "extra": ["--capture=no"]
  }
}
```

**Error for unregistered commands:**

```json
{
  "status": "FAIL",
  "error": "'make' is not a registered command. Use 'exec' for ad-hoc commands."
}
```

---

### exec

Execute an ad-hoc shell command and capture its output.

**Smart Prefix Detection:** If the command matches a registered command prefix, this tool automatically uses `run()` instead. For example, if `test` is registered as `pytest -v`, then calling this tool with `pytest -v tests/unit/` will be run as `run("test", extra=["tests/unit/"])`. This provides cleaner event references (e.g., `test:1:3` instead of ad-hoc refs).

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `command` | string | Yes | Shell command to run |
| `args` | string[] | No | Additional arguments |
| `timeout` | number | No | Timeout in seconds (default: 300) |

**Returns:** Same structure as `run`.

**Example:**

```json
{
  "arguments": {
    "command": "make",
    "args": ["-j8"]
  }
}
```

---

### query

Query stored log events with SQL or simple filter expressions.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `sql` | string | No* | SQL query against blq_load_events() |
| `filter` | string | No* | Simple filter expressions (alternative to SQL) |
| `limit` | number | No | Max rows to return (default: 100) |

*Either `sql` or `filter` must be provided.

**Filter Syntax:**

The `filter` parameter supports simple expressions as an alternative to raw SQL:

| Syntax | Meaning | Example |
|--------|---------|---------|
| `key=value` | Exact match | `severity=error` |
| `key=v1,v2` | Multiple values (OR) | `severity=error,warning` |
| `key~pattern` | Contains (ILIKE) | `ref_file~test` |
| `key!=value` | Not equal | `tool_name!=mypy` |

Multiple filters are AND'd together (space or comma separated):

```json
{"filter": "severity=error ref_file~test"}
```

**Returns:**

```json
{
  "columns": ["ref_file", "ref_line", "message"],
  "rows": [
    ["src/main.c", 15, "undefined variable 'foo'"],
    ["src/utils.c", 42, "unused variable 'bar'"]
  ],
  "row_count": 2
}
```

**Examples:**

```json
// Using SQL
{
  "tool": "query",
  "arguments": {
    "sql": "SELECT ref_file, COUNT(*) as count FROM blq_load_events() WHERE severity='error' GROUP BY ref_file",
    "limit": 10
  }
}

// Using filter syntax
{
  "tool": "query",
  "arguments": {
    "filter": "severity=error",
    "limit": 10
  }
}

// Multiple filters
{
  "tool": "query",
  "arguments": {
    "filter": "severity=error,warning ref_file~test"
  }
}
```

---

### events

Get events with optional severity filter. Replaces separate `errors` and `warnings` tools.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | number | No | Max events to return (default: 20) |
| `run_id` | number | No | Filter to specific run (by serial number) |
| `source` | string | No | Filter to specific source name |
| `severity` | string | No | Filter by severity: "error", "warning", or "error,warning" |
| `file_pattern` | string | No | Filter by file path pattern (SQL LIKE) |
| `run_ids` | number[] | No | Batch mode: get events from multiple runs |
| `limit_per_run` | number | No | Max events per run in batch mode (default: 10) |

**Returns:**

```json
{
  "events": [
    {
      "ref": "1:1",
      "run_ref": "build:1",
      "severity": "error",
      "ref_file": "src/main.c",
      "ref_line": 15,
      "ref_column": 5,
      "message": "undefined variable 'foo'",
      "tool_name": "gcc",
      "category": "error",
      "fingerprint": "gcc_error_a1b2c3d4",
      "log_line": 42
    }
  ],
  "total_count": 3
}
```

**Examples:**

```json
// Get errors only
{
  "tool": "events",
  "arguments": {
    "severity": "error",
    "limit": 10
  }
}

// Get warnings only
{
  "tool": "events",
  "arguments": {
    "severity": "warning"
  }
}

// Batch mode: errors from multiple runs
{
  "tool": "events",
  "arguments": {
    "run_ids": [1, 2, 3],
    "severity": "error"
  }
}
```

---

### inspect

Get comprehensive event details with context and optional enrichment. Replaces separate `event` and `context` tools.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ref` | string | Yes | Event reference (e.g., "build:1:3" or "1:3") |
| `lines` | number | No | Lines of context before/after (default: 5) |
| `include_log_context` | boolean | No | Include surrounding log lines (default: true) |
| `include_source_context` | boolean | No | Include source file context (default: true) |
| `include_git_context` | boolean | No | Include git blame and history (default: false) |
| `include_fingerprint_history` | boolean | No | Include fingerprint occurrence history (default: false) |
| `refs` | string[] | No | Batch mode: inspect multiple events at once |

**Returns:**

```json
{
  "ref": "build:1:3",
  "severity": "error",
  "ref_file": "src/main.c",
  "ref_line": 15,
  "ref_column": 5,
  "message": "undefined variable 'foo'",
  "tool_name": "gcc",
  "category": "error",
  "fingerprint": "gcc_error_a1b2c3d4",
  "log_context": {
    "lines": [
      {"line": 40, "text": "gcc -c src/main.c -o main.o"},
      {"line": 41, "text": "In file included from src/main.c:1:"},
      {"line": 42, "text": "src/main.c:15:5: error: undefined variable 'foo'", "is_event": true},
      {"line": 43, "text": "     int x = foo + 1;"},
      {"line": 44, "text": "             ^~~"}
    ]
  },
  "source_context": {
    "file": "src/main.c",
    "lines": [
      {"line": 13, "text": "int main() {"},
      {"line": 14, "text": "    int y = 10;"},
      {"line": 15, "text": "    int x = foo + 1;", "is_error": true},
      {"line": 16, "text": "    return x + y;"},
      {"line": 17, "text": "}"}
    ]
  }
}
```

**With git context (`include_git_context=true`):**

```json
{
  "git_context": {
    "file": "src/main.c",
    "line": 15,
    "blame": {
      "author": "alice@example.com",
      "commit": "abc1234",
      "time": "2024-01-15T10:30:00Z"
    },
    "recent_commits": [
      {"hash": "abc1234", "message": "Refactor data processing", "author": "alice@example.com"}
    ]
  }
}
```

**With fingerprint history (`include_fingerprint_history=true`):**

```json
{
  "fingerprint_history": {
    "fingerprint": "gcc_error_a1b2c3d4",
    "first_seen": {"run_ref": "build:1", "time": "2024-01-10T08:00:00Z"},
    "occurrences": 4,
    "is_regression": true
  }
}
```

**Examples:**

```json
// Basic inspect with log and source context
{
  "tool": "inspect",
  "arguments": {
    "ref": "build:1:3"
  }
}

// With git context
{
  "tool": "inspect",
  "arguments": {
    "ref": "build:1:3",
    "include_git_context": true
  }
}

// Batch mode: inspect multiple events
{
  "tool": "inspect",
  "arguments": {
    "ref": "build:1:1",
    "refs": ["build:1:1", "build:1:2", "build:1:3"],
    "include_git_context": true
  }
}
```

---

### output

Get raw stdout/stderr output for a run. Useful when structured parsing didn't capture the information you need.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `run_id` | number | Yes | Run serial number (e.g., 1, 2, 3) |
| `stream` | string | No | Stream name: 'stdout', 'stderr', or 'combined' |
| `tail` | number | No | Return only last N lines |
| `head` | number | No | Return only first N lines |

**Returns:**

```json
{
  "run_id": 1,
  "stream": "combined",
  "byte_length": 4523,
  "total_lines": 156,
  "returned_lines": 10,
  "content": "...",
  "streams": ["combined"]
}
```

**Example:**

```json
{
  "tool": "output",
  "arguments": {
    "run_id": 1,
    "tail": 20
  }
}
```

---

### info

Get detailed information about a specific run, including running commands. For failed runs with `context=N`, includes aggregated summaries to help identify patterns.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ref` | string | No | Run reference (e.g., "build:5") or UUID. If omitted, uses most recent run. |
| `head` | number | No | Return first N lines of output |
| `tail` | number | No | Return last N lines of output |
| `errors` | boolean | No | Include error events (default: false) |
| `warnings` | boolean | No | Include warning events (default: false) |
| `severity` | string | No | Filter events by severity (e.g., "error", "error,warning") |
| `limit` | number | No | Max events to return (default: 20) |
| `context` | number | No | Show N lines of log context around each event |

**Returns (basic):**

```json
{
  "run_id": 5,
  "run_ref": "build:5",
  "source_name": "build",
  "command": "make -j8",
  "status": "RUNNING",
  "is_running": true,
  "attempt_id": "abc123-...",
  "started_at": "2024-01-15T10:30:00Z",
  "cwd": "/home/user/project",
  "git_branch": "main",
  "output": "...",
  "output_lines": 50
}
```

**Returns (with `context=N` for failed runs):**

When `context=N` is specified for a failed run, the response includes a compact event format with aggregated summaries:

```json
{
  "run_ref": "build:5",
  "status": "FAIL",
  "error_count": 3,
  "errors_by_category": {"compile": 3},
  "events": [
    {
      "ref": "5:1",
      "location": "src/main.c:42",
      "context": "    40 | int x = 0;\n>>> 42 | int y = foo;\n    43 | return x;"
    }
  ],
  "summary": {
    "by_fingerprint": [
      {"fingerprint": "abc123", "count": 2, "example_message": "undefined variable"}
    ],
    "by_file": [
      {"file": "src/main.c", "count": 3}
    ],
    "affected_commits": [
      {"hash": "abc1234", "author": "alice@example.com", "message": "Refactor core", "files": ["src/main.c"]}
    ]
  }
}
```

**Summary fields for failed runs:**
- `by_fingerprint`: Error counts grouped by fingerprint (helps identify duplicate/related errors)
- `by_file`: Error counts grouped by file (identifies problematic files)
- `affected_commits`: Recent git commits that touched files with errors (helps find root cause)

For running commands, `info` reads from the live output directory. For completed commands, it reads from blob storage.

**Examples:**

```json
// Basic info with tail
{
  "tool": "info",
  "arguments": {
    "ref": "build:5",
    "tail": 20,
    "errors": true
  }
}

// Compact view with context (recommended for failed runs)
{
  "tool": "info",
  "arguments": {
    "ref": "build:5",
    "context": 3
  }
}

// Most recent run (no ref)
{
  "tool": "info",
  "arguments": {
    "context": 5
  }
}
```

---

### status

Get current status summary of all sources.

**Parameters:** None

**Returns:**

```json
{
  "sources": [
    {
      "name": "build",
      "status": "FAIL",
      "error_count": 3,
      "warning_count": 5,
      "last_run": "2024-01-15T10:30:00Z",
      "run_id": 5
    },
    {
      "name": "test",
      "status": "OK",
      "error_count": 0,
      "warning_count": 2,
      "last_run": "2024-01-15T10:25:00Z",
      "run_id": 4
    }
  ]
}
```

---

### history

Get run history.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | number | No | Max runs to return (default: 20) |
| `source` | string | No | Filter to specific source name |
| `status` | string | No | Filter by status: "running", "completed", "orphaned" |

**Returns:**

```json
{
  "runs": [
    {
      "run_id": 5,
      "source_name": "build",
      "status": "FAIL",
      "error_count": 3,
      "warning_count": 5,
      "started_at": "2024-01-15T10:30:00Z",
      "duration_seconds": 12.5,
      "exit_code": 2,
      "cwd": "/home/user/project",
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

Run metadata fields:

| Field | Type | Description |
|-------|------|-------------|
| `cwd` | string | Working directory |
| `hostname` | string | Machine name |
| `platform` | string | OS (Linux, Darwin, Windows) |
| `arch` | string | Architecture (x86_64, arm64) |
| `git_commit` | string | HEAD commit SHA |
| `git_branch` | string | Current branch |
| `git_dirty` | boolean | Uncommitted changes present |
| `ci` | object | CI provider info (if running in CI) |

---

### diff

Compare errors between two runs.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `run1` | number | Yes | First run ID (baseline) |
| `run2` | number | Yes | Second run ID (comparison) |

**Returns:**

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
    {
      "ref_file": "src/old.c",
      "message": "unused variable"
    }
  ],
  "new": [
    {
      "ref": "6:1",
      "ref_file": "src/new.c",
      "message": "undefined function"
    }
  ]
}
```

---

### register_command

Register a new command in the command registry.

**Idempotent Registration:** If a command with the same name or identical command string already exists, the existing command is used instead of failing. This allows agents to safely call register_command without checking if a command already exists. Use `force=true` to overwrite an existing command.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | Yes | Command name (e.g., "build", "test") |
| `cmd` | string | Yes | Shell command (whitespace-normalized for comparison) |
| `description` | string | No | Human-readable description |
| `timeout` | number | No | Timeout in seconds (default: 300) |
| `capture` | boolean | No | Whether to capture and parse logs (default: true) |
| `format` | string | No | Log format for parsing (auto-detected from command if not specified) |
| `force` | boolean | No | Overwrite existing command (default: false) |
| `run_now` | boolean | No | Run the command immediately after registering (default: false) |

**Returns:**

```json
{
  "success": true,
  "message": "Registered command 'build': make -j8"
}
```

If `run_now=true`, the response also includes a `run` key with the run result.

**Example:**

```json
{
  "tool": "register_command",
  "arguments": {
    "name": "build",
    "cmd": "make -j8",
    "description": "Build the project",
    "timeout": 300,
    "capture": true,
    "run_now": true
  }
}
```

---

### unregister_command

Remove a command from the registry.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | string | Yes | Command name to remove |

**Returns:**

```json
{
  "success": true,
  "message": "Unregistered command 'build'"
}
```

**Example:**

```json
{
  "tool": "unregister_command",
  "arguments": {
    "name": "build"
  }
}
```

---

### commands

List all registered commands.

**Parameters:** None

**Returns:**

```json
{
  "commands": [
    {
      "name": "build",
      "cmd": "make -j8",
      "description": "Build the project",
      "timeout": 300,
      "format": "auto",
      "capture": true
    },
    {
      "name": "test",
      "tpl": "pytest {path} {flags}",
      "defaults": {"path": "tests/", "flags": "-v"},
      "description": "Run tests",
      "timeout": 600,
      "format": "auto",
      "capture": true
    }
  ]
}
```

Commands are returned as a list. Template commands use `tpl` instead of `cmd`, with optional `defaults` for parameter values.

**Example:**

```json
{
  "tool": "commands",
  "arguments": {}
}
```

---

### clean

Database cleanup and maintenance. This is a potentially destructive operation.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `mode` | string | No | Cleanup mode (see below). Default: "data" |
| `confirm` | boolean | No | Must be true to proceed (safety check). Default: false |
| `days` | number | No | For prune mode: remove data older than N days |

**Modes:**

| Mode | Description |
|------|-------------|
| `data` | Clear run data, keep config and commands |
| `prune` | Remove data older than N days (requires `days` parameter) |
| `schema` | Recreate database schema (clears all data, keeps config files) |
| `full` | Delete and recreate entire .lq directory |

**Returns:**

```json
{
  "success": true,
  "message": "Cleared all run data. Config and commands preserved.",
  "mode": "data"
}
```

For prune mode, also returns cleanup stats:

```json
{
  "success": true,
  "message": "Removed data older than 30 days.",
  "mode": "prune",
  "removed": {
    "invocations": 15,
    "events": 243,
    "blobs": 12,
    "bytes_freed": 1048576
  }
}
```

**Examples:**

```json
// Clear all data
{
  "tool": "clean",
  "arguments": {
    "mode": "data",
    "confirm": true
  }
}

// Prune old data
{
  "tool": "clean",
  "arguments": {
    "mode": "prune",
    "days": 30,
    "confirm": true
  }
}
```

**Security Note:** This tool can be disabled via configuration or `--safe-mode`. See [Security Controls](#security-controls) below.

---

## Resources

Resources provide read-only access to blq data.

### blq://status

Current status of all sources.

**URI:** `blq://status`

**MIME Type:** `application/json`

**Content:** Same as `status` tool response.

---

### blq://runs

List of all runs.

**URI:** `blq://runs`
**URI with filter:** `blq://runs?source=build&limit=10`

**MIME Type:** `application/json`

**Content:** Same as `history` tool response.

---

### blq://events

All stored events (with optional filtering).

**URI:** `blq://events`
**URI with filter:** `blq://events?severity=error&run_id=5`

**MIME Type:** `application/json`

---

### blq://errors

Recent errors across all runs.

**URI:** `blq://errors`
**URI for specific run:** `blq://errors/1`

**MIME Type:** `application/json`

**Content:** Same as `errors` tool response.

---

### blq://warnings

Recent warnings across all runs.

**URI:** `blq://warnings`
**URI for specific run:** `blq://warnings/1`

**MIME Type:** `application/json`

**Content:** Same as `warnings` tool response.

---

### blq://context/{ref}

Log context around a specific event.

**URI:** `blq://context/build:1:2`

**MIME Type:** `application/json`

**Content:** Same as `context` tool response.

---

### blq://event/{ref}

Single event details.

**URI:** `blq://event/build:1:3`

**MIME Type:** `application/json`

**Content:** Same as `event` tool response.

---

### blq://commands

Registered commands.

**URI:** `blq://commands`

**MIME Type:** `application/json`

**Content:**

```json
{
  "commands": [
    {
      "name": "build",
      "cmd": "make -j8",
      "description": "Build the project",
      "timeout": 300,
      "capture": true,
      "format": "auto"
    },
    {
      "name": "test",
      "tpl": "pytest {path} {flags}",
      "defaults": {"path": "tests/", "flags": "-v"},
      "description": "Run tests",
      "timeout": 600,
      "capture": true,
      "format": "auto"
    }
  ]
}
```

---

### blq://guide

Agent usage guide with detailed instructions.

**URI:** `blq://guide`

**MIME Type:** `text/markdown`

**Content:** Comprehensive guide for AI agents using blq MCP tools, including:
- Key concepts (shared database between CLI and MCP)
- Why use blq tools instead of Bash
- Recommended workflows
- Reference format explanations
- Best practices

---

## Prompts

Prompts are templates for common agent workflows. When an agent selects a prompt, the server fills in the template variables with current data, giving the agent relevant context and clear instructions.

### fix-errors

Guide the agent through fixing build errors systematically.

**Arguments:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `run_id` | number | No | Specific run to fix (default: latest) |
| `file_pattern` | string | No | Focus on specific files |

**Example prompt (rendered):**

```
You are helping fix build errors in a software project.

## Current Status

| Source | Status | Errors | Warnings |
|--------|--------|--------|----------|
| build  | FAIL   | 3      | 5        |

## Errors to Fix

1. **ref: 5:1** `src/main.c:15:5`
   ```
   error: use of undeclared identifier 'config'
   ```

2. **ref: 5:2** `src/main.c:23:12`
   ```
   error: no member named 'timeout' in 'struct options'
   ```

3. **ref: 5:3** `src/utils.c:42:1`
   ```
   error: expected ';' after expression
   ```

## Instructions

1. Read each error and understand the root cause
2. Use `event(ref="5:1")` for full context if the message is unclear
3. Use `context(ref="5:1")` to see surrounding log lines
4. Fix errors in dependency order:
   - Missing includes/declarations first
   - Then type errors
   - Then syntax errors
5. After fixing, run `run(command="build")` to verify
6. Repeat until build passes

**Tips:**
- Error 5:1 and 5:2 are in the same file - likely related
- Check if 'config' was recently renamed or moved
- The ';' error (5:3) is often caused by macro expansion issues
```

---

### analyze-regression

Help identify why a build started failing between two runs.

**Arguments:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `good_run` | number | No | Last known good run ID |
| `bad_run` | number | No | First failing run ID (default: latest) |

**Example prompt (rendered):**

```
You are analyzing why a build started failing.

## Run Comparison

| Metric | Run 4 (good) | Run 5 (bad) | Delta |
|--------|--------------|-------------|-------|
| Status | OK           | FAIL        |       |
| Errors | 0            | 3           | +3    |
| Warnings | 12         | 15          | +3    |

## New Errors (not in Run 4)

1. **ref: 5:1** `src/auth.c:156:8`
   ```
   error: implicit declaration of function 'validate_token'
   ```

2. **ref: 5:2** `src/auth.c:203:15`
   ```
   error: 'TOKEN_EXPIRY' undeclared
   ```

3. **ref: 5:3** `src/auth.c:210:5`
   ```
   error: too few arguments to function 'create_session'
   ```

## Analysis Hints

- All 3 new errors are in `src/auth.c`
- Errors reference `validate_token`, `TOKEN_EXPIRY`, `create_session`
- These symbols may have been modified or moved

## Instructions

1. Check recent changes to authentication-related files
2. Look for renamed functions or changed signatures
3. Use `event(ref="5:1")` for full error context
4. Identify the root cause (likely a single change that broke multiple things)
5. Suggest the minimal fix to restore the build
```

---

### summarize-run

Generate a concise summary of a build/test run for reports or PR comments.

**Arguments:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `run_id` | number | No | Run to summarize (default: latest) |
| `format` | string | No | Output format: "brief", "detailed", "pr-comment" |

**Example prompt (rendered):**

```
Summarize this build/test run for a PR comment.

## Run Details

- **Run ID:** 5
- **Command:** make -j8
- **Status:** FAIL (exit code 2)
- **Duration:** 45.2 seconds
- **Started:** 2024-01-15 10:30:00

## Results

- **Errors:** 3
- **Warnings:** 15 (12 pre-existing, 3 new)

## Errors by File

| File | Count | Types |
|------|-------|-------|
| src/auth.c | 3 | undeclared identifier, missing args |

## Error Details

1. `src/auth.c:156` - implicit declaration of function 'validate_token'
2. `src/auth.c:203` - 'TOKEN_EXPIRY' undeclared
3. `src/auth.c:210` - too few arguments to function 'create_session'

## Instructions

Generate a summary suitable for a GitHub PR comment:
- Lead with pass/fail status
- List the key errors (not all 15 warnings)
- Group related errors
- Suggest what might have caused the failure
- Keep it concise (under 200 words)

**Output format:**
```markdown
## Build Status: ❌ Failed

**3 errors** in `src/auth.c` related to authentication functions.

### Errors
- `validate_token` - undeclared (missing include?)
- `TOKEN_EXPIRY` - undeclared constant
- `create_session` - wrong number of arguments

### Likely Cause
Recent changes to auth API. Check if `auth.h` was modified.
```
```

---

### investigate-flaky

Help investigate intermittently failing tests.

**Arguments:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `test_pattern` | string | No | Filter to specific test names |
| `lookback` | number | No | Number of runs to analyze (default: 10) |

**Example prompt (rendered):**

```
You are investigating flaky (intermittently failing) tests.

## Test Failure History (last 10 runs)

| Run | Status | Failed Tests |
|-----|--------|--------------|
| 10  | FAIL   | test_concurrent_write |
| 9   | OK     | - |
| 8   | OK     | - |
| 7   | FAIL   | test_concurrent_write |
| 6   | OK     | - |
| 5   | FAIL   | test_concurrent_write, test_timeout |
| 4   | OK     | - |
| 3   | OK     | - |
| 2   | FAIL   | test_concurrent_write |
| 1   | OK     | - |

## Flaky Test Analysis

| Test | Failures | Rate | Pattern |
|------|----------|------|---------|
| test_concurrent_write | 4/10 | 40% | Random |
| test_timeout | 1/10 | 10% | Rare |

## Most Recent Failure Details

**ref: 10:1** `tests/test_db.py:145`
```
FAILED test_concurrent_write - AssertionError: Expected 100 rows, got 98
```

## Instructions

1. Focus on `test_concurrent_write` (most frequent)
2. Use `event(ref="10:1")` to see full failure output
3. Look for patterns:
   - Race conditions (concurrent, parallel, thread)
   - Timing issues (timeout, sleep, wait)
   - Resource contention (connection, file, lock)
4. Check if failures correlate with system load or time of day
5. Suggest fixes or ways to make the test more deterministic
```

---

## Configuration

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "blq": {
      "command": "blq",
      "args": ["mcp", "serve"],
      "cwd": "/path/to/your/project"
    }
  }
}
```

### Project Configuration

Use `blq mcp install` to create a `.mcp.json` file for automatic server discovery:

```bash
blq mcp install
```

This creates:

```json
{
  "mcpServers": {
    "blq": {
      "command": "blq",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BLQ_DIR` | Path to .lq directory | Auto-detect |
| `BLQ_TIMEOUT` | Default command timeout (seconds) | 300 |
| `BLQ_MCP_DISABLED_TOOLS` | Comma-separated list of tools to disable | (none) |

---

## Security Controls

blq allows disabling sensitive MCP tools for security. This is useful when running the MCP server in untrusted environments or when you want to limit agent capabilities.

### Safe Mode (Recommended)

The easiest way to secure the MCP server is to use safe mode:

```bash
blq mcp serve --safe-mode
```

This disables all state-modifying tools: `exec`, `clean`, `register_command`, `unregister_command`.

### Disabling Specific Tools

For fine-grained control, disable specific tools:

**Via CLI:**

```bash
blq mcp serve --disabled-tools exec,clean
```

**Via `.lq/config.toml`:**

```toml
[mcp]
disabled_tools = ["exec", "clean", "register_command", "unregister_command"]
```

**Via environment variable:**

```bash
export BLQ_MCP_DISABLED_TOOLS="exec,clean,register_command,unregister_command"
blq mcp serve
```

### Sensitive Tools

Consider disabling these tools in production/CI environments:

| Tool | Risk | Recommendation |
|------|------|----------------|
| `exec` | Runs arbitrary commands | Disable in untrusted environments |
| `clean` | Deletes data | Disable in production |
| `register_command` | Modifies registry | Disable if commands are fixed |
| `unregister_command` | Modifies registry | Disable if commands are fixed |

When a disabled tool is called, the server returns an error:

```json
{
  "error": "Tool 'exec' is disabled. Enable it by removing from mcp.disabled_tools in .lq/config.toml or BLQ_MCP_DISABLED_TOOLS environment variable."
}
```

---

## Security Considerations

- **Command running**: The `run` tool only runs registered commands. Ad-hoc commands go through a separate tool.
- **File access**: The server only accesses files within the project directory.
- **SQL injection**: `query` uses parameterized queries where possible. Complex queries are sandboxed to read-only operations.

---

## Examples

### Agent Workflow: Fix Build Errors

```
1. Agent calls run(command="build") if registered
   → Gets structured error list with conditional tail

2. Agent calls info(ref="build:1", context=3)
   → Gets errors with log context and summary (by_file, affected_commits)

3. If more details needed, agent calls inspect(ref="1:1")
   → Gets full details with source context

4. Agent reads source file and makes fix

5. Agent calls run(command="build")
   → Verifies fix worked

6. Repeat until build passes
```

### Agent Workflow: Investigate Regression

```
1. Agent calls history(limit=10)
   → Sees run 5 failed, run 4 passed

2. Agent calls diff(run1=4, run2=5)
   → Gets list of new errors

3. Agent calls info(ref="build:5", context=3)
   → Gets errors with context and summary.affected_commits

4. For deeper investigation, calls inspect(ref="5:1", include_git_context=true)
   → Gets git blame and recent commits for affected lines
```
