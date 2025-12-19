-- blq.sql - BLQ (Build Log Query) Schema and Macros
-- This file defines the schema, views, and macros for querying blq logs.
--
-- Directory structure (Hive partitioned):
--   .lq/
--   ├── logs/
--   │   └── date=YYYY-MM-DD/
--   │       └── source=run|import|capture/
--   │           └── {run_id}_{name}_{timestamp}.parquet
--   ├── raw/           # Optional: raw log files
--   │   └── {run_id}.log
--   ├── blq.duckdb     # Database with views and macros
--   └── schema.sql     # This file (reference copy)
--
-- Usage:
--   .read .lq/schema.sql
--   SELECT * FROM blq_status();

-- ============================================================================
-- CONFIGURATION
-- ============================================================================

-- Base path for logs (overridden by LogStore at runtime)
CREATE OR REPLACE MACRO blq_base_path() AS '.lq/logs';

-- Status badge based on error/warning counts
-- Returns: '[FAIL]' if errors, '[WARN]' if warnings, '[ OK ]' otherwise
CREATE OR REPLACE MACRO blq_status_badge(error_count, warning_count) AS
    CASE
        WHEN error_count > 0 THEN '[FAIL]'
        WHEN warning_count > 0 THEN '[WARN]'
        ELSE '[ OK ]'
    END;

-- ============================================================================
-- CORE DATA ACCESS
-- ============================================================================

-- Load all events from parquet files (table-returning macro)
-- This is the primary data access point - always use this or blq_events view
CREATE OR REPLACE MACRO blq_load_events() AS TABLE
SELECT
    *,
    -- Extract partition columns if not already present
    regexp_extract(filename, 'date=([^/]+)', 1) AS log_date,
    regexp_extract(filename, 'source=([^/]+)', 1) AS partition_source
FROM read_parquet(
    blq_base_path() || '/**/*.parquet',
    hive_partitioning = true,
    filename = true,
    union_by_name = true
);

-- Load runs with aggregated stats (table-returning macro)
CREATE OR REPLACE MACRO blq_load_runs() AS TABLE
SELECT
    run_id,
    source_name,
    source_type,
    command,
    MIN(started_at) AS started_at,
    MAX(completed_at) AS completed_at,
    MAX(exit_code) AS exit_code,
    COUNT(*) AS event_count,
    COUNT(*) FILTER (WHERE severity = 'error') AS error_count,
    COUNT(*) FILTER (WHERE severity = 'warning') AS warning_count,
    MAX(log_date) AS log_date
FROM blq_load_events()
GROUP BY run_id, source_name, source_type, command;

-- Load latest run per source with status badge (table-returning macro)
CREATE OR REPLACE MACRO blq_load_source_status() AS TABLE
SELECT
    source_name,
    blq_status_badge(error_count, warning_count) AS badge,
    error_count,
    warning_count,
    event_count,
    started_at,
    completed_at,
    exit_code,
    run_id
FROM blq_load_runs()
QUALIFY row_number() OVER (PARTITION BY source_name ORDER BY started_at DESC) = 1
ORDER BY source_name;

-- ============================================================================
-- CONVENIENCE VIEWS (created when data exists)
-- ============================================================================

-- These views wrap the load macros for simpler SQL syntax.
-- They are created by LogStore after first data write.
-- Until then, use the blq_load_*() macros directly.

-- CREATE OR REPLACE VIEW blq_events AS SELECT * FROM blq_load_events();
-- CREATE OR REPLACE VIEW blq_runs AS SELECT * FROM blq_load_runs();
-- CREATE OR REPLACE VIEW blq_source_status AS SELECT * FROM blq_load_source_status();

-- ============================================================================
-- STATUS MACROS
-- ============================================================================

-- Quick status overview (for `blq status`)
CREATE OR REPLACE MACRO blq_status() AS TABLE
SELECT
    badge || ' ' || source_name AS status,
    error_count AS errors,
    warning_count AS warnings,
    age(now(), started_at::TIMESTAMP) AS age
FROM blq_load_source_status()
ORDER BY
    CASE WHEN badge = '[FAIL]' THEN 0
         WHEN badge = '[WARN]' THEN 1
         WHEN badge = '[ .. ]' THEN 2
         ELSE 3 END,
    source_name;

