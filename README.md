# blq - Build Log Query

A CLI tool for capturing, querying, and analyzing build/test logs using [DuckDB](https://duckdb.org) and 
the [duck_hunt](https://duckdb.org/community_extensions/extensions/duck_hunt) extension. We pronouce 
`blq` like "bleak", as in we have bleak outlook on the outcome of our hunt through the logs.

## Features

- **Capture logs** from commands, files, or stdin
- **Query directly** with SQL or simple filter syntax
- **Structured output** in JSON, CSV, or Markdown for agent integration
- **Event references** for drilling into specific errors
- **Command registry** for reusable build/test commands
- **Parameterized commands** with `{placeholder}` syntax and defaults
- **Live inspection** - monitor running commands with `--follow` and `--status`
- **Run metadata** - captures git, environment, system, and CI context
- **User configuration** at `~/.config/blq/config.toml` for global preferences
- **MCP server** for AI agent integration
- **60+ log formats** supported via duck_hunt extension

## Installation

```bash
pip install blq-cli
```

Initialize in your project (installs duck_hunt extension, adds `.lq/` to `.gitignore`):
```bash
blq init                     # Basic init (adds .lq/ to .gitignore)
blq init --detect --yes      # Auto-detect and register build/test commands
blq init --no-gitignore      # Skip .gitignore modification
blq mcp install              # Create .mcp.json for AI agents
```

## Quick Start

```bash
# Query a log file directly
blq q build.log
blq q -s ref_file,ref_line,message build.log

# Filter with simple syntax
blq f severity=error build.log
blq f severity=error,warning ref_file~main build.log

# Run and capture a command
blq run make -j8
blq run --json make test

# View recent errors and history
blq errors
blq history                   # Show all runs
blq history test              # Filter by tag

# Drill into a specific error
blq event test:5              # All events from run test:5
blq event test:5:3            # Specific event
blq context test:5:3          # Log context around event

# Get run details
blq info test:5               # Details for a specific run
```

## Commands

### Querying

| Command | Description |
|---------|-------------|
| `blq query` / `blq q` | Query log files or stored events |
| `blq filter` / `blq f` | Filter with simple key=value syntax |
| `blq sql <query>` | Run arbitrary SQL |
| `blq shell` | Interactive SQL shell |

### Capturing

| Command | Description |
|---------|-------------|
| `blq run <cmd>` | Run registered command and capture output |
| `blq exec <cmd>` | Execute ad-hoc command and capture output |
| `blq import <file>` | Import existing log file |
| `blq capture` | Capture from stdin |

### Viewing

| Command | Description |
|---------|-------------|
| `blq errors` | Show recent errors |
| `blq warnings` | Show recent warnings |
| `blq events` | Show events with severity filter |
| `blq event <ref>` | Show event details or all events from a run |
| `blq context <ref>` | Show log context around event |
| `blq status` | Show status overview |
| `blq info <ref>` | Show detailed info for a run (supports UUID) |
| `blq history [tag]` | Show run history, optionally filtered |

### CI Integration

| Command | Description |
|---------|-------------|
| `blq ci check` | Compare errors against baseline, exit 0/1 for CI gates |
| `blq ci comment` | Post error summary as GitHub PR comment |
| `blq report` | Generate markdown report of build/test results |
| `blq watch` | Watch for file changes and auto-run commands |

### Management

| Command | Description |
|---------|-------------|
| `blq init` | Initialize .lq directory |
| `blq register` | Register a reusable command |
| `blq unregister` | Remove a registered command |
| `blq commands` | List registered commands |
| `blq config` | View/edit user configuration |
| `blq prune` | Remove old logs |
| `blq formats` | List available log formats |
| `blq completions` | Generate shell completions (bash/zsh/fish) |

### MCP & Hooks

| Command | Description |
|---------|-------------|
| `blq mcp install` | Create/update .mcp.json for AI agents |
| `blq mcp install --hooks` | Also install Claude Code hooks |
| `blq mcp serve` | Start MCP server |
| `blq hooks install git` | Install git pre-commit hook |
| `blq hooks install claude-code` | Install Claude Code hooks for agent integration |
| `blq hooks uninstall <target>` | Remove hooks (git, github, gitlab, claude-code) |
| `blq hooks status` | Show hook status |
| `blq hooks generate <cmds>` | Generate portable hook scripts |

## Query Examples

```bash
# Select specific columns
blq q -s ref_file,ref_line,severity,message build.log

# Filter with SQL WHERE clause
blq q -f "severity='error' AND ref_file LIKE '%main%'" build.log

# Order and limit results
blq q -o "ref_line" -n 10 build.log

# Output as JSON (great for agents)
blq q --json build.log

# Output as CSV
blq q --csv build.log

# Query stored events (no file argument)
blq q -f "severity='error'"
```

## Filter Syntax

The `blq filter` command provides grep-like simplicity:

```bash
# Exact match
blq f severity=error build.log

# Multiple values (OR)
blq f severity=error,warning build.log

# Contains (LIKE)
blq f ref_file~main build.log

# Not equal
blq f severity!=info build.log

# Invert match (like grep -v)
blq f -v severity=error build.log

# Count matches
blq f -c severity=error build.log

# Case insensitive
blq f -i message~undefined build.log
```

## Structured Output for Agents

```bash
# JSON output with errors
blq run --json make

# Markdown summary
blq run --markdown make

# Quiet mode (no streaming, just results)
blq run --quiet --json make
```

Output includes event references for drill-down:
```json
{
  "run_id": 5,
  "status": "FAIL",
  "errors": [
    {
      "ref": "build:5:1",
      "ref_file": "src/main.c",
      "ref_line": 15,
      "message": "undefined variable 'foo'"
    }
  ]
}
```

References can then be used with `blq event build:5:1` or `blq context build:5:1`.

## Command Registry

Register frequently-used commands:

```bash
# Auto-detect commands from build files (Makefile, package.json, etc.)
blq init --detect --yes

# Or register manually
blq register build "make -j8" --description "Build project"
blq register test "pytest -v" --timeout 600
blq register format "black ." --no-capture  # Skip log capture for fast commands

# Run by name
blq run build
blq run test

# Run without log capture (fast mode for CI/pre-commit)
blq run --no-capture format

# List registered commands
blq commands
```

**Auto-detected build systems:** Makefile, package.json (npm/yarn), pyproject.toml, Cargo.toml, go.mod, CMakeLists.txt, configure, build.gradle, pom.xml, Dockerfile, docker-compose.yml

**Format auto-detection:** When registering commands, blq automatically detects the appropriate log format based on the command (e.g., `mypy` → `mypy_text`, `pytest` → `pytest_text`).

### Parameterized Commands

Commands can have placeholders that are filled at runtime:

```toml
# In .lq/commands.toml
[commands.test]
tpl = "pytest {path} {flags}"
defaults = { path = "tests/", flags = "-v" }
description = "Run tests"

[commands.deploy]
tpl = "kubectl apply -f {file} -n {namespace}"
defaults = { namespace = "default" }
# 'file' has no default, so it's required
```

**Placeholder syntax:**

| Syntax | Mode | Description |
|--------|------|-------------|
| `{name}` | Keyword-only, required | Must use `name=value` |
| `{name=default}` | Keyword-only, optional | Uses default if not provided |
| `{name:}` | Positional-able, required | Can use positional or `name=value` |
| `{name:=default}` | Positional-able, optional | Positional or keyword, with default |

**Usage:**

```bash
# Use defaults
blq run test                         # pytest tests/ -v

# Override with key=value
blq run test path=tests/unit/        # pytest tests/unit/ -v

# Override with --key=value
blq run test --flags="-vvs -x"       # pytest tests/ -vvs -x

# Positional args (for positional-able placeholders)
blq run deploy manifest.yaml prod    # kubectl apply -f manifest.yaml -n prod

# Passthrough extra args with -- or ::
blq run test -- --capture=no         # pytest tests/ -v --capture=no

# Dry run to see expanded command
blq run test --dry-run               # Shows: pytest tests/ -v

# Get help for a command
blq run test --help                  # Shows parameters and defaults
```

**Hooks and CI-detected commands:** When using `--detect`, blq may discover commands from CI config files (GitHub Actions, GitLab CI, etc.). Be aware that adding these to git hooks via `blq hooks add` could duplicate checks that already run in CI. Commands detected from local build files (Makefile, package.json scripts) are generally better candidates for pre-commit hooks.

## CI Integration

blq provides commands for CI/CD pipeline integration:

```bash
# Check for new errors vs baseline (exits 1 if new errors found)
blq ci check                          # Auto-detect baseline from main/master
blq ci check --baseline main          # Compare against specific branch
blq ci check --baseline 42            # Compare against run ID
blq ci check --fail-on-any            # Fail if any errors (no baseline)

# Post error summary as PR comment (requires GITHUB_TOKEN)
blq ci comment                        # Create new comment
blq ci comment --update               # Update existing blq comment
blq ci comment --diff --baseline main # Include diff vs baseline

# Generate markdown report
blq report                            # Report on latest run
blq report --baseline main            # Include comparison
blq report --output report.md         # Save to file
blq report --summary-only             # Summary without error details
```

### GitHub Actions Example

```yaml
- name: Run tests
  run: blq run test

- name: Check for regressions
  run: blq ci check --baseline main

- name: Post results
  if: github.event_name == 'pull_request'
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: blq ci comment --update --diff
```

## Watch Mode

Automatically run commands when files change:

```bash
blq watch build              # Watch and run 'build' on changes
blq watch test --debounce 500  # Custom debounce (ms)
blq watch lint --exclude "*.log,dist/*"  # Exclude patterns
blq watch --once build       # Run once then exit (for CI)
```

## Live Inspection

Inspect long-running commands while they're still executing:

```bash
# Filter history by status
blq history --status=running     # Show only running commands
blq history --status=completed   # Show only completed commands
blq history --status=orphaned    # Show crashed commands (no exit code)

# View output from a running command
blq info build:5 --tail=50       # Last 50 lines of output
blq info build:5 --head=20       # First 20 lines

# Follow output in real-time (like tail -f)
blq info build:5 --follow        # Stream live output
blq info build:5 --follow --tail=20  # Show last 20 lines, then follow
```

This works with the MCP server too - agents can monitor running builds and get partial results before completion.

## Run Metadata

Each `blq run` automatically captures execution context:

| Field | Description |
|-------|-------------|
| `hostname` | Machine name |
| `platform` | OS (Linux, Darwin, Windows) |
| `arch` | Architecture (x86_64, arm64) |
| `git_commit` | Current commit SHA |
| `git_branch` | Current branch |
| `git_dirty` | Uncommitted changes present |
| `environment` | Captured env vars (PATH, VIRTUAL_ENV, etc.) |
| `ci` | CI provider info (auto-detected) |

Query metadata with SQL:
```bash
blq sql "SELECT hostname, git_branch, environment['VIRTUAL_ENV'] FROM blq_load_events()"
```

## User Configuration

blq supports user-level configuration at `~/.config/blq/config.toml`:

```toml
[init]
auto_mcp = true           # Create .mcp.json on init (default: true if fastmcp installed)
auto_gitignore = true     # Add .lq/ to .gitignore
auto_detect = false       # Auto-detect commands on init

[register]
auto_init = true          # Auto-init .lq/ when registering commands

[mcp]
safe_mode = false         # Default to safe mode for MCP server

[hooks]
auto_claude_code = true   # Auto-install Claude Code hooks with mcp install

[defaults]
extra_capture_env = ["MY_CUSTOM_VAR"]  # Additional env vars to capture
```

Manage configuration via CLI:

```bash
blq config                    # Show non-default settings
blq config --all              # Show all settings with defaults
blq config set hooks.auto_claude_code true
blq config get init.auto_mcp
blq config unset register.auto_init
blq config --edit             # Open in $EDITOR
```

With `auto_init = true`, you can register commands without explicitly running `blq init` first:

```bash
cd new-project
blq register build "make -j8"  # Auto-initializes .lq/ with notice
```

## MCP Server

blq includes an MCP server for AI agent integration:

```bash
blq mcp install              # Create .mcp.json config
blq mcp install --hooks      # Also install Claude Code hooks
blq mcp serve                # stdio transport (Claude Desktop)
blq mcp serve --transport sse  # HTTP/SSE transport
```

Tools available: `run`, `query`, `events`, `inspect`, `output`, `status`, `info`, `history`, `diff`, `commands`, `register_command`, `unregister_command`, `clean`

**Security:** Disable sensitive tools via config:
```toml
# .lq/config.toml
[mcp]
disabled_tools = ["clean", "register_command"]
```

See [MCP Guide](docs/mcp.md) for details.

## Claude Code Integration

blq integrates with Claude Code via hooks that help agents use blq's structured output instead of raw Bash:

```bash
blq hooks install claude-code    # Install suggest hook
blq hooks uninstall claude-code  # Remove hooks
```

The suggest hook runs after Bash commands and notifies Claude when a registered blq command could have been used instead:

```
Tip: Use blq MCP tool run(command="test") instead.
Using the blq MCP run tool parses output into structured events,
reducing context usage. Query errors with events() or inspect().
```

**Auto-install with MCP:** Set `hooks.auto_claude_code = true` in user config, then hooks install automatically with `blq mcp install`:

```bash
blq config set hooks.auto_claude_code true
blq mcp install  # Now includes Claude Code hooks
```

## Global Options

| Flag | Description |
|------|-------------|
| `-V, --version` | Show version number |
| `-F, --log-format` | Log format hint (default: auto) |

## Python API

blq provides a Python API for programmatic access:

```python
from blq.storage import BlqStorage

# Open the repository
storage = BlqStorage.open()

# Query runs and events (returns DuckDB relations)
runs = storage.runs().df()              # Get as DataFrame
errors = storage.errors(limit=10).df()  # Recent errors

# Filter events
storage.events(severity="error")              # By severity
storage.events(run_id=1)                      # By run
storage.events(severity=["error", "warning"]) # Multiple severities

# Check for data
if storage.has_data():
    latest = storage.latest_run_id()
    print(f"Latest run: {latest}")

# Write a new run
run_id = storage.write_run(
    {"command": "make", "source_name": "build", "source_type": "run", "exit_code": 0},
    events=[{"severity": "error", "message": "undefined reference"}],
)

# Raw SQL queries
result = storage.sql("SELECT * FROM blq_status()").fetchall()
```

See [Python API Guide](docs/python-api.md) for full documentation.

## Storage

blq uses [BIRD](https://magic-bird-shq.readthedocs.io/en/latest/bird_spec/) (Buffer and Invocation Record Database) for storage. Data is stored in DuckDB tables with content-addressed blob storage for outputs:

```
.lq/
├── blq.duckdb     # Database with tables and SQL macros
├── blobs/         # Content-addressed output storage
│   └── content/
│       └── ab/
│           └── {hash}.bin
├── raw/           # Optional raw logs (--keep-raw)
├── config.toml    # Project configuration
├── commands.toml  # Registered commands
└── schema.sql     # SQL schema reference
```

### Tables

| Table | Description |
|-------|-------------|
| `invocations` | Command executions (runs) with metadata |
| `events` | Parsed diagnostics (errors, warnings) |
| `outputs` | Captured stdout/stderr references |
| `sessions` | CLI/MCP session tracking |

### SQL Macros (blq_ prefix)

All SQL macros use the `blq_` prefix:

```bash
# Direct DuckDB access
duckdb .lq/blq.duckdb "SELECT * FROM blq_status()"
duckdb .lq/blq.duckdb "SELECT * FROM blq_errors(20)"
duckdb .lq/blq.duckdb "SELECT * FROM blq_load_events() WHERE severity='error'"
```

| Macro | Description |
|-------|-------------|
| `blq_load_events()` | All events with run metadata |
| `blq_load_runs()` | Runs with aggregated event counts |
| `blq_status()` | Quick status overview |
| `blq_errors(n)` | Recent errors (default: 10) |
| `blq_warnings(n)` | Recent warnings (default: 10) |
| `blq_history(n)` | Run history (default: 20) |
| `blq_diff(run1, run2)` | Compare errors between runs |

## Documentation

See [docs/](docs/) for detailed documentation:

- [Getting Started](docs/getting-started.md)
- [Commands Reference](docs/commands/)
- [Query Guide](docs/query-guide.md)
- [Python API Guide](docs/python-api.md)
- [Integration Guide](docs/integration.md)

## License

MIT
