-- bird_schema.sql - BIRD (Buffer and Invocation Record Database) Schema for blq
--
-- This schema implements the BIRD specification using DuckDB tables (single-writer mode).
-- All reads go through views, writes go directly to tables.
--
-- Directory structure:
--   .lq/
--   ├── blq.duckdb          # Database with tables and views
--   ├── blobs/              # Content-addressed output storage
--   │   └── content/
--   │       ├── ab/
--   │       │   └── {hash}--{hint}.bin
--   │       └── ...
--   └── config.toml
--
-- BIRD spec: https://github.com/teaguesterling/magic/blob/main/docs/bird_spec.md

-- ============================================================================
-- CONFIGURATION
-- ============================================================================

-- Schema version for migrations
CREATE TABLE IF NOT EXISTS blq_metadata (
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);

-- Insert schema version (ignore if exists)
INSERT OR IGNORE INTO blq_metadata VALUES ('schema_version', '2.1.0');
INSERT OR IGNORE INTO blq_metadata VALUES ('storage_mode', 'duckdb');

-- Base path for blob storage (set at runtime)
CREATE OR REPLACE MACRO blq_blob_root() AS '.lq/blobs/content';

-- ============================================================================
-- CORE TABLES (BIRD Schema)
-- ============================================================================

-- Sessions table: tracks invoker sessions (shell, CLI, MCP)
CREATE TABLE IF NOT EXISTS sessions (
    -- Identity
    session_id        VARCHAR PRIMARY KEY,      -- e.g., "test" (source_name), "exec-2024-12-30"
    client_id         VARCHAR NOT NULL,         -- e.g., "blq-shell", "blq-mcp"

    -- Invoker information
    invoker           VARCHAR NOT NULL,         -- e.g., "blq", "blq-mcp"
    invoker_pid       INTEGER,                  -- Process ID (if applicable)
    invoker_type      VARCHAR NOT NULL,         -- "cli", "mcp", "import", "capture"

    -- Timing
    registered_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Context
    cwd               VARCHAR,                  -- Initial working directory

    -- Partitioning
    date              DATE NOT NULL DEFAULT CURRENT_DATE
);

-- ============================================================================
-- ATTEMPTS/OUTCOMES TABLES (BIRD v5 pattern for long-running commands)
-- ============================================================================

