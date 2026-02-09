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
- MCP server (`blq serve`) for AI agent integration
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
- Full mypy type checking compliance
- 340+ unit tests
- Comprehensive documentation (README, docs/)

### TODO
- [ ] Implement sync feature (see `docs/design-sync.md`)
- [ ] Consider integration with duckdb_mcp for ATTACH/DETACH workflow

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

Configurable in `.lq/config.yaml`:
```yaml
capture_env:
  - PATH
  - VIRTUAL_ENV
  - CC
  - CXX
  # ... (30+ defaults)
```

Per-command overrides in `commands.yaml`:
```yaml
commands:
  build:
    cmd: "make -j8"
    capture_env:
      - EXTRA_VAR
```

### CI Auto-Detection

Supports: GitHub Actions, GitLab CI, Jenkins, CircleCI, Travis CI, Buildkite, Azure Pipelines

```sql
SELECT ci['provider'], ci['run_id'] FROM blq_load_events() WHERE ci IS NOT NULL
```

## Project Identification

Detected at `blq init` and stored in `.lq/config.yaml`:

```yaml
project:
  namespace: teaguesterling  # from git remote owner
  project: blq               # from git remote repo
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
```yaml
commands:
  format:
    cmd: "black ."
    capture: false  # Skip log capture
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

## MCP Server Tools

The MCP server (`blq serve`) provides these tools for AI agents:

| Tool | Description |
|------|-------------|
| `run` | Run a registered command |
| `exec` | Execute an ad-hoc shell command |
| `query` | Query stored events with SQL |
| `errors` | Get recent errors |
| `warnings` | Get recent warnings |
| `event` | Get details for a specific event by ref |
| `context` | Get log context around an event |
| `status` | Get status summary |
| `history` | Get run history |
| `diff` | Compare errors between runs |
| `register_command` | Register a new command (with format auto-detection) |
| `unregister_command` | Remove a registered command |
| `list_commands` | List all registered commands |
| `reset` | Reset database (modes: data, schema, full) |

## Integration Points

- **duck_hunt extension**: For enhanced log parsing (60+ formats)
- **duckdb_mcp**: For MCP server integration (agents can query logs)

## Related Projects

- `../duck_hunt/` - DuckDB extension for log parsing
- `../duckdb_mcp/` - MCP server extension for DuckDB
