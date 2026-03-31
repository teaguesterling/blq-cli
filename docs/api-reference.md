# API Reference

Complete reference for blq's Python API and MCP tool schemas. For tutorial-style usage, see [Python API Guide](python-api.md) and [MCP Guide](mcp.md).

Source files: [`src/blq/query.py`](../src/blq/query.py), [`src/blq/storage.py`](../src/blq/storage.py), [`src/blq/services/`](../src/blq/services/), [`src/blq/serve.py`](../src/blq/serve.py)

---

## BlqStorage

**Module:** `blq.storage`

Low-level storage interface backed by BIRD (DuckDB tables + content-addressed blobs). Query methods return `DuckDBPyRelation` objects -- call `.df()` for DataFrame or `.fetchall()` for tuples.

### Construction

```python
from blq.storage import BlqStorage

storage = BlqStorage.open()                  # auto-find .bird from cwd
storage = BlqStorage.open("/path/to/.bird")  # explicit path
```

Supports context manager:

```python
with BlqStorage.open() as storage:
    errors = storage.errors().df()
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `path` | `Path` | Path to `.bird` directory |
| `connection` | `duckdb.DuckDBPyConnection` | Underlying DuckDB connection |

### Data Checks

```python
storage.has_data() -> bool       # any runs exist
storage.has_runs() -> bool       # alias for has_data()
storage.has_events() -> bool     # any parsed events exist
```

### Run Queries

```python
storage.runs(limit: int | None = None) -> DuckDBPyRelation
```
All runs with aggregated event counts, newest first. Columns: `run_id`, `source_name`, `source_type`, `command`, `tag`, `started_at`, `completed_at`, `exit_code`, `cwd`, `executable_path`, `hostname`, `platform`, `arch`, `git_commit`, `git_branch`, `git_dirty`, `ci`, `event_count`, `error_count`, `warning_count`.

```python
storage.run(run_id: int) -> DuckDBPyRelation
storage.latest_run_id() -> int | None
```

### Event Queries

```python
storage.events(
    run_id: int | None = None,
    severity: str | list[str] | None = None,
    limit: int | None = None,
) -> DuckDBPyRelation
```

```python
storage.errors(run_id: int | None = None, limit: int = 20) -> DuckDBPyRelation
storage.warnings(run_id: int | None = None, limit: int = 20) -> DuckDBPyRelation
storage.event(run_serial: int, event_id: int) -> dict[str, Any] | None
storage.error_count(run_id: int | None = None) -> int
storage.warning_count(run_id: int | None = None) -> int
```

### Status

```python
storage.status() -> DuckDBPyRelation       # blq_status() summary
storage.source_status() -> DuckDBPyRelation # per-source latest run
```

### Output

```python
storage.get_output(run_id: str | int, stream: str | None = None) -> bytes | None
storage.get_output_info(run_id: str | int) -> list[dict[str, Any]]
```
`stream` accepts `'stdout'`, `'stderr'`, `'combined'`, or `None` (any).

### SQL

```python
storage.sql(query: str, params: list | None = None)
    -> DuckDBPyRelation | DuckDBPyConnection
```
Without `params`, returns a relation. With `params` (using `?` placeholders), returns a connection result. Both support `.fetchall()` and `.fetchone()`.

```python
storage.sql("SELECT * FROM blq_load_events() WHERE fingerprint = ?", [fp])
```

### Write Operations

```python
storage.write_run(
    run_meta: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    output: bytes | None = None,
) -> str  # returns invocation UUID
```

`run_meta` keys: `command`, `source_name`, `source_type`, `exit_code`, `started_at`, `completed_at`, `cwd`, `hostname`, `platform`, `arch`, `git_commit`, `git_branch`, `git_dirty`, `ci`, `environment`.

### Maintenance

```python
storage.prune(days: int = 30) -> int                    # remove old data
storage.prune_by_max_runs(max_runs: int) -> int          # keep N per source
storage.prune_by_size(max_size_mb: int) -> int            # cap total output size
storage.cleanup_blobs() -> tuple[int, int]                # (deleted, bytes_freed)
storage.total_output_size() -> int                        # total bytes
```

---

## LogStore

**Module:** `blq.query`

Higher-level query API with fluent `LogQuery` builder. Returns pandas DataFrames.

### Construction

```python
from blq.query import LogStore

