# Integration Guide

This guide covers integrating lq with AI agents, CI/CD pipelines, and other tools.

## AI Agent Integration

bblq is designed to work well with AI coding assistants like Claude, GPT, and others.

### Structured Output

Use `--json` for machine-readable output:

```bash
blq run --json --quiet make
```

Output:
```json
{
  "run_id": 1,
  "status": "FAIL",
  "exit_code": 2,
  "errors": [
    {
      "ref": "1:1",
      "file_path": "src/main.c",
      "line_number": 15,
      "message": "undefined variable 'foo'"
    }
  ]
}
```

### Drill-Down Workflow

The structured output includes event references that agents can use to get more context:

```bash
# Agent runs build, gets error summary
blq run --json make

# Agent sees ref "1:1", gets details
blq event 1:1

# Agent needs more context
blq context 1:1 --lines 5
```

### Query for Analysis

Agents can query logs directly:

```bash
# Get all errors as JSON
blq q --json -f "severity='error'" build.log

# Count errors by file
blq sql "SELECT file_path, COUNT(*) as count
        FROM read_duck_hunt_log('build.log', 'auto')
        WHERE severity='error'
        GROUP BY 1
        ORDER BY 2 DESC"
```

### Markdown for Reports

For generating reports or PR comments:

```bash
blq run --markdown make
blq q --markdown -s file_path,line_number,message build.log
```

## CI/CD Integration

### GitHub Actions

```yaml
- name: Build with log capture
  run: |
    blq init
    blq run --json make > build_result.json
  continue-on-error: true

- name: Upload build results
  uses: actions/upload-artifact@v3
  with:
    name: build-logs
    path: |
      build_result.json
      .lq/logs/
```

### GitLab CI

```yaml
build:
  script:
    - blq init
    - blq run --json make | tee build_result.json
  artifacts:
    paths:
      - build_result.json
      - .lq/logs/
    when: always
```

### Jenkins

```groovy
pipeline {
    stages {
        stage('Build') {
            steps {
                sh 'blq init'
                sh 'blq run --json make > build_result.json || true'
                archiveArtifacts artifacts: 'build_result.json,.lq/logs/**'
            }
        }
    }
}
```

## Command Registry for CI

Register standard commands for consistent CI builds:

```bash
# Setup (in repo or CI init)
blq register build "make -j8" --description "Build project"
blq register test "pytest -v" --timeout 600
blq register lint "ruff check ." --format eslint

# CI script
blq run build
blq run test
blq run lint
```

Store `commands.yaml` in your repo for reproducibility.

## MCP Server Integration

bblq is designed to work with MCP (Model Context Protocol) servers for AI agent access.

### With duckdb_mcp

```sql
-- Expose lq_events as a queryable resource
ATTACH ':memory:' AS lq_db;

-- Load lq schema
.read .lq/schema.sql

-- Publish as MCP tool
SELECT mcp_publish_tool(
    'lq_errors',
    'Get recent build errors',
    'SELECT * FROM lq_events WHERE severity = ''error'' ORDER BY run_id DESC LIMIT 20',
    '{}',
    '[]',
    'json'
);
```

### Future: lq serve

A dedicated MCP server for blq is planned:

```bash
blq serve --port 8080
```

This will expose:
- Query endpoints
- Event detail endpoints
- Log capture endpoints

## Shell Integration

### Bash Alias

```bash
# In ~/.bashrc
alias make='blq run make'
alias pytest='blq run pytest'
```

### Fish Function

```fish
function make --wraps make
    blq run make $argv
end
```

### Zsh Hook

```zsh
# Capture all failed commands
preexec() {
    if [[ $? -ne 0 ]]; then
        blq import /tmp/last_output.log --name "$1"
    fi
}
```

## Data Export

### Export to Parquet

The data is already in parquet format:

```bash
cp -r .lq/logs/ /path/to/export/
```

### Export to CSV

```bash
blq sql "COPY (SELECT * FROM lq_events) TO 'events.csv' (HEADER)"
```

### Export to JSON Lines

```bash
blq sql "COPY (SELECT * FROM lq_events) TO 'events.jsonl'"
```

## Programmatic Access

### Python API

bblq provides a fluent Python API for programmatic access:

```python
from blq import LogStore, LogQuery

# Open the repository
store = LogStore.open()

# Query errors with chaining
errors = (
    store.errors()
    .filter(file_path="%main%")
    .select("file_path", "line_number", "message")
    .order_by("line_number")
    .limit(10)
    .df()
)

# Query a log file directly (without storing)
events = LogQuery.from_file("build.log").filter(severity="error").df()

# Aggregations
errors_by_file = store.errors().group_by("file_path").count()
severity_counts = store.events().value_counts("severity")
```

See [Python API Guide](python-api.md) for full documentation.

### Direct SQL Access

For complex queries, use the underlying DuckDB connection:

```python
from blq import LogStore

store = LogStore.open()
conn = store.connection

# Run arbitrary SQL
result = conn.sql("""
    SELECT file_path, COUNT(*) as count
    FROM lq_events
    WHERE severity = 'error'
    GROUP BY file_path
    ORDER BY count DESC
""").df()
```

### Direct Parquet Access

Any tool that reads parquet can access the data:

```python
import pandas as pd
df = pd.read_parquet('.lq/logs/')
```

```r
library(arrow)
df <- read_parquet('.lq/logs/')
```
