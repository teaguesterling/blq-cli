# lq query

Query log files or stored events using SQL.

**Alias:** `blq q`

## Synopsis

```bash
blq query [OPTIONS] [FILE...]
blq q [OPTIONS] [FILE...]
```

## Description

The `query` command provides SQL-like querying of log files or stored events. When a file is specified, it queries the file directly using duck_hunt. Without a file, it queries previously captured events from `.lq/logs/`.

## Options

| Option | Description |
|--------|-------------|
| `-s, --select COLS` | Columns to select (comma-separated) |
| `-f, --filter WHERE` | SQL WHERE clause |
| `-o, --order ORDER` | SQL ORDER BY clause |
| `-n, --limit N` | Maximum rows to return |
| `--json, -j` | Output as JSON |
| `--csv` | Output as CSV |
| `--markdown, --md` | Output as Markdown table |

## Examples

### Basic Query

Query all events from a log file:

```bash
blq q build.log
```

### Select Columns

```bash
blq q -s file_path,line_number,severity,message build.log
```

Output:
```
  file_path  line_number severity                  message
 src/main.c           15    error undefined variable 'foo'
 src/main.c           28  warning   unused variable 'temp'
```

### Filter with WHERE Clause

```bash
blq q -f "severity='error'" build.log
blq q -f "severity='error' AND file_path LIKE '%main%'" build.log
blq q -f "line_number > 100" build.log
```

### Order and Limit

```bash
blq q -o "line_number DESC" -n 10 build.log
blq q -o "file_path, line_number" build.log
```

### Output Formats

```bash
# JSON (great for scripts and agents)
blq q --json build.log

# CSV (for spreadsheets)
blq q --csv build.log > errors.csv

# Markdown (for documentation)
blq q --markdown build.log
```

### Query Stored Events

Without a file argument, queries the `lq_events` view:

```bash
# All stored errors
blq q -f "severity='error'"

# Errors from today
blq q -f "severity='error' AND date = current_date"

# Errors from a specific run
blq q -f "run_id = 5"
```

### Specify Log Format

Use the global `-F` flag to hint the log format:

```bash
blq -F gcc q build.log
blq -F pytest q test_output.log
```

## Available Columns

When querying log files, these columns are typically available:

| Column | Description |
|--------|-------------|
| `event_id` | Unique event ID within the log |
| `severity` | error, warning, info, etc. |
| `file_path` | Source file path |
| `line_number` | Line number in source |
| `column_number` | Column number in source |
| `message` | Error/warning message |
| `error_fingerprint` | Unique fingerprint for deduplication |
| `tool_name` | Tool that produced the event |
| `category` | Event category |

For stored events, additional columns include:
- `run_id` - Run identifier
- `source_name` - Name of the source
- `source_type` - Type (run, import, capture)

## See Also

- [filter](filter.md) - Simple key=value filtering
- [sql](sql.md) - Raw SQL queries
- [errors](errors.md) - Quick error viewing
