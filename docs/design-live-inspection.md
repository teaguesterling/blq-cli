# Design: Live Inspection of Long-Running Tasks

**Status:** Phase 1-4 Implemented
**Date:** 2026-02-10
**Last Updated:** 2026-02-10

## Overview

This feature enables inspection of long-running build/test commands while they're still executing. It implements the BIRD v5 attempts/outcomes split architecture to track command lifecycle from start to completion.

## Implementation Status

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | Schema changes (attempts/outcomes tables) | **Done** |
| Phase 2 | Live output streaming | **Done** |
| Phase 3 | CLI integration (`--status`, `--follow`) | **Done** |
| Phase 4 | MCP integration | **Done** |
| Phase 5 | Live event extraction | Optional |

## Architecture: Attempts/Outcomes Split

### Status Derivation

BIRD v5 derives command status from the relationship between `attempts` and `outcomes` tables:

| Condition | Status | Description |
|-----------|--------|-------------|
| No matching outcome | `pending` | Command is still running |
| Outcome with NULL exit_code | `orphaned` | Command crashed (exit code unknown) |
| Outcome with exit_code | `completed` | Command finished normally |

### Data Flow

```
Command Start                    Command End
     │                                │
     ▼                                ▼
┌─────────────┐              ┌──────────────┐
│   attempts  │──────────────│   outcomes   │
│   table     │   attempt_id │   table      │
└─────────────┘              └──────────────┘
     │                                │
     │                                │
     └────────────┬───────────────────┘
                  ▼
           ┌─────────────┐
           │ invocations │  (also written for backward compatibility)
           │   table     │
           └─────────────┘
                  │
                  ▼
           ┌─────────────┐
           │   events    │  (joined via invocation_id)
           │   table     │
           └─────────────┘
```

## Phase 1: Schema Changes (Implemented)

### Tables Added

**`attempts` table** - Written at command START:

```sql
CREATE TABLE attempts (
    id                UUID PRIMARY KEY,
    session_id        VARCHAR NOT NULL,
    timestamp         TIMESTAMP NOT NULL,
    cwd               VARCHAR NOT NULL,
    cmd               VARCHAR NOT NULL,
    executable        VARCHAR,
    format_hint       VARCHAR,
    client_id         VARCHAR NOT NULL,
    hostname          VARCHAR,
    username          VARCHAR,
    tag               VARCHAR,
    source_name       VARCHAR,
    source_type       VARCHAR,
    environment       JSON,
    platform          VARCHAR,
    arch              VARCHAR,
    git_commit        VARCHAR,
    git_branch        VARCHAR,
    git_dirty         BOOLEAN,
    ci                JSON,
    date              DATE NOT NULL
);
```

**`outcomes` table** - Written at command COMPLETION:

```sql
CREATE TABLE outcomes (
    attempt_id        UUID PRIMARY KEY,
    completed_at      TIMESTAMP NOT NULL,
    duration_ms       BIGINT,
    exit_code         INTEGER,  -- NULL = crashed/unknown
    signal            INTEGER,
    timeout           BOOLEAN DEFAULT FALSE,
    date              DATE NOT NULL
);
```

### Views Added

**`attempts_with_status`** - Joins attempts with outcomes to derive status:

```sql
CREATE VIEW attempts_with_status AS
SELECT
    a.*,
    o.completed_at,
    o.duration_ms,
    o.exit_code,
    o.signal,
    o.timeout,
    CASE
        WHEN o.attempt_id IS NULL THEN 'pending'
        WHEN o.exit_code IS NULL THEN 'orphaned'
        ELSE 'completed'
    END AS status
FROM attempts a
LEFT JOIN outcomes o ON a.id = o.attempt_id;
```

### SQL Macros Added

| Macro | Description |
|-------|-------------|
| `blq_load_attempts()` | Returns attempts with status column |
| `blq_running()` | Returns only pending attempts (status='pending') |
| `blq_history_status(status)` | Filter invocations by status |

### Backward Compatibility

The existing `invocations` table is kept and written to at command completion (alongside attempts+outcomes). This ensures:
- Events table can still join via `invocation_id`
- Existing `blq_load_events()` and other macros continue to work
- No migration needed for existing data

