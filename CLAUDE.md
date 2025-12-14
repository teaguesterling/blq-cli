# lq Development Notes

## Project Status

This is the initial scaffolding for `lq` (Log Query) - a CLI tool for capturing, storing, and querying build/test logs using DuckDB.

### Completed
- Python package structure with pyproject.toml (hatchling build)
- CLI module (`src/lq/cli.py`) with all core commands
- SQL schema with views and macros (`src/lq/schema.sql`)
- Hive-partitioned parquet storage design
- Basic error/warning parsing fallback
- Integration hooks for duck_hunt extension
- Pythonic query API (`LogQuery`, `LogStore`, `LogQueryGrouped`)
- Structured output (JSON, Markdown, CSV)
- Command registry for reusable build/test commands
- Query and filter commands for direct log file inspection
- MCP server (`lq serve`) for AI agent integration
- Run metadata capture (environment, git, system, CI context)
- Project detection from git remote or filesystem path
- Command auto-detection from build files (`lq init --detect`)
- Capture/no-capture mode for fast execution (`lq run --no-capture`)
- 173 unit tests
- Comprehensive documentation (README, docs/)

### TODO
- [ ] Implement sync feature (see `docs/design-sync.md`)
- [ ] Consider integration with duckdb_mcp for ATTACH/DETACH workflow

## Architecture

```
lq (Python CLI)
    │
    ├── Writes parquet files to .lq/logs/date=.../source=.../
    │
    ├── Uses duckdb Python API directly
    │
    └── Optionally uses duck_hunt extension for 44+ format parsing
```

## Run Metadata

Each `lq run` captures comprehensive execution context:

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
SELECT ci['provider'], ci['run_id'] FROM lq_events WHERE ci IS NOT NULL
```

## Project Identification

Detected at `lq init` and stored in `.lq/config.yaml`:

```yaml
project:
  namespace: teaguesterling  # from git remote owner
  project: lq                # from git remote repo
```

Fallback for non-git projects uses filesystem path:
- `/home/user/Projects/myapp` → `namespace=home__user__Projects, project=myapp`

## Command Auto-Detection

`lq init --detect` scans for build system files and registers appropriate commands:

| File | Commands |
|------|----------|
| `Makefile` | build, test, clean |
| `package.json` | build, test, lint (if scripts exist) |
| `pyproject.toml` | test (pytest), lint (ruff) |
| `Cargo.toml` | build, test |
| `go.mod` | build, test |
| `CMakeLists.txt` | build, test |
| `Dockerfile` | docker-build |
| `docker-compose.yml` | docker-up, docker-build |

Commands can have `capture: false` for fast execution without log parsing:
```yaml
commands:
  format:
    cmd: "black ."
    capture: false  # Skip log capture
```

Runtime override: `lq run --no-capture <cmd>` or `lq run --capture <cmd>`

## Key Design Decisions

1. **Parquet over DuckDB files**: Enables concurrent writes without locking
2. **Hive partitioning**: Efficient date/source-based queries
3. **Project-local storage**: `.lq/` directory in project root
4. **Optional duck_hunt**: Works with basic parsing if extension not available
5. **Python duckdb API**: No subprocess calls to duckdb CLI
6. **MAP for variable data**: Environment and CI use MAP(VARCHAR, VARCHAR) for flexible keys
7. **PyYAML required**: Clean YAML handling without fallbacks

## Integration Points

- **duck_hunt extension**: For enhanced log parsing (60+ formats)
- **duckdb_mcp**: For MCP server integration (agents can query logs)

## Related Projects

- `../duck_hunt/` - DuckDB extension for log parsing
- `../duckdb_mcp/` - MCP server extension for DuckDB
