# Exploration: SQLite as BIRD OLTP Backend

*A prompt for exploring SQLite as the transactional write layer for BIRD, with DuckDB for analytics.*

## Context

The BIRD spec v5 (at `~/Projects/magic/docs/spec/v5/bird-v5.md`) defines a layered architecture:
- **D-layers** (D0-D4): logical query interface (attempts, outcomes, events, etc.)
- **S-layers**: storage implementation (currently parquet files or DuckDB tables)

blq currently uses DuckDB tables for both OLTP (writes) and OLAP (reads), with a retry/backoff pattern to handle DuckDB's single-writer limitation.

Kibitzer (`~/Projects/kibitzer`) uses JSON state files for high-frequency writes (every tool call) and wants to share data with blq.

## The Proposal

Add SQLite as an S-layer option for BIRD — specifically for the OLTP (write) path:

```
Tool hooks (kibitzer)  →  .bird/bird.sqlite  ←  blq run
MCP servers            →  (OLTP writes)      ←  blq exec
                              ↓
                         DuckDB ATTACH
                              ↓
                        .bird/blq.duckdb
                          (OLAP reads)
                              ↓
                    blq events / blq sql / MCP queries
```

### Why SQLite for writes
- WAL mode handles concurrent writes gracefully (vs DuckDB single-writer)
- Designed for embedded OLTP (vs DuckDB designed for OLAP)
- Kibitzer writes on *every tool call* — needs fast, concurrent inserts
- DuckDB's `sqlite` extension can ATTACH and read SQLite tables natively
- Eliminates the retry/backoff dance in blq's Window 1/Window 2 pattern

### Why keep DuckDB for reads
- OLAP queries (aggregations, joins, window functions) are DuckDB's strength
- Macros like `blq_load_events()` use DuckDB-specific SQL
- duck_hunt parsing runs inside DuckDB
- DuckDB's columnar storage is better for analytics over large datasets

## Questions to Explore

### 1. BIRD Spec Changes

Read the full BIRD v5 spec at `~/Projects/magic/docs/spec/v5/bird-v5.md`.

- How does the S-layer abstraction work? Can SQLite be added as an S-layer without changing D-layers?
- Should SQLite replace parquet as the default, or be a third option?
- How does DuckDB's `ATTACH 'bird.sqlite' AS bird_sqlite (TYPE SQLITE)` interact with BIRD's schema ownership model?
- Should the `invocations` view join across the DuckDB/SQLite boundary, or should we materialize from SQLite into DuckDB periodically?

### 2. Write Path

- Which tables need to be in SQLite? (attempts, outcomes, events, outputs, sessions)
- Should blob storage stay on the filesystem (content-addressed .bin files)?
- How to handle the metadata MAP column — SQLite doesn't have MAP type (use JSON instead?)
- Schema for SQLite tables — mirror the DuckDB schema with type adaptations?

### 3. Read Path

Two approaches:
- **Live ATTACH**: DuckDB ATTACHes SQLite file, views read directly from it. Simple but may have locking issues.
- **Sync/materialize**: Periodic copy from SQLite → DuckDB tables. More complex but fully decoupled.

### 4. Kibitzer Integration

- Should kibitzer write to the same `.bird/bird.sqlite` file?
- What tables does kibitzer need? (tool_calls, mode_changes, patterns, suggestions)
- Should these be BIRD D-layer tables or kibitzer-specific tables in a `kibitzer` schema?
- How does the coach's pattern detection query blq's events alongside kibitzer's tool_call history?

### 5. Migration

- How to migrate existing `.bird/blq.duckdb` data to the new SQLite + DuckDB split?
- Should `blq.duckdb` still exist (for macros, views, materialized analytics)?
- Backward compatibility with v1.0 databases?

### 6. Performance

- SQLite WAL mode write performance for kibitzer's volume (~100 writes/min during active agent session)
- DuckDB ATTACH read performance vs native table performance
- Memory impact of keeping both connections open

## Research Steps

1. Read BIRD v5 spec in full — understand S-layer abstraction
2. Prototype: create a SQLite DB with BIRD tables, ATTACH from DuckDB, run blq macros
3. Benchmark: write throughput (SQLite WAL vs DuckDB retry pattern)
4. Benchmark: read performance (ATTACH vs native tables)
5. Design the schema mapping (DuckDB types → SQLite types, especially MAP → JSON)
6. Prototype kibitzer writing tool_calls to the same SQLite DB
7. Test cross-database queries (kibitzer tool_calls JOIN blq events)

## References

- BIRD v5 spec: `~/Projects/magic/docs/spec/v5/bird-v5.md`
- BIRD v4 spec: `~/Projects/magic/docs/bird_spec.md`
- blq schema: `src/blq/bird_schema.sql`
- blq BirdStore: `src/blq/bird.py` (Window 1/2 pattern)
- kibitzer state: `~/Projects/kibitzer/src/kibitzer/state.py`
- kibitzer architecture: `~/Projects/kibitzer/docs/architecture.md`
- DuckDB SQLite extension: https://duckdb.org/docs/extensions/sqlite
