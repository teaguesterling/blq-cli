# BIRD v5 Proposal: Layered Architecture for Diverse Clients

**From:** blq development team
**To:** BIRD spec maintainers
**Date:** 2026-02-09
**Status:** Draft for discussion

## Executive Summary

After implementing BIRD-compatible storage in blq (a CLI build log query tool), we've identified opportunities to make the BIRD specification more flexible while maintaining interoperability. This proposal introduces a **layered architecture** that separates core requirements from optional extensions, enabling diverse clients (shq, blq, CI systems, IDE integrations) to adopt BIRD at appropriate complexity levels.

The key insight: **BIRD's current spec optimizes for shq's shell-hook use case, but the concepts generalize well with some adjustments.**

---

## Part 1: Analysis of Current State

### What Works Well

1. **Dual storage backends** - Parquet for concurrent writes, DuckDB for simplicity. This flexibility is essential.

2. **Content-addressed blobs** - BLAKE3 + sharding is the right design. The 70-90% dedup ratio is real.

3. **UUIDv7 identifiers** - Time-ordered UUIDs enable natural sorting and range queries.

4. **Schema separation** - Invocations/outputs/events as distinct tables is cleaner than flattened schemas.

5. **Multi-client UUID sharing** - `BIRD_INVOCATION_UUID` prevents duplicate recording across nested clients.

### What Could Be Improved

| Current Approach | Challenge | Proposed Solution |
|------------------|-----------|-------------------|
| Complex directory structure | Overkill for single-writer clients | Layered profiles |
| Mandatory session tracking | CLI tools don't have persistent sessions | Optional sessions |
| In-flight pending files | Synchronous CLI doesn't need crash recovery | Optional feature |
| `.bird/` directory name | Conflicts with tool-specific naming | Universal `.bird/db/` + app namespacing |
| Events schema | Missing fields tools like blq need | Extension mechanism |
| No schema versioning | Hard to evolve schema | `bird_meta` table |
| Uncompressed blobs only | Wastes 77-89% storage | Optional compression |

---

## Part 2: Proposed Layered Architecture

### Layer 0: BIRD Core (Mandatory)

The absolute minimum for BIRD compatibility. **All BIRD clients must implement this.**

```
.bird/                           # Universal BIRD root
├── db/
│   └── bird.duckdb              # Required: DuckDB database with tables
└── config.toml                  # Required: TOML configuration
```

**Core Schema (invocations only):**

```sql
CREATE TABLE invocations (
    -- Required fields (Layer 0)
    id                UUID PRIMARY KEY,
    timestamp         TIMESTAMP NOT NULL,
    cmd               VARCHAR NOT NULL,
    exit_code         INTEGER,              -- NULL for pending/crashed
    client_id         VARCHAR NOT NULL,     -- Identifies the BIRD client

    -- Timing
    duration_ms       BIGINT,

    -- Context
    cwd               VARCHAR,
    hostname          VARCHAR,

    -- Partitioning
    date              DATE NOT NULL
);
```

**Rationale:** This is the smallest useful unit. A client that only records "what commands ran, when, and how they exited" is still BIRD-compatible and can participate in the ecosystem.

### Layer 1: Output Capture (Optional)

Add stdout/stderr capture with content-addressed storage.

```
.bird/
├── db/
│   └── bird.duckdb
├── blobs/
│   └── content/
│       └── {hash[0:2]}/{hash}.bin[.gz|.zst]  # Optional compression
└── config.toml
```

**Additional Schema:**

```sql
CREATE TABLE outputs (
    id                UUID PRIMARY KEY,
    invocation_id     UUID NOT NULL,
    stream            VARCHAR NOT NULL,      -- stdout, stderr, combined
    content_hash      VARCHAR NOT NULL,
    byte_length       BIGINT NOT NULL,
    storage_type      VARCHAR NOT NULL,      -- inline, blob
    storage_ref       VARCHAR NOT NULL,
    date              DATE NOT NULL
);
```

**Rationale:** Many tools only care about metadata, not output. Making this optional reduces complexity for simple clients.

### Layer 2: Event Parsing (Optional)

Add parsed diagnostics (errors, warnings, test results).

**Additional Schema:**

```sql
CREATE TABLE events (
    id                UUID PRIMARY KEY,
    invocation_id     UUID NOT NULL,

    -- Classification
    severity          VARCHAR,               -- error, warning, info, note
    event_type        VARCHAR,               -- diagnostic, test_result, etc.

    -- Location (optional)
    ref_file          VARCHAR,
    ref_line          INTEGER,
    ref_column        INTEGER,

    -- Content
    message           VARCHAR,

    -- Parsing metadata
    format_used       VARCHAR,

    -- Partitioning
    date              DATE NOT NULL
);
```

