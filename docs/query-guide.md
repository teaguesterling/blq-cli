# Query Guide

This guide covers techniques for effectively querying logs with blq.

## Query Methods

### 1. Filter Command (Simple)

Use simple filter expressions:

```bash
blq filter severity=error build.log
blq filter severity=error,warning ref_file~test
```

Filter syntax:
- `key=value` - exact match
- `key=v1,v2` - multiple values (OR)
- `key~pattern` - contains (case-insensitive)
- `key!=value` - not equal

### 2. Query Command (Flexible)

Query with SQL WHERE clauses:

```bash
blq query -f "severity='error'" build.log
blq query -s ref_file,message -f "ref_line > 100"
```

### 3. SQL Command (Full Power)

Run arbitrary SQL:

```bash
blq sql "SELECT * FROM blq_load_events() WHERE severity = 'error'"
```

### 4. MCP Query Tool

For AI agents, the `query` tool accepts both SQL and filter syntax:

```python
# SQL
query(sql="SELECT * FROM blq_load_events() WHERE severity = 'error'")

# Filter syntax
query(filter="severity=error ref_file~test")
```

## Choosing Your Tool

| Use Case | Tool | Example |
|----------|------|---------|
| Quick filter | `blq filter` | `blq filter severity=error` |
| Column selection | `blq query` | `blq query -s file,message` |
| Complex conditions | `blq query` | `blq query -f "line > 100"` |
| Full SQL | `blq sql` | `blq sql "SELECT ..."` |
| Interactive | `blq shell` | `blq shell` |
| MCP/Agents | `query` tool | `query(filter="severity=error")` |

## Common Patterns

### Find All Errors

```bash
# Simple filter
blq filter severity=error

# With location info
blq query -s ref_file,ref_line,message -f "severity='error'"

# MCP
query(filter="severity=error")
```

### Group by File

```bash
blq sql "SELECT ref_file, COUNT(*) as errors
        FROM blq_load_events()
        WHERE severity='error'
        GROUP BY ref_file
        ORDER BY errors DESC"
```

### Find Repeated Errors

Using error fingerprints:

```bash
blq sql "SELECT fingerprint, COUNT(*) as occurrences,
               ANY_VALUE(message) as example
        FROM blq_load_events()
        WHERE severity = 'error'
        GROUP BY fingerprint
        HAVING COUNT(*) > 1
        ORDER BY occurrences DESC"
```

### Compare Runs

```bash
# Use the diff command
blq diff 4 5

# Or via SQL
blq sql "SELECT DISTINCT fingerprint, message
        FROM blq_load_events()
        WHERE run_serial = 5
          AND fingerprint NOT IN (
              SELECT fingerprint FROM blq_load_events()
              WHERE run_serial = 4
          )"
```

### Timeline of Errors

```bash
blq sql "SELECT date, source_name, COUNT(*) as errors
        FROM blq_load_events()
        WHERE severity = 'error'
        GROUP BY date, source_name
        ORDER BY date DESC"
```

## Available Fields

### Core Event Fields

| Field | Description |
|-------|-------------|
| `severity` | error, warning, info, note |
| `ref_file` | Source file path |
| `ref_line` | Line in source file |
| `ref_column` | Column in source file |
| `message` | Error/warning text |
| `code` | Error code (e.g., E501, F401) |
| `tool_name` | Detected tool (gcc, pytest, etc.) |
| `category` | Error category (compile, lint, test) |
| `fingerprint` | Unique hash for deduplication |

### Run Context Fields

| Field | Description |
|-------|-------------|
| `run_serial` | Sequential run number |
| `run_ref` | Human-friendly run reference (e.g., "build:3") |
| `ref` | Full event reference (e.g., "build:3:1") |
| `source_name` | Command name (build, test, etc.) |
| `source_type` | run, import, or capture |
| `date` | Partition date |

### Metadata Fields

| Field | Description |
|-------|-------------|
| `hostname` | Machine name |
| `platform` | OS (Linux, Darwin, Windows) |
| `git_commit` | HEAD commit SHA |
| `git_branch` | Current branch |
| `git_dirty` | Uncommitted changes present |

## SQL Macros

blq provides table-returning macros:

| Macro | Description |
|-------|-------------|
| `blq_load_events()` | All events with run context |
| `blq_load_runs()` | Aggregated run statistics |
| `blq_status()` | Quick status overview |
| `blq_errors(n)` | Recent errors (default: 10) |
| `blq_warnings(n)` | Recent warnings (default: 10) |
| `blq_history(n)` | Run history (default: 20) |
| `blq_diff(run1, run2)` | Compare two runs |

## Output Formats

### Table (Default)

```bash
blq query -s ref_file,message
```

### JSON

```bash
blq query --json
blq errors -j
```

Best for:
- Piping to `jq`
- Agent/LLM consumption
- API responses

### CSV

```bash
blq query --csv > errors.csv
```

### Markdown

```bash
blq query --markdown
```

Best for:
- Documentation
- GitHub comments
- Reports

## Performance Tips

### Limit Results

Always use `-n` when exploring:

```bash
blq query -n 10
blq errors -n 5
```

### Select Only Needed Columns

```bash
# Fast
blq query -s ref_file,message

# Slower (returns all columns)
blq query
```

### Filter by Run

Query specific runs for faster results:

```bash
blq sql "SELECT * FROM blq_load_events() WHERE run_serial = 5"
```

## Interactive Shell

For exploratory analysis:

```bash
blq shell
```

In the shell, you have full DuckDB SQL plus all blq macros:

```sql
-- Recent errors
SELECT * FROM blq_errors(20);

-- Status overview
SELECT * FROM blq_status();

-- Custom query
SELECT ref_file, COUNT(*)
FROM blq_load_events()
WHERE severity = 'error'
GROUP BY 1;
```
