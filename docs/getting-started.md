# Getting Started

## Installation

### From PyPI

```bash
pip install blq-cli
```

### From Source

```bash
git clone https://github.com/yourusername/lq.git
cd lq
pip install -e .
```

## Initialize Your Project

Run `blq init` in your project directory:

```bash
cd my-project
blq init
```

This creates a `.lq/` directory and installs the `duck_hunt` extension for log parsing.

```
Initialized .lq at /path/to/my-project/.lq
  blq.duckdb    - DuckDB database with tables and macros
  blobs/        - Content-addressed output storage
  config.toml   - Project configuration
  commands.toml - Registered commands
  duck_hunt     - Installed successfully
```

**Options:**

```bash
blq init --detect --yes      # Auto-detect and register commands
blq init --no-gitignore      # Skip .gitignore modification
blq init --no-mcp            # Skip .mcp.json creation
```

## Your First Query

### Query a Log File Directly

If you have an existing log file:

```bash
blq q build.log
```

Select specific columns:

```bash
blq q -s ref_file,ref_line,severity,message build.log
```

Filter for errors:

```bash
blq f severity=error build.log
```

### Run and Capture

Run a command and capture its output:

```bash
blq run make -j8
```

This:
1. Runs `make -j8`
2. Parses the output for errors/warnings
3. Stores events in `.lq/logs/`
4. Prints a summary

### View Results

```bash
# Recent errors
blq errors

# All warnings
blq warnings

# Overall status
blq status
```

## Output Formats

### Default Table

```bash
blq q -s ref_file,severity,message build.log
```

```
  ref_file severity                  message
 src/main.c    error undefined variable 'foo'
src/utils.c    error        missing semicolon
```

### JSON

```bash
blq q --json build.log
```

```json
[
  {"ref_file": "src/main.c", "severity": "error", "message": "undefined variable 'foo'"},
  {"ref_file": "src/utils.c", "severity": "error", "message": "missing semicolon"}
]
```

### CSV

```bash
blq q --csv build.log
```

### Markdown

```bash
blq q --markdown build.log
```

## Shell Completions

Enable tab completion for your shell:

```bash
# Bash (add to ~/.bashrc)
eval "$(blq completions bash)"

# Zsh (add to ~/.zshrc)
eval "$(blq completions zsh)"

# Fish
blq completions fish > ~/.config/fish/completions/blq.fish
```

## Parameterized Commands

Commands can have placeholders that are filled at runtime:

```bash
# Register a parameterized command
blq register test "pytest {path:=tests/} {flags:=-v}"

# Use with defaults
blq run test                    # pytest tests/ -v

# Override parameters
blq run test path=tests/unit/   # pytest tests/unit/ -v
blq run test tests/unit/ -x     # pytest tests/unit/ -v -x (positional + extra)

# See what command will run
blq run test --dry-run          # Shows: pytest tests/ -v
```

See the [README](../README.md#parameterized-commands) for full placeholder syntax.

## Live Inspection

Monitor long-running commands while they're still running:

```bash
# See running commands
blq history --status=running

# Follow output in real-time
blq info build:5 --follow

# Get last N lines from a running command
blq info build:5 --tail=50
```

## Next Steps

- [Commands Reference](commands/) - Learn all available commands
- [Query Guide](query-guide.md) - Master querying techniques
- [Integration Guide](integration.md) - Use with AI agents
- [MCP Guide](mcp.md) - AI agent integration via MCP
