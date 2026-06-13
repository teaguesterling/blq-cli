"""Canonical ref parser and resolver for blq.

Provides a single, clean implementation of ref parsing and resolution
that can be used by both CLI and MCP without coupling to either.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from blq.storage import BlqStorage

log = logging.getLogger("blq-services")

# UUID pattern: 8-4-4-4-12 hex digits
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedRef:
    """A parsed reference to a run or event.

    Supports multiple formats:
    - "5" - bare run serial
    - "build:3" - tag:serial
    - "test:5:2" - tag:serial:event
    - "5:2" - serial:event
    - "~1" - relative (most recent)
    - "test:~2" - relative with tag
    - UUID string - direct attempt ID
    """

    tag: str | None = None
    run_serial: int | None = None
    event_id: int | None = None
    relative: int | None = None
    uuid: str | None = None

    @property
    def is_relative(self) -> bool:
        """True when this is a relative reference (~N)."""
        return self.relative is not None

    @property
    def run_ref(self) -> str:
        """Format as a human-friendly run reference string."""
        if self.uuid:
            return self.uuid
        if self.is_relative:
            offset = f"~{self.relative}"
            return f"{self.tag}:{offset}" if self.tag else offset
        if self.tag and self.run_serial is not None:
            return f"{self.tag}:{self.run_serial}"
        if self.run_serial is not None:
            return str(self.run_serial)
        if self.tag:
            return self.tag
        return ""


def parse_ref(ref: str) -> ParsedRef:
    """Parse a ref string into a ParsedRef.

    Formats:
    - "5"          -> bare run serial
    - "build:3"    -> tag:serial
    - "test:5:2"   -> tag:serial:event
    - "5:2"        -> serial:event
    - "~1"         -> relative (most recent)
    - "test:~2"    -> relative with tag
    - UUID         -> uuid field

    For two-part refs like "5:2" vs "build:3": if the first part parses
    as an integer, it's serial:event. Otherwise it's tag:serial.

    Raises:
        ValueError: On empty or unparseable input.
    """
    if not ref or not ref.strip():
        raise ValueError("Empty ref")

    ref = ref.strip()

    # Check for UUID
    if _UUID_RE.match(ref):
        return ParsedRef(uuid=ref)

    parts = ref.split(":")

    if len(parts) == 1:
        part = parts[0]
        # Relative: ~N
        if part.startswith("~") and part[1:].isdigit():
            return ParsedRef(relative=int(part[1:]))
        # Bare serial
        try:
            return ParsedRef(run_serial=int(part))
        except ValueError:
            # Bare tag (source name) - for fallback resolution
            return ParsedRef(tag=part)

    if len(parts) == 2:
        first, second = parts
        # Check if first part is relative (~N)
        if first.startswith("~") and first[1:].isdigit():
            return ParsedRef(relative=int(first[1:]), event_id=int(second))
        # If first part is an int -> serial:event
        try:
            serial = int(first)
            event = int(second)
            return ParsedRef(run_serial=serial, event_id=event)
        except ValueError:
            pass
        # First part is tag
        tag = first
        if second.startswith("~") and second[1:].isdigit():
            return ParsedRef(tag=tag, relative=int(second[1:]))
        try:
            return ParsedRef(tag=tag, run_serial=int(second))
        except ValueError:
            raise ValueError(f"Invalid ref: {ref!r}")

    if len(parts) == 3:
        first, second, third = parts
        tag = first
        # tag:~N:event
        if second.startswith("~") and second[1:].isdigit():
            return ParsedRef(tag=tag, relative=int(second[1:]), event_id=int(third))
        try:
            return ParsedRef(tag=tag, run_serial=int(second), event_id=int(third))
        except ValueError:
            raise ValueError(f"Invalid ref: {ref!r}")

    raise ValueError(f"Invalid ref: {ref!r}")


def resolve_run_ref(storage: BlqStorage, ref: str) -> dict | None:
    """Resolve a run ref string to a dict of run data.

    Handles:
    1. UUID -> query by run_id (attempt UUID stored as run_id)
    2. Relative (~N) -> ORDER BY started_at DESC OFFSET N-1, optionally filtered by tag
    3. Serial (with optional tag) -> WHERE run_id = serial
    4. Source name fallback -> WHERE source_name = tag ORDER BY started_at DESC LIMIT 1

    Returns None if not found. Fault-tolerant (catches exceptions).
    """
    try:
        parsed = parse_ref(ref)
    except ValueError:
        log.debug("Failed to parse ref: %s", ref)
        return None

    conn = storage.connection

    try:
        # Get column names once
        columns = [d[0] for d in conn.execute("SELECT * FROM blq_load_runs() LIMIT 0").description]

        row = None

        if parsed.uuid:
            # UUID lookup - run_id in the schema is the identifier
            row = conn.execute(
                "SELECT * FROM blq_load_runs() WHERE run_id = ?",
                [parsed.uuid],
            ).fetchone()

        elif parsed.is_relative:
            assert parsed.relative is not None
            offset = parsed.relative - 1  # ~1 means most recent (offset 0)
            if parsed.tag:
                row = conn.execute(
                    """
                    SELECT * FROM blq_load_runs()
                    WHERE source_name = ? OR tag = ?
                    ORDER BY started_at DESC
                    LIMIT 1 OFFSET ?
                    """,
                    [parsed.tag, parsed.tag, offset],
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM blq_load_runs()
                    ORDER BY started_at DESC
                    LIMIT 1 OFFSET ?
                    """,
                    [offset],
                ).fetchone()

        elif parsed.run_serial is not None:
            if parsed.tag:
                row = conn.execute(
                    """
                    SELECT * FROM blq_load_runs()
                    WHERE run_id = ? AND (source_name = ? OR tag = ?)
                    """,
                    [parsed.run_serial, parsed.tag, parsed.tag],
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM blq_load_runs() WHERE run_id = ?",
                    [parsed.run_serial],
                ).fetchone()

        elif parsed.tag:
            # Source name fallback - just a bare tag string
            row = conn.execute(
                """
                SELECT * FROM blq_load_runs()
                WHERE source_name = ? OR tag = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                [parsed.tag, parsed.tag],
            ).fetchone()

        if row is None:
            return None

        return dict(zip(columns, row))

    except Exception:
        log.debug("Failed to resolve ref: %s", ref, exc_info=True)
        return None
