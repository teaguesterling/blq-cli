# Python API Guide

bblq provides a fluent Python API for programmatic access to log data. This guide covers the `LogQuery` and `LogStore` classes.

## Quick Start

```python
from blq import LogStore, LogQuery

# Open the repository
store = LogStore.open()

# Get recent errors
errors = store.errors().limit(10).df()

# Query with filtering and selection
results = (
    store.events()
    .filter(severity="error")
    .filter(file_path="%main%")
    .select("file_path", "line_number", "message")
    .order_by("line_number")
    .limit(10)
    .df()
)

# Query a log file directly (without storing)
events = (
    LogQuery.from_file("build.log")
    .filter(severity=["error", "warning"])
    .df()
)
```

## LogStore

`LogStore` manages the `.lq` repository and provides access to stored events.

### Opening a Store

```python
from blq import LogStore

# Auto-find .lq in current or parent directories
store = LogStore.open()

# Explicit path
store = LogStore.open("/path/to/project/.lq")
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `path` | `Path` | Path to `.lq` directory |
| `logs_path` | `Path` | Path to logs subdirectory |
| `connection` | `DuckDBPyConnection` | Underlying DuckDB connection |

### Methods

#### `events() -> LogQuery`

Query all stored events.

```python
all_events = store.events().df()
```

#### `errors() -> LogQuery`

Convenience method for querying errors (equivalent to `.events().filter(severity="error")`).

```python
errors = store.errors().df()
```

#### `warnings() -> LogQuery`

Convenience method for querying warnings.

```python
warnings = store.warnings().df()
```

#### `run(run_id: int) -> LogQuery`

Query events from a specific run.

```python
# Get all events from run 1
run_events = store.run(1).df()

# Get errors from run 1
run_errors = store.run(1).filter(severity="error").df()
```

#### `runs() -> DataFrame`

Get summary of all runs.

```python
runs = store.runs()
# Returns: run_id, source_name, source_type, command, started_at, completed_at, exit_code
```

#### `latest_run() -> int | None`

Get the most recent run ID.

```python
latest = store.latest_run()  # e.g., 5
```

#### `event(run_id: int, event_id: int) -> dict | None`

Get a specific event by reference.

```python
event = store.event(1, 3)  # Equivalent to ref "1:3"
if event:
    print(event["message"])
```

#### `has_data() -> bool`

Check if the store has any data.

```python
if store.has_data():
    print(f"Latest run: {store.latest_run()}")
else:
    print("No logs stored yet")
```

## LogQuery

`LogQuery` provides a fluent interface for building queries. Operations are deferred until a terminal method is called.

### Creating Queries

#### From LogStore

```python
store = LogStore.open()
query = store.events()
```

#### From Log File

Parse a log file using the duck_hunt extension:

```python
query = LogQuery.from_file("build.log")
query = LogQuery.from_file("output.log", format="gcc")
```

#### From Parquet Files

Read parquet files directly:

```python
query = LogQuery.from_parquet(".lq/logs/")
query = LogQuery.from_parquet("events.parquet", hive_partitioning=False)
```

#### From SQL

Create a query from raw SQL:

```python
conn = store.connection
query = LogQuery.from_sql(conn, "SELECT * FROM lq_events WHERE run_id = 1")
```

#### From Table

Query a table or view directly:

```python
conn = store.connection
query = LogQuery.from_table(conn, "lq_events")
```

### Filtering

#### `filter(_condition=None, **kwargs) -> LogQuery`

Filter rows by conditions. Multiple calls are combined with AND.

**Exact match:**
```python
query.filter(severity="error")
query.filter(run_id=1)
```

**Multiple values (IN clause):**
```python
query.filter(severity=["error", "warning"])
```

**LIKE pattern:**
```python
query.filter(file_path="%main%")     # Contains 'main'
query.filter(file_path="%.py")       # Ends with '.py'
query.filter(message="%undefined%")  # Contains 'undefined'
```

**Raw SQL condition:**
```python
query.filter("line_number > 100")
query.filter("file_path LIKE '%test%' AND severity = 'error'")
```

**Multiple conditions (AND):**
```python
query.filter(severity="error", file_path="main.c")
# Equivalent to:
query.filter(severity="error").filter(file_path="main.c")
```

#### `exclude(**kwargs) -> LogQuery`

Exclude rows matching conditions (NOT filter).

```python
query.exclude(severity="info")         # NOT severity = 'info'
query.exclude(file_path="%test%")      # NOT file_path LIKE '%test%'
```

#### `where(condition: str) -> LogQuery`

Add a raw SQL WHERE condition.

```python
query.where("line_number BETWEEN 10 AND 50")
query.where("file_path IS NOT NULL")
```

### Projection

#### `select(*columns) -> LogQuery`

Select specific columns.

```python
query.select("file_path", "line_number", "message")
```

#### `order_by(*columns, desc=False) -> LogQuery`

Order results.

```python
query.order_by("line_number")
query.order_by("severity", "line_number")
query.order_by("run_id", desc=True)  # Descending
```

#### `limit(n: int) -> LogQuery`

Limit number of results.

```python
query.limit(10)
```

### Execution Methods

These methods execute the query and return results.

#### `df() -> DataFrame`

Execute and return a pandas DataFrame.

```python
df = query.df()
```

#### `fetchall() -> list[tuple]`

Execute and return all rows as tuples.

```python
rows = query.fetchall()
for row in rows:
    print(row)
