# Python API Guide

blq provides a fluent Python API for programmatic access to log data.

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
    .filter(ref_file="%main%")
    .select("ref_file", "ref_line", "message")
    .order_by("ref_line")
    .limit(10)
    .df()
)

# Query a log file directly (without storing)
events = LogQuery.from_file("build.log").filter(severity="error").df()
```

---

## LogStore

Manages the `.lq` repository and provides access to stored events.

### Opening

```python
from blq import LogStore

store = LogStore.open()                    # Auto-find .lq
store = LogStore.open("/path/to/.lq")      # Explicit path
```

### Query Methods

```python
store.events()           # All events → LogQuery
store.errors()           # Errors only → LogQuery
store.warnings()         # Warnings only → LogQuery
store.run(5)             # Events from run 5 → LogQuery
store.runs()             # Run summaries → DataFrame
store.latest_run()       # Most recent run ID → int
store.event(5, 1)        # Specific event → dict
store.has_data()         # Has any data → bool
```

### Direct SQL

```python
conn = store.connection
result = conn.sql("""
    SELECT ref_file, COUNT(*) as errors
    FROM blq_load_events()
    WHERE severity = 'error'
    GROUP BY ref_file
""").df()
```

---

## LogQuery

Fluent interface for building queries. Operations are deferred until a terminal method is called.

### Creating Queries

```python
# From store
query = store.events()

# From log file
query = LogQuery.from_file("build.log")
query = LogQuery.from_file("output.log", format="gcc")

# From SQL
query = LogQuery.from_sql(conn, "SELECT * FROM blq_load_events() WHERE run_serial = 1")
```

### Filtering

```python
# Exact match
query.filter(severity="error")

# Multiple values (IN)
query.filter(severity=["error", "warning"])

# LIKE pattern
query.filter(ref_file="%main%")     # Contains 'main'
query.filter(ref_file="%.py")       # Ends with '.py'

# Raw SQL condition
query.filter("ref_line > 100")

# Multiple conditions (AND)
query.filter(severity="error", ref_file="main.c")

# Exclude
query.exclude(severity="info")
```

### Projection

```python
query.select("ref_file", "ref_line", "message")
query.order_by("ref_line")
query.order_by("run_serial", desc=True)
query.limit(10)
```

### Terminal Methods

```python
query.df()           # → pandas DataFrame
query.fetchall()     # → list[tuple]
query.fetchone()     # → tuple | None
query.count()        # → int
query.exists()       # → bool
query.show()         # Print to stdout
```

### Inspection

```python
query.columns        # Column names
query.dtypes         # Column types
query.describe()     # Statistics
query.explain()      # Query plan
```

---

## Aggregation

### Group By

```python
grouped = query.group_by("ref_file")

grouped.count()                           # Count per group
grouped.sum("ref_line")                   # Sum per group
grouped.avg("ref_line")                   # Average per group
grouped.min("ref_line")                   # Min per group
grouped.max("ref_line")                   # Max per group
grouped.agg(total="COUNT(*)", first="MIN(ref_line)")
```

### Value Counts

```python
query.value_counts("severity")
# → DataFrame with 'severity' and 'count' columns
```

---

## Examples

### Most Error-Prone Files

```python
from blq import LogStore

store = LogStore.open()
error_counts = store.errors().group_by("ref_file").count()
print(error_counts.head(10))
```

### Build Trends

```python
from blq import LogStore

store = LogStore.open()
for _, run in store.runs().iterrows():
    errors = store.run(run["run_id"]).filter(severity="error").count()
    print(f"Run {run['run_id']}: {errors} errors")
```

### Filter and Export

```python
from blq import LogStore

store = LogStore.open()

errors = (
    store.errors()
    .filter(ref_file="%src%")
    .filter("ref_line < 100")
    .select("ref_file", "ref_line", "message")
    .order_by("ref_file", "ref_line")
    .df()
)

errors.to_csv("errors_report.csv", index=False)
```

### Query Log File Directly

```python
from blq import LogQuery

# Parse without storing
errors = (
    LogQuery.from_file("build.log")
    .filter(severity="error")
    .select("ref_file", "ref_line", "message")
    .df()
)

# Check for failures
if LogQuery.from_file("test.log").filter(message="%FAILED%").exists():
    print("Tests failed!")
```

### Integration with pandas/matplotlib

```python
from blq import LogStore
import matplotlib.pyplot as plt

store = LogStore.open()
counts = store.events().value_counts("severity")

counts.plot(kind="bar", x="severity", y="count")
plt.title("Events by Severity")
plt.savefig("severity_chart.png")
```