## Phase 2: Live Output Streaming (Implemented)

### Directory Structure

```
.lq/live/
└── {attempt_uuid}/
    ├── combined.log    # Interleaved stdout+stderr
    └── meta.json       # Metadata about the running command
```

**meta.json contents:**
```json
{
    "cmd": "pytest tests/",
    "source_name": "test",
    "started_at": "2024-01-15T10:30:00",
    "pid": 12345,
    "attempt_id": "abc123-...",
    "run_id": 5
}
```

### Execution Flow

The `_execute_with_live_output()` function in `src/blq/commands/execution.py` implements:

1. **Command Start:**
   - Write `AttemptRecord` to `attempts` table (visible as 'pending')
   - Create live output directory: `.lq/live/{attempt_id}/`
   - Write `meta.json` with command metadata
   - Open `combined.log` for streaming writes

2. **During Execution:**
   - Stream subprocess output line-by-line
   - Write each line to `combined.log` with immediate flush
   - Optionally echo to stdout (unless `--quiet`)

3. **Command Completion:**
   - Write `OutcomeRecord` to `outcomes` table
   - Write `InvocationRecord` to `invocations` table (same ID as attempt)
   - Parse output for events using duck_hunt
   - Write events to `events` table
   - Finalize live output (move to blob storage if configured)
   - Clean up live directory

### BirdStore Methods

| Method | Description |
|--------|-------------|
| `write_attempt(record)` | Write attempt record at start |
| `write_outcome(record)` | Write outcome record at completion |
| `get_attempt_status(id)` | Get current status of an attempt |
| `get_running_attempts()` | List all pending attempts |
| `create_live_dir(id, meta)` | Create live output directory |
| `get_live_output_path(id, stream)` | Get path to live output file |
| `read_live_output(id, stream, tail)` | Read live output (optionally tail N lines) |
| `cleanup_live_dir(id)` | Remove live directory after completion |
| `list_live_attempts()` | List attempts with active live directories |
| `finalize_live_output(id, stream)` | Move live output to blob storage |

### Data Classes

**`AttemptRecord`** - Command start metadata:
```python
@dataclass
class AttemptRecord:
    id: str                    # UUID
    session_id: str
    cmd: str
    cwd: str
    client_id: str
    timestamp: datetime
    executable: str | None
    format_hint: str | None
    hostname: str | None
    tag: str | None
    source_name: str | None
    source_type: str | None
    environment: dict | None
    platform: str | None
    arch: str | None
    git_commit: str | None
    git_branch: str | None
    git_dirty: bool | None
    ci: dict | None
```

**`OutcomeRecord`** - Command completion metadata:
```python
@dataclass
class OutcomeRecord:
    attempt_id: str
    exit_code: int | None      # NULL = crashed/unknown
    completed_at: datetime
    duration_ms: int | None
    signal: int | None
    timeout: bool
```

## Key Files

| File | Role |
|------|------|
| `src/blq/bird_schema.sql` | SQL schema with attempts/outcomes tables and macros |
| `src/blq/bird.py` | BirdStore class with attempt/outcome/live output methods |
| `src/blq/commands/execution.py` | `_execute_with_live_output()` function |
| `tests/test_attempts_outcomes.py` | Tests for attempts/outcomes and live output |

## Usage Examples

### Checking Running Commands

```python
# Via BirdStore
store = BirdStore.open(lq_dir)
running = store.get_running_attempts()
for r in running:
    print(f"{r['cmd']} running since {r['timestamp']}")
```

```sql
-- Via SQL
SELECT * FROM blq_running();
SELECT * FROM blq_load_attempts() WHERE status = 'pending';
```

### Reading Live Output

```python
# Read last 20 lines of a running command's output
store = BirdStore.open(lq_dir)
output = store.read_live_output(attempt_id, "combined", tail=20)
print(output)
```

### Checking Command Status

```python
store = BirdStore.open(lq_dir)
status = store.get_attempt_status(attempt_id)
# Returns: 'pending', 'orphaned', or 'completed'
```

## Phase 3: CLI Integration (Implemented)

### `blq history --status` Filter

