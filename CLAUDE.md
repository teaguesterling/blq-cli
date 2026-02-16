# blq Development Notes

## Project Status

This is the initial scaffolding for `blq` (Build Log Query) - a CLI tool for capturing, storing, and querying build/test logs using DuckDB.

### Completed
- Python package structure with pyproject.toml (hatchling build)
- CLI module (`src/blq/cli.py`) with all core commands
- SQL schema with table-returning macros (`src/blq/schema.sql`)
- blq.duckdb database file with pre-loaded macros
- **BIRD storage backend** (DuckDB tables with content-addressed blob storage)
- Migration command (`blq migrate --to-bird`) for parquet to BIRD conversion
- Basic error/warning parsing fallback
- Integration hooks for duck_hunt extension
- Pythonic query API (`LogQuery`, `LogStore`, `LogQueryGrouped`)
- Structured output (JSON, Markdown, CSV)
- Command registry for reusable build/test commands
- Query and filter commands for direct log file inspection
- MCP server (`blq mcp serve`) for AI agent integration
- MCP security controls (disable sensitive tools via config)
- Run metadata capture (environment, git, system, CI context)
- Project detection from git remote or filesystem path
- Command auto-detection from build files (`blq init --detect`)
- Capture/no-capture mode for fast execution (`blq run --no-capture`)
- Ad-hoc command execution (`blq exec`) - run without registry
- Shell completions for bash, zsh, fish (`blq completions`)
- List available log formats (`blq formats`)
- Version flag (`blq --version`)
- Watch mode for continuous capture (`blq watch`) - Issue #7
- CI integration commands (`blq ci check`, `blq ci comment`) - Issue #8
- Report generation (`blq report`) - markdown summaries with baseline comparison
- Format auto-detection for registered commands (e.g., `mypy` → `mypy_text`)
- Output stats in run results (lines, bytes, tail) for visibility
- **Terminal-friendly output formatting** with smart column selection
- **History filtering** (`blq history test` or `blq history -t test`)
- **Run details** via `blq info <ref>` (supports run refs and UUIDs)
- **Flexible event refs** (run_id, run_id:event_id, tag:run_id, tag:run_id:event_id)
- **Run events** via `blq event <run_ref>` shows all events from a run
- **Automatic .gitignore** handling in `blq init` (`--gitignore`/`--no-gitignore`)
- **Inspect command** with dual context (log + source)
- **Consolidated MCP tools** (reduced from 22 to 12 tools)
- **CLI command subgroups** (`blq commands list/register/unregister`)
- **Clean command** (`blq clean data/prune/schema/full`) for database maintenance
- **Timeout handling** for command execution with partial output capture
- **Verbose mode** (`-v`) for run/exec with summary output
- **MCP safe mode** (`--safe-mode`/`--disabled-tools` for `blq mcp serve`)
- **Blob cleanup** in prune mode (orphaned content-addressed blobs)
- **Query filter syntax** (`query(filter="severity=error ref_file~test")`)
- **Parameterized commands** with `tpl` templates and `defaults` (see design doc)
- **TOML config format** (`config.toml`, `commands.toml`)
- **User configuration** at `~/.config/blq/config.toml` for global preferences
- **`blq config` command** for viewing/editing user configuration
- **Auto-init on register** when `auto_init = true` in user config
- **Live inspection** of running commands (attempts/outcomes architecture)
- **History status filter** (`blq history --status=running/completed/orphaned`)
- **Live output viewing** (`blq info <ref> --tail/--head/--follow`)
- **Claude Code hooks** integration (`blq hooks install claude-code`)
- **`hooks.auto_claude_code`** config for auto-installing Claude Code hooks
- **Dry-run mode** (`blq run <cmd> --dry-run`) to preview expanded commands
- **Unified git module** (`blq.git`) with provider abstraction (subprocess + duck_tails)
- **Event enrichment** for inspect command (`--source`, `--git`, `--fingerprint`, `--full`)
- **Info summaries** for failed runs (`by_fingerprint`, `by_file`, `affected_commits`)
- **Conditional tail** in run output (2 lines with errors, full without, none on success)
- **Fingerprint field** included in events output
- **Source name** in run results for correct run_ref construction
- **Database lock contention handling** with retry logic and exponential backoff
- **Minimized DB lock time** during command execution (Window 1/Window 2 pattern)
- **`blq output` enhancements**:
  - `--grep/-g PATTERN` for searching log content with regex
  - `--context/-C N` for context lines around grep matches
  - `--lines/-l SPEC` for line selection (e.g., '100-200', '42 +/-5')
  - `--debug-formats` to show format detection diagnosis
