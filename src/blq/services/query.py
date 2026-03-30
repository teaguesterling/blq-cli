"""Query services for blq.

Provides shared business logic for status, history, events, and diff queries.
Called by both CLI and MCP layers — no argparse, no output formatting.
"""

from __future__ import annotations

import logging
from typing import Any

from blq.storage import BlqStorage

log = logging.getLogger("blq-services")


# ============================================================================
# Private helpers
# ============================================================================


def _compute_status(
    error_count: int,
    warning_count: int,
    exit_code: int | None,
    db_status: str | None,
) -> str:
    """Return a human-readable status string.

    Possible values: 'OK', 'FAIL', 'WARN', 'RUNNING', 'ORPHANED'
    """
    if db_status == "pending":
        return "RUNNING"
    if db_status == "orphaned":
        return "ORPHANED"
    if error_count and error_count > 0:
        return "FAIL"
    if exit_code is not None and exit_code != 0:
        return "FAIL"
    if warning_count and warning_count > 0:
        return "WARN"
    return "OK"


def _build_run_ref(tag: str | None, source_name: str | None, run_serial: Any) -> str:
    """Build a human-friendly run reference string.

    Returns 'tag:serial' if tag is present, else 'serial'.
    Uses tag first, then source_name as fallback for the prefix.
    """
    prefix = tag if tag else source_name
    if prefix and run_serial is not None:
        return f"{prefix}:{run_serial}"
    if run_serial is not None:
        return str(run_serial)
    return ""


# ============================================================================
# Public query functions
# ============================================================================


def query_status(storage: BlqStorage) -> list[dict[str, Any]]:
    """Query per-source status (latest run per command).

    Returns a list of dicts with keys:
        name, status, error_count, warning_count, last_run, run_ref, run_serial

    Returns [] on error or when no data exists.
    """
    try:
        conn = storage.connection
        result = conn.execute("SELECT * FROM blq_load_source_status()")
        columns = [d[0] for d in result.description]
        rows = result.fetchall()
    except Exception:
        log.debug("query_status: failed to query blq_load_source_status()", exc_info=True)
        return []

    if not rows:
        return []

    output = []
    for row in rows:
        data = dict(zip(columns, row))
        run_serial = data.get("run_id")
        tag = None  # blq_load_source_status doesn't expose tag directly
        source_name = data.get("source_name")
        exit_code = data.get("exit_code")
        error_count = data.get("error_count", 0) or 0
        warning_count = data.get("warning_count", 0) or 0
        db_status = data.get("status")

        status = _compute_status(error_count, warning_count, exit_code, db_status)
        run_ref = _build_run_ref(tag, source_name, run_serial)

        output.append({
            "name": source_name,
            "status": status,
            "error_count": error_count,
            "warning_count": warning_count,
            "last_run": data.get("started_at"),
            "run_ref": run_ref,
            "run_serial": run_serial,
        })

    return output


