# Query Guide

This guide covers techniques for effectively querying logs with lq.

## Two Ways to Query

### 1. Query Files Directly

Query log files without importing them:

```bash
blq q build.log
blq f severity=error build.log
```

This uses the `duck_hunt` extension to parse the file on-the-fly.

### 2. Query Stored Events

Query previously captured events:

```bash
blq q -f "severity='error'"
blq f severity=error
```

This queries the `lq_events` view which combines all stored parquet files.

## Choosing Your Tool

| Use Case | Tool | Example |
|----------|------|---------|
| Quick filter | `blq filter` | `blq f severity=error log.txt` |
| Column selection | `blq query` | `blq q -s file,message log.txt` |
| Complex conditions | `blq query` | `blq q -f "line > 100"` |
| Full SQL | `blq sql` | `blq sql "SELECT ..."` |
| Interactive | `blq shell` | `blq shell` |

## Common Patterns

### Find All Errors

```bash
# Simple
blq f severity=error build.log

# With location info
blq q -s file_path,line_number,message -f "severity='error'" build.log
```

### Group by File

```bash
blq sql "SELECT file_path, COUNT(*) as errors
        FROM read_duck_hunt_log('build.log', 'auto')
        WHERE severity='error'
        GROUP BY file_path
        ORDER BY errors DESC"
```

### Find Repeated Errors

Using error fingerprints:

```bash
blq sql "SELECT error_fingerprint, COUNT(*) as occurrences,
               ANY_VALUE(message) as example
        FROM lq_events
        GROUP BY error_fingerprint
        HAVING COUNT(*) > 1
        ORDER BY occurrences DESC"
```

### Compare Runs

```bash
# Errors in latest run but not previous
blq sql "SELECT DISTINCT error_fingerprint, message
        FROM lq_events
        WHERE run_id = (SELECT MAX(run_id) FROM lq_events)
          AND error_fingerprint NOT IN (
              SELECT error_fingerprint FROM lq_events
              WHERE run_id = (SELECT MAX(run_id) - 1 FROM lq_events)
          )"
```

### Timeline of Errors

```bash
blq sql "SELECT date, source_name, COUNT(*) as errors
        FROM lq_events
        WHERE severity = 'error'
        GROUP BY date, source_name
        ORDER BY date DESC"
```

## Available Fields

### From Log Files

| Field | Description |
|-------|-------------|
| `event_id` | Sequential ID within the file |
| `severity` | error, warning, info, note |
| `file_path` | Source file path |
| `line_number` | Line in source file |
| `column_number` | Column in source file |
| `message` | Error/warning text |
| `error_fingerprint` | Unique hash for deduplication |
| `tool_name` | Detected tool (gcc, pytest, etc.) |
| `category` | Error category |

### From Stored Events

Additional fields:

| Field | Description |
|-------|-------------|
| `run_id` | Unique run identifier |
| `source_name` | Name given to the source |
| `source_type` | run, import, or capture |
| `date` | Partition date |

## Output Formats

### Table (Default)

```bash
blq q -s file_path,message build.log
```

```
  file_path                   message
 src/main.c undefined variable 'foo'
```

### JSON

```bash
blq q --json build.log
```

Best for:
- Piping to `jq`
- Agent/LLM consumption
- API responses

### CSV

```bash
blq q --csv build.log > errors.csv
```

Best for:
- Spreadsheet import
- Data analysis tools

### Markdown

```bash
blq q --markdown build.log
```

Best for:
- Documentation
- GitHub comments
- Reports

## Performance Tips

### Limit Results

Always use `-n` when exploring:

```bash
blq q -n 10 build.log
```

### Select Only Needed Columns

```bash
# Fast
blq q -s file_path,message build.log

# Slow (returns all columns)
blq q build.log
```

### Use Date Partitions

Stored data is partitioned by date. Filter by date for faster queries:

```bash
blq sql "SELECT * FROM lq_events WHERE date = '2024-01-15'"
```

## Advanced: Raw SQL

For complex analysis, use `blq sql` or `blq shell`:

```bash
# Ad-hoc query
blq sql "SELECT file_path, COUNT(*) FROM lq_events GROUP BY 1"

# Interactive session
blq shell
```

In the shell, you have full DuckDB SQL available plus:
- `lq_events` view - all stored events
- `lq_ref(run_id, event_id)` - create event reference
- `lq_location(file, line, col)` - format location string
