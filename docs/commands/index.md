# Commands Reference

## Global Options

These options apply to all commands:

| Option | Description |
|--------|-------------|
| `-F, --log-format FORMAT` | Log format hint for parsing (default: `auto`) |
| `-h, --help` | Show help message |

## Command Categories

### Querying

| Command | Alias | Description |
|---------|-------|-------------|
| [query](query.md) | `q` | Query log files or stored events with SQL |
| [filter](filter.md) | `f` | Filter with simple key=value syntax |
| [sql](sql.md) | | Run arbitrary SQL queries |
| [shell](shell.md) | | Interactive DuckDB shell |

### Capturing

| Command | Description |
|---------|-------------|
| [run](run.md) | Run a command and capture output |
| [import](import.md) | Import an existing log file |
| [capture](capture.md) | Capture from stdin |

### Viewing

| Command | Description |
|---------|-------------|
| [errors](errors.md) | Show recent errors |
| [warnings](warnings.md) | Show recent warnings |
| [event](event.md) | Show details for a specific event |
| [context](context.md) | Show log context around an event |
| [status](status.md) | Show status of all sources |
| [history](history.md) | Show run history |
| [summary](summary.md) | Aggregate summary |

### Management

| Command | Description |
|---------|-------------|
| [init](init.md) | Initialize .lq directory |
| [register](register.md) | Register a reusable command |
| [unregister](unregister.md) | Remove a registered command |
| [commands](commands.md) | List registered commands |
| [prune](prune.md) | Remove old log files |

## Quick Reference

```bash
# Query a file
blq q build.log
blq q -s file_path,message build.log
blq q -f "severity='error'" build.log

# Filter (simple syntax)
blq f severity=error build.log
blq f -c severity=error build.log    # count only

# Run commands
blq run make
blq run --json make test

# View events
blq errors
blq event 1:3
blq context 1:3

# Manage commands
blq register build "make -j8"
blq run build
```
