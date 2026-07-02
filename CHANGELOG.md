# Changelog

## v1.0.2

### Bug fixes
- **Self-heal DBs stuck without `extension_data`.** The 2.3->2.4 migration renamed `sandbox` -> `extension_data` via `RENAME COLUMN`, which DuckDB blocks when non-view dependencies (FKs/constraints) exist on `attempts`/`invocations` — even after dropping views. The rename failed silently but the schema version advanced anyway, leaving DBs permanently stuck without `extension_data` and crashing every `write_attempt` ("Table attempts does not have a column extension_data"). `_ensure_schema` now self-heals version-independently: it gates on a missing column, adds `extension_data` via `ADD COLUMN` (never blocked), copies any existing `sandbox` data across (wrapped as `{"sandbox": ...}`, no data loss), and re-applies the idempotent schema to recreate views. Healthy/fresh DBs stay a fast no-op. Repairs on next connect (the CLI heals on the next `blq run`; a running MCP server heals after restart).
