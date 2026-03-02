# SQL Commands

blq stores all log data in a DuckDB database (BIRD storage) and provides commands for direct SQL access.

## sql - Execute SQL Queries

Run arbitrary SQL queries against the log database.

```bash
blq sql "SELECT * FROM blq_load_events() LIMIT 10"
blq sql "SELECT ref_file, COUNT(*) FROM blq_load_events() GROUP BY ref_file"
blq sql "FROM blq_status()"
```

### Usage

```bash
blq sql <query>
```

Queries can span multiple words (quoted or unquoted):
```bash
blq sql SELECT COUNT\(*\) FROM blq_load_events()
blq sql "SELECT COUNT(*) FROM blq_load_events()"
```

### Available Table Macros

| Macro | Description |
|-------|-------------|
| `blq_load_events()` | All parsed events (errors, warnings, info) |
| `blq_load_runs()` | Run metadata (command, exit code, timestamps) |
| `blq_load_source_status()` | Latest run status per source |

### Available Macros

| Macro | Description |
|-------|-------------|
| `blq_status()` | Quick status overview |
| `blq_status_verbose()` | Detailed status with exit codes |
| `blq_errors(n)` | Recent errors (default n=10) |
| `blq_errors_for(src, n)` | Errors for specific source |
| `blq_warnings(n)` | Recent warnings (default n=10) |
| `blq_summary()` | Aggregate by tool/category |
| `blq_summary_latest()` | Summary for latest run only |
| `blq_history(n)` | Run history (default n=20) |
| `blq_diff(run1, run2)` | Compare errors between runs |
| `blq_event(id)` | Get event by ID |
| `blq_files()` | List all files with events |
| `blq_file(path)` | Events for specific file |
| `blq_similar_events(fp, n)` | Events in same file |

### Example Queries

**Errors by file:**
```bash
blq sql "SELECT ref_file, COUNT(*) as errors FROM blq_load_events() WHERE severity='error' GROUP BY ref_file ORDER BY errors DESC"
```

**Recent runs with errors:**
```bash
blq sql "SELECT run_id, source_name, error_count FROM blq_load_runs() WHERE error_count > 0 ORDER BY started_at DESC LIMIT 10"
```

**Using macros:**
```bash
blq sql "FROM blq_errors(20)"
blq sql "FROM blq_diff(1, 2)"
blq sql "FROM blq_file('src/main.c')"
```

**Time-based queries:**
```bash
blq sql "SELECT * FROM blq_load_events() WHERE started_at > now() - INTERVAL '1 hour'"
```

**Run metadata:**
```bash
blq sql "SELECT run_id, git_commit, git_branch, ci['provider'] as ci FROM blq_load_runs()"
```

## shell - Interactive DuckDB Shell

Start an interactive DuckDB shell with the log database loaded.

```bash
blq shell
```

This opens a DuckDB CLI session with:
- duck_hunt extension loaded
- Schema and macros loaded from `.lq/schema.sql`
- Custom prompt `blq> `

### Interactive Session

```
blq shell
blq> SELECT COUNT(*) FROM blq_load_events();
┌──────────────┐
│ count_star() │
├──────────────┤
│          142 │
└──────────────┘
blq> FROM blq_status();
...
blq> .quit
```

### Shell Features

The shell supports all DuckDB CLI features:
- Tab completion
- Multi-line queries
- `.commands` for help
- `.timer on` for query timing
- `.mode` for output format

### Use Cases

**Exploratory analysis:**
```sql
-- Check schema
.schema

-- Sample data
SELECT * FROM blq_load_events() LIMIT 5;

-- Find patterns
SELECT message, COUNT(*)
FROM blq_load_events()
WHERE severity = 'error'
GROUP BY message
ORDER BY COUNT(*) DESC;
```

**Ad-hoc investigation:**
```sql
-- What's breaking?
FROM blq_errors(50);

-- Compare runs
FROM blq_diff(3, 5);

-- Find related errors
FROM blq_similar_events('src/auth.c', 20);
```

## Schema Reference

### blq_load_events() Columns

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | INTEGER | Run identifier |
| `event_id` | INTEGER | Event number within run |
| `severity` | VARCHAR | error, warning, info, debug |
| `ref_file` | VARCHAR | Source file path |
| `ref_line` | INTEGER | Line number |
| `ref_column` | INTEGER | Column number |
| `message` | VARCHAR | Event message |
| `tool_name` | VARCHAR | Tool that generated event |
| `category` | VARCHAR | Error category |
| `error_code` | VARCHAR | Error code (e.g., E0001) |
| `source_name` | VARCHAR | Source name (build, test, etc.) |
| `started_at` | TIMESTAMP | When the run started |

### blq_load_runs() Columns

| Column | Type | Description |
|--------|------|-------------|
| `run_id` | INTEGER | Run identifier |
| `source_name` | VARCHAR | Source name |
| `command` | VARCHAR | Command executed |
| `started_at` | TIMESTAMP | Start timestamp |
| `completed_at` | TIMESTAMP | End timestamp |
| `exit_code` | INTEGER | Process exit code |
| `error_count` | INTEGER | Number of errors |
| `warning_count` | INTEGER | Number of warnings |
| `event_count` | INTEGER | Total events |
| `cwd` | VARCHAR | Working directory |
| `hostname` | VARCHAR | Machine hostname |
| `platform` | VARCHAR | OS (Linux, Darwin, Windows) |
| `arch` | VARCHAR | Architecture |
| `git_commit` | VARCHAR | Git HEAD SHA |
| `git_branch` | VARCHAR | Git branch |
| `git_dirty` | BOOLEAN | Uncommitted changes |
| `ci` | MAP | CI provider and context |
| `environment` | MAP | Captured env vars |

## Tips

### Escaping in Shell

When using `blq sql` from bash, escape or quote special characters:
```bash
blq sql "SELECT * FROM blq_load_events() WHERE message LIKE '%undefined%'"
blq sql 'SELECT * FROM blq_load_events() WHERE severity = '"'"'error'"'"
```

### Export Results

```bash
# To CSV
blq sql "SELECT * FROM blq_load_events()" > events.csv

# To JSON (use DuckDB format)
blq shell
blq> .mode json
blq> SELECT * FROM blq_load_events();
```

### Complex Analysis

For complex analysis, use the shell:
```bash
blq shell << 'EOF'
.timer on
WITH error_files AS (
    SELECT ref_file, COUNT(*) as errors
    FROM blq_load_events()
    WHERE severity = 'error'
    GROUP BY ref_file
)
SELECT * FROM error_files WHERE errors > 5 ORDER BY errors DESC;
EOF
```