-- Attempts table: written at command START (before we know the outcome)
-- Enables tracking of running commands via LEFT JOIN with outcomes
CREATE TABLE IF NOT EXISTS attempts (
    -- Identity
    id                UUID PRIMARY KEY DEFAULT uuid(),  -- UUIDv7 when available
    session_id        VARCHAR NOT NULL,                 -- References sessions.session_id

    -- Timing (start only - completion is in outcomes)
    timestamp         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Context
    cwd               VARCHAR NOT NULL,

    -- Command
    cmd               VARCHAR NOT NULL,                 -- Full command string
    executable        VARCHAR,                          -- Extracted executable name
    pid               INTEGER,                          -- Process ID of the command

    -- Format detection
    format_hint       VARCHAR,                          -- Detected format (gcc, pytest, etc.)

    -- Client identity
    client_id         VARCHAR NOT NULL,                 -- e.g., "blq-shell"
    hostname          VARCHAR,
    username          VARCHAR,

    -- BIRD spec: user-defined tag (non-unique alias)
    tag               VARCHAR,                          -- e.g., "build-v1.2.3"

    -- blq-specific fields
    source_name       VARCHAR,                          -- Registered command name
    source_type       VARCHAR,                          -- 'run', 'exec', 'import', 'capture'
    environment       JSON,                             -- Captured environment variables
    platform          VARCHAR,                          -- OS (Linux, Darwin, Windows)
    arch              VARCHAR,                          -- Architecture (x86_64, arm64)
    git_commit        VARCHAR,                          -- HEAD SHA
    git_branch        VARCHAR,                          -- Current branch
    git_dirty         BOOLEAN,                          -- Uncommitted changes
    ci                JSON,                             -- CI provider context

    -- Partitioning
    date              DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Outcomes table: written at command COMPLETION
-- Commands without outcomes are "pending" (still running)
-- Commands with outcomes but NULL exit_code are "orphaned" (crashed)
CREATE TABLE IF NOT EXISTS outcomes (
    -- Identity (1:1 with attempts)
    attempt_id        UUID PRIMARY KEY,                 -- References attempts.id

    -- Timing
    completed_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_ms       BIGINT,                           -- Wall-clock duration

    -- Result
    exit_code         INTEGER,                          -- NULL = crashed/unknown
    signal            INTEGER,                          -- If killed by signal (SIGTERM=15, SIGKILL=9)
    timeout           BOOLEAN DEFAULT FALSE,            -- If killed by timeout

    -- Partitioning
    date              DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Invocations table: command executions (was "runs" in blq v1)
CREATE TABLE IF NOT EXISTS invocations (
    -- Identity
    id                UUID PRIMARY KEY DEFAULT uuid(),  -- UUIDv7 when available
    session_id        VARCHAR NOT NULL,                 -- References sessions.session_id

    -- Timing
    timestamp         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_ms       BIGINT,

    -- Context
    cwd               VARCHAR NOT NULL,

    -- Command
    cmd               VARCHAR NOT NULL,                 -- Full command string
    executable        VARCHAR,                          -- Extracted executable name
    pid               INTEGER,                          -- Process ID of the command

    -- Result
    exit_code         INTEGER NOT NULL,

    -- Format detection
    format_hint       VARCHAR,                          -- Detected format (gcc, pytest, etc.)

    -- Client identity
    client_id         VARCHAR NOT NULL,                 -- e.g., "blq-shell"
    hostname          VARCHAR,
    username          VARCHAR,

    -- BIRD spec: user-defined tag (non-unique alias)
    tag               VARCHAR,                          -- e.g., "build-v1.2.3"

    -- blq-specific fields
    source_name       VARCHAR,                          -- Registered command name
    source_type       VARCHAR,                          -- 'run', 'exec', 'import', 'capture'
    environment       JSON,                             -- Captured environment variables
    platform          VARCHAR,                          -- OS (Linux, Darwin, Windows)
    arch              VARCHAR,                          -- Architecture (x86_64, arm64)
    git_commit        VARCHAR,                          -- HEAD SHA
    git_branch        VARCHAR,                          -- Current branch
    git_dirty         BOOLEAN,                          -- Uncommitted changes
    ci                JSON,                             -- CI provider context

    -- Partitioning
    date              DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Outputs table: captured stdout/stderr
CREATE TABLE IF NOT EXISTS outputs (
    -- Identity
    id                UUID PRIMARY KEY DEFAULT uuid(),
    invocation_id     UUID NOT NULL,                    -- References invocations.id

    -- Stream
    stream            VARCHAR NOT NULL,                 -- 'stdout', 'stderr', 'combined'

    -- Content identification
    content_hash      VARCHAR NOT NULL,                 -- BLAKE3 hash (hex, 64 chars)
    byte_length       BIGINT NOT NULL,

    -- Storage location (polymorphic)
    storage_type      VARCHAR NOT NULL,                 -- 'inline' or 'blob'
    storage_ref       VARCHAR NOT NULL,                 -- data: URI or file: path

    -- Content metadata
    content_type      VARCHAR,                          -- MIME type or format hint

    -- Partitioning
    date              DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Events table: parsed diagnostics (errors, warnings, test results)
CREATE TABLE IF NOT EXISTS events (
    -- Identity
    id                UUID PRIMARY KEY DEFAULT uuid(),
    invocation_id     UUID NOT NULL,                    -- References invocations.id
    event_index       INTEGER NOT NULL,                 -- Index within invocation

    -- Client identity (denormalized for cross-client queries)
    client_id         VARCHAR NOT NULL,
    hostname          VARCHAR,

    -- Event classification
    event_type        VARCHAR,                          -- 'diagnostic', 'test_result', etc.
    severity          VARCHAR,                          -- 'error', 'warning', 'info', 'note'

    -- Source location (BIRD spec names)
    ref_file          VARCHAR,                          -- Source file path
    ref_line          INTEGER,                          -- Line number
    ref_column        INTEGER,                          -- Column number

    -- Content
    message           VARCHAR,                          -- Error/warning message
    code              VARCHAR,                          -- Error code (e.g., "E0308")
    rule              VARCHAR,                          -- Rule name (e.g., "no-unused-vars")

    -- blq-specific fields
    tool_name         VARCHAR,                          -- Tool that generated event
    category          VARCHAR,                          -- Error category
    fingerprint       VARCHAR,                          -- Unique identifier for dedup
    log_line_start    INTEGER,                          -- Start line in raw log
    log_line_end      INTEGER,                          -- End line in raw log
    context           VARCHAR,                          -- Surrounding context
    metadata          JSON,                             -- Format-specific extras

    -- Parsing metadata
    format_used       VARCHAR,                          -- Parser format (gcc, cargo, pytest)

    -- Partitioning
    date              DATE NOT NULL DEFAULT CURRENT_DATE
);

-- Blob registry: tracks content-addressed blobs for deduplication
CREATE TABLE IF NOT EXISTS blob_registry (
    content_hash      VARCHAR PRIMARY KEY,              -- BLAKE3 hash (hex)
    byte_length       BIGINT NOT NULL,
    compression       VARCHAR DEFAULT 'none',           -- 'none', 'gzip', 'zstd'
    ref_count         INTEGER DEFAULT 1,
    first_seen        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    storage_path      VARCHAR NOT NULL                  -- Relative path within blobs/
);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Attempts indexes
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
CREATE INDEX IF NOT EXISTS idx_attempts_date ON attempts(date);
CREATE INDEX IF NOT EXISTS idx_attempts_source ON attempts(source_name);
CREATE INDEX IF NOT EXISTS idx_attempts_timestamp ON attempts(timestamp DESC);

-- Outcomes indexes
CREATE INDEX IF NOT EXISTS idx_outcomes_date ON outcomes(date);
CREATE INDEX IF NOT EXISTS idx_outcomes_completed ON outcomes(completed_at DESC);

-- Legacy invocations indexes (for backward compatibility)
CREATE INDEX IF NOT EXISTS idx_invocations_session ON invocations(session_id);
CREATE INDEX IF NOT EXISTS idx_invocations_date ON invocations(date);
CREATE INDEX IF NOT EXISTS idx_invocations_source ON invocations(source_name);
CREATE INDEX IF NOT EXISTS idx_invocations_timestamp ON invocations(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_events_invocation ON events(invocation_id);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_file ON events(file_path);

CREATE INDEX IF NOT EXISTS idx_outputs_invocation ON outputs(invocation_id);

-- ============================================================================
-- COMPATIBILITY VIEWS (blq v1 API)
-- ============================================================================

-- blq_load_events() - returns events with invocation metadata joined
-- This provides backward compatibility with the v1 flat schema
CREATE OR REPLACE VIEW blq_events_flat AS
WITH numbered_invocations AS (
    -- Compute run_serial per invocation (global sequence by timestamp)
    SELECT
        id,
        tag,
        ROW_NUMBER() OVER (ORDER BY timestamp) AS run_serial
    FROM invocations
)
SELECT
    -- Event identity
    e.event_index AS event_id,

    -- Run identity
    i.id AS run_id,                                           -- UUID (internal)
    ni.run_serial,                                            -- Global sequence number
    CASE
        WHEN ni.tag IS NOT NULL THEN ni.tag || ':' || ni.run_serial::VARCHAR
        ELSE ni.run_serial::VARCHAR
    END AS run_ref,                                           -- Human-friendly: "build:1" or "3"

    -- Event reference: "tag:serial:event" or "serial:event"
    CASE
        WHEN ni.tag IS NOT NULL THEN ni.tag || ':' || ni.run_serial::VARCHAR || ':' || e.event_index::VARCHAR
        ELSE ni.run_serial::VARCHAR || ':' || e.event_index::VARCHAR
    END AS ref,

    -- Invocation fields (denormalized for v1 compatibility)
    i.source_name,
    i.source_type,
    i.cmd AS command,
    i.timestamp AS started_at,
    i.timestamp + INTERVAL (i.duration_ms / 1000) SECOND AS completed_at,
    i.exit_code,
    i.cwd,
    i.executable AS executable_path,
    i.hostname,
    i.platform,
    i.arch,
    i.git_commit,
    i.git_branch,
    i.git_dirty,
    i.ci,
    i.environment,
    i.tag,

    -- Event fields
    e.severity,
    e.message,
    e.ref_file,
    e.ref_line,
    e.ref_column,
    e.tool_name,
    e.category,
    e.code,
    e.rule,
    e.fingerprint,
    e.log_line_start,
    e.log_line_end,
    e.context,
    e.metadata,

    -- Partition info
    i.date AS log_date,
    i.source_type AS partition_source,

    -- Internal IDs for advanced queries
    i.id AS invocation_id,
    e.id AS event_uuid
FROM events e
JOIN invocations i ON e.invocation_id = i.id
JOIN numbered_invocations ni ON i.id = ni.id;

-- blq_load_events() macro for backward compatibility
CREATE OR REPLACE MACRO blq_load_events() AS TABLE
SELECT * FROM blq_events_flat;

-- ============================================================================
-- BIRD-NATIVE VIEWS
-- ============================================================================

-- Attempts with outcomes joined - provides status derivation
-- Status: 'pending' (no outcome), 'orphaned' (outcome without exit_code), 'completed'
CREATE OR REPLACE VIEW attempts_with_status AS
SELECT
    a.id,
    a.session_id,
    a.timestamp,
    a.cwd,
    a.cmd,
    a.executable,
    a.format_hint,
    a.client_id,
    a.hostname,
    a.username,
    a.tag,
    a.source_name,
    a.source_type,
    a.environment,
    a.platform,
    a.arch,
    a.git_commit,
    a.git_branch,
    a.git_dirty,
    a.ci,
    a.date,
    o.completed_at,
    o.exit_code,
    o.duration_ms,
    o.signal,
    o.timeout,
    -- Status derived from join (BIRD v5 pattern)
    CASE
        WHEN o.attempt_id IS NULL THEN 'pending'
        WHEN o.exit_code IS NULL THEN 'orphaned'
        ELSE 'completed'
    END AS status
FROM attempts a
LEFT JOIN outcomes o ON a.id = o.attempt_id;

-- Recent invocations (last 14 days)
CREATE OR REPLACE VIEW invocations_recent AS
SELECT * FROM invocations
WHERE date >= CURRENT_DATE - INTERVAL '14 days';

-- Recent events (last 14 days)
CREATE OR REPLACE VIEW events_recent AS
SELECT * FROM events
WHERE date >= CURRENT_DATE - INTERVAL '14 days';

-- ============================================================================
-- MACROS (Updated for BIRD schema)
-- ============================================================================

-- Status badge
CREATE OR REPLACE MACRO blq_status_badge(error_count, warning_count, exit_code := 0) AS
    CASE
        WHEN exit_code = -1 THEN '[TIME]'
        WHEN error_count > 0 THEN '[FAIL]'
        WHEN exit_code != 0 THEN '[FAIL]'
        WHEN warning_count > 0 THEN '[WARN]'
        ELSE '[ OK ]'
    END;

-- Load runs with aggregated stats (uses invocations table)
CREATE OR REPLACE MACRO blq_load_runs() AS TABLE
SELECT
    ROW_NUMBER() OVER (ORDER BY i.timestamp) AS run_id,
    i.id AS invocation_id,
    i.source_name,
    i.source_type,
    i.cmd AS command,
    i.timestamp AS started_at,
    i.timestamp + INTERVAL (COALESCE(i.duration_ms, 0) / 1000) SECOND AS completed_at,
    i.exit_code,
    i.cwd,
    i.executable AS executable_path,
    i.hostname,
    i.platform,
    i.arch,
    i.git_commit,
    i.git_branch,
    i.git_dirty,
    i.ci,
    i.tag,
    COUNT(e.id) AS event_count,
    COUNT(e.id) FILTER (WHERE e.severity = 'error') AS error_count,
    COUNT(e.id) FILTER (WHERE e.severity = 'warning') AS warning_count,
    COUNT(e.id) FILTER (WHERE e.severity = 'info') AS info_count,
    COUNT(DISTINCT e.fingerprint) FILTER (WHERE e.severity = 'error') AS unique_error_count,
    COUNT(DISTINCT e.fingerprint) FILTER (WHERE e.severity = 'warning') AS unique_warning_count,
    i.date AS log_date
FROM invocations i
LEFT JOIN events e ON e.invocation_id = i.id
GROUP BY i.id, i.source_name, i.source_type, i.cmd, i.timestamp, i.duration_ms,
         i.exit_code, i.cwd, i.executable, i.hostname, i.platform, i.arch,
         i.git_commit, i.git_branch, i.git_dirty, i.ci, i.tag, i.date;

-- ============================================================================
-- ATTEMPTS/OUTCOMES MACROS (for long-running command support)
-- ============================================================================

-- Load attempts with status (pending/orphaned/completed)
CREATE OR REPLACE MACRO blq_load_attempts() AS TABLE
SELECT
    ROW_NUMBER() OVER (ORDER BY a.timestamp) AS run_id,
    a.id AS attempt_id,
    a.session_id,
    a.timestamp AS started_at,
    o.completed_at,
    a.cwd,
    a.cmd AS command,
    a.executable,
    a.format_hint,
    a.client_id,
    a.hostname,
    a.username,
    a.tag,
    a.source_name,
    a.source_type,
    a.environment,
    a.platform,
    a.arch,
    a.git_commit,
    a.git_branch,
    a.git_dirty,
    a.ci,
    a.date,
    o.exit_code,
    o.duration_ms,
    o.signal,
    o.timeout,
    -- Status derived from join (BIRD v5 pattern)
    CASE
        WHEN o.attempt_id IS NULL THEN 'pending'
        WHEN o.exit_code IS NULL THEN 'orphaned'
        ELSE 'completed'
    END AS status,
    -- Elapsed time for pending commands
    CASE
        WHEN o.attempt_id IS NULL THEN
            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - a.timestamp)) * 1000
        ELSE o.duration_ms
    END AS elapsed_ms
FROM attempts a
LEFT JOIN outcomes o ON a.id = o.attempt_id;

-- Load latest run per source with status badge (includes pending/running)
CREATE OR REPLACE MACRO blq_load_source_status() AS TABLE
WITH all_runs AS (
    -- Completed runs with event counts
    SELECT
        source_name,
        blq_status_badge(error_count, warning_count, exit_code) AS badge,
        error_count,
        warning_count,
        info_count,
        event_count,
        unique_error_count,
        unique_warning_count,
        started_at,
        completed_at,
        exit_code,
        run_id,
        invocation_id,
        'completed' AS status
    FROM blq_load_runs()
    UNION ALL
    -- Pending/running attempts (no event counts yet)
    SELECT
        source_name,
        '[ .. ]' AS badge,
        0 AS error_count,
        0 AS warning_count,
        0 AS info_count,
        0 AS event_count,
        0 AS unique_error_count,
        0 AS unique_warning_count,
        started_at,
        NULL AS completed_at,
        NULL AS exit_code,
        run_id,
        attempt_id AS invocation_id,
        'pending' AS status
    FROM blq_load_attempts()
    WHERE status = 'pending'
)
SELECT
    source_name,
    badge,
    error_count,
    warning_count,
    info_count,
    event_count,
    unique_error_count,
    unique_warning_count,
    started_at,
    completed_at,
    exit_code,
    run_id,
    invocation_id,
    status
FROM all_runs
QUALIFY row_number() OVER (PARTITION BY source_name ORDER BY started_at DESC) = 1
ORDER BY source_name;

-- Quick status overview (ordered by age, newest first)
CREATE OR REPLACE MACRO blq_status() AS TABLE
SELECT
    badge,
    source_name,
    error_count,
    warning_count,
    info_count,
    event_count,
    unique_error_count,
    unique_warning_count,
    age(now(), started_at::TIMESTAMP) AS age
FROM blq_load_source_status()
ORDER BY started_at DESC;

-- Recent errors
CREATE OR REPLACE MACRO blq_errors(n := 10) AS TABLE
SELECT
    i.source_name,
    e.ref_file,
    e.ref_line,
    e.ref_column,
    LEFT(e.message, 200) AS message,
    e.tool_name,
    e.category
FROM events e
JOIN invocations i ON e.invocation_id = i.id
WHERE e.severity = 'error'
ORDER BY i.timestamp DESC, e.event_index
LIMIT n;

-- Recent warnings
CREATE OR REPLACE MACRO blq_warnings(n := 10) AS TABLE
SELECT
    i.source_name,
    e.ref_file,
    e.ref_line,
    e.ref_column,
    LEFT(e.message, 200) AS message,
    e.tool_name,
    e.category
FROM events e
JOIN invocations i ON e.invocation_id = i.id
WHERE e.severity = 'warning'
ORDER BY i.timestamp DESC, e.event_index
LIMIT n;

-- Run history
CREATE OR REPLACE MACRO blq_history(n := 20) AS TABLE
SELECT
    run_id,
    blq_status_badge(error_count, warning_count, exit_code) AS badge,
    source_name,
    event_count,
    error_count,
    warning_count,
    info_count,
    started_at,
    age(completed_at::TIMESTAMP, started_at::TIMESTAMP) AS duration
FROM blq_load_runs()
ORDER BY started_at DESC
LIMIT n;

-- Get running commands (attempts without outcomes)
CREATE OR REPLACE MACRO blq_running() AS TABLE
SELECT
    ROW_NUMBER() OVER (ORDER BY timestamp) AS run_id,
    id AS attempt_id,
    source_name,
    cmd AS command,
    timestamp AS started_at,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - timestamp)) * 1000 AS elapsed_ms,
    tag,
    hostname
FROM attempts a
WHERE NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.attempt_id = a.id)
ORDER BY timestamp DESC;

