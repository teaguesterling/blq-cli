# Design: Unified Git Integration with duck_tails

## Overview

Consolidate all git-related logic into a unified `blq.git` submodule, with optional duck_tails DuckDB extension support for richer queries and better performance.

## Goals

1. **Unified git logic**: Single source of truth for all git operations
2. **duck_tails integration**: Use DuckDB extension when available for SQL-native git queries
3. **Graceful fallback**: Subprocess-based fallback when duck_tails unavailable
4. **Richer context**: Enable git-aware event enrichment (blame, history, changes)
5. **Fingerprint tracking**: Track error recurrence across git history

## Current State

Git operations are scattered across the codebase:

| Location | Function | Implementation |
|----------|----------|----------------|
| `commands/core.py` | `capture_git_info()` | 3 subprocess calls |
| `commands/hooks_cmd.py` | `_find_git_dir()` | Path traversal |
| `commands/init_cmd.py` | Various | Subprocess for remote detection |

Each uses subprocess directly, with no caching or unified error handling.

## Proposed Architecture

### New Module: `src/blq/git.py`

```
src/blq/git.py
    │
    ├── GitContext (dataclass)
    │   ├── commit, branch, dirty (basic info)
    │   ├── author, commit_time, message (HEAD details)
    │   └── files_changed (files in HEAD commit)
    │
    ├── GitFileContext (dataclass)
    │   ├── last_author, last_modified (blame info)
    │   ├── recent_commits (history for file)
    │   └── recent_diff (changes summary)
    │
    ├── GitProvider (protocol)
    │   ├── DuckTailsProvider (uses duck_tails extension)
    │   └── SubprocessProvider (fallback, current behavior)
    │
    └── Public API
        ├── get_context() -> GitContext
        ├── get_file_context(path, line?) -> GitFileContext
        ├── get_blame(path, line) -> BlameInfo
        ├── get_file_history(path, limit) -> list[CommitInfo]
        ├── get_diff(path, from_ref, to_ref) -> DiffInfo
        ├── find_git_root() -> Path | None
        └── is_git_repo() -> bool
```

### Provider Selection

```python
def _get_provider(conn: duckdb.DuckDBPyConnection | None = None) -> GitProvider:
    """Get the best available git provider.

    Prefers duck_tails if:
    1. A DuckDB connection is provided
    2. duck_tails extension is available and loaded

    Falls back to subprocess otherwise.
    """
    if conn is not None:
        try:
            conn.execute("SELECT * FROM git_log() LIMIT 0")
            return DuckTailsProvider(conn)
        except duckdb.Error:
            pass
    return SubprocessProvider()
```

## Data Structures

### GitContext (run-level)

Captured at invocation time, stored in `invocations` table:

```python
@dataclass
class GitContext:
    """Git repository state at invocation time."""
    # Basic (current capture_git_info)
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None

    # Extended (new with duck_tails)
    author: str | None = None
    commit_time: datetime | None = None
    message: str | None = None
    files_changed: list[str] | None = None

    # Repository info
    remote_url: str | None = None
    repo_root: Path | None = None
```

### GitFileContext (event-level)

Computed on-demand for `blq inspect --git`:

```python
@dataclass
class GitFileContext:
    """Git context for a specific file location."""
    path: str
    line: int | None = None

    # Blame info (who last touched this line)
    last_author: str | None = None
    last_commit: str | None = None
    last_modified: datetime | None = None

    # Recent history
    recent_commits: list[CommitInfo] | None = None

    # Changes since reference (e.g., last successful run)
    changed_since: str | None = None  # reference commit
    diff_summary: str | None = None

@dataclass
class CommitInfo:
    """Summary of a git commit."""
    hash: str
    short_hash: str
    author: str
    time: datetime
    message: str
    files_changed: list[str] | None = None

@dataclass
class BlameInfo:
    """Blame information for a line."""
    commit: str
    author: str
    time: datetime
    line_content: str
```

## duck_tails Queries

### Run-Level Context

```sql
-- Get current HEAD info
SELECT
    commit_hash as commit,
    author,
    commit_time,
    message,
    files_changed
FROM git_log()
LIMIT 1;

-- Get current branch
SELECT name as branch
FROM git_branches()
WHERE is_head = true;
```

### File-Level Context

```sql
-- Blame for a specific line
SELECT
    commit_hash,
    author,
    commit_time,
    line_content
FROM git_blame(?)
WHERE line_number = ?;

-- Recent commits touching a file
SELECT
    commit_hash,
    author,
    commit_time,
    message
FROM git_log()
WHERE ? = ANY(files_changed)
ORDER BY commit_time DESC
LIMIT 5;

-- Diff since reference
SELECT *
FROM read_git_diff(?, ?, 'HEAD');
```

### Fingerprint History

