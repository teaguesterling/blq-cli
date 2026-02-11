# Inspect Command Enrichment (SKILL.md Update)

This section should be merged into SKILL.md under the "Tool Reference" or after the `inspect` tool description.

---

## Event Enrichment with `inspect`

The `inspect` tool supports optional enrichment to provide deeper context about errors:

### Enrichment Options

| Flag (CLI) | MCP Parameter | Description |
|------------|---------------|-------------|
| `--source` / `-s` | `include_source_context` | Source file lines around error location |
| `--git` / `-g` | `include_git_context` | Git blame and recent commits for the file |
| `--fingerprint` / `-f` | `include_fingerprint_history` | Error occurrence history and regression detection |
| `--full` | (set all to true) | Enable all enrichment |

### CLI Examples

```bash
# Basic inspect (log context always included)
blq inspect build:1:3

# With source file context
blq inspect build:1:3 --source

# With git blame and history
blq inspect build:1:3 --git

# With fingerprint tracking
blq inspect build:1:3 --fingerprint

# All enrichment combined
blq inspect build:1:3 --full

# JSON output with enrichment
blq inspect build:1:3 --full --json
```

### MCP Examples

```python
# Basic inspect
blq.inspect(ref="build:1:3")

# With git context
blq.inspect(ref="build:1:3", include_git_context=True)

# With fingerprint history
blq.inspect(ref="build:1:3", include_fingerprint_history=True)

# Full enrichment
blq.inspect(
    ref="build:1:3",
    include_source_context=True,
    include_git_context=True,
    include_fingerprint_history=True
)

# Batch mode with enrichment
blq.inspect(
    ref="build:1:1",
    refs=["build:1:1", "build:1:2", "build:1:3"],
    include_git_context=True
)
```

### Enrichment Output

#### Git Context (`--git` / `include_git_context`)

Shows who last modified the error location and recent changes to the file:

```
== Git Context ==
  Last modified: 2024-01-15 10:30 by alice@example.com
  Commit: abc1234

  Recent changes:
    abc1234 (2024-01-15) Refactor data processing
    def5678 (2024-01-10) Add error handling
    ghi9012 (2024-01-05) Initial implementation
```

JSON format:
```json
{
  "git_context": {
    "file": "src/main.py",
    "line": 42,
    "blame": {
      "author": "alice@example.com",
      "commit": "abc1234",
      "modified": "2024-01-15T10:30:00"
    },
    "recent_commits": [
      {"hash": "abc1234", "author": "alice", "time": "2024-01-15T10:30:00", "message": "Refactor data processing"}
    ]
  }
}
```

#### Fingerprint History (`--fingerprint` / `include_fingerprint_history`)

Tracks error occurrences across runs and detects regressions:

```
== Fingerprint History ==
  Fingerprint: 7f3a2b1c4d5e...
  First seen: build:1 (2024-01-10 09:00)
  Last seen: build:5 (2024-01-15 14:30)
  Occurrences: 4
  Status: REGRESSION (was fixed, reappeared)
```

JSON format:
```json
{
  "fingerprint_history": {
    "fingerprint": "7f3a2b1c4d5e...",
    "first_seen": {"run_ref": "build:1", "timestamp": "2024-01-10T09:00:00"},
    "last_seen": {"run_ref": "build:5", "timestamp": "2024-01-15T14:30:00"},
    "occurrences": 4,
    "is_regression": true
  }
}
```

### When to Use Enrichment

| Scenario | Recommended Flags |
|----------|-------------------|
| Quick error lookup | (none) - just log context |
| Understanding error location | `--source` |
| Finding who introduced a bug | `--git` |
| Checking if error is new or recurring | `--fingerprint` |
| Full investigation | `--full` |

### Performance Notes

- `--source`: Fast, reads local file
- `--git`: Moderate, runs git commands (faster with duck_tails extension)
- `--fingerprint`: Fast, queries events table
- Enrichment is computed on-demand, not stored with events
