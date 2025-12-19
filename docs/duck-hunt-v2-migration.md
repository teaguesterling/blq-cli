# Duck Hunt Schema V2 Migration Plan

## Overview

This document outlines the changes needed to migrate blq to duck_hunt's Schema V2, plus the subsequent `lq_` to `blq_` schema rename and blq.duckdb architecture.

## Key Changes

### 1. Field Renames (duck_hunt V2)

| Old Field (blq) | New Field | Location |
|-----------------|-----------|----------|
| `error_fingerprint` | `fingerprint` | PARQUET_SCHEMA, schema.sql, Python code |

### 2. SQL Macro/View Renames (blq_ prefix)

All SQL macros and views renamed from `lq_*` to `blq_*`:

| Old Name | New Name |
|----------|----------|
| `lq_base_path()` | `blq_base_path()` |
| `lq_events` (view) | `blq_load_events()` (macro) |
| `lq_runs` (view) | `blq_load_runs()` (macro) |
| `lq_source_status` (view) | `blq_load_source_status()` (macro) |
| `lq_status()` | `blq_status()` |
| `lq_errors()` | `blq_errors()` |
| `lq_warnings()` | `blq_warnings()` |
| `lq_ref()` | `blq_ref()` |
| `lq_location()` | `blq_location()` |
| `lq_short_fp()` | `blq_short_fp()` |
| `lq_history()` | `blq_history()` |
| `lq_diff()` | `blq_diff()` |
| `status_badge()` | `blq_status_badge()` |

### 3. Architecture Changes (blq.duckdb)

- **blq.duckdb**: Database file created at `blq init` with all macros pre-loaded
- **Placeholder parquet**: Empty file at `logs/date=1970-01-01/source=_placeholder/` for glob validation
- **Table-returning macros**: Views converted to macros that are evaluated at query time

### blq-Owned Fields (No Change Needed)

These fields are blq's own metadata, NOT from duck_hunt:

- `completed_at` - Run completion timestamp (blq computes this)
- `environment` - Captured env vars (blq's MAP field)
- `duration_sec` - Run duration (blq computes this)

### New duck_hunt Fields (Available but Optional)

These fields are new in duck_hunt V2 and can be used if needed:

- `target` - Destination (IP:port, HTTP path)
- `actor_type` - Type: user, service, system, anonymous
- `external_id` - External correlation ID
- `subunit` - Hierarchy level 4
- `subunit_id` - ID for level 4
- `scope`, `group`, `unit` - Generic hierarchy (was workflow/job/step)
- `pattern_id` - Pattern cluster ID
- `similarity_score` - Pattern similarity

## Schema Architecture

### Table-Returning Macros

The schema now uses table-returning macros instead of views for data access:

```sql
-- Core data access (always works, even with no data)
CREATE OR REPLACE MACRO blq_load_events() AS TABLE
SELECT * FROM read_parquet(blq_base_path() || '/**/*.parquet', ...);

-- Aggregated runs
CREATE OR REPLACE MACRO blq_load_runs() AS TABLE
SELECT ... FROM blq_load_events() GROUP BY ...;

-- All other macros reference blq_load_events()
CREATE OR REPLACE MACRO blq_status() AS TABLE
SELECT ... FROM blq_load_source_status() ...;
```

### Why Macros Instead of Views?

Views fail at creation time if `read_parquet()` glob matches no files. Table-returning macros are only evaluated at query time, so they can be created even when no data exists.

### blq.duckdb Database File

At `blq init`:
1. Create placeholder parquet file (ensures glob always matches)
2. Create `blq.duckdb` with all macros pre-loaded
3. CLI/MCP opens database and overrides `blq_base_path()` with absolute path

Benefits:
- Faster startup (no schema parsing)
- Direct DuckDB CLI access: `duckdb .lq/blq.duckdb "SELECT * FROM blq_status()"`
- Consistent macro definitions

## Files Updated

### Core Files

| File | Changes |
|------|---------|
| `src/blq/schema.sql` | Complete rewrite with `blq_` prefix and table-returning macros |
| `src/blq/commands/core.py` | `ConnectionFactory.create()` uses blq.duckdb, `blq_base_path()` |
| `src/blq/commands/init_cmd.py` | `_create_placeholder_parquet()`, `_create_database()`, SQL splitting |
| `src/blq/query.py` | `LogStore` uses blq.duckdb, `LogQuery.from_table()` handles macros |
| `src/blq/commands/management.py` | `blq_status()`, `blq_status_verbose()` |
| `src/blq/serve.py` | Updated docstrings |

### Test Files

| File | Changes |
|------|---------|
| `tests/test_core.py` | `blq_base_path()`, `blq_load_events()` |
| `tests/test_mcp_server.py` | `blq_load_events()` in SQL queries |
| `tests/test_query_filter.py` | `blq_base_path()` |

## Testing

```bash
python -m pytest -v
```

All 234 tests pass.

## Existing Data

Existing `.lq/` directories are incompatible with the new schema. Reinitialize:

```bash
mv .lq .lq~
blq init
```

## Timeline

1. ✅ Create SQL tests for new schema
2. ✅ Update PARQUET_SCHEMA in core.py (`fingerprint`)
3. ✅ Update schema.sql (`blq_` prefix, table-returning macros)
4. ✅ Update Python code (all `lq_` references)
5. ✅ Implement blq.duckdb architecture
6. ✅ Run all tests (234 passed)