```sql
-- All occurrences of an error fingerprint
SELECT
    e.run_serial,
    e.timestamp,
    e.tag,
    i.git_commit,
    i.git_branch
FROM events e
JOIN invocations i ON e.run_id = i.id
WHERE e.fingerprint = ?
ORDER BY e.timestamp;

-- Detect regression pattern (fixed then reappeared)
WITH error_timeline AS (
    SELECT
        run_serial,
        timestamp,
        LAG(run_serial) OVER (ORDER BY run_serial) as prev_run
    FROM events
    WHERE fingerprint = ?
)
SELECT
    run_serial,
    timestamp,
    (run_serial - prev_run) > 1 as is_regression
FROM error_timeline
WHERE prev_run IS NOT NULL;
```

## CLI Integration

### Enhanced `blq inspect`

```bash
# Current behavior (unchanged)
blq inspect build:1:3

# With source context (native file read)
blq inspect build:1:3 --source
blq inspect build:1:3 -s

# With git context (duck_tails or subprocess)
blq inspect build:1:3 --git
blq inspect build:1:3 -g

# With fingerprint history
blq inspect build:1:3 --fingerprint
blq inspect build:1:3 -f

# All enrichment
blq inspect build:1:3 --full
```

### Output Format

```
Error: build:1:3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Location: src/main.py:42:15
Message:  undefined variable 'foo'
Severity: error

Source Context:
  39 │ def process_data(items):
  40 │     results = []
  41 │     for item in items:
→ 42 │         results.append(foo(item))
  43 │     return results

Git Context:
  Last modified: 2024-01-15 by alice@example.com
  Commit: abc1234 "Refactor data processing"

  Recent changes to src/main.py:
    abc1234 (2 days ago)  Refactor data processing
    def5678 (5 days ago)  Add error handling
    ghi9012 (1 week ago)  Initial implementation

Fingerprint History:
  Fingerprint: 7f3a2b1c...
  First seen:  build:1 (2024-01-10)
  Occurrences: 3 runs
  Status:      Regression (was fixed in build:2, reappeared in build:4)
```

## MCP Integration

### Enhanced `inspect` tool

```python
def mcp_inspect(
    ref: str,
    refs: list[str] | None = None,
    lines: int = 5,
    source: bool = False,
    git: bool = False,
    fingerprint: bool = False,
) -> dict:
    """Get event details with optional enrichment."""
```

### New `git_context` tool (optional)

```python
def mcp_git_context(
    path: str,
    line: int | None = None,
    history_limit: int = 5,
) -> dict:
    """Get git context for a file location."""
```

## Migration Path

### Phase 1: Create `blq.git` module

1. Create `src/blq/git.py` with `SubprocessProvider`
2. Move `capture_git_info()` logic to new module
3. Update callers to use new API
4. Add tests

### Phase 2: Add duck_tails support

1. Implement `DuckTailsProvider`
2. Add provider selection logic
3. Add extended GitContext fields
4. Update run capture to use richer context

### Phase 3: Event enrichment

1. Implement `get_file_context()` and related methods
2. Add `--source`, `--git`, `--fingerprint` flags to `inspect`
3. Update MCP `inspect` tool
4. Add tests for enrichment

### Phase 4: Fingerprint tracking

1. Add fingerprint history queries
2. Implement regression detection
3. Add to inspect output
4. Consider `blq fingerprint <hash>` command

## Configuration

### Project config (`.lq/config.toml`)

```toml
[git]
# Enable git context capture (default: true)
enabled = true

# Capture extended context with duck_tails (default: true if available)
extended = true

# Default enrichment for inspect command
inspect_source = false
inspect_git = false
inspect_fingerprint = false
```

### User config (`~/.config/blq/config.toml`)

```toml
[git]
# User-level defaults (overridden by project config)
inspect_source = true
inspect_git = true
```

## Dependencies

### Required
- None (subprocess fallback always available)

### Optional
- **duck_tails**: DuckDB extension for git queries
  - Richer context
  - Better performance (single connection vs multiple subprocess calls)
  - SQL-native queries for analytics

## Open Questions

1. **Dirty status with duck_tails**: Does duck_tails expose working tree status, or do we always use subprocess for this?

2. **Caching**: Should we cache git context within a session? The repo state shouldn't change during a single blq invocation.

3. **Performance**: For bulk operations (e.g., enriching many events), should we batch duck_tails queries?

4. **Diff reference**: What should `--git` compare against by default? Last successful run? HEAD~1? Configurable?

5. **MCP resources**: Should we add `blq://git/blame/{path}:{line}` style resources?

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/blq/git.py` | Create | Unified git module |
| `src/blq/commands/core.py` | Modify | Remove `capture_git_info()`, use new module |
| `src/blq/commands/hooks_cmd.py` | Modify | Use `git.find_git_root()` |
| `src/blq/commands/init_cmd.py` | Modify | Use new git module |
| `src/blq/commands/inspect_cmd.py` | Modify | Add enrichment flags |
| `src/blq/serve.py` | Modify | Update MCP inspect tool |
| `tests/test_git.py` | Create | Tests for git module |
| `docs/git-integration.md` | Create | User documentation |