Filter run history by status:

```bash
blq history --status=running    # Show only pending/running commands
blq history --status=completed  # Show only completed commands
blq history --status=orphaned   # Show crashed commands
blq history --status=all        # Show all (default)

# Combine with tag filter
blq history --status=running test  # Running 'test' commands only
```

**Implementation:**
- Added `--status` / `-s` argument to CLI parser (`src/blq/cli.py`)
- Modified `cmd_history()` to use `blq_history_status()` macro when filtering
- Maps user-friendly "running" to database "pending" status

### `blq info --tail` and `--head`

View output from a specific run:

```bash
blq info build:5 --tail=50    # Last 50 lines of output
blq info build:5 --head=20    # First 20 lines of output
```

**For running commands:** Reads from `.lq/live/{attempt_id}/combined.log`
**For completed commands:** Reads from blob storage

### `blq info --follow`

Stream output from a running command (like `tail -f`):

```bash
blq info build:5 --follow     # Stream live output
blq info build:5 --follow --tail=20  # Show last 20 lines, then follow
```

**Implementation:**
- Added `--tail`, `--head`, `--follow` arguments to CLI parser
- Created `_show_run_output()` helper function
- `_show_live_output()` handles running commands (reads from live dir)
- `_show_stored_output()` handles completed commands (reads from blob storage)
- Follow mode polls every 100ms and checks if command is still running

**Key files:**
- `src/blq/cli.py` - Added CLI arguments
- `src/blq/commands/management.py` - Updated `cmd_info()` and added helpers

## Future Work

## Phase 4: MCP Integration (Implemented)

### `history()` Tool with Status Filter

Filter run history by status in MCP:

```python
# Filter by status
blq.history(status="running")    # Show only running commands
blq.history(status="completed")  # Show only completed commands
blq.history(status="orphaned")   # Show crashed commands

# Combine with source filter
blq.history(source="test", status="running")
```

**Implementation:**
- Added `status` parameter to `history()` tool
- Maps "running" to "pending" database status
- Returns "RUNNING", "ORPHANED", or completion status ("OK", "FAIL", "WARN")

### `info()` Tool with Live Output

View run info including running commands:

```python
# Works for both running and completed commands
blq.info(ref="build:5")           # Get run info
blq.info(ref="build:5", tail=50)  # Get last 50 lines of output

# For running commands, reads from live output directory
# For completed commands, reads from blob storage
```

**Implementation:**
- Updated `_info_impl()` to check `blq_load_attempts()` for pending runs
- Added `is_running` and `attempt_id` fields to response
- Modified output fetching to read from live directory for running commands
- Uses `BirdStore.read_live_output()` for pending attempts

**Key files:**
- `src/blq/serve.py` - Updated `_history_impl()`, `_info_impl()`, and info tool

## Future Work

### Phase 5: Live Event Extraction (Optional)

Parse events from live output while command is still running:

```bash
blq events build:5  # Shows partial events during long build
```

## Design Decisions

1. **Combined stream only**: For simplicity, we capture combined stdout+stderr interleaved. Separate streams can be added later.

2. **Write invocations for backward compatibility**: Rather than converting `invocations` to a view, we write to both `attempts` AND `invocations` tables. This ensures existing joins (events → invocations) continue to work.

3. **Same ID for attempt and invocation**: Using the same UUID ensures events can be linked to both the attempt (for status) and the invocation (for existing queries).

4. **Cleanup on completion**: Live directories are cleaned up after command completion to avoid disk bloat. Orphaned directories indicate crashed commands.

5. **No PID storage**: Process ID is stored in meta.json but not in the database. Kill functionality is out of scope.

## Testing

Run the test suite:

```bash
# All attempts/outcomes tests
pytest tests/test_attempts_outcomes.py -v

# Just live output streaming tests
pytest tests/test_attempts_outcomes.py::TestLiveOutputStreaming -v
```

Test coverage:
- AttemptRecord/OutcomeRecord dataclass creation
- BirdStore attempt/outcome write operations
- Status derivation (pending, completed, orphaned)
- Live directory creation/cleanup
- Live output read/write
- Finalization to blob storage (inline and blob modes)
