# blq Agent Interface

This document describes how AI agents should interact with `blq` for efficient log analysis.

## Token-Efficient Workflow

**Problem**: Raw build logs can be 10MB+, burning context when agents just need "what failed?"

**Solution**: Use `blq` commands for structured, minimal-token queries.

## Running Commands

| Command | Purpose |
|---------|---------|
| `blq run <name>` | Run a registered command (fails if not found) |
| `blq run -R <cmd>` | Register and run in one step |
| `blq exec <cmd>` | Run any shell command (ad-hoc) |

```bash
# Check what's registered
blq commands

# Run registered command
blq run --json --quiet build

# Run ad-hoc command
blq exec --json --quiet make -j8
```

### Quick Status Check (~10 tokens output)
```bash
blq status
```
Output:
```
[ OK ] make
[FAIL] gh run 123
[WARN] eslint
```

### Get Errors (~200 tokens for 5 errors)
```bash
blq errors --limit 5
```

### Compact Format (~100 tokens for 10 errors)
```bash
blq errors --compact --limit 10
```
Output:
```
src/main.cpp:42:5: undefined reference to 'foo'
src/utils.cpp:15:1: missing semicolon
```

### JSON for Programmatic Use
```bash
blq errors --json --limit 5
```

## MCP Tools

blq provides a built-in MCP server (`blq mcp serve`). Core tools:

| Tool | Description | Token Cost |
|------|-------------|------------|
| `status` | Quick status badges | ~10 |
| `events` | Errors/warnings with filtering | ~200 |
| `info` | Run details with context | ~300 |
| `inspect` | Event details with source context | ~200 |
| `run` | Run command, get structured results | Variable |
| `query` | SQL or filter expressions | Variable |

## Recommended Agent Workflow

1. **Start with status**: `blq status` - see if anything failed
2. **Drill into failures**: `blq errors --limit 5`
3. **Get context**: `blq inspect <ref>` - see error with surrounding code
4. **Compare runs**: `blq diff <run1> <run2>` - what changed?

## SQL Macros Available

```sql
-- Quick queries
FROM blq_status();           -- Status badges
FROM blq_errors(10);         -- Recent errors
FROM blq_warnings(10);       -- Recent warnings

-- Filtered queries
FROM blq_errors_for('make', 5);  -- Errors for specific source

-- History
FROM blq_history(20);        -- Run history
FROM blq_diff(run1, run2);   -- Compare two runs
```

## Storage

Logs are stored in `.lq/` using BIRD storage (DuckDB tables with content-addressed blobs):

```
.lq/
├── blq.duckdb           # DuckDB database (events, runs, metadata)
├── blobs/content/        # Content-addressed blob storage
├── config.toml           # Project configuration
└── commands.toml         # Registered commands
```

Direct database access:
```sql
duckdb .lq/blq.duckdb "SELECT * FROM blq_load_events() WHERE severity = 'error'"
```