store = LogStore.open()                    # auto-find .bird
store = LogStore.open("/path/to/.bird")    # explicit path
store = LogStore("/path/to/.bird")         # direct init
store = LogStore.from_parquet_root("path") # raw parquet directory
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `path` | `Path` | `.bird` directory path |
| `logs_path` | `Path` | Logs subdirectory path |
| `connection` | `duckdb.DuckDBPyConnection` | DuckDB connection |

### Query Methods

```python
store.events() -> LogQuery            # all events
store.errors() -> LogQuery            # severity='error'
store.warnings() -> LogQuery          # severity='warning'
store.run(run_id: int) -> LogQuery    # events from one run
store.runs() -> pd.DataFrame          # run summaries
store.latest_run() -> int | None      # most recent run_id
store.event(run_id: int, event_id: int) -> dict[str, Any] | None
store.has_data() -> bool
```

---

## LogQuery

**Module:** `blq.query`

Fluent query builder wrapping a DuckDB relation. All operations are deferred until a terminal method is called.

### Construction

```python
from blq.query import LogQuery

LogQuery.from_file(path, format="auto", conn=None) -> LogQuery
LogQuery.from_content(content: str, format="auto", conn=None) -> LogQuery
LogQuery.from_sql(conn, sql, params=None) -> LogQuery
LogQuery.from_table(conn, table_name) -> LogQuery
LogQuery.from_parquet(path, conn=None, hive_partitioning=True) -> LogQuery
LogQuery.from_relation(rel, conn) -> LogQuery
```

### Filtering

```python
.filter(severity="error")              # exact match
.filter(severity=["error", "warning"]) # IN clause
.filter(ref_file="%main%")             # ILIKE pattern
.filter(severity="!info")              # NOT equal
.filter(ref_line=100)                  # numeric equality
.filter(severity=None)                 # IS NULL
.filter("ref_line > 100")              # raw SQL condition
.exclude(severity="info")              # NOT (severity = 'info')
.where("ref_line BETWEEN 10 AND 50")   # raw SQL WHERE
```

### Projection

```python
.select(*columns: str) -> LogQuery
.order_by(*columns: str, desc: bool = False) -> LogQuery
.limit(n: int) -> LogQuery
```

### Terminal Methods

```python
.df() -> pd.DataFrame
.fetchall() -> list[tuple]
.fetchone() -> tuple | None
.count() -> int
.exists() -> bool
.show(n: int = 10) -> None          # print to stdout
.explain() -> str                    # query plan
.describe() -> pd.DataFrame         # statistics
```

### Inspection

```python
.columns -> list[str]
.dtypes -> list[str]
```

### Aggregation

```python
.group_by(*columns) -> LogQueryGrouped
.value_counts(column: str) -> pd.DataFrame
```

---

## LogQueryGrouped

**Module:** `blq.query`

Returned by `LogQuery.group_by()`. All methods return `pd.DataFrame`.

```python
grouped = query.group_by("ref_file")
grouped.count() -> pd.DataFrame
grouped.sum(column: str) -> pd.DataFrame
grouped.avg(column: str) -> pd.DataFrame
grouped.min(column: str) -> pd.DataFrame
grouped.max(column: str) -> pd.DataFrame
grouped.agg(**aggregations: str) -> pd.DataFrame
```

`agg` example: `.agg(total="COUNT(*)", first_line="MIN(ref_line)")`

---

## Service Layer

**Module:** `blq.services`

Pure business logic shared by CLI and MCP. All query functions take `BlqStorage` as the first argument and return structured dicts/lists.

### Refs

```python
from blq.services import parse_ref, resolve_run_ref, ParsedRef
```

