# Data Schema Reference

blq stores build logs and parsed events in a DuckDB database at `.lq/blq.duckdb`. This document describes the schema for users who want to write custom SQL queries.

## Directory Structure

```
.lq/
├── blq.duckdb          # DuckDB database (tables, macros)
├── blobs/              # Content-addressed output storage
│   └── content/
│       ├── ab/
│       │   └── {hash}.bin
│       └── ...
├── config.toml         # Project configuration
└── commands.toml       # Registered commands
```

## Tables

### attempts

Written when a command **starts**. Commands without a matching `outcomes` row are still running.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `session_id` | VARCHAR | Session identifier |
| `timestamp` | TIMESTAMP | When command started |
| `cwd` | VARCHAR | Working directory |
| `cmd` | VARCHAR | Full command string |
| `executable` | VARCHAR | Extracted executable name |
| `pid` | INTEGER | Process ID |
| `format_hint` | VARCHAR | Detected format (gcc, pytest, etc.) |
| `client_id` | VARCHAR | Client identifier (blq-shell, blq-mcp) |
| `hostname` | VARCHAR | Machine hostname |
| `username` | VARCHAR | User who ran command |
| `tag` | VARCHAR | User-defined tag |
| `source_name` | VARCHAR | Registered command name |
| `source_type` | VARCHAR | run, exec, import, capture |
| `environment` | JSON | Captured environment variables |
| `platform` | VARCHAR | OS (Linux, Darwin, Windows) |
| `arch` | VARCHAR | Architecture (x86_64, arm64) |
| `git_commit` | VARCHAR | HEAD SHA |
| `git_branch` | VARCHAR | Current branch |
| `git_dirty` | BOOLEAN | Uncommitted changes present |
| `ci` | JSON | CI provider context |
| `date` | DATE | Partition date |

### outcomes

Written when a command **completes**. One-to-one with `attempts`.

| Column | Type | Description |
|--------|------|-------------|
| `attempt_id` | UUID | References `attempts.id` |
| `completed_at` | TIMESTAMP | When command finished |
| `duration_ms` | BIGINT | Wall-clock duration in milliseconds |
| `exit_code` | INTEGER | Exit code (NULL = crashed) |
| `signal` | INTEGER | If killed by signal (15=SIGTERM, 9=SIGKILL) |
| `timeout` | BOOLEAN | If killed by timeout |
| `date` | DATE | Partition date |

### events

Parsed diagnostics (errors, warnings, test results).

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `invocation_id` | UUID | References `invocations.id` |
| `event_index` | INTEGER | Index within invocation (1-based) |
| `client_id` | VARCHAR | Client identifier |
| `hostname` | VARCHAR | Machine hostname |
| `event_type` | VARCHAR | diagnostic, test_result, etc. |
| `severity` | VARCHAR | error, warning, info, note |
| `ref_file` | VARCHAR | Source file path |
| `ref_line` | INTEGER | Line number |
| `ref_column` | INTEGER | Column number |
| `message` | VARCHAR | Error/warning message |
| `code` | VARCHAR | Error code (e.g., E0308) |
| `rule` | VARCHAR | Rule name (e.g., no-unused-vars) |
| `tool_name` | VARCHAR | Tool that generated event |
| `category` | VARCHAR | Error category |
| `fingerprint` | VARCHAR | Unique identifier for deduplication |
| `log_line_start` | INTEGER | Start line in raw log |
| `log_line_end` | INTEGER | End line in raw log |
| `context` | VARCHAR | Surrounding context |
| `metadata` | JSON | Format-specific extras |
| `format_used` | VARCHAR | Parser format (gcc, cargo, pytest) |
| `date` | DATE | Partition date |

### outputs

Captured stdout/stderr, with content stored in blobs.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `invocation_id` | UUID | References `invocations.id` |
| `stream` | VARCHAR | stdout, stderr, combined |
| `content_hash` | VARCHAR | BLAKE2b hash (64 hex chars) |
| `byte_length` | BIGINT | Content size in bytes |
| `storage_type` | VARCHAR | inline or blob |
| `storage_ref` | VARCHAR | data: URI or file: path |
| `content_type` | VARCHAR | MIME type or format hint |
| `date` | DATE | Partition date |

