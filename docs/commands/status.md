# Status and History Commands

blq provides commands to monitor the state of your log captures and view run history.

## status - Current Source Status

Show a quick overview of all sources and their latest run status, or details for a specific run.

```bash
blq status                    # Quick status overview
blq status --verbose          # Detailed status
blq status test:24            # Show details for run test:24
blq status test:24 --details  # Show all fields for run
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `REF` | | Optional run reference to show details (e.g., `test:24`) |
| `--verbose` | `-v` | Show detailed status / all fields |
| `--details` | `-d` | Show all fields for a specific run |
| `--json` | `-j` | Output as JSON |
| `--markdown` | `-m` | Output as Markdown |

### Output

Default output shows status badges and counts:

```
Source      Err   Warn  Age
----------  ----  ----  ------
build       3     5     5m ago
test        0     12    2m ago
lint        0     0     10m ago
```

### Run Details

When given a run reference, shows detailed information:

```bash
$ blq status test:24
Field                Value
-------------------  -------------------------------------------
Run Ref              test:24
Source Name          test
Command              pytest tests/ -v
Error Count          2
Warning Count        5
Started At           2024-01-15 10:30:00 (5m ago)
Exit Code            1
Git Branch           main
Git Commit           abc1234
```

With `--details`, shows additional fields like hostname, platform, cwd, etc.

## history - Run History

Show the history of all captured runs with filtering support.

```bash
blq history                   # Show recent runs
blq history -n 50             # Show last 50 runs
blq history test              # Filter by tag/source name
blq history -t build          # Filter by tag using flag
blq history --json            # Output as JSON
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `REF` | | Optional filter by tag or ref (e.g., `test` or `test:24`) |
| `--tag NAME` | `-t` | Filter by tag/source name |
| `--limit N` | `-n` | Maximum results (default: 20) |
| `--json` | `-j` | Output as JSON |
| `--markdown` | `-m` | Output as Markdown |

### Output

The output uses a compact E/W/T format showing errors/warnings/total:

```
Ref           E/W/T      When       Branch      Commit    Command
----------    ---------  ---------  ----------  --------  ------------------
test:5        2/5/7      5m ago     main        abc1234   pytest tests/
build:4       0/12/12    10m ago    feature/x   def5678   make -j8
lint:3        15/0/15    1d ago     main        ghi9012   ruff check src/
```

Column meanings:
- **Ref**: Run reference (tag:run_id) for use with other commands
- **E/W/T**: Errors/Warnings/Total event count
- **When**: Relative time since run started
- **Branch/Commit**: Git context
- **Command**: The command that was run

### Filtering Examples

```bash
# Show only test runs
blq history test

# Show only build runs using flag
blq history -t build

# Combine with limit
blq history test -n 5
```

## summary - Error/Warning Summary

Show aggregate statistics by tool and category.

```bash
blq summary                   # Summary across all runs
blq summary --latest          # Summary for latest run only
blq summary --json            # Output as JSON
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--latest` | `-l` | Show summary for latest run only |
| `--json` | `-j` | Output as JSON |
| `--markdown` | `-m` | Output as Markdown |

### Output

```
  tool_name    category  errors  warnings  total
       gcc   undefined       2         0      2
       gcc        type       1         3      4
       gcc      unused       0         5      5
    pytest  assertion        3         0      3
```

With `--latest`, only events from the most recent run are counted.

## Use Cases

### Checking Build Status

Quick check after a build:
```bash
blq run make && blq status
```

### Viewing Run Details

Get full details for a specific run:
```bash
blq status build:5
blq status build:5 --details  # All metadata
blq status build:5 --json     # JSON for scripting
```

### Filtering History by Source

View history for specific tools:
```bash
blq history test      # All test runs
blq history build     # All build runs
blq history -t lint   # All lint runs
```

### Finding Recurring Issues

Use summary to identify patterns:
```bash
blq summary
# Shows which tools/categories produce the most errors
```

### Comparing Runs

Check history to see trends:
```bash
blq history -n 10
# See if error counts are going up or down
```

## Integration with Workflows

### CI Scripts

```bash
#!/bin/bash
blq run make
if [ $? -ne 0 ]; then
    echo "Build failed. Summary:"
    blq summary --latest
    exit 1
fi
```

### Development Workflow

```bash
# Morning check: see what broke overnight
blq status

# After changes: run and check
blq run make
blq status

# Get details on latest run
blq status build:$(blq history build -n1 --json | jq -r '.[0].run_id')
```

### Quick Health Check

```bash
# One-liner to check project health
blq status | grep -E 'FAIL|WARN' && echo "Issues found" || echo "All clear"
```