```python
parse_ref(ref: str) -> ParsedRef
```
Parses ref strings into structured form. Raises `ValueError` on invalid input.

| Input | Result |
|-------|--------|
| `"5"` | `ParsedRef(run_serial=5)` |
| `"build:3"` | `ParsedRef(tag="build", run_serial=3)` |
| `"test:5:2"` | `ParsedRef(tag="test", run_serial=5, event_id=2)` |
| `"5:2"` | `ParsedRef(run_serial=5, event_id=2)` |
| `"~1"` | `ParsedRef(relative=1)` |
| `"test:~2"` | `ParsedRef(tag="test", relative=2)` |
| UUID | `ParsedRef(uuid="...")` |

`ParsedRef` properties: `is_relative -> bool`, `run_ref -> str`.

```python
resolve_run_ref(storage: BlqStorage, ref: str) -> dict | None
```
Resolves a ref string to a run data dict. Returns `None` if not found.

### Queries

```python
from blq.services import query_status, query_history, query_events, query_diff
```

```python
query_status(storage: BlqStorage) -> list[dict[str, Any]]
```
Returns per-source status: `name`, `status`, `error_count`, `warning_count`, `last_run`, `run_ref`, `run_serial`.

```python
query_history(
    storage: BlqStorage,
    limit: int = 20,
    source: str | None = None,
    status: str | None = None,   # 'running', 'completed', 'orphaned'
) -> list[dict[str, Any]]
```
Returns: `run_ref`, `run_serial`, `source_name`, `status`, `error_count`, `warning_count`, `started_at`, `exit_code`, `command`, `git_commit`, `git_branch`, `git_dirty`.

```python
query_events(
    storage: BlqStorage,
    severity: str | None = None,       # 'error', 'warning', or comma-separated
    run_id: int | None = None,
    source: str | None = None,
    file_pattern: str | None = None,
    limit: int = 20,
    default_to_latest: bool = False,
    suppressed_fingerprints: list[str] | None = None,
    all_runs: bool = False,
) -> dict[str, Any]                    # {"events": [...], "total_count": int}
```

```python
query_diff(storage: BlqStorage, run1: int, run2: int) -> dict[str, Any]
```
Returns: `summary` (`run1_errors`, `run2_errors`, `fixed`, `new`, `unchanged`), `fixed` (list), `new` (list).

### Inspect

```python
from blq.services import (
    get_source_context, get_log_context,
    get_git_context, get_fingerprint_history,
)
```

```python
get_source_context(
    ref_file: str | None, ref_line: int | None,
    source_root: Path, context_lines: int = 3,
) -> str | None

get_log_context(
    storage: BlqStorage | None, run_id: int,
    log_line_start: int | None, log_line_end: int | None,
    context_lines: int = 3,
) -> str | None

get_git_context(
    ref_file: str | None, ref_line: int | None,
    source_root: Path, history_limit: int = 2,
) -> dict[str, Any] | None    # {file, line, blame, recent_commits}

get_fingerprint_history(
    storage: BlqStorage | None, fingerprint: str | None,
) -> dict[str, Any] | None    # {fingerprint, first_seen, last_seen, occurrences, is_regression}
```

### Execution

```python
from blq.services import run_result_to_concise

run_result_to_concise(full_result: dict[str, Any], source_name: str) -> dict[str, Any]
```
Converts a `RunResult.to_json()` dict into the concise response format with keys: `run_ref`, `cmd`, `status`, `exit_code`, `duration_sec`, `summary`, `output_stats`. Conditionally includes `errors` (max 10), `warnings` (max 5), `infos` (max 5).

---

## MCP Tools

MCP server started via `blq mcp serve`. Tools are callable by any MCP client.

### run

