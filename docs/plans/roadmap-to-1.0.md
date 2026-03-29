# Roadmap to v1.0.0

*What needs to happen before blq can claim API stability.*

## Criteria for 1.0

1. **Core features complete** — no major capability gaps that would require breaking changes
2. **API stable** — CLI commands, MCP tools, Python API, and TOML config format won't change incompatibly
3. **Storage format finalized** — BIRD spec settled, directory name migration done
4. **Architecture clean** — no major structural debt that forces workarounds

## Milestones

### 0.10.x — Sandbox Hardening (current)

What we have: bwrap enforcement, strace profiling, sandbox CLI, auto-detect presets.

**Remaining:**
- [ ] nsjail engine for full-stack enforcement (seccomp + cgroup + namespaces)
  - Spack package for nsjail installation (see `docs/plans/explore-nsjail-spack-package.md`)
  - Python wrapper for nsjail config (see `docs/plans/explore-nsjail-python-wrapper.md`)
- [ ] Phase 0 Tier 3: seccomp learning mode (requires nsjail)
- [ ] Phase 3: spec tightening from observed usage (the ratchet)
  - `blq sandbox tighten <cmd>` — auto-narrow spec based on N runs of profiling data
- [ ] Sandbox violation events with specificity (not just "failed in sandbox" but "write blocked to /etc/foo")

### 0.11.x — Unified Service Layer

The biggest architectural debt. Currently MCP shells out to `blq run --json` and reimplements query logic independently from the CLI.

**Goal:** Single implementation for each operation, called by both CLI and MCP.

- [ ] Extract command execution into a service module (not tied to argparse or MCP)
- [ ] Extract query operations (events, status, history, info, inspect) into service module
- [ ] CLI becomes a thin argparse → service adapter
- [ ] MCP becomes a thin tool → service adapter
- [ ] Remove subprocess calls from MCP server (`_run_impl` currently runs `python -m blq run`)
- [ ] Shared error handling and output formatting

**Why before 1.0:** Every new feature currently needs parallel implementation in CLI and MCP. The duplication makes the API surface unreliable — MCP and CLI can diverge silently. A service layer means one source of truth.

### 0.12.x — Sync (#21)

Fetch and query logs from CI systems. This is the last major feature gap.

- [ ] Design finalization (see `docs/design/design-sync.md`)
- [ ] CI log fetching (GitHub Actions, GitLab CI)
- [ ] Central store for cross-project aggregation
- [ ] `blq sync pull` / `blq sync push` commands
- [ ] MCP sync tools

**Why before 1.0:** Users expect to query CI logs the same way they query local logs. Without sync, blq is local-only, which limits its value for teams.

### 0.13.x — BIRD Spec Finalization

Settle the storage format before committing to API stability.

- [ ] Migrate `.lq/` → `.bird/` directory (or decide to keep `.lq/`)
- [ ] Finalize BIRD schema version (currently 2.4.0)
- [ ] Running process tracking in BIRD spec
- [ ] Migration path from 0.x → 1.0 schema
- [ ] Document storage format guarantees

**Why before 1.0:** Changing the storage directory or schema after 1.0 would be a breaking change. Lock it down now.

### 0.14.x — Polish & Stability

- [ ] Plugin system for third-party extensions (commands, fields, engines)
- [ ] Comprehensive error messages (every failure path should explain what to do)
- [ ] Performance audit (startup time, query latency, large log handling)
- [ ] Integration test suite covering CLI → MCP → DuckDB round-trips
- [ ] API documentation (Python API reference, MCP tool schemas)
- [ ] duckdb_mcp integration exploration (ATTACH/DETACH workflow)

### 1.0.0 — Stable Release

**Guarantees:**
- CLI command names and flags won't change incompatibly
- MCP tool names and parameters are stable
- TOML config format is stable (new fields may be added, existing won't change)
- Python API (`LogStore`, `LogQuery`) is stable
- Storage format won't require migration within 1.x
- Sandbox spec dimensions and presets won't change incompatibly

**What's explicitly NOT guaranteed at 1.0:**
- SQL macro signatures (DuckDB internals may evolve)
- Extension engine protocol (may add methods)
- Internal module structure (imports may change)

## Sequencing

```
0.10.x  Sandbox hardening (nsjail, spec tightening)
  ↓
0.11.x  Unified service layer (architecture cleanup)
  ↓
0.12.x  Sync (CI log fetching, cross-project)
  ↓
0.13.x  BIRD spec finalization (storage format)
  ↓
0.14.x  Polish (plugins, perf, docs, tests)
  ↓
1.0.0   Stable release
```

The order matters:
- Sandbox hardening first because it's close to done and doesn't require architecture changes
- Service layer before sync because sync would otherwise need dual CLI/MCP implementation
- BIRD finalization after sync because sync may influence the storage format
- Polish last because it benefits from all prior work being stable

## Non-goals for 1.0

- Windows support (bwrap/nsjail are Linux-only)
- GUI or web interface
- Real-time streaming (blq is batch-oriented)
- Multi-tenant / hosted service
- Backward compatibility with pre-0.10 storage formats (migration tools provided, but no runtime support)
