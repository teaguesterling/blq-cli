# Query Guide

blq provides multiple ways to query build/test logs, from simple filters to full SQL.

## Quick Reference

| Use Case | Command | Example |
|----------|---------|---------|
| Quick filter | `blq filter` | `blq filter severity=error` |
| Column selection | `blq query` | `blq query -s file,message` |
| Full SQL | `blq sql` | `blq sql "SELECT ..."` |
| Interactive | `blq shell` | SQL REPL with macros |

---

## Filter Command

Simple filter expressions for quick queries.

```bash
blq filter severity=error
blq filter severity=error,warning ref_file~test
blq filter ref_file~main severity!=info
```

**Syntax:**
- `key=value` — exact match
- `key=v1,v2` — multiple values (OR)
- `key~pattern` — contains (case-insensitive)
- `key!=value` — not equal

Works on stored events or log files:

```bash
blq filter severity=error                 # Query stored events
blq filter severity=error build.log       # Query a log file directly
```

---

## Query Command

More control with column selection and SQL-style filters.

```bash
blq query -s ref_file,ref_line,message -f "severity='error'"
blq query -s ref_file,message -f "ref_line > 100" -n 20
```

**Options:**
- `-s, --select` — columns to return
- `-f, --filter` — SQL WHERE clause
- `-n, --limit` — max rows

---

## SQL Command

Full DuckDB SQL for complex analysis.

```bash
blq sql "SELECT ref_file, COUNT(*) as errors
         FROM blq_load_events()
         WHERE severity='error'
         GROUP BY ref_file
         ORDER BY errors DESC"
```

### SQL Macros

| Macro | Description |
|-------|-------------|
| `blq_load_events()` | All events with run context |
| `blq_load_runs()` | Aggregated run statistics |
| `blq_status()` | Quick status overview |
| `blq_errors(n)` | Recent errors (default: 10) |
| `blq_warnings(n)` | Recent warnings (default: 10) |
| `blq_history(n)` | Run history (default: 20) |
| `blq_diff(run1, run2)` | Compare two runs |

---

## Common Patterns

### Find All Errors

```bash
blq filter severity=error
blq query -s ref_file,ref_line,message -f "severity='error'"
blq errors                                 # Built-in shortcut
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

Using fingerprints to find duplicate errors across runs:

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
# Built-in diff command
blq diff 4 5

# Find new errors in run 5
blq sql "SELECT fingerprint, message
         FROM blq_load_events()
         WHERE run_serial = 5
           AND fingerprint NOT IN (
               SELECT fingerprint FROM blq_load_events()
               WHERE run_serial = 4
           )"
```

### Errors by Day

```bash
blq sql "SELECT date, source_name, COUNT(*) as errors
         FROM blq_load_events()
         WHERE severity = 'error'
         GROUP BY date, source_name
         ORDER BY date DESC"
```

---

## Available Fields

### Event Fields

| Field | Description |
|-------|-------------|
| `severity` | error, warning, info, note |
| `ref_file` | Source file path |
| `ref_line` | Line number |
| `ref_column` | Column number |
| `message` | Error/warning text |
| `code` | Error code (e.g., E501) |
| `tool_name` | Detected tool (gcc, pytest, mypy) |
| `category` | Error category (compile, lint, test) |
| `fingerprint` | Hash for deduplication |

### Run Context

| Field | Description |
|-------|-------------|
| `run_serial` | Sequential run number |
| `run_ref` | Human-friendly ref (e.g., "build:3") |
| `ref` | Full event ref (e.g., "build:3:1") |
| `source_name` | Command name |
| `date` | Run date |

### Metadata

| Field | Description |
|-------|-------------|
| `hostname` | Machine name |
| `platform` | OS |
| `git_commit` | HEAD SHA |
| `git_branch` | Current branch |
| `git_dirty` | Uncommitted changes |

---

## Output Formats

```bash
blq query -s ref_file,message              # Table (default)
blq query --json                           # JSON
blq query --csv > errors.csv               # CSV
blq query --markdown                       # Markdown table
```

---

## Interactive Shell

For exploratory analysis:

```bash
blq shell
```

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

---

## Performance Tips

1. **Limit results** when exploring: `blq query -n 10`
2. **Select only needed columns**: `blq query -s ref_file,message`
3. **Filter by run** for faster queries: `WHERE run_serial = 5`