Run a registered command and capture output.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` | required | Registered command name |
| `args` | `dict[str,str] \| list[str] \| None` | `None` | Named args (dict) or positional args (list) |
| `extra` | `list[str] \| None` | `None` | Passthrough arguments appended to command |
| `timeout` | `int \| None` | `None` | Timeout in seconds |
| `lines` | `str \| None` | `None` | Line selection for inline output (e.g. `'+20-'`) |
| `commands` | `list[str] \| None` | `None` | Batch mode: run multiple commands in sequence |
| `stop_on_failure` | `bool` | `True` | Stop batch on first failure |

**Returns:** `{run_ref, cmd, status, exit_code, duration_sec, summary, output_stats, errors?, warnings?}`

### exec

Execute an ad-hoc shell command. Do not use pipes/redirects -- use `output()` tool instead.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` | required | Shell command (no pipes) |
| `args` | `list[str] \| None` | `None` | Additional arguments |
| `timeout` | `int \| None` | `None` | Timeout in seconds |
| `shell` | `bool` | `False` | Allow shell syntax |
| `lines` | `str \| None` | `None` | Inline output line selection |

**Returns:** Same shape as `run`.

### status

No parameters. Returns `{sources: [{name, status, error_count, warning_count, last_run, run_ref}]}`.

### events

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | `int` | `20` | Max events |
| `run_id` | `int \| None` | `None` | Filter by run serial |
| `source` | `str \| None` | `None` | Filter by source name |
| `severity` | `str \| None` | `None` | `'error'`, `'warning'`, or comma-separated |
| `file_pattern` | `str \| None` | `None` | SQL LIKE pattern for `ref_file` |
| `all_runs` | `bool` | `False` | Show all runs (default: most recent only) |
| `run_ids` | `list[int] \| None` | `None` | Batch mode: multiple run IDs |
| `limit_per_run` | `int` | `10` | Max events per run in batch mode |

**Returns:** `{events: [...], total_count: int}`

### inspect

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | `str` | required | Event ref (e.g. `"build:1:3"`) |
| `lines` | `int` | `5` | Context lines before/after |
| `include_log_context` | `bool` | `True` | Include log output context |
| `include_source_context` | `bool` | `True` | Include source file context |
| `include_git_context` | `bool` | `False` | Include git blame/history |
| `include_fingerprint_history` | `bool` | `False` | Include occurrence history |
| `refs` | `list[str] \| None` | `None` | Batch mode: multiple refs |

**Returns:** `{ref, severity, ref_file, ref_line, message, log_context?, source_context?, git_context?, fingerprint_history?}`

### info

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | `str \| None` | `None` | Run ref or UUID. `None` = most recent |
| `head` | `int \| None` | `None` | First N lines of output |
| `tail` | `int \| None` | `None` | Last N lines of output |
| `errors` | `bool` | `False` | Include error events |
| `warnings` | `bool` | `False` | Include warning events |
| `severity` | `str \| None` | `None` | Filter events by severity |
| `limit` | `int` | `20` | Max events |
| `context` | `int \| None` | `None` | Log context lines around each event |

**Returns:** `{run_ref, status, exit_code, command, started_at, events?, output?, summary?}`

### history

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | `int` | `20` | Max runs |
| `source` | `str \| None` | `None` | Filter by source name |
| `status` | `str \| None` | `None` | `'running'`, `'completed'`, `'orphaned'` |

**Returns:** `{runs: [...]}`

