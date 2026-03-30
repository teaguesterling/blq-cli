"""Inspect service: context-fetching functions for event enrichment.

Provides source, log, git, and fingerprint context for events.
Used by both CLI (commands/events.py) and MCP (serve.py).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from blq.storage import BlqStorage

logger = logging.getLogger(__name__)


def get_source_context(
    ref_file: str | None,
    ref_line: int | None,
    source_root: Path,
    context_lines: int = 3,
) -> str | None:
    """Get source file context around a specific line.

    Args:
        ref_file: Path to the source file (relative to source_root).
        ref_line: 1-indexed line number of the event.
        source_root: Root path for resolving relative file paths.
        context_lines: Number of lines before/after to include.

    Returns:
        Formatted context string with line numbers and markers, or None.
    """
    if ref_file is None or ref_line is None:
        return None

    try:
        import blq.output as output_mod

        return output_mod.read_source_context(
            ref_file,
            ref_line,
            ref_root=str(source_root),
            context=context_lines,
        )
    except Exception:
        logger.debug("Failed to get source context for %s:%s", ref_file, ref_line, exc_info=True)
        return None


def get_log_context(
    storage: BlqStorage | None,
    run_id: int,
    log_line_start: int | None,
    log_line_end: int | None,
    context_lines: int = 3,
) -> str | None:
    """Get log output context around the event's line range.

    Args:
        storage: BlqStorage instance with get_output().
        run_id: The run ID to fetch output for.
        log_line_start: 1-indexed start line of the event in the log.
        log_line_end: 1-indexed end line of the event in the log.
        context_lines: Number of lines before/after to include.

    Returns:
        Formatted context string, or None.
    """
    if log_line_start is None or log_line_end is None:
        return None

    if storage is None:
        return None

    try:
        import blq.output as output_mod

        output_bytes = storage.get_output(run_id)
        if output_bytes is None:
            return None

        content = output_bytes.decode("utf-8", errors="replace")
        lines = content.splitlines()
        return output_mod.format_context(
            lines,
            log_line_start,
            log_line_end,
            context=context_lines,
        )
    except Exception:
        logger.debug("Failed to get log context for run %s", run_id, exc_info=True)
        return None


def get_git_context(
    ref_file: str | None,
    ref_line: int | None,
    source_root: Path,
    history_limit: int = 2,
) -> dict[str, Any] | None:
    """Get git blame and history context for a source file location.

    Args:
        ref_file: Path to the source file (relative to source_root).
        ref_line: Line number for blame info (optional).
        source_root: Root path for resolving relative file paths.
        history_limit: Maximum number of recent commits to include.

    Returns:
        Dict with file, line, blame, and recent_commits, or None.
    """
    if ref_file is None:
        return None

    try:
        import blq.git as git_mod

        file_path = source_root / ref_file
        if not file_path.exists():
            return None

        ctx = git_mod.get_file_context(str(file_path), line=ref_line, history_limit=history_limit)

        result: dict[str, Any] = {
            "file": ref_file,
            "line": ref_line,
        }

        if ctx.last_author:
            result["blame"] = {
                "author": ctx.last_author,
                "commit": ctx.last_commit,
                "modified": ctx.last_modified.isoformat() if ctx.last_modified else None,
            }

        if ctx.recent_commits:
            result["recent_commits"] = [
                {
                    "hash": c.short_hash,
                    "author": c.author,
                    "time": c.time.isoformat() if c.time else None,
                    "message": c.message,
                }
                for c in ctx.recent_commits
            ]

        return result
    except Exception:
        logger.debug("Failed to get git context for %s", ref_file, exc_info=True)
        return None


def get_fingerprint_history(
    storage: BlqStorage | None,
    fingerprint: str | None,
) -> dict[str, Any] | None:
    """Get occurrence history for an event fingerprint.

    Queries all events with the same fingerprint and returns summary info
    including first/last seen and regression detection.

    Args:
        storage: BlqStorage instance with sql() method.
        fingerprint: The fingerprint value to look up.

    Returns:
        Dict with fingerprint, first_seen, last_seen, occurrences,
        and is_regression, or None.
    """
    if fingerprint is None:
        return None

    if storage is None:
        return None

    try:
        result = storage.sql(
            """
            SELECT
                run_serial,
                run_ref,
                timestamp,
                tag
            FROM blq_load_events()
            WHERE fingerprint = ?
            ORDER BY started_at ASC
            """,
            [fingerprint],
        ).fetchall()

        if not result:
            return None

        first = result[0]
        last = result[-1]

        # Detect regression: gap > 1 in consecutive run_serial values
        is_regression = False
        if len(result) >= 2:
            run_serials = [r[0] for r in result]
            for i in range(1, len(run_serials)):
                if run_serials[i] - run_serials[i - 1] > 1:
                    is_regression = True
                    break

        return {
            "fingerprint": fingerprint[:16] + "..." if len(fingerprint) > 16 else fingerprint,
            "first_seen": {
                "run_ref": first[1],
                "timestamp": first[2].isoformat() if first[2] else None,
            },
            "last_seen": {
                "run_ref": last[1],
                "timestamp": last[2].isoformat() if last[2] else None,
            },
            "occurrences": len(result),
            "is_regression": is_regression,
        }
    except Exception:
        logger.debug("Failed to get fingerprint history for %s", fingerprint, exc_info=True)
        return None