**Extension fields** (client-specific, stored in metadata column or separate table):
- `error_code` / `code` / `rule` - Tool-specific error codes
- `tool_name` - Detected tool (gcc, pytest, ruff)
- `category` - Error category (compile, lint, test)
- `fingerprint` - Deduplication hash
- `test_name`, `status` - For test results
- `context` - Surrounding log lines
- `log_line_start`, `log_line_end` - Position in output

**Rationale:** Different tools parse different things. The core events table is minimal; extensions let tools add what they need without breaking compatibility.

### Layer 3: Sessions (Optional)

Add persistent session tracking (for shell hooks, long-running processes).

```sql
CREATE TABLE sessions (
    session_id        VARCHAR PRIMARY KEY,
    client_id         VARCHAR NOT NULL,
    invoker           VARCHAR NOT NULL,
    invoker_pid       INTEGER,
    invoker_type      VARCHAR,              -- shell, cli, hook, mcp
    registered_at     TIMESTAMP NOT NULL,
    date              DATE NOT NULL
);

-- Invocations reference sessions
ALTER TABLE invocations ADD COLUMN session_id VARCHAR;
```

**Rationale:** Shell hooks (shq) need sessions. CLI tools (blq) don't - they can use a synthetic session ID or omit it entirely.

### Layer 4: Multi-Writer & Crash Recovery (Optional)

For concurrent writers and crash recovery:

```
$BIRD_ROOT/
├── db/
│   ├── bird.duckdb
│   ├── pending/                 # In-flight tracking
│   │   └── {session}--{uuid}.pending
│   └── data/
│       ├── recent/              # Hot tier (parquet files)
│       │   ├── invocations/status=pending|completed|orphaned/
│       │   ├── outputs/
│       │   └── events/
│       └── archive/             # Cold tier
└── config.toml
```

**Additional features:**
- Pending file tracking
- Status partitioning (pending/completed/orphaned)
- Hot/cold tier management
- Compaction

**Rationale:** This complexity is necessary for shq (shell hooks run concurrently, can crash). It's overkill for blq (CLI, synchronous, single-writer).

### Layer 5: Remote Sync (Optional)

Push/pull to remote storage:

```toml
[[remotes]]
name = "team"
type = "s3"
uri = "s3://bucket/bird/"
```

**Rationale:** Valuable for CI/CD integration but not needed by all clients.

---

## Part 3: Specific Proposals

### Proposal 1: Universal `.bird/` with App Namespacing

**Current spec:** Mandates `.bird/` for project-level storage.

**Proposed:** Keep `.bird/` as universal, with `.bird/db/` as the standard database location. Apps extend via namespaced files:

```
.bird/
├── db/
│   └── bird.duckdb          # Universal database location
├── blobs/
│   └── content/             # Universal blob storage
├── config.toml              # Universal config
│
├── blq.commands.toml        # blq-specific: command registry
├── shq.hints.toml           # shq-specific: format hints
└── {app}.{purpose}.toml     # Pattern: app-namespaced files
```

**Migration for blq:**
- Migrate from `.lq/` to `.bird/`
- Move `commands.yaml` → `.bird/blq.commands.toml`
- Move database to `.bird/db/bird.duckdb`

**Benefits:**
- Single discovery point (`.bird/`)
- No conflicts between apps
- Apps can coexist in same project
- Clear ownership via filename prefix

### Proposal 2: Git-Tracked vs Local Data

Make explicit which files should be version-controlled:

```
$BIRD_ROOT/
├── config.yaml          # GIT-TRACK: Project configuration
├── commands.yaml        # GIT-TRACK: Registered commands (client-specific)
├── format-hints.toml    # GIT-TRACK: Format detection hints
├── .gitignore           # Auto-generated to ignore local data
│
├── bird.duckdb          # LOCAL: Database
├── bird.duckdb.wal      # LOCAL: WAL file
├── blobs/               # LOCAL: Content-addressed storage
├── pending/             # LOCAL: In-flight tracking
└── *.log                # LOCAL: Error logs
```

**Auto-generated .gitignore:**
```gitignore
# BIRD local data (auto-generated, do not edit)
bird.duckdb
bird.duckdb.wal
blobs/
pending/
*.log
```

**Benefits:**
- Teams can share command registrations and config
- Local data stays local
- Clear separation

