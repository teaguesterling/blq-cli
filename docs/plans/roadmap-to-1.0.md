# Roadmap to v1.0.0

*What needs to happen before blq can claim API stability.*

## Criteria for 1.0

1. **Core features complete** — no major capability gaps that would require breaking changes
2. **API stable** — CLI commands, MCP tools, Python API, and TOML config format won't change incompatibly
3. **Storage format finalized** — BIRD spec settled, directory name migration done
4. **Architecture clean** — no major structural debt that forces workarounds

## Milestones

### 0.10.x — Sandbox Hardening (current)

**Done:**
- [x] Bwrap enforcement engine (network, filesystem, PID, tmpfs)
- [x] Strace profiling (`blq sandbox profile`)
- [x] Sandbox CLI (list, inspect, suggest, profile)
- [x] Auto-detect presets on init
- [x] `--sandbox` flag on register
- [x] Sandbox violation events
- [x] MCP sandbox_info tool
- [x] Annotator plugin system (RunContext, eager/deferred dispatch)
- [x] Spec tightening (`blq sandbox tighten`)

**Remaining:**
- [ ] Specific violation events — detect permission-denied patterns (#29)
- [ ] First annotator plugin — source context lookup (#30)

**Deferred to post-1.0:**
- nsjail engine (#42) — requires building from source
- seccomp learning mode (#43) — requires nsjail

### 0.11.x — Unified Service Layer

The biggest architectural debt. Currently MCP shells out to `blq run --json` and reimplements query logic independently from the CLI.

**Goal:** Single implementation for each operation, called by both CLI and MCP.

- [ ] Extract execution service (#31)
- [ ] Extract query services (#32)

**Why before 1.0:** Every new feature currently needs parallel implementation in CLI and MCP. The duplication makes the API surface unreliable — MCP and CLI can diverge silently. A service layer means one source of truth.

### 0.12.x — Sync (#21)

Fetch and query logs from CI systems. This is the last major feature gap.

- [ ] CI log fetching — GitHub Actions, GitLab CI (#33)
- [ ] Central store for cross-project aggregation (#34)

**Why before 1.0:** Users expect to query CI logs the same way they query local logs. Without sync, blq is local-only, which limits its value for teams.

### 0.12.x — BIRD Spec Finalization

Settle the storage format before committing to API stability.

- [x] `.lq/` → `.bird/` directory migration (#35) — auto-migration on first access
- [ ] Finalize schema version, document storage guarantees (#36)

**Why before 1.0:** Changing the storage directory or schema after 1.0 would be a breaking change. Lock it down now.

### 0.14.x — Polish & Stability

- [ ] Plugin system documentation and API stability (#37)
- [ ] Performance audit (#38)
- [ ] Comprehensive error messages (#39)
- [ ] Integration test suite — CLI → MCP → DuckDB round-trips (#40)
- [ ] API documentation — Python reference, MCP schemas (#41)

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

## Post-1.0

- [ ] nsjail sandbox engine (#42)
- [ ] seccomp learning mode (#43)
- [ ] duckdb_mcp integration (ATTACH/DETACH workflow)
- [ ] Windows support exploration

## Sequencing

```
0.10.x  Sandbox hardening — finish remaining items
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

## Issue Tracker

All roadmap items are tracked as GitHub issues with milestone labels:

| Label | Issues |
|-------|--------|
| `milestone:0.10.x` | #29, #30 |
| `milestone:0.11.x` | #31, #32 |
| `milestone:0.12.x` | #21, #33, #34 |
| `milestone:0.13.x` | #35, #36 |
| `milestone:0.14.x` | #37, #38, #39, #40, #41 |
| `post-1.0` | #42, #43 |