-- Verbose status with more details
CREATE OR REPLACE MACRO blq_status_verbose() AS TABLE
SELECT
    badge || ' ' || source_name AS status,
    error_count || ' errors, ' || warning_count || ' warnings' AS summary,
    CASE
        WHEN age(now(), started_at::TIMESTAMP) < INTERVAL '1 minute' THEN 'just now'
        WHEN age(now(), started_at::TIMESTAMP) < INTERVAL '1 hour' THEN
            extract(minute FROM age(now(), started_at::TIMESTAMP))::INT || 'm ago'
        WHEN age(now(), started_at::TIMESTAMP) < INTERVAL '1 day' THEN
            extract(hour FROM age(now(), started_at::TIMESTAMP))::INT || 'h ago'
        ELSE started_at::DATE::VARCHAR
    END AS age,
    exit_code
FROM blq_load_source_status()
ORDER BY started_at DESC;

-- ============================================================================
-- ERROR/WARNING MACROS
-- ============================================================================

-- Recent errors (for `blq errors`)
CREATE OR REPLACE MACRO blq_errors(n := 10) AS TABLE
SELECT
    source_name,
    file_path,
    line_number,
    column_number,
    LEFT(message, 200) AS message,
    tool_name,
    category
FROM blq_load_events()
WHERE severity = 'error'
ORDER BY started_at DESC, event_id
LIMIT n;

-- Recent errors for a specific source
CREATE OR REPLACE MACRO blq_errors_for(src, n := 10) AS TABLE
SELECT
    file_path,
    line_number,
    column_number,
    LEFT(message, 200) AS message,
    tool_name,
    category
FROM blq_load_events()
WHERE severity = 'error' AND source_name = src
ORDER BY started_at DESC, event_id
LIMIT n;

-- Recent warnings (for `blq warnings`)
CREATE OR REPLACE MACRO blq_warnings(n := 10) AS TABLE
SELECT
    source_name,
    file_path,
    line_number,
    column_number,
    LEFT(message, 200) AS message,
    tool_name,
    category
FROM blq_load_events()
WHERE severity = 'warning'
ORDER BY started_at DESC, event_id
LIMIT n;

-- ============================================================================
-- SUMMARY MACROS
-- ============================================================================

-- Aggregate summary by tool and category
CREATE OR REPLACE MACRO blq_summary() AS TABLE
SELECT
    tool_name,
    category,
    COUNT(*) FILTER (WHERE severity = 'error') AS errors,
    COUNT(*) FILTER (WHERE severity = 'warning') AS warnings,
    COUNT(*) AS total
FROM blq_load_events()
GROUP BY tool_name, category
HAVING errors > 0 OR warnings > 0
ORDER BY errors DESC, warnings DESC;

-- Summary for latest run only
CREATE OR REPLACE MACRO blq_summary_latest() AS TABLE
WITH latest_run AS (
    SELECT run_id FROM blq_load_runs() ORDER BY started_at DESC LIMIT 1
)
SELECT
    tool_name,
    category,
    COUNT(*) FILTER (WHERE severity = 'error') AS errors,
    COUNT(*) FILTER (WHERE severity = 'warning') AS warnings,
    COUNT(*) AS total
FROM blq_load_events()
WHERE run_id = (SELECT run_id FROM latest_run)
GROUP BY tool_name, category
HAVING errors > 0 OR warnings > 0
ORDER BY errors DESC, warnings DESC;

-- ============================================================================
-- DETAIL MACROS
-- ============================================================================

-- Get full event details by ID
CREATE OR REPLACE MACRO blq_event(id) AS TABLE
SELECT * FROM blq_load_events() WHERE event_id = id;

-- Get events for a specific file
CREATE OR REPLACE MACRO blq_file(path) AS TABLE
SELECT
    line_number,
    column_number,
    severity,
    message,
    tool_name
FROM blq_load_events()
WHERE file_path LIKE '%' || path || '%'
ORDER BY line_number, column_number;

-- ============================================================================
-- HISTORY MACROS
-- ============================================================================

-- Run history
CREATE OR REPLACE MACRO blq_history(n := 20) AS TABLE
SELECT
    run_id,
    blq_status_badge(error_count, warning_count) AS badge,
    source_name,
    error_count,
    warning_count,
    started_at,
    age(completed_at::TIMESTAMP, started_at::TIMESTAMP) AS duration
