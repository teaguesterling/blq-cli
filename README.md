# blq - Build Log Query

A CLI tool for capturing, querying, and analyzing build/test logs. blq parses 60+ log formats into structured data, stores run history with git context, and provides an MCP server for AI agent integration.

**Documentation:** https://blq-cli.readthedocs.io/

## Why blq?

Build tools output thousands of lines of text. Finding the actual errors means scrolling through noise. blq solves this by:

- **Parsing logs into structured events** - errors, warnings with file:line:column locations
- **Storing run history** - compare runs, track regressions, see what changed
- **Capturing context** - git commit, branch, environment, CI info for every run
- **Providing agent tools** - MCP server lets AI agents query errors without raw log parsing

## Installation

```bash
pip install blq-cli
```

## Project Setup

Initialize blq in your project:

```bash
cd your-project
blq init --detect
```

This will:
- Create `.lq/` directory for the database
- Add `.lq/` to `.gitignore`
- Auto-detect and register build/test commands from Makefile, package.json, pyproject.toml, etc.

### Register Commands

If auto-detect missed something, register commands manually:

```bash
blq register build "make -j8"
blq register test "pytest -v"
blq register lint "ruff check ."
```

### Setup for AI Agents (MCP)

```bash
blq mcp install
```

This creates `.mcp.json` for agent discovery. The MCP server provides tools like `run`, `events`, `inspect`, `diff` for structured build log access.

## Basic Usage

```bash
# Run a registered command
blq run build
blq run test

# View errors from the last run
blq errors

# See run history
blq history

# Get details on a specific run
blq info build:5

# Inspect a specific error with context
blq inspect build:5:1

# Compare two runs
blq diff 4 5
```

## Key Features

| Feature | Description |
|---------|-------------|
| **60+ log formats** | GCC, Clang, pytest, mypy, ESLint, TypeScript, Rust, Go, and more |
| **Run history** | Every run stored with git commit, branch, environment |
| **Event references** | `build:5:1` format for drilling into specific errors |
| **Structured output** | JSON, CSV, Markdown for scripts and agents |
| **MCP server** | AI agents can query errors without parsing raw logs |
| **CI integration** | `blq ci check` for regression detection, `blq ci comment` for PR comments |
| **Parameterized commands** | Templates with `{placeholder}` syntax and defaults |

## Documentation

For detailed guides and reference:

- **[Getting Started](https://blq-cli.readthedocs.io/en/latest/getting-started/)** - Installation and first steps
- **[CLI Reference](https://blq-cli.readthedocs.io/en/latest/cli/)** - All commands and options
- **[MCP Guide](https://blq-cli.readthedocs.io/en/latest/mcp/)** - AI agent integration
- **[Python API](https://blq-cli.readthedocs.io/en/latest/python-api/)** - Programmatic access

## Quick Reference

```bash
# Querying
blq errors                    # Recent errors
blq events --severity=warning # Warnings
blq history                   # Run history
blq info <ref>                # Run details
blq inspect <ref>             # Error with context

# Running
blq run <command>             # Run registered command
blq run test --json           # JSON output for scripts

# Management
blq commands                  # List registered commands
blq register <name> <cmd>     # Add command
blq clean data                # Clear run history

# CI
blq ci check --baseline main  # Check for regressions
blq report                    # Generate markdown report
```

## License

MIT
