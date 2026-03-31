# Schema Stability Guarantees

*What's stable, what may change, and migration paths.*

## Schema Version: 3.0.0

Starting with blq 1.0, the BIRD schema follows semantic versioning:
- **Major** (3.x) — breaking changes (table renames, column removal)
- **Minor** (x.1) — additive changes (new columns, new tables, new macros)
- **Patch** (x.x.1) — macro/view changes with no table modifications

## Stable Tables (Public API)

These tables and their columns are part of the stable API. They will not be renamed or removed within a major version.

### `invocations`

The primary run record table. One row per completed command execution.

| Column | Type | Stable | Notes |
|--------|------|--------|-------|
| `id` | UUID | Yes | Primary key |
| `session_id` | VARCHAR | Yes | |
| `timestamp` | TIMESTAMP | Yes | When command ran |
| `duration_ms` | BIGINT | Yes | Wall-clock duration |
| `cmd` | VARCHAR | Yes | Command string |
| `exit_code` | INTEGER | Yes | |
| `source_name` | VARCHAR | Yes | Registered command name |
| `source_type` | VARCHAR | Yes | run, exec, import, capture |
| `cwd` | VARCHAR | Yes | Working directory |
| `executable` | VARCHAR | Yes | |
| `hostname` | VARCHAR | Yes | |
| `platform` | VARCHAR | Yes | |
| `arch` | VARCHAR | Yes | |
| `git_commit` | VARCHAR | Yes | |
| `git_branch` | VARCHAR | Yes | |
| `git_dirty` | BOOLEAN | Yes | |
| `ci` | JSON | Yes | CI provider context |
| `extension_data` | JSON | Yes | Sandbox specs, grades, metrics |
| `tag` | VARCHAR | Yes | User-defined tag |
| `date` | DATE | Yes | Partition date |

### `events`

Parsed diagnostics from command output.

| Column | Type | Stable | Notes |
|--------|------|--------|-------|
| `id` | UUID | Yes | Primary key |
| `invocation_id` | UUID | Yes | FK to invocations |
| `event_index` | INTEGER | Yes | Position within run |
| `severity` | VARCHAR | Yes | error, warning, info, note |
| `ref_file` | VARCHAR | Yes | Source file path |
| `ref_line` | INTEGER | Yes | |
| `ref_column` | INTEGER | Yes | |
| `message` | VARCHAR | Yes | |
| `code` | VARCHAR | Yes | Error code |
| `fingerprint` | VARCHAR | Yes | Dedup identifier |
| `metadata` | JSON | Yes | Annotations, format extras |
| `format_used` | VARCHAR | Yes | Parser format |
| `date` | DATE | Yes | |

### `attempts` / `outcomes`

Long-running command support (attempt starts, outcome completes).

| Table | Stability | Notes |
|-------|-----------|-------|
| `attempts` | Yes | Same columns as invocations + pid |
| `outcomes` | Yes | exit_code, duration_ms, signal, timeout |

### `outputs` / `blob_registry`

Content-addressed output storage.

| Table | Stability | Notes |
|-------|-----------|-------|
| `outputs` | Yes | Links invocations to blobs |
| `blob_registry` | Yes | Blob dedup tracking |

### `sessions`

Invoker session tracking.

| Table | Stability | Notes |
|-------|-----------|-------|
| `sessions` | Yes | session_id, client_id, invoker |

## Stable Macros (Public API)

These macros will not be removed within a major version, though their internal implementation may change.

| Macro | Stable | Purpose |
|-------|--------|---------|
| `blq_load_events()` | Yes | All events with run metadata |
| `blq_load_runs()` | Yes | Completed runs with counts |
| `blq_load_attempts()` | Yes | All attempts with status |
| `blq_load_source_status()` | Yes | Latest run per source |
| `blq_status()` | Yes | Status overview |
| `blq_errors(n)` | Yes | Recent errors |
| `blq_warnings(n)` | Yes | Recent warnings |
| `blq_history(n)` | Yes | Run history |
| `blq_diff(run1, run2)` | Yes | Compare runs |
| `blq_sandbox_summary()` | Yes | Sandbox spec distribution |

## Internal (May Change)

These are implementation details that may change between minor versions:

- `blq_metadata` table (schema version tracking)
- `blq_base_path()` macro (internal path resolution)
- `blq_blob_root()` macro (blob path construction)
- `blq_ref()`, `blq_parse_ref()` macros (ref formatting)
- `blq_status_badge()` macro (display formatting)
- `blq_read_lines()`, `blq_search_lines()` macros (require read_lines extension)
- View column order (columns won't be removed, but order may change)
- Internal helper macros (blq_location, blq_errors_json, etc.)

## Migration Path

### 2.x → 3.0

Automatic. blq handles this transparently:
1. Directory rename: `.lq/` → `.bird/` (auto-migrated on first access)
2. Schema version: updated automatically when database is opened
3. No table or column changes from 2.4.0

### Future Migrations (3.x)

Minor version migrations (3.0 → 3.1, etc.) will:
- Only add columns (never remove)
- Only add tables (never remove)
- Only add macros (never remove)
- Run automatically on database open
- Be backward-compatible within 3.x

## Directory Structure

```
.bird/
├── blq.duckdb          # DuckDB database
├── blobs/              # Content-addressed output storage
│   └── content/
│       └── ab/{hash}.bin
├── config.toml         # Project configuration
├── commands.toml       # Registered commands
├── schema.sql          # Human-readable schema reference
├── raw/                # Optional raw log files
└── live/               # Ephemeral (running commands only)
```