### Proposal 3: Command Registry (Extension)

blq has a concept of "registered commands" that BIRD doesn't have. This is useful for:
- Reusable build/test commands
- Format hints per command
- Capture configuration per command

**Proposed extension (optional for any client):**

```yaml
# commands.yaml (git-tracked)
commands:
  build:
    cmd: "make -j8"
    description: "Build the project"
    format_hint: "gcc"
    capture_output: true
    timeout: 600

  test:
    cmd: "pytest"
    format_hint: "pytest"
    capture_output: true
```

**Schema extension:**

```sql
-- Add to invocations
ALTER TABLE invocations ADD COLUMN source_name VARCHAR;   -- Registered command name
ALTER TABLE invocations ADD COLUMN source_type VARCHAR;   -- run, exec, import, capture
```

**Rationale:** This is CLI-tool specific but generalizes well. A shell could have command aliases too.

### Proposal 4: Git/CI Metadata (Extension)

blq captures VCS and CI context. Propose as standard extension fields:

```sql
-- Add to invocations (optional columns)
ALTER TABLE invocations ADD COLUMN git_commit VARCHAR;
ALTER TABLE invocations ADD COLUMN git_branch VARCHAR;
ALTER TABLE invocations ADD COLUMN git_dirty BOOLEAN;
ALTER TABLE invocations ADD COLUMN ci JSON;              -- CI provider context
```

**CI JSON structure:**
```json
{
  "provider": "github_actions",
  "workflow": "CI",
  "run_id": "12345",
  "run_number": 42,
  "job": "test",
  "actor": "username"
}
```

**Rationale:** Build tools (blq, CI systems) need this. Shell hooks (shq) might not. Making it optional satisfies both.

### Proposal 5: Simplified Storage for Single-Writer

When a client knows it's the only writer, it can use a simpler layout:

```
$BIRD_ROOT/                      # Simplified (single-writer)
├── bird.duckdb                  # All tables in one DB
├── blobs/content/               # Flat blob storage
└── config.yaml
```

vs

```
$BIRD_ROOT/                      # Full (multi-writer)
├── db/
│   ├── bird.duckdb
│   ├── pending/
│   └── data/
│       ├── recent/
│       │   ├── invocations/status=.../date=.../
│       │   └── ...
│       └── archive/
└── config.toml
```

**Selection:**
```toml
[storage]
mode = "duckdb"           # Single-writer: tables in bird.duckdb
# mode = "parquet"        # Multi-writer: parquet files + DuckDB views
layout = "simplified"     # Or "full" for complete directory structure
```

**Rationale:** The full structure adds overhead (many directories, files, compaction) that single-writer clients don't need.

### Proposal 6: TOML as Standard Configuration Format

**Decision:** Standardize on TOML for all BIRD configuration files.

```
.bird/
├── config.toml              # Main configuration
├── blq.commands.toml        # App-specific configs also TOML
└── shq.hints.toml
```

**Rationale:**
- YAML has surprising edge cases (Norway problem: `NO` → `false`, implicit typing)
- JSON lacks comments, trailing commas
- TOML is simple, explicit, and unambiguous
- Rust ecosystem (shq) already uses TOML
- Python has excellent TOML support (`tomllib` in stdlib since 3.11)

**Migration for blq:** Convert `config.yaml` and `commands.yaml` to TOML format.

### Proposal 7: Events Extension Mechanism

The events table should support client-specific fields without schema changes:

**Option A: JSON metadata column**
```sql
CREATE TABLE events (
    -- Core fields (Layer 2)
    id, invocation_id, severity, message, ref_file, ref_line, ...

    -- Extension fields
    metadata JSON                -- Client-specific data
);
```

**Option B: Standardized extension columns**
```sql
CREATE TABLE events (
    -- Core fields
    ...

    -- Standardized extensions (nullable)
    error_code        VARCHAR,   -- E0308, F401, etc.
    tool_name         VARCHAR,   -- gcc, pytest, ruff
    category          VARCHAR,   -- compile, lint, test
    fingerprint       VARCHAR,   -- Dedup hash
    test_name         VARCHAR,   -- For test events
    test_status       VARCHAR,   -- passed, failed, skipped
    context           VARCHAR,   -- Surrounding lines
    log_line_start    INTEGER,
    log_line_end      INTEGER
);
```

**Recommendation:** Option B (standardized extensions) for interoperability, with Option A (metadata JSON) as escape hatch for truly custom data.

### Proposal 8: MCP Integration (Extension)

blq provides an MCP (Model Context Protocol) server for AI agent integration. This could be a BIRD extension:

```toml
[mcp]
enabled = true
transport = "stdio"           # Or "sse"
disabled_tools = ["reset"]    # Security: disable dangerous tools
safe_mode = false             # Disable all state-modifying tools
```

**Rationale:** AI agents are increasingly important for developer tools. BIRD clients that expose MCP should have a standard configuration format.

---

## Part 4: Compatibility Matrix

| Feature | shq (shell) | blq (CLI) | CI System | IDE Plugin |
|---------|-------------|-----------|-----------|------------|
| **Layer 0: Core** | ✓ | ✓ | ✓ | ✓ |
| **Layer 1: Outputs** | ✓ | ✓ | ✓ | Optional |
| **Layer 2: Events** | ✓ | ✓ | ✓ | ✓ |
| **Layer 3: Sessions** | ✓ | Optional | Optional | ✓ |
| **Layer 4: Multi-Writer** | ✓ | ✗ | Optional | ✗ |
| **Layer 5: Remote Sync** | Optional | ✓ | ✓ | Optional |
| **Git/CI Metadata** | Optional | ✓ | ✓ | Optional |
| **Command Registry** | Optional | ✓ | ✓ | ✗ |
| **MCP Server** | ✗ | ✓ | Optional | ✓ |

---

## Part 5: Migration Path

### For blq

1. **Phase 1:** Adopt BIRD v5 core schema (current blq is already close)
2. **Phase 2:** Move from `.lq/` to `.bird/` or maintain both via discovery
3. **Phase 3:** Implement sync for CI integration

### For shq

1. **Phase 1:** Document which layers shq implements
2. **Phase 2:** Make sessions optional for clients that don't need them
3. **Phase 3:** Add extension columns to events table

### For New Clients

1. Start with Layer 0 (core only)
2. Add layers as needed
3. Use extension mechanism for custom fields

---

## Part 6: Resolved Design Decisions

Based on discussion, the following decisions have been made:

### 1. Directory Structure: Universal `.bird/db/` with App-Specific Extensions

**Decision:** `.bird/db/` is the universal standard. App-specific files live in `.bird/` with naming conventions.

```
.bird/                           # Universal BIRD root
├── db/
│   └── bird.duckdb              # Universal database location
├── blobs/
│   └── content/                 # Universal blob storage
├── config.toml                  # Universal config format
│
├── blq.commands.toml            # App-specific: blq command registry
├── shq.sessions.toml            # App-specific: shq session config
└── myapp.custom.toml            # App-specific: custom app config
```

**Naming convention for app-specific files:** `{app}.{purpose}.toml`

**Rationale:** This provides a single discovery point (`.bird/`) while allowing apps to extend without conflicts.

### 2. Schema Versioning: Version Table Required

**Decision:** `bird.duckdb` must contain a `bird_meta` table:

```sql
CREATE TABLE bird_meta (
    key       VARCHAR PRIMARY KEY,
    value     VARCHAR NOT NULL
);

-- Required entries:
INSERT INTO bird_meta VALUES ('schema_version', '5');
INSERT INTO bird_meta VALUES ('created_at', '2026-02-09T10:30:00Z');
INSERT INTO bird_meta VALUES ('client_name', 'blq');      -- Primary client
INSERT INTO bird_meta VALUES ('client_version', '0.7.0');
```

**Migration:** Clients check `schema_version` on connect and can run migrations if needed.

### 3. Blob Compression: Supported, Not Required

**Decision:** Compression is optional. Blobs may be stored as:
- `.bin` - uncompressed (default)
- `.bin.gz` - gzip compressed
- `.bin.zst` - zstd compressed

**Empirical data from blq:**
```
Build log compression ratios (pytest, ruff, make output):
- gzip: 11-23% of original (77-89% storage savings)
- zstd: 12-23% of original (similar)

1.5MB uncompressed → ~200-300KB compressed
```

**Storage reference format:**
```
file:ab/abc123.bin       # Uncompressed
file:ab/abc123.bin.gz    # Gzip
file:ab/abc123.bin.zst   # Zstd
```

**Rationale:** Compression yields significant savings for text-heavy build logs but adds complexity. Making it optional lets clients choose based on their storage/CPU tradeoffs.

### 4. Standard Macros: Not Required, Tables Are Core

**Decision:** BIRD does not mandate specific SQL macros. The core contract is:

1. **Tables exist** with defined schemas (invocations, outputs, events, sessions)
2. **DuckDB file** provides SQL access to those tables
3. **Clients may add macros** for convenience but these are not part of the spec

