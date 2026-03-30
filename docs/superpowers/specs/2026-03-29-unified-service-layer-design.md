# Unified Service Layer Design

## Problem

CLI and MCP implement the same operations independently, causing:
- Query results that can diverge silently between CLI and MCP
- ~500 lines of duplicated logic across serve.py and commands/
- Every new feature requires parallel implementation in both layers
- Two parallel ref parsers for the same syntax
- MCP shells out to `blq run` as a subprocess instead of sharing the execution path

## Architecture

A new `src/blq/services/` package containing pure business logic functions. Each function takes a `BlqStorage` instance and query parameters, returns structured data (dicts/lists). No argparse, no MCP, no output formatting.

```
CLI (argparse)  →  services/  ←  MCP (fastmcp)
                      ↓
                  BlqStorage
                      ↓
                   DuckDB
```

**The rule:** Services return data. CLI formats it for the terminal. MCP serializes it as JSON. Neither the CLI nor MCP contains query logic.

## Approach

**Bottom-up extraction.** Start with shared query logic (inspect helpers, history, ref resolution), leave execution as subprocess for now. The subprocess gives crash isolation; eliminating it is a follow-up.

**serve.py stays as one file** but gets thinner. After extraction, `_*_impl()` functions become thin adapters calling service functions. Splitting serve.py into `mcp/` modules is a follow-up once the service layer is stable.

## Module Layout

```
src/blq/services/
├── __init__.py        # Re-exports for convenience
├── refs.py            # Canonical ref resolver
├── query.py           # status, history, events, diff
├── inspect.py         # Event inspection with context layers
├── execution.py       # RunResult → concise dict conversion
└── commands.py        # list/register/unregister operations
```

## Service Function Signatures

All services take `BlqStorage` as first argument (caller opens and passes it):

```python
# refs.py
def parse_ref(ref: str) -> ParsedRef
def resolve_run(storage: BlqStorage, ref: str) -> dict | None

# query.py
def query_status(storage: BlqStorage) -> list[dict]
def query_history(storage: BlqStorage, limit: int, source: str | None, status: str | None) -> list[dict]
def query_events(storage: BlqStorage, severity: str | None, run_id: int | None,
                  source: str | None, file_pattern: str | None, limit: int) -> dict
def query_diff(storage: BlqStorage, run1: int, run2: int) -> dict

# inspect.py
def inspect_event(storage: BlqStorage, ref: str, source_root: Path,
                   include_source: bool, include_git: bool, include_fingerprint: bool) -> dict

# execution.py
def run_result_to_concise(result: dict, source_name: str) -> dict

# commands.py
def list_commands(config: BlqConfig) -> list[dict]
def register_command(config: BlqConfig, name: str, cmd: str, **kwargs) -> dict
def unregister_command(config: BlqConfig, name: str) -> dict
```

## What Gets Extracted

### refs.py — Canonical ref resolver
Merges `resolve_ref()` from `management.py:35` and `_parse_ref()` + `_parse_run_ref()` from `serve.py:227/262`. Two parallel parsers for the same `tag:serial:event` syntax become one.

### query.py — Status, history, events, diff
- **status**: Merges `cmd_status()` (management.py) and `_status_impl()` (serve.py). Shared: `status_str` computation, `run_ref` construction.
- **history**: Merges `cmd_history()` and `_history_impl()`. SQL query and status mapping are near-identical.
- **events**: Merges `cmd_events()` and `_errors_impl()`/`_warnings_impl()`/`_events_impl()`. WHERE building, suppression logic, event-dict shaping.
- **diff**: Currently MCP-only. Service function makes it available to CLI too.

### inspect.py — Event inspection with context
Moves four private helpers from `events.py` into shared functions: `_get_log_context()`, `_get_source_context()`, `_get_git_context()`, `_get_fingerprint_history()`. Eliminates ~180 lines of duplication in serve.py.

### execution.py — Result conversion
Extracts the `RunResult → concise dict` shaping from `_run_impl()` and `_exec_impl()` in serve.py (~100 lines duplicated between the two). Subprocess bridge stays; only the response shaping is shared.

### commands.py — Command registry
Extracts command list/register/unregister logic that's currently in both `registry.py` and serve.py's `_register_command_impl()`.

## Migration Strategy

**Phase 1: Extract services (no callers changed)**
- Create `services/` modules with shared logic
- Write tests for each service function independently
- Existing CLI and MCP untouched

**Phase 2: Wire CLI to services**
- CLI handlers call service functions, keep only argparse + formatting
- Run full test suite after each command migration

**Phase 3: Wire MCP to services**
- MCP `_*_impl()` functions call service functions
- Subprocess bridge for run/exec stays
- serve.py shrinks from ~4,400 to ~2,000 lines

**Phase 4: Cleanup**
- Remove dead code
- Verify CLI and MCP produce identical results

## What This Does NOT Do

- **No direct execution from MCP** — subprocess bridge stays (crash isolation)
- **No serve.py split** — stays as one file, just thinner
- **No new features** — pure refactor, behavior unchanged
- **No storage layer changes** — BlqStorage stays as-is

## Success Criteria

- All existing tests pass with no changes
- CLI and MCP produce identical results for: status, history, events, info, inspect, diff
- serve.py shrinks by ~1,500 lines
- No query logic remains in CLI command handlers or MCP `_*_impl()` functions
- One canonical ref resolver used everywhere
