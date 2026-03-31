# blq Documentation

**blq** (Build Log Query) turns build output into a queryable database. Instead of scrolling through logs, ask questions: "What errors?", "What changed?", "Show me that file."

## Why blq?

- **Structured events** — Errors and warnings with file:line locations, not raw text
- **Run history** — Every build stored with git context, compare across runs
- **60+ formats** — GCC, Clang, pytest, mypy, ESLint, TypeScript, Rust, Go, and more
- **AI agent tools** — MCP server for structured access without log parsing

## Quick Start

```bash
pip install blq-cli
cd your-project
blq init --detect

blq run build
blq errors
blq inspect build:3:1
```

## Guides

| Guide | Description |
|-------|-------------|
| [Getting Started](getting-started.md) | Installation, setup, core workflows |
| [Query Guide](query-guide.md) | Filtering, SQL, output formats |
| [MCP Guide](mcp.md) | AI agent integration |
| [Sandbox Guide](sandbox.md) | Execution isolation, presets, profiling |
| [Integration](integration.md) | CI/CD, shell completions, hooks |
| [Python API](python-api.md) | Programmatic access |
| [API Reference](api-reference.md) | Full Python API and MCP tool schemas |
| [Plugin Guide](plugin-guide.md) | Writing extensions, engines, and annotators |
| [Schema Stability](schema-stability.md) | What's stable, migration paths |

## Commands

| Command | Description |
|---------|-------------|
| `blq run <cmd>` | Run registered command, capture output |
| `blq errors` | Recent errors |
| `blq inspect <ref>` | Event details with source context |
| `blq diff <r1> <r2>` | Compare runs |
| `blq history` | Run history |
| `blq info <ref>` | Run details |

See [Commands Reference](commands/) for all commands.

## Storage

Logs are stored in `.bird/` in your project:

```
.bird/
├── blq.duckdb      # DuckDB database
├── blobs/          # Content-addressed output storage
├── config.toml     # Project configuration
└── commands.toml   # Registered commands
```

## Event References

Every error gets a reference like `build:3:1`:
- `build` — command name
- `3` — run number
- `1` — event within run

Use refs to drill down: `blq inspect build:3:1`, `blq info build:3`
