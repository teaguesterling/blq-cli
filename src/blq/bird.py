"""
BIRD (Buffer and Invocation Record Database) storage backend for blq.

This module implements the BIRD specification using DuckDB tables (single-writer mode).
All reads go through views, writes go directly to tables.

BIRD spec: https://github.com/teaguesterling/magic/blob/main/docs/bird_spec.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import duckdb

# Logger for lock contention warnings
logger = logging.getLogger("blq-bird")

# Type variable for retry function
T = TypeVar("T")

# Default retry settings for lock contention
DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_DELAY = 0.05  # 50ms
DEFAULT_MAX_DELAY = 2.0  # 2 seconds
DEFAULT_BACKOFF_FACTOR = 2.0


def _is_lock_error(error: Exception) -> bool:
    """Check if an exception is a database lock error."""
    error_str = str(error).lower()
    return any(
        phrase in error_str
        for phrase in ["database is locked", "could not set lock", "lock timeout"]
    )


def retry_on_lock(
    func: Callable[..., T],
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> T:
    """Execute a function with retry on database lock errors.

    Uses exponential backoff with jitter to avoid thundering herd.

    Args:
        func: Function to execute (no arguments)
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        backoff_factor: Multiplier for delay after each retry

    Returns:
        Result of the function

    Raises:
        The last exception if all retries fail
    """
    delay = initial_delay
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except duckdb.Error as e:
            if not _is_lock_error(e):
                raise  # Not a lock error, don't retry

            last_error = e
            if attempt < max_retries:
                # Add jitter (±25%) to avoid synchronized retries
                jitter = delay * 0.25 * (2 * random.random() - 1)
                sleep_time = min(delay + jitter, max_delay)
                logger.debug(
                    f"Database locked, retry {attempt + 1}/{max_retries} after {sleep_time:.3f}s"
                )
                time.sleep(sleep_time)
                delay = min(delay * backoff_factor, max_delay)

    # All retries exhausted
    assert last_error is not None
    logger.warning(f"Database lock retry exhausted after {max_retries} attempts")
    raise last_error


# Schema version
BIRD_SCHEMA_VERSION = "2.1.0"

# Storage thresholds (per BIRD spec)
DEFAULT_INLINE_THRESHOLD = 4096  # 4KB - outputs smaller than this are stored inline
MAX_INLINE_THRESHOLD = 1048576  # 1MB - max recommended for inline storage per spec


@dataclass
class SessionRecord:
    """A BIRD session (invoker context)."""

    session_id: str
    client_id: str
    invoker: str
    invoker_type: str  # "cli", "mcp", "import", "capture"
    invoker_pid: int | None = None
    cwd: str | None = None
    registered_at: datetime = field(default_factory=datetime.now)
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


@dataclass
class InvocationRecord:
    """A BIRD invocation (command execution)."""

    # Identity
    id: str  # UUID
    session_id: str

    # Command
    cmd: str
    cwd: str
    exit_code: int

    # Client
    client_id: str

    # Timing
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: int | None = None

    # Optional fields
    executable: str | None = None
    format_hint: str | None = None
    hostname: str | None = None
    username: str | None = None
    pid: int | None = None  # Process ID of the command

    # BIRD spec: user-defined tag (non-unique alias for this invocation)
    tag: str | None = None

    # blq-specific fields
    source_name: str | None = None
    source_type: str | None = None
    environment: dict[str, str] | None = None
    platform: str | None = None
    arch: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    ci: dict[str, str] | None = None

    # Partitioning
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    @classmethod
    def generate_id(cls) -> str:
        """Generate a new UUID for an invocation."""
        # TODO: Use UUIDv7 when available for time-ordered IDs
        return str(uuid.uuid4())


@dataclass
class AttemptRecord:
    """A BIRD attempt (command start - written before completion).

    This enables tracking of running commands. Status is derived by LEFT JOIN
    with OutcomeRecord:
    - No outcome = 'pending' (still running)
    - Outcome with NULL exit_code = 'orphaned' (crashed)
    - Outcome with exit_code = 'completed'
    """

    # Identity
    id: str  # UUID
    session_id: str

    # Command
    cmd: str
    cwd: str

    # Client
    client_id: str

    # Timing (start only)
    timestamp: datetime = field(default_factory=datetime.now)

    # Optional fields
    executable: str | None = None
    format_hint: str | None = None
    hostname: str | None = None
    username: str | None = None
    pid: int | None = None  # Process ID of the command

    # BIRD spec: user-defined tag
    tag: str | None = None

    # blq-specific fields
    source_name: str | None = None
    source_type: str | None = None
    environment: dict[str, str] | None = None
    platform: str | None = None
    arch: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_dirty: bool | None = None
    ci: dict[str, str] | None = None

    # Partitioning
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    @classmethod
    def generate_id(cls) -> str:
        """Generate a new UUID for an attempt."""
        return str(uuid.uuid4())


@dataclass
class OutcomeRecord:
    """A BIRD outcome (command completion - written after command finishes).

    Links to AttemptRecord via attempt_id.
    """

    # Identity (1:1 with attempt)
    attempt_id: str  # References AttemptRecord.id

    # Result
    exit_code: int | None = None  # NULL = crashed/unknown

    # Timing
    completed_at: datetime = field(default_factory=datetime.now)
    duration_ms: int | None = None

    # Termination details
    signal: int | None = None  # If killed by signal
    timeout: bool = False  # If killed by timeout

    # Partitioning
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


@dataclass
class OutputRecord:
    """A BIRD output (captured stdout/stderr)."""

    id: str  # UUID
    invocation_id: str
    stream: str  # 'stdout', 'stderr', 'combined'
    content_hash: str  # BLAKE3 hash
    byte_length: int
    storage_type: str  # 'inline' or 'blob'
    storage_ref: str  # data: URI or file: path
    content_type: str | None = None
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


@dataclass
class EventRecord:
    """A BIRD event (parsed diagnostic)."""

    id: str  # UUID
    invocation_id: str
    event_index: int
    client_id: str

    # Classification
    severity: str | None = None
    event_type: str | None = None

    # Location
    ref_file: str | None = None
    ref_line: int | None = None
    ref_column: int | None = None

    # Content
    message: str | None = None
    code: str | None = None
    rule: str | None = None

    # blq-specific
    tool_name: str | None = None
    category: str | None = None
    fingerprint: str | None = None
    log_line_start: int | None = None
    log_line_end: int | None = None
    context: str | None = None
    metadata: dict | None = None

    # Parsing metadata
    format_used: str | None = None
    hostname: str | None = None
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


class BirdStore:
    """BIRD storage backend using DuckDB tables.

    This class manages BIRD-compatible storage in DuckDB mode (single-writer).
    It handles sessions, invocations, outputs, and events tables.

    Example:
        store = BirdStore.open(".lq/")

        # Register session (once per CLI invocation)
        store.ensure_session("test", "blq-shell", "blq", "cli")

        # Write invocation
        inv_id = store.write_invocation(record)

        # Write events
        store.write_events(inv_id, events)
    """

    def __init__(self, lq_dir: Path, conn: duckdb.DuckDBPyConnection):
        """Initialize BirdStore.

        Args:
            lq_dir: Path to .lq directory
            conn: Open DuckDB connection
        """
        self._lq_dir = lq_dir
        self._conn = conn
        self._blob_dir = lq_dir / "blobs" / "content"
        self._inline_threshold = DEFAULT_INLINE_THRESHOLD

    @property
    def inline_threshold(self) -> int:
        """Current inline storage threshold in bytes."""
        return self._inline_threshold

    @inline_threshold.setter
    def inline_threshold(self, value: int) -> None:
        """Set inline storage threshold (capped at MAX_INLINE_THRESHOLD)."""
        if value > MAX_INLINE_THRESHOLD:
            value = MAX_INLINE_THRESHOLD
        self._inline_threshold = max(0, value)

    @classmethod
    def open(cls, lq_dir: Path | str) -> BirdStore:
        """Open or create a BirdStore.

        Args:
            lq_dir: Path to .lq directory

        Returns:
            BirdStore instance
        """
        lq_dir = Path(lq_dir)
        db_path = lq_dir / "blq.duckdb"

        # Open database
        conn = duckdb.connect(str(db_path))

        # Initialize schema if needed
        cls._ensure_schema(conn, lq_dir)

        return cls(lq_dir, conn)

    @classmethod
    def open_with_retry(
        cls,
        lq_dir: Path | str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
    ) -> BirdStore:
        """Open or create a BirdStore with retry on lock contention.

        Use this when opening from code that may run concurrently with other
        writers (e.g., parallel command execution, hooks).

        Args:
            lq_dir: Path to .lq directory
            max_retries: Maximum number of retry attempts
            initial_delay: Initial delay between retries in seconds

        Returns:
            BirdStore instance

        Raises:
            duckdb.Error: If all retries fail
        """
        return retry_on_lock(
            lambda: cls.open(lq_dir),
            max_retries=max_retries,
            initial_delay=initial_delay,
        )

    @classmethod
    def _ensure_schema(
        cls, conn: duckdb.DuckDBPyConnection, lq_dir: Path, force: bool = False
    ) -> None:
        """Ensure BIRD schema is initialized.

        Args:
            conn: DuckDB connection
            lq_dir: Path to .lq directory
            force: If True, reload schema even if it exists (for reinit)
        """
        # Check if schema is already initialized (skip on force)
        if not force:
            try:
                result = conn.execute(
                    "SELECT value FROM blq_metadata WHERE key = 'schema_version'"
                ).fetchone()
                if result:
                    # Schema exists
                    return
            except duckdb.Error:
                pass  # Table doesn't exist, need to create

        # Load schema from SQL file
        schema_path = Path(__file__).parent / "bird_schema.sql"
        if schema_path.exists():
            schema_sql = schema_path.read_text()
            # Execute statements using proper SQL splitting
            statements = cls._split_sql_statements(schema_sql)
            for stmt in statements:
                try:
                    conn.execute(stmt)
                except duckdb.Error as e:
                    # Log but continue - some statements may fail on re-init
                    if "already exists" not in str(e).lower():
                        pass  # Ignore

        # Create blob directory
        blob_dir = lq_dir / "blobs" / "content"
        blob_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _split_sql_statements(sql: str) -> list[str]:
        """Split SQL into individual statements, handling comments and semicolons.

        Simple parser that handles:
        - Line comments (--)
        - Block comments (/* */)
        - Semicolons inside comments

        Returns list of non-empty statements.
        """
        statements = []
        current = []
        in_line_comment = False
        in_block_comment = False
        i = 0

        while i < len(sql):
            c = sql[i]

            # Check for comment start
            if not in_line_comment and not in_block_comment:
                if c == "-" and i + 1 < len(sql) and sql[i + 1] == "-":
                    in_line_comment = True
                    current.append(c)
                    i += 1
                    current.append(sql[i])
                elif c == "/" and i + 1 < len(sql) and sql[i + 1] == "*":
                    in_block_comment = True
                    current.append(c)
                    i += 1
                    current.append(sql[i])
                elif c == ";":
                    # End of statement
                    stmt = "".join(current).strip()
                    if stmt:
                        statements.append(stmt)
                    current = []
                else:
                    current.append(c)
            elif in_line_comment:
                current.append(c)
                if c == "\n":
                    in_line_comment = False
            elif in_block_comment:
                current.append(c)
                if c == "*" and i + 1 < len(sql) and sql[i + 1] == "/":
                    i += 1
                    current.append(sql[i])
                    in_block_comment = False

            i += 1

        # Add final statement if any
        stmt = "".join(current).strip()
        if stmt:
            statements.append(stmt)

        return statements

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> BirdStore:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def execute_with_retry(
        self,
        func: Callable[[], T],
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> T:
        """Execute a function with retry on lock errors.

        Use this for critical write operations that must succeed.

        Args:
            func: Function to execute (should use self._conn)
            max_retries: Maximum number of retry attempts

        Returns:
            Result of the function
        """
        return retry_on_lock(func, max_retries=max_retries)

    # =========================================================================
    # Session Management
    # =========================================================================

    def ensure_session(
        self,
        session_id: str,
        client_id: str,
        invoker: str,
        invoker_type: str,
        cwd: str | None = None,
    ) -> None:
        """Ensure a session exists, creating if needed.

        Args:
            session_id: Session identifier (e.g., source_name for CLI)
            client_id: Client identifier (e.g., "blq-shell")
            invoker: Invoker name (e.g., "blq")
            invoker_type: Invoker type ("cli", "mcp", "import", "capture")
            cwd: Initial working directory
        """
        # Check if session exists
        result = self._conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", [session_id]
        ).fetchone()

        if result:
            return  # Session already exists

        # Create session
        pid = os.getpid()
        now = datetime.now()
        date = now.strftime("%Y-%m-%d")

        self._conn.execute(
            """
            INSERT INTO sessions (session_id, client_id, invoker, invoker_pid,
                                  invoker_type, registered_at, cwd, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [session_id, client_id, invoker, pid, invoker_type, now, cwd, date],
        )

    # =========================================================================
    # Invocation Management
    # =========================================================================

    def write_invocation(self, record: InvocationRecord) -> str:
        """Write an invocation record.

        Args:
            record: Invocation record to write

        Returns:
            The invocation ID
        """
        self._conn.execute(
            """
            INSERT INTO invocations (
                id, session_id, timestamp, duration_ms, cwd, cmd, executable, pid,
                exit_code, format_hint, client_id, hostname, username, tag,
                source_name, source_type, environment, platform, arch,
                git_commit, git_branch, git_dirty, ci, date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record.id,
                record.session_id,
                record.timestamp,
                record.duration_ms,
                record.cwd,
                record.cmd,
                record.executable,
                record.pid,
                record.exit_code,
                record.format_hint,
                record.client_id,
                record.hostname,
                record.username,
                record.tag,
                record.source_name,
                record.source_type,
                json.dumps(record.environment) if record.environment else None,
                record.platform,
                record.arch,
                record.git_commit,
                record.git_branch,
                record.git_dirty,
                json.dumps(record.ci) if record.ci else None,
                record.date,
            ],
        )
        return record.id

    def get_next_run_number(self) -> int:
        """Get the next run number (for backward compatibility).

        Returns:
            Next sequential run number
        """
        # Count from invocations only (completed runs)
        # With the live output pattern, attempts are written at start and
        # invocations at completion with the same ID, so counting invocations
        # gives us the count of completed runs.
        result = self._conn.execute("""
            SELECT COUNT(*) FROM invocations
        """).fetchone()
        return (result[0] if result else 0) + 1

    # =========================================================================
    # Attempt/Outcome Management (BIRD v5 pattern)
    # =========================================================================

    def write_attempt(self, record: AttemptRecord) -> str:
        """Write an attempt record (at command START).

        Call this when a command starts executing. The attempt will have
        'pending' status until write_outcome() is called.

        Args:
            record: Attempt record to write

        Returns:
            The attempt ID
        """
        self._conn.execute(
            """
            INSERT INTO attempts (
                id, session_id, timestamp, cwd, cmd, executable, pid,
                format_hint, client_id, hostname, username, tag,
                source_name, source_type, environment, platform, arch,
                git_commit, git_branch, git_dirty, ci, date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record.id,
                record.session_id,
                record.timestamp,
                record.cwd,
                record.cmd,
                record.executable,
                record.pid,
                record.format_hint,
                record.client_id,
                record.hostname,
                record.username,
                record.tag,
                record.source_name,
                record.source_type,
                json.dumps(record.environment) if record.environment else None,
                record.platform,
                record.arch,
                record.git_commit,
                record.git_branch,
                record.git_dirty,
                json.dumps(record.ci) if record.ci else None,
                record.date,
            ],
        )
        return record.id

    def write_outcome(self, record: OutcomeRecord) -> None:
        """Write an outcome record (at command COMPLETION).

        Call this when a command finishes executing. Links to the
        attempt via attempt_id.

        Args:
            record: Outcome record to write
        """
        self._conn.execute(
            """
            INSERT INTO outcomes (
                attempt_id, completed_at, duration_ms, exit_code,
                signal, timeout, date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record.attempt_id,
                record.completed_at,
                record.duration_ms,
                record.exit_code,
                record.signal,
                record.timeout,
                record.date,
            ],
        )

    def get_running_attempts(self) -> list[dict]:
        """Get attempts without outcomes (running commands).

        Returns:
            List of running attempt dicts with elapsed time
        """
        result = self._conn.execute("""
            SELECT
                a.id,
                a.session_id,
                a.timestamp,
                a.cmd,
                a.source_name,
                a.tag,
                a.hostname,
                EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - a.timestamp)) * 1000 AS elapsed_ms
            FROM attempts a
            WHERE NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.attempt_id = a.id)
            ORDER BY a.timestamp DESC
        """).fetchall()

        columns = [
            "id",
            "session_id",
            "timestamp",
            "cmd",
            "source_name",
            "tag",
            "hostname",
            "elapsed_ms",
        ]
        return [dict(zip(columns, row)) for row in result]

    def get_attempt_status(self, attempt_id: str) -> str | None:
        """Get the status of an attempt.

        Args:
            attempt_id: The attempt UUID

        Returns:
            'pending', 'orphaned', 'completed', or None if not found
        """
        result = self._conn.execute(
            """
            SELECT
                CASE
                    WHEN o.attempt_id IS NULL THEN 'pending'
                    WHEN o.exit_code IS NULL THEN 'orphaned'
                    ELSE 'completed'
                END AS status
            FROM attempts a
            LEFT JOIN outcomes o ON a.id = o.attempt_id
            WHERE a.id = ?
            """,
            [attempt_id],
        ).fetchone()

        return result[0] if result else None

    def update_attempt_pid(self, attempt_id: str, pid: int) -> None:
        """Update the pid field for an attempt after process starts.

        Args:
            attempt_id: The attempt UUID
            pid: Process ID of the command
        """
        self._conn.execute(
            "UPDATE attempts SET pid = ? WHERE id = ?",
            [pid, attempt_id],
        )

    # =========================================================================
    # Live Output Management
    # =========================================================================

    def get_live_dir(self, attempt_id: str) -> Path:
        """Get the live output directory for an attempt.

        Args:
            attempt_id: The attempt UUID

        Returns:
            Path to live output directory (.lq/live/{attempt_id}/)
        """
        return self._lq_dir / "live" / attempt_id

    def create_live_dir(self, attempt_id: str, meta: dict) -> Path:
        """Create live output directory and metadata file.

        Args:
            attempt_id: The attempt UUID
            meta: Metadata dict with cmd, source_name, started_at, pid, etc.

        Returns:
            Path to the created live directory
        """
        live_dir = self.get_live_dir(attempt_id)
        live_dir.mkdir(parents=True, exist_ok=True)

        # Write metadata
        meta_path = live_dir / "meta.json"
        meta_path.write_text(json.dumps(meta, default=str, indent=2))

        return live_dir

    def get_live_output_path(self, attempt_id: str, stream: str = "combined") -> Path:
        """Get path to a live output file.

        Args:
            attempt_id: The attempt UUID
            stream: Stream name ('stdout', 'stderr', 'combined')

        Returns:
            Path to the live output file
        """
        live_dir = self.get_live_dir(attempt_id)
        return live_dir / f"{stream}.log"

    def read_live_output(
        self,
        attempt_id: str,
        stream: str = "combined",
        tail: int | None = None,
        head: int | None = None,
    ) -> str | None:
        """Read from live output file.

        Args:
            attempt_id: The attempt UUID
            stream: Stream name ('stdout', 'stderr', 'combined')
            tail: If set, return only last N lines
            head: If set, return only first N lines

        Returns:
            Output content as string, or None if file doesn't exist
        """
        output_path = self.get_live_output_path(attempt_id, stream)
        if not output_path.exists():
            return None

        content = output_path.read_text()

        if tail is not None:
            lines = content.splitlines(keepends=True)
            content = "".join(lines[-tail:])
        elif head is not None:
            lines = content.splitlines(keepends=True)
            content = "".join(lines[:head])

        return content

    def cleanup_live_dir(self, attempt_id: str) -> bool:
        """Remove live output directory after completion.

        Args:
            attempt_id: The attempt UUID

        Returns:
            True if directory was removed, False if it didn't exist
        """
        import shutil

        live_dir = self.get_live_dir(attempt_id)
        if live_dir.exists():
            shutil.rmtree(live_dir)
            return True
        return False

    def list_live_attempts(self) -> list[dict]:
        """List attempts with live output directories.

        Returns:
            List of dicts with attempt_id, meta, and live_dir path
        """
        live_root = self._lq_dir / "live"
        if not live_root.exists():
            return []

        results = []
        for attempt_dir in live_root.iterdir():
            if attempt_dir.is_dir():
                meta_path = attempt_dir / "meta.json"
                meta = {}
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                    except json.JSONDecodeError:
                        pass

                results.append(
                    {
                        "attempt_id": attempt_dir.name,
                        "meta": meta,
                        "live_dir": str(attempt_dir),
                    }
                )

        return results

    def finalize_live_output(
        self,
        attempt_id: str,
        stream: str = "combined",
    ) -> OutputRecord | None:
        """Move live output to blob storage and clean up.

        Args:
            attempt_id: The attempt UUID
            stream: Stream name to finalize

        Returns:
            OutputRecord if output was saved, None otherwise
        """
        live_path = self.get_live_output_path(attempt_id, stream)
        if not live_path.exists():
            return None

        content = live_path.read_bytes()
        if not content:
            return None

        # Write to blob storage
        record = self.write_output(attempt_id, stream, content)

        return record

    # =========================================================================
    # Output Management
    # =========================================================================

    def write_output(
        self,
        invocation_id: str,
        stream: str,
        content: bytes,
        content_type: str | None = None,
    ) -> OutputRecord:
        """Write output content, choosing inline or blob storage.

        Args:
            invocation_id: ID of the invocation
            stream: Stream name ('stdout', 'stderr', 'combined')
            content: Raw output bytes
            content_type: Optional MIME type

        Returns:
            OutputRecord with storage details
        """
        # Compute hash
        content_hash = hashlib.blake2b(content, digest_size=32).hexdigest()
        byte_length = len(content)

        # Determine storage type
        if byte_length < self._inline_threshold:
            # Inline storage as data: URI
            import base64

            b64 = base64.b64encode(content).decode("ascii")
            storage_type = "inline"
            storage_ref = f"data:application/octet-stream;base64,{b64}"
        else:
            # Blob storage
            storage_path = self._write_blob(content_hash, content)
            storage_type = "blob"
            storage_ref = f"file:{storage_path}"

        # Create record
        record = OutputRecord(
            id=str(uuid.uuid4()),
            invocation_id=invocation_id,
            stream=stream,
            content_hash=content_hash,
            byte_length=byte_length,
            storage_type=storage_type,
            storage_ref=storage_ref,
            content_type=content_type,
        )

        # Write to database
        self._conn.execute(
            """
            INSERT INTO outputs (
                id, invocation_id, stream, content_hash, byte_length,
                storage_type, storage_ref, content_type, date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record.id,
                record.invocation_id,
                record.stream,
                record.content_hash,
                record.byte_length,
                record.storage_type,
                record.storage_ref,
                record.content_type,
                record.date,
            ],
        )

        return record

    def read_output(
        self,
        invocation_id: str,
        stream: str | None = None,
    ) -> bytes | None:
        """Read output content for an invocation.

        Args:
            invocation_id: ID of the invocation
            stream: Stream name ('stdout', 'stderr', 'combined') or None for any

        Returns:
            Raw output bytes, or None if not found
        """
        if stream:
            result = self._conn.execute(
                "SELECT storage_type, storage_ref FROM outputs "
                "WHERE invocation_id = ? AND stream = ?",
                [invocation_id, stream],
            ).fetchone()
        else:
            result = self._conn.execute(
                "SELECT storage_type, storage_ref FROM outputs WHERE invocation_id = ? LIMIT 1",
                [invocation_id],
            ).fetchone()

        if not result:
            return None

        storage_type: str = result[0]
        storage_ref: str = result[1]

        if storage_type == "inline":
            # Parse data: URI
            import base64

            # Format: data:application/octet-stream;base64,<data>
            if storage_ref.startswith("data:") and ";base64," in storage_ref:
                b64_data = storage_ref.split(";base64,", 1)[1]
                return base64.b64decode(b64_data)
            return None
        elif storage_type == "blob":
            # Read from file
            if storage_ref.startswith("file:"):
                rel_path = storage_ref[5:]  # Remove "file:" prefix
                blob_path = self._blob_dir / rel_path
                if blob_path.exists():
                    return blob_path.read_bytes()
            return None
        else:
            return None

    def get_output_info(
        self,
        invocation_id: str,
    ) -> list[dict[str, Any]]:
        """Get output metadata for an invocation.

        Args:
            invocation_id: ID of the invocation

        Returns:
            List of output records with stream, byte_length, etc.
        """
        result = self._conn.execute(
            """
            SELECT stream, byte_length, storage_type, content_type
            FROM outputs
            WHERE invocation_id = ?
            ORDER BY stream
            """,
            [invocation_id],
        ).fetchall()

        return [
            {
                "stream": row[0],
                "byte_length": row[1],
                "storage_type": row[2],
                "content_type": row[3],
            }
            for row in result
        ]

    def _write_blob(self, content_hash: str, content: bytes) -> str:
        """Write content to blob storage.

        Args:
            content_hash: BLAKE2b hash of content
            content: Raw bytes

        Returns:
            Relative path to blob file
        """
        # Create subdirectory based on first 2 chars of hash
        subdir = content_hash[:2]
        blob_subdir = self._blob_dir / subdir
        blob_subdir.mkdir(parents=True, exist_ok=True)

        # Write blob file
        blob_path = blob_subdir / f"{content_hash}.bin"
        relative_path = f"{subdir}/{content_hash}.bin"

        # Atomic write with temp file
        temp_path = blob_subdir / f".tmp.{content_hash}.bin"
        try:
            temp_path.write_bytes(content)
            temp_path.rename(blob_path)
        except FileExistsError:
            # Another process wrote the same blob - that's fine
            temp_path.unlink(missing_ok=True)

        # Update blob registry
        self._register_blob(content_hash, len(content), relative_path)

        return relative_path

    def _register_blob(self, content_hash: str, byte_length: int, storage_path: str) -> None:
        """Register or update blob in registry."""
        try:
            # Try insert
            self._conn.execute(
                """
                INSERT INTO blob_registry (content_hash, byte_length, storage_path)
                VALUES (?, ?, ?)
                """,
                [content_hash, byte_length, storage_path],
            )
        except duckdb.Error:
            # Already exists, update access time and ref count
            self._conn.execute(
                """
                UPDATE blob_registry
                SET last_accessed = CURRENT_TIMESTAMP, ref_count = ref_count + 1
                WHERE content_hash = ?
                """,
                [content_hash],
            )

    def cleanup_orphaned_blobs(self) -> tuple[int, int]:
        """Remove blobs that are no longer referenced by any output.

        Returns:
            Tuple of (blobs_deleted, bytes_freed)
        """
        # Find orphaned blobs (in registry but not referenced by outputs)
        result = self._conn.execute("""
            SELECT br.content_hash, br.byte_length, br.storage_path
            FROM blob_registry br
            LEFT JOIN outputs o ON br.content_hash = o.content_hash
            WHERE o.content_hash IS NULL
        """).fetchall()

        blobs_deleted = 0
        bytes_freed = 0

        for content_hash, byte_length, storage_path in result:
            # Delete the blob file
            blob_path = self._blob_dir / storage_path
            if blob_path.exists():
                try:
                    blob_path.unlink()
                    bytes_freed += byte_length
                    blobs_deleted += 1
                except OSError:
                    pass  # File may already be gone

            # Remove from registry
            self._conn.execute(
                "DELETE FROM blob_registry WHERE content_hash = ?",
                [content_hash],
            )

        # Clean up empty subdirectories
        for subdir in self._blob_dir.iterdir():
            if subdir.is_dir() and not any(subdir.iterdir()):
                try:
                    subdir.rmdir()
                except OSError:
                    pass

        return blobs_deleted, bytes_freed

    # =========================================================================
    # Event Management
    # =========================================================================

    def write_events(
        self,
        invocation_id: str,
        events: list[dict[str, Any]],
        client_id: str,
        format_used: str | None = None,
        hostname: str | None = None,
    ) -> int:
        """Write parsed events for an invocation.

        Args:
            invocation_id: ID of the invocation
            events: List of parsed event dicts
            client_id: Client identifier
            format_used: Parser format used
            hostname: Hostname (denormalized)

        Returns:
            Number of events written
        """
        if not events:
            return 0

        date = datetime.now().strftime("%Y-%m-%d")

        for idx, event in enumerate(events):
            event_id = str(uuid.uuid4())

            self._conn.execute(
                """
                INSERT INTO events (
                    id, invocation_id, event_index, client_id, hostname,
                    event_type, severity, ref_file, ref_line, ref_column,
                    message, code, rule, tool_name, category, fingerprint,
                    log_line_start, log_line_end, context, metadata,
                    format_used, date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id,
                    invocation_id,
                    event.get("event_id", idx),  # Use event_id if provided
                    client_id,
                    hostname,
                    event.get("event_type"),
                    event.get("severity"),
                    event.get("ref_file"),
                    event.get("ref_line"),
                    event.get("ref_column"),
                    event.get("message"),
                    event.get("error_code") or event.get("code"),
                    event.get("rule"),
                    event.get("tool_name"),
                    event.get("category"),
                    event.get("fingerprint"),
                    event.get("log_line_start"),
                    event.get("log_line_end"),
                    event.get("context"),
                    json.dumps(event.get("metadata")) if event.get("metadata") else None,
                    format_used,
                    date,
                ],
            )

        return len(events)

    # =========================================================================
    # Query Helpers
    # =========================================================================

    def invocation_count(self) -> int:
        """Get total number of invocations."""
        result = self._conn.execute("SELECT COUNT(*) FROM invocations").fetchone()
        return result[0] if result else 0

    def event_count(self) -> int:
        """Get total number of events."""
        result = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return result[0] if result else 0

    def recent_invocations(self, limit: int = 10) -> list[dict]:
        """Get recent invocations.

        Args:
            limit: Maximum number to return

        Returns:
            List of invocation dicts
        """
        result = self._conn.execute(
            """
            SELECT id, session_id, timestamp, duration_ms, cmd, exit_code,
                   source_name, source_type
            FROM invocations
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()

        columns = [
            "id",
            "session_id",
            "timestamp",
            "duration_ms",
            "cmd",
            "exit_code",
            "source_name",
            "source_type",
        ]
        return [dict(zip(columns, row)) for row in result]

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """Get the underlying DuckDB connection for direct queries."""
        return self._conn


def write_bird_invocation(
    events: list[dict[str, Any]],
    run_meta: dict[str, Any],
    lq_dir: Path,
    output: bytes | None = None,
) -> tuple[str, Path]:
    """Write a run to BIRD storage.

    This is the main entry point for writing invocations, replacing
    write_run_parquet() for BIRD-enabled projects.

    Args:
        events: Parsed events from the command output
        run_meta: Run metadata dict (same format as write_run_parquet)
        lq_dir: Path to .lq directory
        output: Optional raw output bytes to store

    Returns:
        Tuple of (invocation_id, db_path)
    """
    with BirdStore.open(lq_dir) as store:
        # Determine session and client IDs
        source_name = run_meta.get("source_name", "unknown")
        source_type = run_meta.get("source_type", "run")
        client_id = f"blq-{source_type}"

        # For CLI runs, session_id = source_name
        # For exec, session_id = "exec-{date}"
        if source_type == "run":
            session_id = source_name
        else:
            session_id = f"{source_type}-{datetime.now().strftime('%Y-%m-%d')}"

        # Ensure session exists
        store.ensure_session(
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
        # Use tag for the logical command name (how user refers to it)
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
        inv_id = store.write_invocation(invocation)

        # Write output if provided
        if output is not None:
            store.write_output(inv_id, "combined", output)

        # Write events
        hostname = run_meta.get("hostname")
        store.write_events(
            inv_id,
            events,
            client_id=client_id,
            format_used=run_meta.get("format_hint"),
            hostname=hostname,
        )

        return inv_id, lq_dir / "blq.duckdb"