**Rationale:** Macros are implementation conveniences. The interoperability contract is the table schemas. A client reading another client's `bird.duckdb` can query tables directly without needing matching macro definitions.

**Recommended (not required) macros:**
```sql
-- Convenience macro for recent errors
CREATE MACRO bird_errors(n := 10) AS TABLE
    SELECT * FROM events WHERE severity = 'error'
    ORDER BY timestamp DESC LIMIT n;
```

### 5. Configuration Format: TOML Standard

**Decision:** Standardize on TOML for all BIRD configuration.

**Rationale:**
- YAML has surprising edge cases (Norway problem, implicit typing)
- JSON lacks comments
- TOML is simple, unambiguous, and widely supported
- Rust ecosystem (shq) already uses TOML
- Python has excellent TOML support (`tomllib` in stdlib since 3.11)

### 6. Cross-Client Queries: ATTACH Pattern

**Decision:** When multiple BIRD clients exist, use DuckDB's `ATTACH`:

```sql
-- From shq, attach blq's database
ATTACH '.bird/db/bird.duckdb' AS bird;

-- Query unified view
SELECT * FROM bird.invocations
WHERE client_id LIKE 'blq%'
ORDER BY timestamp DESC;
```

**Same-database scenario:** If shq and blq share `.bird/db/bird.duckdb`, they distinguish records via `client_id`.

---

## Appendix A: blq's Current Implementation and Migration Path

**Current blq structure (pre-BIRD v5):**

```
.lq/
├── blq.duckdb           # DuckDB mode (single-writer)
├── blobs/content/       # Simplified blob layout
├── config.yaml          # YAML config
├── commands.yaml        # Command registry
└── schema.sql           # Reference only
```

**Target BIRD v5 structure:**

```
.bird/
├── db/
│   └── bird.duckdb      # Universal database location
├── blobs/
│   └── content/         # Blob storage (with optional compression)
├── config.toml          # TOML config
└── blq.commands.toml    # App-namespaced command registry
```

**Migration steps:**
1. Create `.bird/db/` directory
2. Move/migrate database to `.bird/db/bird.duckdb`
3. Add `bird_meta` table with schema version
4. Move blobs to `.bird/blobs/content/`
5. Convert `config.yaml` → `.bird/config.toml`
6. Convert `commands.yaml` → `.bird/blq.commands.toml`
7. Optionally compress existing blobs
8. Update `.gitignore` (`.bird/db/`, `.bird/blobs/` ignored; `.bird/*.toml` tracked)

**Tables:**
- `bird_meta` - Schema version and client info (new)
- `sessions` - Minimal session tracking
- `invocations` - With source_name, source_type, git metadata, CI context
- `outputs` - Content-addressed storage
- `events` - With tool_name, category, code, fingerprint, context

**MCP Tools:**
- run, exec, query, errors, warnings, events, inspect, info, status, history, diff, register_command, unregister_command, clean

---

## Appendix B: Proposed config.toml for blq (BIRD v5)

```toml
# .bird/config.toml

[bird]
schema_version = "v5"
client_name = "blq"
client_version = "0.7.0"
layers = [0, 1, 2]           # Core + Outputs + Events

[storage]
mode = "duckdb"              # Single-writer
compression = "gzip"         # Optional: gzip, zstd, or none
inline_threshold = 4096      # Bytes before blob storage

[project]
namespace = "teaguesterling"
name = "blq"

[capture]
env_vars = [
    "PATH",
    "VIRTUAL_ENV",
    "CC",
    "CXX",
]

[mcp]
disabled_tools = []
safe_mode = false
```

**Separate file:** `.bird/blq.commands.toml`
```toml
# .bird/blq.commands.toml - blq command registry

[commands.build]
cmd = "make -j8"
description = "Build the project"
format_hint = "gcc"
capture_output = true

[commands.test]
cmd = "pytest"
description = "Run tests"
format_hint = "pytest"
timeout = 600
```

---

## Summary

This proposal aims to make BIRD more accessible to diverse clients while maintaining the power needed for advanced use cases like shq. The key principles:

1. **Layered complexity** - Start simple, add features as needed
2. **Extension mechanism** - Client-specific fields without breaking compatibility
3. **Configuration flexibility** - Support multiple formats and layouts
4. **Clear separation** - Git-tracked config vs local data
5. **Discovery** - Multiple directory names with standard detection

We believe these changes would make BIRD a stronger foundation for the ecosystem of command-execution tracking tools.

---

*Prepared by the blq development team. We welcome feedback and discussion.*
