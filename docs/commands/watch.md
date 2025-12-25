# watch - Watch Mode

Watch for file changes and automatically run commands.

## Synopsis

```bash
blq watch [OPTIONS] <command> [command...]
```

## Description

The `watch` command monitors the filesystem for changes and automatically runs registered commands when files are modified. This is useful for continuous testing, building, or linting during development.

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--debounce MS` | `-d` | Debounce delay in milliseconds (default: 300) |
| `--exclude PATTERNS` | `-e` | Comma-separated patterns to exclude |
| `--include PATTERNS` | `-i` | Comma-separated patterns to include (default: all) |
| `--once` | | Run once and exit (useful for CI) |
| `--quiet` | `-q` | Suppress file change notifications |

## Examples

### Basic Usage

```bash
# Watch and run a single command
blq watch build

# Watch and run multiple commands in sequence
blq watch lint test

# Watch with short alias
blq watch test
```

### Debounce Control

The debounce delay prevents multiple rapid file saves from triggering multiple builds:

```bash
# Quick debounce for fast feedback
blq watch --debounce 100 test

# Longer debounce for expensive builds
blq watch --debounce 1000 build
```

### File Filtering

```bash
# Exclude patterns
blq watch --exclude "*.log,dist/*,node_modules/*" build

# Include only specific patterns
blq watch --include "*.py,*.pyx" test

# Combine include and exclude
blq watch --include "src/*" --exclude "*.pyc" build
```

### Run Once Mode

Useful for CI or scripted workflows:

```bash
# Run commands once on any pending changes, then exit
blq watch --once build test
```

### Quiet Mode

```bash
# Suppress "File changed: ..." messages
blq watch --quiet test
```

## Default Excludes

By default, watch mode excludes common non-source files:

- `.git/*` - Git internals
- `.lq/*` - blq data directory
- `__pycache__/*` - Python bytecode
- `*.pyc` - Python compiled files
- `node_modules/*` - Node.js dependencies
- `dist/*`, `build/*` - Build outputs
- `*.log` - Log files

## Behavior

### Change Detection

Watch mode detects:
- File creation
- File modification
- File deletion
- File moves/renames

### Debouncing

When multiple files change rapidly (e.g., during a git checkout or IDE save-all), watch mode waits for the debounce period before running commands. This prevents unnecessary duplicate runs.

### Sequential Execution

When multiple commands are specified, they run in sequence:

```bash
blq watch lint test build
# On file change:
#   1. Run lint
#   2. If lint passes, run test
#   3. If test passes, run build
```

If any command fails, subsequent commands are still run (to provide full feedback).

## Integration Examples

### Development Workflow

Terminal 1 - Watch for changes:
```bash
blq watch test
```

Terminal 2 - Edit code normally, tests run automatically.

### Pre-commit Alternative

Use watch mode as a lightweight pre-commit alternative:

```bash
# Run lint and test on every save
blq watch lint test
```

### CI Watch Mode

Use `--once` in CI to process any accumulated changes:

```bash
# In CI, run once to catch any issues
blq watch --once lint test build
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Clean exit (Ctrl+C) or `--once` completed |
| 1 | Command failed (with `--once`) |

In continuous mode, watch keeps running even if commands fail.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Ctrl+C | Stop watching and exit |
| Enter | Force re-run commands immediately |

## See Also

- [run](run.md) - Run registered commands
- [registry](registry.md) - Register commands
- [exec](exec.md) - Execute ad-hoc commands