```

#### `fetchone() -> tuple | None`

Execute and return the first row.

```python
row = query.filter(run_id=1, event_id=1).fetchone()
```

#### `count() -> int`

Count matching rows.

```python
error_count = query.filter(severity="error").count()
```

#### `exists() -> bool`

Check if any rows match.

```python
if query.filter(severity="error").exists():
    print("Found errors")
```

### Inspection

#### `columns -> list[str]`

Get column names.

```python
cols = query.columns  # ['run_id', 'event_id', 'severity', ...]
```

#### `dtypes -> list[str]`

Get column types.

```python
types = query.dtypes  # ['BIGINT', 'BIGINT', 'VARCHAR', ...]
```

#### `describe() -> DataFrame`

Get statistical description.

```python
stats = query.describe()
```

#### `show(n=10) -> None`

Print first n rows to stdout.

```python
query.show()
query.show(20)
```

#### `explain() -> str`

Get the query execution plan.

```python
plan = query.explain()
print(plan)
```

### Aggregation

#### `group_by(*columns) -> LogQueryGrouped`

Group by columns for aggregation.

```python
grouped = query.group_by("file_path")
```

#### `value_counts(column) -> DataFrame`

Count occurrences of each value.

```python
severity_counts = query.value_counts("severity")
# Returns DataFrame with 'severity' and 'count' columns, sorted by count descending
```

## LogQueryGrouped

Returned by `group_by()`, provides aggregation methods.

### Methods

#### `count() -> DataFrame`

Count rows in each group.

```python
errors_by_file = store.errors().group_by("file_path").count()
```

#### `sum(column) -> DataFrame`

Sum values in each group.

```python
totals = query.group_by("category").sum("amount")
```

#### `avg(column) -> DataFrame`

Average values in each group.

```python
averages = query.group_by("file_path").avg("line_number")
```

#### `min(column) / max(column) -> DataFrame`

Minimum/maximum values in each group.

```python
first_errors = store.errors().group_by("file_path").min("line_number")
```

#### `agg(**aggregations) -> DataFrame`

Custom aggregations.

```python
stats = query.group_by("file_path").agg(
    total="COUNT(*)",
    first_line="MIN(line_number)",
    last_line="MAX(line_number)"
)
```

## Complete Examples

### Find Most Error-Prone Files

```python
from blq import LogStore

store = LogStore.open()
error_counts = (
    store.errors()
    .group_by("file_path")
    .count()
)
print(error_counts.head(10))
```

### Analyze Build Trends

```python
from blq import LogStore

store = LogStore.open()
runs = store.runs()

for _, run in runs.iterrows():
    errors = store.run(run["run_id"]).filter(severity="error").count()
    warnings = store.run(run["run_id"]).filter(severity="warning").count()
    print(f"Run {run['run_id']}: {errors} errors, {warnings} warnings")
```

### Filter and Export

```python
from blq import LogStore

store = LogStore.open()

# Get specific errors and export
errors = (
    store.errors()
    .filter(file_path="%src%")
    .filter("line_number < 100")
    .select("file_path", "line_number", "message")
    .order_by("file_path", "line_number")
    .df()
)

errors.to_csv("errors_report.csv", index=False)
```

### Query Log Files Without Storing

```python
from blq import LogQuery

# Parse and query a log file directly
errors = (
    LogQuery.from_file("build.log")
    .filter(severity="error")
    .select("file_path", "line_number", "message")
    .df()
)

# Check for specific patterns
if LogQuery.from_file("test.log").filter(message="%FAILED%").exists():
    print("Tests failed!")
```

### Direct SQL Access

```python
from blq import LogStore

store = LogStore.open()
conn = store.connection

# Run arbitrary SQL
result = conn.sql("""
    SELECT
        file_path,
        COUNT(*) as error_count,
        COUNT(DISTINCT error_fingerprint) as unique_errors
    FROM lq_events
    WHERE severity = 'error'
    GROUP BY file_path
    ORDER BY error_count DESC
    LIMIT 10
""").df()
```

## Integration with pandas

`LogQuery` returns pandas DataFrames, enabling further analysis:

```python
from blq import LogStore
import matplotlib.pyplot as plt

store = LogStore.open()

# Get error counts by severity
counts = store.events().value_counts("severity")

# Plot with pandas
counts.plot(kind="bar", x="severity", y="count")
plt.title("Events by Severity")
plt.savefig("severity_chart.png")
```
