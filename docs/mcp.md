# MCP Server Guide

blq provides an MCP (Model Context Protocol) server that gives AI agents structured access to build/test logs. Instead of parsing thousands of lines of raw output, agents get queryable events with file locations, severity, and context.

## Why MCP?

When an agent runs `pytest` via Bash, it gets 500 lines of text. With blq MCP:

```json
{
  "run_ref": "test:5",
  "status": "FAIL",
  "errors": [
    {"ref": "test:5:1", "file": "tests/test_auth.py", "line": 45, "message": "AssertionError: expected 200, got 401"},
    {"ref": "test:5:2", "file": "tests/test_api.py", "line": 112, "message": "ConnectionError: timeout"}
  ]
}
```

The agent can then `inspect("test:5:1")` to get source context, or `diff(4, 5)` to see what changed.

## Quick Start

```bash
# Install MCP configuration
blq mcp install

# Start the server (for testing)
blq mcp serve
```

The `.mcp.json` file enables automatic server discovery by Claude Code and other MCP clients.

## Core Workflow

The typical agent workflow:

```
1. run(command="test")           → Get structured results
2. events(severity="error")      → List all errors
3. inspect(ref="test:5:1")       → See source context
4. diff(run1=4, run2=5)          → Compare with previous run
5. Fix the code
6. run(command="test")           → Verify fix
```

## Tools Reference

### Running Commands

| Tool | Description |
|------|-------------|
| `run` | Run a registered command, get structured results |
| `exec` | Run ad-hoc command (auto-detects registered prefixes) |
| `commands` | List registered commands |
| `register_command` | Add a command to registry |
| `unregister_command` | Remove a command |

### Querying Results

| Tool | Description |
|------|-------------|
| `events` | Get events by severity, run, file pattern |
| `inspect` | Get event details with source/log context |
| `info` | Get run details (omit ref for most recent) |
| `output` | Get raw stdout/stderr |
| `status` | Quick status summary |
| `history` | Run history with git context |
| `diff` | Compare errors between runs |
| `query` | SQL or filter expressions |

### Maintenance

| Tool | Description |
|------|-------------|
| `clean` | Database cleanup (data, prune, schema, full) |

---

## Tool Details

### run

Run a registered command and capture output.

```json
{"command": "test"}
{"command": "test", "args": {"path": "tests/unit/"}}
{"command": "test", "extra": ["--capture=no"]}
```

**Returns:**

```json
{
  "run_ref": "test:5",
  "status": "FAIL",
  "exit_code": 1,
  "summary": {"error_count": 2, "warning_count": 5},
  "errors": [
    {"ref": "5:1", "ref_file": "tests/test_auth.py", "ref_line": 45, "message": "AssertionError..."}
  ],
  "tail": ["FAILED tests/test_auth.py::test_login"],
  "duration_sec": 12.5
}
```

**Tail behavior:**
- Failed + errors extracted: 2 lines (summary)
- Failed + no errors: Full output (fallback)
- Success: No tail

---

### exec

Run ad-hoc shell command. If it matches a registered command prefix, uses `run` instead for cleaner refs.

```json
{"command": "pytest tests/unit/"}
```

---

### events

Get events with filtering. Replaces separate errors/warnings tools.

```json
{"severity": "error", "limit": 10}
{"run_id": 5, "severity": "error,warning"}
{"run_ids": [3, 4, 5], "severity": "error"}  // batch mode
{"file_pattern": "%test%"}
```

**Returns:**

```json
{
  "events": [
    {
      "ref": "5:1",
      "run_ref": "test:5",
      "severity": "error",
      "ref_file": "tests/test_auth.py",
      "ref_line": 45,
      "message": "AssertionError: expected 200, got 401",
      "fingerprint": "pytest_assertion_a1b2c3d4"
    }
  ],
  "total_count": 2
}
```

---

### inspect

Get comprehensive event details with context.

```json
{"ref": "test:5:1"}
{"ref": "test:5:1", "include_git_context": true}
{"refs": ["test:5:1", "test:5:2"]}  // batch mode
```

**Returns:**

```json
{
  "ref": "test:5:1",
  "severity": "error",
  "ref_file": "tests/test_auth.py",
  "ref_line": 45,
  "message": "AssertionError: expected 200, got 401",
  "fingerprint": "pytest_assertion_a1b2c3d4",
  "log_context": {
    "lines": [
      {"line": 43, "text": "    def test_login(self):"},
      {"line": 44, "text": "        response = client.post('/login', data={...})"},
      {"line": 45, "text": ">       assert response.status_code == 200", "is_event": true},
      {"line": 46, "text": "E       AssertionError: expected 200, got 401"}
    ]
  },
  "source_context": {
    "file": "tests/test_auth.py",
    "lines": [
      {"line": 43, "text": "    def test_login(self):"},
      {"line": 44, "text": "        response = client.post('/login', data={'user': 'test'})"},
      {"line": 45, "text": "        assert response.status_code == 200", "is_error": true},
      {"line": 46, "text": "        assert response.json()['token']"}
    ]
  }
}
```

**With `include_git_context=true`:**