- **`blq_read_lines` SQL macro** for line selection with markers (requires read_lines extension)
- **`blq_search_lines` SQL macro** for regex search with context
- **`--compact` output mode** for run/exec commands (adaptive event vs raw output)
- **Template command support** in MCP `register_command` (`tpl` and `defaults` params)
- Full mypy type checking compliance
- 730+ unit tests
- Comprehensive documentation (README, docs/)

### TODO

**Features:**
- [ ] Implement sync feature (see `docs/design-sync.md`) - Issue #21
- [ ] Plugin system for adding commands or extra fields to existing commands

**Architecture:**
- [ ] Refactor MCP and CLI command processors into unified service layer
- [ ] Consider integration with duckdb_mcp for ATTACH/DETACH workflow

**BIRD Spec:**
- [ ] Migrate from `.lq/` to `.bird/` directory (pending spec finalization)
- [ ] Running process tracking (pending BIRD spec)
- [ ] Migrate to updated BIRD spec (when ready)

**Maintenance:**
- [ ] Configurable autoprune (periodic cleanup with predefined limits)

## Architecture

```
blq (Python CLI)
    │
    ├── .lq/blq.duckdb     - BIRD database with tables and macros
    │   ├── sessions       - Invoker sessions (shell, CLI, MCP)
    │   ├── invocations    - Command executions with metadata
    │   ├── outputs        - Captured stdout/stderr (content-addressed)
    │   └── events         - Parsed diagnostics (errors, warnings)
    │
    ├── .lq/blobs/         - Content-addressed blob storage
    │   └── content/ab/{hash}.bin
    │
    ├── Uses duckdb Python API directly
    │
    └── Optionally uses duck_hunt extension for 60+ format parsing
```

### Storage Modes

BIRD is the default storage mode. Legacy parquet mode is still supported:

| Mode | Storage | Use Case |
|------|---------|----------|
| **BIRD** (default) | DuckDB tables + blobs | Single-writer CLI, simpler queries |
| Parquet (legacy) | Hive-partitioned files | Multi-writer scenarios |

```bash
blq init                    # Uses BIRD by default
blq init --parquet          # Use legacy parquet mode
blq migrate --to-bird       # Convert parquet to BIRD
```

### SQL Schema (blq_ prefix)

All SQL macros use the `blq_` prefix:

| Macro | Description |
|-------|-------------|
| `blq_load_events()` | Load all events from parquet files |
| `blq_load_runs()` | Aggregated run statistics |
| `blq_status()` | Quick status overview |
| `blq_errors(n)` | Recent errors (default: 10) |
| `blq_warnings(n)` | Recent warnings (default: 10) |
| `blq_history(n)` | Run history (default: 20) |
| `blq_diff(run1, run2)` | Compare two runs |
| `blq_read_lines(content, lines_spec, marks)` | Line selection with markers (requires read_lines) |
| `blq_search_lines(content, pattern, ctx)` | Regex search with context (requires read_lines) |

Direct DuckDB access:
```bash
duckdb .lq/blq.duckdb "SELECT * FROM blq_status()"
```

## Run Metadata

Each `blq run` captures comprehensive execution context:

| Field | Type | Description |
|-------|------|-------------|
| `cwd` | VARCHAR | Working directory |
| `executable_path` | VARCHAR | Full path to command executable |
| `environment` | MAP(VARCHAR, VARCHAR) | Captured env vars (configurable) |
| `hostname` | VARCHAR | Machine hostname |
| `platform` | VARCHAR | OS (Linux, Darwin, Windows) |
| `arch` | VARCHAR | Architecture (x86_64, arm64) |
| `git_commit` | VARCHAR | HEAD SHA |
| `git_branch` | VARCHAR | Current branch |
| `git_dirty` | BOOLEAN | Uncommitted changes present |
| `ci` | MAP(VARCHAR, VARCHAR) | CI provider + context (auto-detected) |