def query_history(
    storage: BlqStorage,
    limit: int = 20,
    source: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Query run history with optional filters.

    Args:
        storage: BlqStorage instance
        limit: Maximum number of runs to return (newest first)
        source: Filter by source_name (exact match)
        status: Filter by status string. Accepts 'running' (mapped to 'pending'),
                'orphaned', or 'completed'.

    Returns a list of dicts with keys:
        run_ref, run_serial, source_name, status, error_count, warning_count,
        started_at, exit_code, command, git_commit, git_branch, git_dirty

    Returns [] on error or when no data exists.
    """
    # Map user-facing status names to internal DB values
    _status_map = {
        "running": "pending",
        "pending": "pending",
        "orphaned": "orphaned",
        "completed": "completed",
    }

    try:
        if not storage.has_data():
            return []

        conn = storage.connection

        # Build WHERE clauses dynamically; use ? placeholders for user values
        where_parts: list[str] = []
        params: list[Any] = []

        if source is not None:
            where_parts.append("a.source_name = ?")
            params.append(source)

        if status is not None:
            db_status = _status_map.get(status.lower(), status.lower())
            where_parts.append("a.status = ?")
            params.append(db_status)

        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Query blq_load_attempts() with a LEFT JOIN to events for counts.
        # blq_load_attempts() already has run_id, source_name, status, etc.
        sql = f"""
            SELECT
                a.run_id,
                a.source_name,
                a.tag,
                a.status,
                a.started_at,
                a.exit_code,
                a.command,
                a.git_commit,
                a.git_branch,
                a.git_dirty,
                COUNT(e.id) FILTER (WHERE e.severity = 'error') AS error_count,
                COUNT(e.id) FILTER (WHERE e.severity = 'warning') AS warning_count
            FROM blq_load_attempts() a
            LEFT JOIN events e ON e.invocation_id = a.attempt_id
            {where_clause}
            GROUP BY
                a.run_id, a.source_name, a.tag, a.status, a.started_at,
                a.exit_code, a.command, a.git_commit, a.git_branch, a.git_dirty
            ORDER BY a.started_at DESC
            LIMIT {int(limit)}
        """

        result = conn.execute(sql, params)
        columns = [d[0] for d in result.description]
        rows = result.fetchall()
    except Exception:
        log.debug("query_history: failed to query attempts", exc_info=True)
        return []

    if not rows:
        return []

    output = []
    for row in rows:
        data = dict(zip(columns, row))
        run_serial = data.get("run_id")
        tag = data.get("tag")
        source_name = data.get("source_name")
        exit_code = data.get("exit_code")
        error_count = data.get("error_count", 0) or 0
        warning_count = data.get("warning_count", 0) or 0
        db_status = data.get("status")

        run_ref = _build_run_ref(tag, source_name, run_serial)
        human_status = _compute_status(error_count, warning_count, exit_code, db_status)

        output.append({
            "run_ref": run_ref,
            "run_serial": run_serial,
            "source_name": source_name,
            "status": human_status,
            "error_count": error_count,
            "warning_count": warning_count,
            "started_at": data.get("started_at"),
            "exit_code": exit_code,
            "command": data.get("command"),
            "git_commit": data.get("git_commit"),
            "git_branch": data.get("git_branch"),
            "git_dirty": data.get("git_dirty"),
        })

    return output


def query_events(
    storage: BlqStorage,
    severity: str | None = None,
    run_id: int | None = None,
    source: str | None = None,
    file_pattern: str | None = None,
    limit: int = 20,
    default_to_latest: bool = False,
    suppressed_fingerprints: list[str] | None = None,
    all_runs: bool = False,
) -> dict[str, Any]:
    """Query events with optional filters.

    Args:
        storage: BlqStorage instance
        severity: Filter by severity ('error', 'warning', or comma-separated)
        run_id: Filter by run serial number
        source: Filter by source_name
        file_pattern: Filter by ref_file (LIKE pattern, e.g. '%main%')
        limit: Maximum number of events to return
        default_to_latest: If True and no run_id/source given, show only latest run
        suppressed_fingerprints: Fingerprints to exclude from results
        all_runs: If True, show events from all runs (overrides default_to_latest)

    Returns a dict with:
        events: list of event dicts
        total_count: int — total matching events before limit

    Returns {events: [], total_count: 0} on error.
    """
    _empty: dict[str, Any] = {"events": [], "total_count": 0}

    try:
        if not storage.has_data():
            return _empty

        conn = storage.connection

        where_parts: list[str] = []

        # Severity filter (supports comma-separated)
        if severity is not None:
            if "," in severity:
                severities = [s.strip() for s in severity.split(",")]
                placeholders = ", ".join(f"'{s}'" for s in severities)
                where_parts.append(f"severity IN ({placeholders})")
            else:
                where_parts.append(f"severity = '{severity}'")

        if run_id is not None:
            where_parts.append(f"run_serial = {int(run_id)}")
        elif source:
            where_parts.append(f"source_name = '{source}'")
        elif default_to_latest and not all_runs:
            last_run = conn.execute(
                "SELECT MAX(run_serial) FROM blq_load_events()"
            ).fetchone()
            if last_run and last_run[0]:
                where_parts.append(f"run_serial = {last_run[0]}")

        if file_pattern is not None:
            where_parts.append(f"ref_file LIKE '{file_pattern}'")

        # Suppression filter
        if suppressed_fingerprints:
            fp_list = ", ".join(f"'{fp}'" for fp in suppressed_fingerprints)
            where_parts.append(
                f"(fingerprint IS NULL OR fingerprint NOT IN ({fp_list}))"
            )

        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        count_sql = f"SELECT COUNT(*) FROM blq_load_events() {where_clause}"
        total_count_result = conn.execute(count_sql).fetchone()
        total_count: int = int(total_count_result[0]) if total_count_result else 0

        events_sql = f"""
            SELECT *
            FROM blq_load_events()
            {where_clause}
            ORDER BY run_serial DESC, event_id
            LIMIT {int(limit)}
        """
        result = conn.execute(events_sql)
        columns = [d[0] for d in result.description]
        rows = result.fetchall()

    except Exception:
        log.debug("query_events: failed to query events", exc_info=True)
        return _empty

    events = [dict(zip(columns, row)) for row in rows]
    return {"events": events, "total_count": total_count}


def query_diff(storage: BlqStorage, run1: int, run2: int) -> dict[str, Any]:
    """Compare errors between two runs using fingerprints.

    Args:
        storage: BlqStorage instance
        run1: Serial number of the baseline run
        run2: Serial number of the comparison run

    Returns a dict with:
        summary: {run1_errors, run2_errors, fixed, new, unchanged}
        fixed: list of event dicts present in run1 but not run2
        new: list of event dicts present in run2 but not run1

    Returns dict with empty summary and lists on error.
    """
    _empty: dict[str, Any] = {
        "summary": {
            "run1_errors": 0,
            "run2_errors": 0,
            "fixed": 0,
            "new": 0,
            "unchanged": 0,
        },
        "fixed": [],
        "new": [],
    }

    try:
        conn = storage.connection

        # Fetch errors from each run keyed by fingerprint
        error_sql = """
            SELECT
                fingerprint,
                ref_file,
                ref_line,
                message,
                code,
                ref
            FROM blq_load_events()
            WHERE run_id = ?
              AND severity = 'error'
            ORDER BY ref_file, ref_line
        """
        columns_result = conn.execute(error_sql, [run1])
        columns = [d[0] for d in columns_result.description]
        run1_rows = columns_result.fetchall()

        columns_result2 = conn.execute(error_sql, [run2])
        run2_rows = columns_result2.fetchall()

        run1_by_fp: dict[str | None, dict[str, Any]] = {}
        for row in run1_rows:
            data = dict(zip(columns, row))
            fp = data.get("fingerprint")
            run1_by_fp[fp] = data

        run2_by_fp: dict[str | None, dict[str, Any]] = {}
        for row in run2_rows:
            data = dict(zip(columns, row))
            fp = data.get("fingerprint")
            run2_by_fp[fp] = data

        run1_fps = set(run1_by_fp.keys())
        run2_fps = set(run2_by_fp.keys())

        fixed_fps = run1_fps - run2_fps
        new_fps = run2_fps - run1_fps
        unchanged_fps = run1_fps & run2_fps

        fixed_events = [run1_by_fp[fp] for fp in sorted(fixed_fps, key=str)]
        new_events = [run2_by_fp[fp] for fp in sorted(new_fps, key=str)]

        return {
            "summary": {
                "run1_errors": len(run1_fps),
                "run2_errors": len(run2_fps),
                "fixed": len(fixed_fps),
                "new": len(new_fps),
                "unchanged": len(unchanged_fps),
            },
            "fixed": fixed_events,
            "new": new_events,
        }

    except Exception:
        log.debug("query_diff: failed to compute diff", exc_info=True)
        return _empty