### blob_registry

Tracks content-addressed blobs for deduplication.

| Column | Type | Description |
|--------|------|-------------|
| `content_hash` | VARCHAR | BLAKE2b hash (primary key) |
| `byte_length` | BIGINT | Content size |
| `compression` | VARCHAR | none, gzip, zstd |
| `ref_count` | INTEGER | Reference count |
| `first_seen` | TIMESTAMP | When first stored |
| `last_accessed` | TIMESTAMP | Last access time |
| `storage_path` | VARCHAR | Relative path in blobs/ |

### sessions

Tracks invoker sessions (shell, CLI, MCP).

| Column | Type | Description |
|--------|------|-------------|
| `session_id` | VARCHAR | Primary key |
| `client_id` | VARCHAR | Client identifier |
| `invoker` | VARCHAR | blq, blq-mcp |
| `invoker_pid` | INTEGER | Process ID |
| `invoker_type` | VARCHAR | cli, mcp, import, capture |
| `registered_at` | TIMESTAMP | Session start time |
| `cwd` | VARCHAR | Initial working directory |
| `date` | DATE | Partition date |

### invocations (legacy)

Legacy table for completed runs. New code uses `attempts` + `outcomes`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `session_id` | VARCHAR | Session identifier |
| `timestamp` | TIMESTAMP | When command ran |
| `duration_ms` | BIGINT | Duration in milliseconds |
| `cwd` | VARCHAR | Working directory |
| `cmd` | VARCHAR | Full command string |
| `exit_code` | INTEGER | Exit code |
| `source_name` | VARCHAR | Registered command name |
| ... | ... | (same metadata as `attempts`) |

## Blob Storage

Output content is stored using content-addressed storage:

1. **Hashing**: Content is hashed with BLAKE2b (32 bytes = 64 hex chars)
2. **Deduplication**: Identical outputs share the same blob
3. **Path structure**: `blobs/content/{first-2-chars}/{full-hash}.bin`

Example:
```
blobs/content/ab/abc123def456...789.bin
              ^^
              First 2 chars of hash for sharding
```

**Storage types:**
- `inline`: Small outputs (<4KB) stored as `data:` URIs in the database
- `blob`: Larger outputs stored as files with `file:` references

## Reference Format

Events and runs use human-friendly references:

| Format | Example | Description |
|--------|---------|-------------|
| `source:run_serial` | `build:3` | Run reference |
| `source:run_serial:event_id` | `build:3:1` | Event reference |
| `run_serial` | `3` | Run without source (any command) |
| `run_serial:event_id` | `3:1` | Event without source |

- `source` = registered command name (build, test, lint)
- `run_serial` = sequential run number (1, 2, 3...)
- `event_id` = event index within run (1, 2, 3...)

## Macros

Table-returning macros for common queries. Use with `SELECT * FROM macro_name()`.

### Data Loading

| Macro | Description |
|-------|-------------|
| `blq_load_events()` | All events with run metadata joined |
| `blq_load_runs()` | Completed runs with event counts |
| `blq_load_attempts()` | All attempts with status (pending/completed/orphaned) |
| `blq_load_source_status()` | Latest run per source with status badge |

### Quick Queries

| Macro | Description |
|-------|-------------|
| `blq_status()` | Status overview (latest run per source) |
| `blq_errors(n := 10)` | Recent errors |
| `blq_warnings(n := 10)` | Recent warnings |
| `blq_history(n := 20)` | Run history |
| `blq_running()` | Currently running commands |
| `blq_diff(run1, run2)` | Compare errors between two runs |

### Utilities

| Macro | Description |
|-------|-------------|
| `blq_status_badge(errors, warnings, exit_code)` | Format status badge |
| `blq_ref(run_id, event_id)` | Build reference string |
| `blq_parse_ref(ref)` | Parse reference into components |
| `blq_output(inv_id, stream := 'combined')` | Get output content |

## Example Queries

### Basic Queries

