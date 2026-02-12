# Getting Started

## The Problem

Your build fails. You scroll through 500 lines of output looking for the error. You find it, fix it, rebuild. It fails again—different error this time, buried somewhere else. Sound familiar?

blq turns build output into a queryable database. Instead of reading logs, you ask questions: "What errors?", "What changed since the last working build?", "Show me line 47 of that file."

## Install

```bash
pip install blq-cli
```

## Setup (2 minutes)

```bash
cd your-project
blq init --detect
```

This creates `.lq/` (the database), adds it to `.gitignore`, and auto-detects your build commands from `Makefile`, `pyproject.toml`, `package.json`, etc.

Register any commands it missed:

```bash
blq commands register test "pytest -v"
blq commands register build "make -j8"
```

## Workflow 1: Fixing a Failing Build

Run your build through blq:

```bash
blq run build
```

```
✗ build:3 | FAIL | 4.2s | 12 errors, 35 warnings
```

See what broke:

```bash
blq errors
```

```
Source  Ref        Location        Message
build   build:3:1  src/parser.c:142  expected ';' before '}'
build   build:3:2  src/parser.c:156  'node' undeclared
build   build:3:3  src/lexer.c:89    implicit declaration of 'scan_token'
```

Drill into a specific error with source context:

```bash
blq inspect build:3:1
```

```
Event: build:3:1
File: src/parser.c:142
Message: expected ';' before '}'

Source context:
   140│     if (token->type == TOKEN_EOF) {
   141│         return NULL
   142│     }              ← error: expected ';' before '}'
   143│     return parse_expression(parser);
   144│ }
```

Fix, rebuild, repeat until `blq errors` shows nothing.

## Workflow 2: "What Changed?"

You fixed some things, but now there are new errors. What's different?

```bash
blq diff build:2 build:3
```

```
Fixed (in build:2, not in build:3):
  - src/main.c:45: undefined reference to 'init_logger'

New (in build:3, not in build:2):
  + src/parser.c:142: expected ';' before '}'
  + src/parser.c:156: 'node' undeclared

Unchanged: 2 errors
```

Or compare against any previous run:

```bash
blq history build
```

```
Ref        E/W  When     Git              Command
✗ build:5  3/12 2m ago   *a1b2c3d (main)  make -j8
✗ build:4  1/12 5m ago   f4e5d6c (main)   make -j8
  build:3    ✓  8m ago   b7c8d9e (main)   make -j8   ← last working
✗ build:2  5/15 12m ago  1a2b3c4 (main)   make -j8
```

```bash
blq diff build:3 build:5   # What broke since the last working build?
```

## Workflow 3: AI Agent Integration

blq provides an MCP server so AI agents can query your build errors without parsing raw logs.

Install the MCP configuration:

```bash
blq mcp install   # Creates .mcp.json
```

Now AI agents (Claude Code, etc.) can use tools like:

- `run(command="test")` — run tests, get structured results
- `events(severity="error")` — get all errors
- `inspect(ref="test:5:1")` — get error with source context
- `diff(run1="test:4", run2="test:5")` — compare runs

Instead of an agent reading 2000 lines of pytest output, it gets:

```json
{
  "events": [
    {"ref": "test:5:1", "file": "tests/test_auth.py", "line": 45, "message": "AssertionError: expected 200, got 401"},
    {"ref": "test:5:2", "file": "tests/test_api.py", "line": 112, "message": "ConnectionError: timeout"}
  ]
}
```

The agent can then `inspect` specific errors to see source context, or `diff` against a previous run to understand what changed.

## Key Concepts

### Event References

Every error/warning gets a reference like `build:3:1`:
- `build` — the command name
- `3` — the run number (sequential)
- `1` — the event number within that run

Use these to drill down: `blq inspect build:3:1`, `blq info build:3`

### Run History

Every `blq run` is stored with:
- Exit code, duration, event counts
- Git commit, branch, dirty status
- Full output (for later inspection)

Query history with `blq history`, filter by command: `blq history test`

### Format Detection

blq auto-detects 60+ log formats (GCC, Clang, pytest, mypy, ESLint, TypeScript, Rust, Go, etc.). When you register a command, it guesses the format:

```bash
blq commands register lint "ruff check ."
# Registered command 'lint': ruff check . [format: ruff]
```

Override if needed: `blq commands register build --format gcc "make"`

## Next Steps

- [Query Guide](query-guide.md) — SQL queries, filters, advanced inspection
- [MCP Guide](mcp.md) — Full MCP server documentation
- [CI Integration](ci-cd.md) — `blq ci check` for regression detection
- [Commands Reference](commands/) — All CLI commands