-- History with status filter (for --status=running, --status=completed, etc.)
CREATE OR REPLACE MACRO blq_history_status(status_filter, n := 20) AS TABLE
SELECT
    run_id,
    CASE
        WHEN status = 'pending' THEN '[ .. ]'
        ELSE blq_status_badge(0, 0, exit_code)
    END AS badge,
    source_name,
    status,
    started_at,
    CASE
        WHEN status = 'pending' THEN
            'running ' || (elapsed_ms / 1000)::VARCHAR || 's'
        ELSE
            age(completed_at::TIMESTAMP, started_at::TIMESTAMP)::VARCHAR
    END AS duration
FROM blq_load_attempts()
WHERE status_filter IS NULL OR status = status_filter
ORDER BY started_at DESC
LIMIT n;

-- Compare two runs by run_id
CREATE OR REPLACE MACRO blq_diff(run1, run2) AS TABLE
WITH runs AS (
    SELECT run_id, invocation_id FROM blq_load_runs()
),
r1 AS (
    SELECT e.tool_name, e.category,
           COUNT(*) FILTER (WHERE e.severity = 'error') AS errors
    FROM events e
    JOIN runs r ON e.invocation_id = r.invocation_id
    WHERE r.run_id = run1
    GROUP BY e.tool_name, e.category
),
r2 AS (
    SELECT e.tool_name, e.category,
           COUNT(*) FILTER (WHERE e.severity = 'error') AS errors
    FROM events e
    JOIN runs r ON e.invocation_id = r.invocation_id
    WHERE r.run_id = run2
    GROUP BY e.tool_name, e.category
)
SELECT
    COALESCE(r1.tool_name, r2.tool_name) AS tool_name,
    COALESCE(r1.category, r2.category) AS category,
    COALESCE(r1.errors, 0) AS run1_errors,
    COALESCE(r2.errors, 0) AS run2_errors,
    COALESCE(r2.errors, 0) - COALESCE(r1.errors, 0) AS delta