```sql
-- Recent errors
SELECT * FROM blq_errors(20);

-- All errors from a specific run
SELECT * FROM blq_load_events()
WHERE run_serial = 5 AND severity = 'error';

-- Status overview
SELECT * FROM blq_status();
```

### Filtering Events

```sql
-- Errors in a specific file
SELECT ref, message, ref_line
FROM blq_load_events()
WHERE severity = 'error' AND ref_file LIKE '%parser.c';

-- Errors by fingerprint (deduplicated)
SELECT fingerprint, COUNT(*) as occurrences, MIN(message) as message
FROM blq_load_events()
WHERE severity = 'error'
GROUP BY fingerprint
ORDER BY occurrences DESC;

-- Events from the last hour
SELECT *
FROM blq_load_events()
WHERE started_at > now() - INTERVAL '1 hour';
```

### Run Analysis

```sql
-- Failed runs in the last week
SELECT source_name, run_serial, exit_code, error_count, started_at
FROM blq_load_runs()
WHERE exit_code != 0 AND started_at > now() - INTERVAL '7 days'
ORDER BY started_at DESC;

-- Average duration by command
SELECT source_name,
       AVG(duration_ms) / 1000.0 as avg_seconds,
       COUNT(*) as run_count
FROM blq_load_runs()
GROUP BY source_name;

-- Commands currently running
SELECT source_name, command, elapsed_ms / 1000.0 as seconds
FROM blq_load_attempts()
WHERE status = 'pending';
```

### Comparing Runs

```sql
-- Errors that appear in run 5 but not run 4
SELECT * FROM blq_diff(4, 5);

-- Manual diff using fingerprints
WITH run4 AS (
    SELECT DISTINCT fingerprint FROM blq_load_events()
    WHERE run_serial = 4 AND severity = 'error'
),
run5 AS (
    SELECT DISTINCT fingerprint, message, ref_file, ref_line
    FROM blq_load_events()
    WHERE run_serial = 5 AND severity = 'error'
)
SELECT r5.* FROM run5 r5
LEFT JOIN run4 r4 ON r5.fingerprint = r4.fingerprint
WHERE r4.fingerprint IS NULL;  -- New errors in run 5
```

### Git Integration

```sql
-- Errors by commit
SELECT git_commit, git_branch, COUNT(*) as error_count
FROM blq_load_events()
WHERE severity = 'error'
GROUP BY git_commit, git_branch
ORDER BY error_count DESC;

-- Runs with uncommitted changes
SELECT source_name, run_serial, error_count
FROM blq_load_runs()
WHERE git_dirty = true;
```

### Output Access

```sql
-- Get output info for a run
SELECT o.stream, o.byte_length, o.storage_type
FROM outputs o
JOIN invocations i ON o.invocation_id = i.id
JOIN (SELECT id, ROW_NUMBER() OVER (ORDER BY timestamp) as run_serial
      FROM invocations) numbered ON i.id = numbered.id
WHERE numbered.run_serial = 5;

-- Using the macro
SELECT * FROM blq_output(
    (SELECT id FROM invocations ORDER BY timestamp LIMIT 1 OFFSET 4),
    'combined'
);
```

### Advanced Analysis

```sql
-- Most common error messages
SELECT LEFT(message, 80) as message_prefix, COUNT(*) as count
FROM blq_load_events()
WHERE severity = 'error'
GROUP BY message_prefix
ORDER BY count DESC
LIMIT 10;

-- Error rate over time (by day)
SELECT date,
       SUM(error_count) as total_errors,
       COUNT(*) as run_count,
       SUM(error_count) * 1.0 / COUNT(*) as errors_per_run
FROM blq_load_runs()
GROUP BY date
ORDER BY date DESC;

-- Files with most errors
SELECT ref_file, COUNT(*) as error_count
FROM blq_load_events()
WHERE severity = 'error' AND ref_file IS NOT NULL
GROUP BY ref_file
ORDER BY error_count DESC
LIMIT 10;
```

## Direct Database Access

```bash
# Using DuckDB CLI
duckdb .lq/blq.duckdb "SELECT * FROM blq_status()"

# Using blq sql command
blq sql "SELECT * FROM blq_errors(5)"

# Interactive shell
blq shell
```