FROM blq_load_runs()
ORDER BY started_at DESC
LIMIT n;

-- Compare two runs
CREATE OR REPLACE MACRO blq_diff(run1, run2) AS TABLE
WITH r1 AS (
    SELECT tool_name, category,
           COUNT(*) FILTER (WHERE severity = 'error') AS errors
    FROM blq_load_events() WHERE run_id = run1
    GROUP BY tool_name, category
),
r2 AS (
    SELECT tool_name, category,
           COUNT(*) FILTER (WHERE severity = 'error') AS errors
    FROM blq_load_events() WHERE run_id = run2
    GROUP BY tool_name, category
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

-- Parse event reference back to struct
CREATE OR REPLACE MACRO blq_parse_ref(ref) AS {
    run_id: CAST(split_part(ref, ':', 1) AS INTEGER),
    event_id: CAST(split_part(ref, ':', 2) AS INTEGER)
};

-- Format location string: "src/main.c:15:5"
CREATE OR REPLACE MACRO blq_location(file_path, line_number, column_number) AS
    COALESCE(file_path, '?') ||
    CASE WHEN line_number IS NOT NULL THEN ':' || line_number::VARCHAR ELSE '' END ||
    CASE WHEN column_number IS NOT NULL AND column_number > 0 THEN ':' || column_number::VARCHAR ELSE '' END;

-- Short fingerprint for display: "make_98586554"
CREATE OR REPLACE MACRO blq_short_fp(fp) AS
    CASE WHEN fp IS NULL THEN NULL
         ELSE split_part(fp, '_', 1) || '_' || LEFT(split_part(fp, '_', 3), 8)
    END;

-- Get event by reference string
CREATE OR REPLACE MACRO blq_get_event(ref) AS TABLE
SELECT
    blq_ref(run_id, event_id) AS ref,
    source_name,
    severity,
    blq_location(file_path, line_number, column_number) AS location,
    message,
    fingerprint,
    log_line_start,
    log_line_end
FROM blq_load_events()
WHERE run_id = (blq_parse_ref(ref)).run_id
  AND event_id = (blq_parse_ref(ref)).event_id;

-- Find events with same fingerprint (same error across runs)
CREATE OR REPLACE MACRO blq_similar_events(fp, n := 10) AS TABLE
SELECT
    blq_ref(run_id, event_id) AS ref,
    source_name,
    started_at,
    blq_location(file_path, line_number, column_number) AS location,
    LEFT(message, 80) AS message
FROM blq_load_events()
WHERE fingerprint = fp
ORDER BY started_at DESC
LIMIT n;

-- ============================================================================
-- UTILITY MACROS
-- ============================================================================

-- Compact error format for agents (minimal tokens)
CREATE OR REPLACE MACRO blq_errors_compact(n := 10) AS TABLE
SELECT
    blq_ref(run_id, event_id) AS ref,
    blq_location(file_path, line_number, column_number) || ': ' || LEFT(message, 100) AS error
FROM blq_load_events()
WHERE severity = 'error'
ORDER BY started_at DESC, event_id
LIMIT n;

-- JSON output for MCP/agents
CREATE OR REPLACE MACRO blq_errors_json(n := 10) AS TABLE
SELECT to_json(list(err)) AS json FROM (
    SELECT {
        ref: blq_ref(run_id, event_id),
        file_path: file_path,
        line: line_number,
        col: column_number,
        message: message,
        tool: tool_name,
        category: category,
        fingerprint: blq_short_fp(fingerprint),
        log_lines: CASE WHEN log_line_start IS NOT NULL
                        THEN [log_line_start, log_line_end]
                        ELSE NULL END
    } AS err
    FROM blq_load_events()
    WHERE severity = 'error'
    ORDER BY started_at DESC, event_id
    LIMIT n
);

-- ============================================================================
-- MAINTENANCE
-- ============================================================================

-- Show log file sizes
CREATE OR REPLACE MACRO blq_files() AS TABLE
SELECT
    filename,
    log_date,
    source_type,
    COUNT(*) AS events
FROM blq_load_events()
GROUP BY filename, log_date, source_type
ORDER BY log_date DESC, source_type;