FROM r1 FULL OUTER JOIN r2
  ON r1.tool_name = r2.tool_name AND r1.category = r2.category
WHERE COALESCE(r1.errors, 0) != COALESCE(r2.errors, 0)
ORDER BY ABS(delta) DESC;

-- ============================================================================
-- REFERENCE MACROS
-- ============================================================================

-- Create event reference string: "5:3" for run 5, event 3
CREATE OR REPLACE MACRO blq_ref(run_id, event_id) AS
    run_id::VARCHAR || ':' || event_id::VARCHAR;

-- Parse event reference
CREATE OR REPLACE MACRO blq_parse_ref(ref) AS {
    run_id: CAST(split_part(ref, ':', 1) AS INTEGER),
    event_id: CAST(split_part(ref, ':', 2) AS INTEGER)
};

-- Format location string
CREATE OR REPLACE MACRO blq_location(file_path, line_number, column_number) AS
    COALESCE(file_path, '?') ||
    CASE WHEN line_number IS NOT NULL THEN ':' || line_number::VARCHAR ELSE '' END ||
    CASE WHEN column_number IS NOT NULL AND column_number > 0 THEN ':' || column_number::VARCHAR ELSE '' END;

-- ============================================================================
-- OUTPUT ACCESS
-- ============================================================================

-- Get output content for an invocation
CREATE OR REPLACE MACRO blq_output(inv_id, stream_name := 'combined') AS TABLE
SELECT
    o.stream,
    o.storage_type,
    o.storage_ref,
    o.byte_length,
    o.content_hash
FROM outputs o
WHERE o.invocation_id = inv_id
  AND (stream_name = 'combined' OR o.stream = stream_name);

-- ============================================================================
-- JSON OUTPUT (for MCP/agents)
-- ============================================================================

CREATE OR REPLACE MACRO blq_errors_json(n := 10) AS TABLE
SELECT to_json(list(err)) AS json FROM (
    SELECT {
        ref: blq_ref(
            (SELECT run_id FROM blq_load_runs() r WHERE r.invocation_id = e.invocation_id),
            e.event_index
        ),
        file_path: e.ref_file,
        line: e.ref_line,
        col: e.ref_column,
        message: e.message,
        tool: e.tool_name,
        category: e.category,
        fingerprint: e.fingerprint
    } AS err
    FROM events e
    JOIN invocations i ON e.invocation_id = i.id
    WHERE e.severity = 'error'
    ORDER BY i.timestamp DESC, e.event_index
    LIMIT n
);