### Environment Capture

Configurable in `.lq/config.toml`:
```toml
capture_env = [
    "PATH",
    "VIRTUAL_ENV",
    "CC",
    "CXX",
    # ... (30+ defaults)
]
```

Per-command overrides in `commands.toml`:
```toml
[commands.build]
cmd = "make -j8"
capture_env = ["EXTRA_VAR"]
```

### CI Auto-Detection

Supports: GitHub Actions, GitLab CI, Jenkins, CircleCI, Travis CI, Buildkite, Azure Pipelines

```sql
SELECT ci['provider'], ci['run_id'] FROM blq_load_events() WHERE ci IS NOT NULL
```

## Project Identification

Detected at `blq init` and stored in `.lq/config.toml`:

```toml
[project]
namespace = "teaguesterling"  # from git remote owner
project = "blq"               # from git remote repo
```

Fallback for non-git projects uses filesystem path:
- `/home/user/Projects/myapp` → `namespace=home__user__Projects, project=myapp`

## Command Auto-Detection

`blq init --detect` scans for build system files and registers appropriate commands:

| File | Commands |
|------|----------|
| `Makefile` | build, test, clean |
| `yarn.lock` | build, test, lint (yarn, if scripts exist) |
| `package.json` | build, test, lint (npm, if scripts exist) |
| `pyproject.toml` | test (pytest), lint (ruff) |
| `Cargo.toml` | build, test |
| `go.mod` | build, test |
| `CMakeLists.txt` | build, test |
| `configure` | configure |
| `configure.ac` | autoreconf |
| `build.gradle` | build, test, clean (gradlew) |
| `pom.xml` | build, test, clean (mvn) |
| `Dockerfile` | docker-build |
| `docker-compose.yml` | docker-up, docker-build |

Commands can have `capture: false` for fast execution without log parsing:
```toml
[commands.format]
cmd = "black ."
capture = false  # Skip log capture
```

Runtime override: `blq run --no-capture <cmd>` or `blq run --capture <cmd>`

## Key Design Decisions

1. **BIRD as default storage**: DuckDB tables for simpler queries, content-addressed blobs for outputs
2. **Parquet mode available**: For multi-writer scenarios (legacy, use `--parquet` flag)
3. **Project-local storage**: `.lq/` directory in project root
4. **blq.duckdb for everything**: Tables, views, and macros in single database file
5. **Table-returning macros**: `blq_load_events()` evaluated at query time, not view creation
6. **Backward-compatible views**: `blq_events_flat` provides v1-compatible schema
7. **Optional duck_hunt**: Works with basic parsing if extension not available
8. **Python duckdb API**: No subprocess calls to duckdb CLI
9. **Content-addressed blobs**: Output deduplication with BLAKE2b hashing
10. **JSON for variable data**: Environment and CI stored as JSON in BIRD mode
11. **Minimized lock time**: DB connection opened briefly for writes, closed during subprocess execution
12. **Retry with backoff**: Lock contention handled with exponential backoff and jitter

## Reference Naming Scheme

Events and runs use a human-friendly reference format:

| Field | Format | Example | Description |
|-------|--------|---------|-------------|
| `run_ref` | `tag:serial` or `serial` | `build:1`, `3` | Human-friendly run identifier |
| `ref` | `tag:serial:event` or `serial:event` | `build:1:2`, `3:5` | Full event reference |
| `run_serial` | integer | `1`, `2`, `3` | Sequential run number |
| `event_id` | integer | `1`, `2` | Event index within run |

The `tag` is set from the command's `source_name` (e.g., "build", "test").

Examples:
- `build:1` - First run of the "build" command
- `build:1:3` - Third error in the first build run
- `5:2` - Second error in run 5 (no tag)

## MCP Server

The MCP server (`blq mcp serve`) provides tools for AI agents:

```bash
blq mcp install              # Create .mcp.json config
blq mcp serve                # Start MCP server (stdio)
blq mcp serve --transport sse  # SSE transport
```

### MCP Tools (Consolidated API)

| Tool | Description |
|------|-------------|
| `run` | Run registered command(s) - supports batch mode via `commands` param |
| `query` | Query events with SQL or filter expressions (`filter="severity=error"`) |
| `events` | Get events with severity/run filters - supports batch mode via `run_ids` param |
| `inspect` | Get comprehensive event details with context - supports batch mode via `refs` param |
| `output` | Get raw output for a run |
| `status` | Get status summary |
| `info` | Get detailed run info (omit `ref` for most recent, `context=N` for log context around errors, includes `summary` with aggregations for failed runs) |
| `history` | Get run history |
| `diff` | Compare errors between runs |
| `commands` | List all registered commands |
| `register_command` | Register a command (idempotent, with run_now option) |
| `unregister_command` | Remove a registered command |
| `clean` | Database cleanup (modes: data, prune, schema, full) |

### MCP Security

Disable tools via CLI flags:
```bash
blq mcp serve --safe-mode           # Disables exec, clean, register_command, unregister_command
blq mcp serve -D exec,clean         # Disable specific tools
blq mcp serve -S -D custom_tool     # Combine safe mode with additional tools
```

Or via `.lq/config.toml`:
```toml
[mcp]
disabled_tools = ["clean", "register_command", "unregister_command"]
```

Or via environment: `BLQ_MCP_DISABLED_TOOLS=clean,exec`

### MCP Resources

| Resource | Description |
|----------|-------------|
| `blq://guide` | Agent usage guide |
| `blq://status` | Current status (JSON) |
| `blq://errors` | Recent errors (JSON) |
| `blq://warnings` | Recent warnings (JSON) |
| `blq://context/{ref}` | Log context around event |
| `blq://commands` | Registered commands |

## CLI Commands

### Command Subgroups

Several commands now use subgroups for better organization:

```bash
# Commands management
blq commands list              # List registered commands
blq commands register NAME CMD # Register a new command
blq commands unregister NAME   # Remove a command
blq commands                   # Alias for 'list'

# MCP server
blq mcp install                # Create .mcp.json
blq mcp serve                  # Start MCP server

# Git hooks
blq hooks install              # Install pre-commit hook
blq hooks remove               # Remove hook
blq hooks status               # Show hook status
blq hooks add CMD              # Add command to hook
blq hooks list                 # List commands in hook

# CI integration
blq ci check                   # Check for new errors
blq ci comment                 # Post PR comment
```

### Quick Reference

```bash
# Initialize
blq init [--detect] [--mcp]

# Run commands
blq run <command>              # Run registered command
blq run <command> -j           # JSON output

# Query results
blq status                     # Overview
blq errors                     # Recent errors
blq events --severity error    # Same as errors
blq history                    # Run history
blq info <ref>                 # Run details
blq info                       # Most recent run (no ref)
blq inspect <ref>              # Event with context

# Direct query
blq sql "SELECT * FROM blq_load_events() LIMIT 10"
blq query -f "severity='error'" build.log
```

## Integration Points

- **duck_hunt extension**: For enhanced log parsing (60+ formats)
- **duckdb_mcp**: For MCP server integration (agents can query logs)

## Related Projects

- `../duck_hunt/` - DuckDB extension for log parsing
- `../duckdb_mcp/` - MCP server extension for DuckDB

## Development

### Running Tests

```bash
blq run test-all               # Run all tests via blq
pytest tests/                  # Run directly
pytest tests/test_mcp_server.py -v  # Specific test file
```

### Type Checking

```bash
mypy src/blq/
```

### Linting

```bash
ruff check src/blq/
ruff format src/blq/
```

### Config Options

Key `.lq/config.toml` options:
```toml
[storage]
keep_raw = true               # Always keep raw output

[source_lookup]
enabled = true                # Enable source context in inspect
ref_root = "."                # Root for resolving file paths

[mcp]
disabled_tools = ["clean"]    # Security: disable sensitive tools
```
