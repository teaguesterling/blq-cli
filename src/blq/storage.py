"""
BlqStorage - unified storage abstraction for blq.

This module provides a clean API for storing and querying build logs,
abstracting over the BIRD storage backend. External consumers should
use this class rather than accessing bird.py directly.

Terminology:
- run: A command execution (maps to BIRD invocation)
- event: A parsed diagnostic (error, warning, etc.)
- output: Captured stdout/stderr content

Query methods return DuckDB relations for flexibility. Call .df() for
DataFrame, .fetchall() for tuples, or chain with further SQL operations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from blq.bird import BirdStore, InvocationRecord


@dataclass
class RunRecord:
    """A blq run (command execution).

    This is the public API for run data. Internally maps to BIRD invocation.
    """

    # Identity
    id: str
    run_number: int  # Sequential run number for display

    # Command
    command: str
    source_name: str
    source_type: str  # "run", "exec", "import", "capture"
    tag: str | None = None

    # Execution context
    cwd: str | None = None
    executable_path: str | None = None
    exit_code: int = 0

    # Timing
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # System context
    hostname: str | None = None
    platform: str | None = None
    arch: str | None = None

    # Git context
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None

    # CI context
    ci: dict[str, str] | None = None

    # Environment
    environment: dict[str, str] | None = None


class BlqStorage:
    """Unified storage interface for blq.

    Provides a clean API for storing and querying build logs.
    Uses BIRD storage backend internally.

    Example:
        storage = BlqStorage.open(".lq/")

        # Check for data
        if storage.has_data():
            runs = storage.runs()
            errors = storage.errors()

        # Write a new run
        run_id = storage.write_run(run_meta, events, output)
    """

    def __init__(self, lq_dir: Path, store: BirdStore):
        """Initialize BlqStorage.

        Args:
            lq_dir: Path to .lq directory
            store: BirdStore instance
        """
        self._lq_dir = lq_dir
        self._store = store
        self._conn = store.connection

    @classmethod
    def open(cls, lq_dir: Path | str | None = None) -> BlqStorage:
        """Open a BlqStorage.

        Args:
            lq_dir: Path to .lq directory. If None, searches from cwd.

        Returns:
            BlqStorage instance

        Raises:
            FileNotFoundError: If .lq directory not found
        """
        if lq_dir is None:
            lq_dir = cls._find_lq_dir()
        else:
            lq_dir = Path(lq_dir)

        if not lq_dir.exists():
            raise FileNotFoundError(f".lq directory not found: {lq_dir}")

        store = BirdStore.open(lq_dir)
        return cls(lq_dir, store)

    @staticmethod
    def _find_lq_dir() -> Path:
        """Find .lq directory by searching from cwd upward."""
        current = Path.cwd()
        while current != current.parent:
            lq_path = current / ".lq"
            if lq_path.exists():
                return lq_path
            current = current.parent

        # Check root
        lq_path = current / ".lq"
        if lq_path.exists():
            return lq_path

        raise FileNotFoundError(".lq directory not found. Run 'blq init' to initialize.")

    def close(self) -> None:
        """Close the storage connection."""
        self._store.close()

    def __enter__(self) -> BlqStorage:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def path(self) -> Path:
        """Path to .lq directory."""
        return self._lq_dir

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Get underlying DuckDB connection for advanced queries."""
        return self._conn

    # =========================================================================
    # Data Existence Checks
    # =========================================================================

    def has_data(self) -> bool:
        """Check if any run data exists."""
        return self._store.invocation_count() > 0

    def has_runs(self) -> bool:
        """Check if any runs exist (alias for has_data)."""
        return self.has_data()

    def has_events(self) -> bool:
        """Check if any events exist."""
        return self._store.event_count() > 0

    # =========================================================================
    # Run Queries
    # =========================================================================

    def runs(self, limit: int | None = None) -> duckdb.DuckDBPyRelation:
        """Get all runs with aggregated event counts.

        Args:
            limit: Maximum number of runs to return (newest first)

        Returns:
            Relation with run_id, source_name, started_at, error_count, etc.
            Call .df() for DataFrame, .fetchall() for tuples.
        """
        sql = """
            SELECT
                run_id,
                source_name,
                source_type,
                command,
                tag,
                started_at,
                completed_at,
                exit_code,
                cwd,
                executable_path,
                hostname,
                platform,
                arch,
                git_commit,
                git_branch,
                git_dirty,
                ci,
                event_count,
                error_count,
                warning_count
            FROM blq_load_runs()
            ORDER BY run_id DESC
        """
        if limit:
            sql += f" LIMIT {limit}"

        return self._conn.sql(sql)

    def run(self, run_id: int) -> duckdb.DuckDBPyRelation:
        """Get a specific run by ID.

        Args:
            run_id: The run ID

        Returns:
            Relation with run details (may be empty if not found)
        """
        return self._conn.sql(f"SELECT * FROM blq_load_runs() WHERE run_id = {run_id}")

    def latest_run_id(self) -> int | None:
        """Get the ID of the most recent run.

        Returns:
            Latest run_id or None if no runs
        """
        result = self._conn.sql("SELECT MAX(run_id) FROM blq_load_runs()").fetchone()
        return result[0] if result and result[0] is not None else None

    # =========================================================================
    # Event Queries
    # =========================================================================

    def events(
        self,
        run_id: int | None = None,
        severity: str | list[str] | None = None,
        limit: int | None = None,
    ) -> duckdb.DuckDBPyRelation:
        """Get events with optional filtering.

        Args:
            run_id: Filter to specific run (by serial number)
            severity: Filter by severity ('error', 'warning', or list)
            limit: Maximum events to return

        Returns:
            Relation with event details.
            Call .df() for DataFrame, .fetchall() for tuples.
        """
        conditions = []
        if run_id is not None:
            conditions.append(f"run_serial = {run_id}")
        if severity is not None:
            if isinstance(severity, list):
                sev_list = ", ".join(f"'{s}'" for s in severity)
                conditions.append(f"severity IN ({sev_list})")
            else:
                conditions.append(f"severity = '{severity}'")

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT * FROM blq_load_events()
            WHERE {where}
            ORDER BY run_serial DESC, event_id
        """
        if limit:
            sql += f" LIMIT {limit}"

        return self._conn.sql(sql)

    def errors(self, run_id: int | None = None, limit: int = 20) -> duckdb.DuckDBPyRelation:
        """Get error events.

        Args:
            run_id: Filter to specific run (by serial number)
            limit: Maximum errors to return

        Returns:
            Relation with error events
        """
        return self.events(run_id=run_id, severity="error", limit=limit)

    def warnings(self, run_id: int | None = None, limit: int = 20) -> duckdb.DuckDBPyRelation:
        """Get warning events.

        Args:
            run_id: Filter to specific run (by serial number)
            limit: Maximum warnings to return

        Returns:
            Relation with warning events
        """
        return self.events(run_id=run_id, severity="warning", limit=limit)

    def event(self, run_serial: int, event_id: int) -> dict[str, Any] | None:
        """Get a specific event by reference.

        Args:
            run_serial: Run serial number
            event_id: Event ID within the run

        Returns:
            Event as dict or None if not found
        """
        result = self._conn.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE run_serial = {run_serial} AND event_id = {event_id}
        """).fetchone()

        if result is None:
            return None

        columns = self._conn.sql("SELECT * FROM blq_load_events() LIMIT 0").columns
        return dict(zip(columns, result))

    def error_count(self, run_id: int | None = None) -> int:
        """Count error events.

        Args:
            run_id: Filter to specific run serial (None for all runs)

        Returns:
            Number of error events
        """
        where = f"run_serial = {run_id} AND " if run_id else ""
        result = self._conn.sql(f"""
            SELECT COUNT(*) FROM blq_load_events()
            WHERE {where}severity = 'error'
        """).fetchone()
        return result[0] if result else 0

    def warning_count(self, run_id: int | None = None) -> int:
        """Count warning events.

        Args:
            run_id: Filter to specific run serial (None for all runs)

        Returns:
            Number of warning events
        """
        where = f"run_serial = {run_id} AND " if run_id else ""
        result = self._conn.sql(f"""
            SELECT COUNT(*) FROM blq_load_events()
            WHERE {where}severity = 'warning'
        """).fetchone()
        return result[0] if result else 0

    # =========================================================================
    # Status Queries
    # =========================================================================

    def status(self) -> duckdb.DuckDBPyRelation:
        """Get status summary for all sources.

        Returns:
            Relation with source_name, badge, error_count, warning_count, age
        """
        return self._conn.sql("SELECT * FROM blq_status()")

    def source_status(self) -> duckdb.DuckDBPyRelation:
        """Get detailed status per source (latest run for each).

        Returns:
            Relation with source details and counts
        """
        return self._conn.sql("SELECT * FROM blq_load_source_status()")

    # =========================================================================
    # Write Operations
    # =========================================================================

    def write_run(
        self,
        run_meta: dict[str, Any],
        events: list[dict[str, Any]] | None = None,
        output: bytes | None = None,
    ) -> str:
        """Write a new run with optional events and output.

        Args:
            run_meta: Run metadata dict with keys:
                - command: Command string
                - source_name: Logical name (e.g., "build", "test")
                - source_type: Type ("run", "exec", "import", "capture")
                - exit_code: Exit code
                - started_at: ISO timestamp
                - completed_at: ISO timestamp
                - cwd, hostname, platform, arch, git_*, ci, environment
            events: List of parsed event dicts
            output: Raw output bytes to store

        Returns:
            Run ID (invocation UUID)
        """
        # Ensure session
        source_name = run_meta.get("source_name", "unknown")
        source_type = run_meta.get("source_type", "run")
        client_id = f"blq-{source_type}"

        if source_type == "run":
            session_id = source_name
        else:
            session_id = f"{source_type}-{datetime.now().strftime('%Y-%m-%d')}"

        self._store.ensure_session(
            session_id=session_id,
            client_id=client_id,
            invoker="blq",
            invoker_type="cli",
            cwd=run_meta.get("cwd"),
        )

        # Calculate duration
        started_at = run_meta.get("started_at")
        completed_at = run_meta.get("completed_at")
        duration_ms = None
        if started_at and completed_at:
            try:
                start = datetime.fromisoformat(started_at)
                end = datetime.fromisoformat(completed_at)
                duration_ms = int((end - start).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass

        # Create invocation record
        tag = run_meta.get("tag") or source_name
        invocation = InvocationRecord(
            id=InvocationRecord.generate_id(),
            session_id=session_id,
            cmd=run_meta.get("command", ""),
            cwd=run_meta.get("cwd", os.getcwd()),
            exit_code=run_meta.get("exit_code", 0),
            client_id=client_id,
            timestamp=datetime.now(),
            duration_ms=duration_ms,
            executable=run_meta.get("executable_path"),
            format_hint=run_meta.get("format_hint"),
            hostname=run_meta.get("hostname"),
            username=run_meta.get("username"),
            tag=tag,
            source_name=source_name,
            source_type=source_type,
            environment=run_meta.get("environment"),
            platform=run_meta.get("platform"),
            arch=run_meta.get("arch"),
            git_commit=run_meta.get("git_commit"),
            git_branch=run_meta.get("git_branch"),
            git_dirty=run_meta.get("git_dirty"),
            ci=run_meta.get("ci"),
        )

        # Write invocation
        run_id = self._store.write_invocation(invocation)

        # Write output if provided
        if output is not None:
            self._store.write_output(run_id, "combined", output)

        # Write events if provided
        if events:
            hostname = run_meta.get("hostname")
            self._store.write_events(
                run_id,
                events,
                client_id=client_id,
                format_used=run_meta.get("format_hint"),
                hostname=hostname,
            )

        return run_id

    def get_next_run_number(self) -> int:
        """Get the next sequential run number for display.

        Returns:
            Next run number (1-indexed)
        """
        return self._store.get_next_run_number()

    def get_output(
        self,
        run_id: str | int,
        stream: str | None = None,
    ) -> bytes | None:
        """Get raw output for a run.

        Args:
            run_id: Run serial number or invocation ID
            stream: Stream name ('stdout', 'stderr', 'combined') or None for any

        Returns:
            Raw output bytes, or None if not found
        """
        # Convert serial number to invocation ID if needed
        if isinstance(run_id, int):
            result = self._conn.execute(
                "SELECT id FROM invocations ORDER BY timestamp LIMIT 1 OFFSET ?",
                [run_id - 1],
            ).fetchone()
            if not result:
                return None
            invocation_id = result[0]
        else:
            invocation_id = run_id

        return self._store.read_output(invocation_id, stream)

    def get_output_info(self, run_id: str | int) -> list[dict[str, Any]]:
        """Get output metadata for a run.

        Args:
            run_id: Run serial number or invocation ID

        Returns:
            List of output records with stream, byte_length, etc.
        """
        # Convert serial number to invocation ID if needed
        if isinstance(run_id, int):
            result = self._conn.execute(
                "SELECT id FROM invocations ORDER BY timestamp LIMIT 1 OFFSET ?",
                [run_id - 1],
            ).fetchone()
            if not result:
                return []
            invocation_id = result[0]
        else:
            invocation_id = run_id

        return self._store.get_output_info(invocation_id)

    # =========================================================================
    # SQL Queries
    # =========================================================================

    def sql(self, query: str) -> duckdb.DuckDBPyRelation:
        """Execute a SQL query.

        Args:
            query: SQL query string

        Returns:
            DuckDB relation. Call .df() for DataFrame, .fetchall() for tuples.
        """
        return self._conn.sql(query)

    # =========================================================================
    # Maintenance
    # =========================================================================

    def prune(self, days: int = 30) -> int:
        """Remove data older than specified days.

        Args:
            days: Remove data older than this many days

        Returns:
            Number of invocations pruned
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff.isoformat()

        # Get invocations to delete
        result = self._conn.execute(
            "SELECT id FROM invocations WHERE timestamp < ?",
            [cutoff_str],
        ).fetchall()

        if not result:
            return 0

        invocation_ids = [row[0] for row in result]

        # Delete events for these invocations
        self._conn.execute(
            f"DELETE FROM events WHERE invocation_id IN ({','.join('?' * len(invocation_ids))})",
            invocation_ids,
        )

        # Delete outputs (blobs will be orphaned but cleaned separately)
        self._conn.execute(
            f"DELETE FROM outputs WHERE invocation_id IN ({','.join('?' * len(invocation_ids))})",
            invocation_ids,
        )

        # Delete invocations
        self._conn.execute(
            f"DELETE FROM invocations WHERE id IN ({','.join('?' * len(invocation_ids))})",
            invocation_ids,
        )

        return len(invocation_ids)