```json
{
  "git_context": {
    "blame": {"author": "alice@example.com", "commit": "abc1234", "time": "2024-01-15T10:30:00Z"},
    "recent_commits": [{"hash": "abc1234", "message": "Refactor auth", "author": "alice@example.com"}]
  }
}
```

---

### info

Get run details. Omit `ref` for most recent run.

```json
{"ref": "test:5", "context": 3}
{"tail": 20, "errors": true}
{}  // most recent run
```

**Returns (with `context=N` for failed runs):**

```json
{
  "run_ref": "test:5",
  "status": "FAIL",
  "error_count": 2,
  "events": [
    {"ref": "5:1", "location": "tests/test_auth.py:45", "context": "  43| def test_login...\n> 45| assert..."}
  ],
  "summary": {
    "by_fingerprint": [{"fingerprint": "abc123", "count": 2, "example_message": "AssertionError"}],
    "by_file": [{"file": "tests/test_auth.py", "count": 2}],
    "affected_commits": [{"hash": "abc1234", "author": "alice", "message": "Refactor auth"}]
  }
}
```

---

### diff

Compare errors between two runs.

```json
{"run1": 4, "run2": 5}
```

**Returns:**

```json
{
  "summary": {"run1_errors": 1, "run2_errors": 3, "fixed": 1, "new": 3, "unchanged": 0},
  "fixed": [{"ref_file": "src/old.c", "message": "unused variable"}],
  "new": [{"ref": "5:1", "ref_file": "tests/test_auth.py", "message": "AssertionError..."}]
}
```

---

### query

SQL or filter expressions against events.

```json
{"filter": "severity=error ref_file~test"}
{"sql": "SELECT ref_file, COUNT(*) FROM blq_load_events() WHERE severity='error' GROUP BY 1"}
```

**Filter syntax:**
- `key=value` — exact match
- `key=v1,v2` — multiple values (OR)
- `key~pattern` — contains (ILIKE)
- `key!=value` — not equal

---

### history

Run history with metadata.

```json
{"limit": 10, "source": "test"}
{"status": "running"}  // or "completed", "orphaned"
```

**Returns:**

```json
{
  "runs": [
    {
      "run_id": 5,
      "run_ref": "test:5",
      "status": "FAIL",
      "error_count": 2,
      "duration_seconds": 12.5,
      "git_commit": "abc1234",
      "git_branch": "main",
      "git_dirty": false
    }
  ]
}
```

---

### register_command

Register a command. Idempotent—returns existing if same command already registered.

```json
{"name": "test", "cmd": "pytest -v"}
{"name": "test", "cmd": "pytest -v", "run_now": true}
{"name": "test", "cmd": "pytest -v", "force": true}  // overwrite
```

Format is auto-detected from the command. Duplicate commands (same cmd, different name) return an error.

---

### clean

Database cleanup. Requires `confirm: true`.

```json
{"mode": "data", "confirm": true}      // clear runs, keep config
{"mode": "prune", "days": 30, "confirm": true}  // remove old data
{"mode": "schema", "confirm": true}    // recreate schema
{"mode": "full", "confirm": true}      // delete everything
```

---

## Resources

Resources provide read-only access via URIs:

| Resource | Description |
|----------|-------------|
| `blq://status` | Current status (JSON) |
| `blq://events` | All events (JSON) |
| `blq://errors` | Recent errors (JSON) |
| `blq://commands` | Registered commands (JSON) |
| `blq://guide` | Agent usage guide (Markdown) |

---

## Security Controls

### Safe Mode

Disable state-modifying tools:

```bash
blq mcp serve --safe-mode
```

Disables: `exec`, `clean`, `register_command`, `unregister_command`

### Disable Specific Tools

```bash
blq mcp serve --disabled-tools exec,clean
```

Or in `.lq/config.toml`:

```toml
[mcp]
disabled_tools = ["exec", "clean"]
```

Or via environment:

```bash
export BLQ_MCP_DISABLED_TOOLS="exec,clean"
```

---

## Configuration

### Claude Code / Claude Desktop

`blq mcp install` creates `.mcp.json`:

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

### Transport Options

```bash
blq mcp serve                           # stdio (default)
blq mcp serve --transport sse --port 8080  # HTTP SSE
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BLQ_DIR` | Path to .lq directory | Auto-detect |
| `BLQ_TIMEOUT` | Default timeout (seconds) | 300 |
| `BLQ_MCP_DISABLED_TOOLS` | Tools to disable | (none) |

---

## Example Workflows

### Fix Build Errors

```
1. run(command="build")
   → status=FAIL, 3 errors

2. info(context=3)
   → errors with log context, summary.by_file shows src/auth.c has all 3

3. inspect(ref="build:5:1", include_git_context=true)
   → source context + git blame shows recent change

4. Fix the code

5. run(command="build")
   → status=OK
```

### Investigate Regression

```
1. history(source="test", limit=10)
   → test:5 failed, test:4 passed

2. diff(run1=4, run2=5)
   → 2 new errors in tests/test_auth.py

3. info(ref="test:5", context=3)
   → summary.affected_commits shows commit abc1234 touched test_auth.py

4. inspect(refs=["test:5:1", "test:5:2"], include_git_context=true)
   → both errors from same commit
```
