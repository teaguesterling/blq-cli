# blq - Build Log Query

A CLI tool that turns build output into a queryable database. blq parses 60+ log formats into structured events, stores run history with git context, and provides an MCP server for AI agent integration.

## Why blq?

Your build fails. You scroll through 500 lines of output looking for the error. blq solves this:

```bash
blq run build
# build:3 | exit=1 | 12 errors, 35 warnings

blq errors
# ref       severity  file           line  message
# build:3:1 error     src/parser.c   142   expected ';' before '}'
# build:3:2 error     src/parser.c   156   'node' undeclared

blq inspect build:3:1
# Shows error with source context
```

**Key benefits:**
- **Structured events** — Errors with file:line locations, not raw text
- **Run history** — Every build stored with git commit, branch, environment
- **Comparison** — `blq diff 4 5` shows what changed between runs
- **AI agent tools** — MCP server for structured access without log parsing

## Installation

```bash
pip install blq-cli
```

## Quick Start

```bash
cd your-project
blq init --detect                    # Auto-detect build commands

blq run build                        # Run and capture
blq errors                           # See what broke
blq inspect build:3:1                # Drill into specific error
blq diff 4 5                         # Compare runs
```

Register commands manually if needed:

```bash
blq commands register build "make -j8"
blq commands register test "pytest -v"
```

## AI Agent Integration (MCP)

```bash
blq mcp install                      # Creates .mcp.json
```

Agents can then use tools like `run`, `events`, `inspect`, `diff` to work with structured results instead of parsing raw output. See the [MCP Guide](docs/mcp.md).

## Commands

| Command | Description |
|---------|-------------|
| `blq run <cmd>` | Run registered command, capture output |
| `blq errors` | Recent errors |
| `blq inspect <ref>` | Event details with source context |
| `blq info <ref>` | Run details |
| `blq history` | Run history |
| `blq diff <r1> <r2>` | Compare runs |
| `blq ci check` | Check for regressions vs baseline |

## Event References

Every error gets a reference like `build:3:1`:
- `build` — command name
- `3` — run number
- `1` — event within run

Use refs to drill down: `blq inspect build:3:1`, `blq info build:3`

## Features

| Feature | Description |
|---------|-------------|
| **60+ formats** | GCC, Clang, pytest, mypy, ESLint, TypeScript, Rust, Go, etc. |
| **Run history** | Every run with git commit, branch, environment |
| **Format detection** | Auto-detects format at registration time |
| **CI integration** | `blq ci check`, `blq ci comment` for PR feedback |
| **MCP server** | AI agents query errors without parsing logs |
| **Python API** | Programmatic access via `LogStore`, `LogQuery` |

## Documentation

- [Getting Started](docs/getting-started.md)
- [MCP Guide](docs/mcp.md) — AI agent integration
- [Query Guide](docs/query-guide.md) — Filtering, SQL, output formats
- [Integration](docs/integration.md) — CI/CD, hooks, shell completions
- [Python API](docs/python-api.md)
- [Commands Reference](docs/commands/)

## License

MIT