### query

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sql` | `str \| None` | `None` | Raw SQL query |
| `filter` | `str \| None` | `None` | Filter expression (e.g. `"severity=error ref_file~test"`) |
| `limit` | `int` | `100` | Max rows |

Filter syntax: `key=value` (exact), `key=v1,v2` (IN), `key~pattern` (ILIKE), `key!=value` (not equal). Space-separated filters are AND'd.

**Returns:** `{columns, rows, row_count}`

### output

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | `str` | required | Run ref (e.g. `'5'`, `'test:3'`, `'+1'`) |
| `stream` | `str \| None` | `None` | `'stdout'`, `'stderr'`, `'combined'` |
| `tail` | `int \| None` | `None` | Last N lines |
| `head` | `int \| None` | `None` | First N lines |
| `grep` | `str \| None` | `None` | Regex search pattern |
| `context` | `int` | `0` | Context lines around grep matches |
| `lines` | `str \| None` | `None` | Line spec (e.g. `'100-200'`) |
| `debug_formats` | `bool` | `False` | Show format detection info |

**Returns:** `{output, byte_length, total_lines, ...}`

### diff

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `run1` | `int` | required | Baseline run serial |
| `run2` | `int` | required | Comparison run serial |

**Returns:** `{summary: {run1_errors, run2_errors, fixed, new, unchanged}, fixed: [...], new: [...]}`

### commands

No parameters. Returns `{commands: [{name, cmd, description, ...}]}`.

### register_command

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Command name |
| `cmd` | `str \| None` | `None` | Command string |
| `tpl` | `str \| None` | `None` | Template with `{param}` placeholders |
| `defaults` | `dict[str,str] \| None` | `None` | Default template parameter values |
| `description` | `str` | `""` | Description |
| `timeout` | `int \| None` | `None` | Timeout in seconds |
| `capture` | `bool` | `True` | Capture and parse output |
| `force` | `bool` | `False` | Overwrite existing |
| `format` | `str \| None` | `None` | Log format hint |
| `run_now` | `bool` | `False` | Run immediately after registering |
| `lines` | `str \| None` | `None` | Default output line selection |
| `sandbox` | `str \| dict \| None` | `None` | Sandbox preset or spec dict |
| `lock` | `str \| None` | `None` | Lock name for concurrency control |

**Returns:** `{success, command, run?}`

### unregister_command

| Parameter | Type | Default |
|-----------|------|---------|
| `name` | `str` | required |

**Returns:** `{success: bool}`

### clean

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | `str` | `"data"` | `'data'`, `'prune'`, `'schema'`, `'full'` |
| `confirm` | `bool` | `False` | Must be `True` to proceed |
| `days` | `int \| None` | `None` | Prune: remove older than N days |
| `max_runs` | `int \| None` | `None` | Prune: keep N per source |
| `max_size_mb` | `int \| None` | `None` | Prune: cap total output size |

**Returns:** `{success, message, mode}`

### report

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ref` | `str \| None` | `None` | Run ref (default: latest) |
| `baseline` | `str \| None` | `None` | Baseline run, branch, or commit |
| `warnings` | `bool` | `False` | Include warnings |
| `summary_only` | `bool` | `False` | Omit individual error details |
| `error_limit` | `int` | `20` | Max errors in details |
| `file_limit` | `int` | `10` | Max files in breakdown |

**Returns:** `{report: "markdown...", run_id, total_errors, total_warnings, has_baseline}`

### ci_check

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `baseline` | `str \| None` | `None` | Baseline (auto-detects main/master) |
| `fail_on_any` | `bool` | `False` | Fail on any errors |
| `run_id` | `int \| None` | `None` | Run to check (default: auto-detect) |

**Returns:** `{status: 'OK'|'FAIL', current_run_id, new_errors?, fixed?}`

### ci_generate

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `commands` | `list[str] \| None` | `None` | Commands to generate (default: all) |
| `shell` | `str` | `"bash"` | `'bash'`, `'sh'`, `'zsh'` |

**Returns:** `{scripts: [{name, content, ...}]}`

### sandbox_info

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str \| None` | `None` | Command name (omit for all) |

**Returns:** JSON with sandbox spec, grades, and resource metrics.

---

## MCP Resources

| URI | Type | Description |
|-----|------|-------------|
| `blq://status` | JSON | Current per-source status |
| `blq://runs` | JSON | Run history (up to 100) |
| `blq://events` | JSON | Recent error events |
| `blq://event/{ref}` | JSON | Single event details |
| `blq://errors` | JSON | Recent errors (up to 50) |
| `blq://errors/{run_serial}` | JSON | Errors for a specific run |
| `blq://warnings` | JSON | Recent warnings (up to 50) |
| `blq://warnings/{run_serial}` | JSON | Warnings for a specific run |
| `blq://context/{ref}` | JSON | Log context around an event |
| `blq://commands` | JSON | Registered commands |
| `blq://guide` | Markdown | Agent usage guide |
