"""
Event commands for blq CLI.

Handles viewing event details and context.
"""

from __future__ import annotations

import argparse
import json
import sys

import duckdb

from blq.commands.core import (
    BlqConfig,
    EventRef,
)
from blq.git import get_file_context
from blq.output import format_context, format_errors, get_output_format, read_source_context
from blq.storage import BlqStorage


def _format_location(event: dict) -> str:
    """Format file:line:col location string."""
    ref_file = event.get("ref_file") or "?"
    ref_line = event.get("ref_line")
    ref_column = event.get("ref_column")

    loc = ref_file
    if ref_line is not None:
        loc += f":{ref_line}"
        if ref_column and ref_column > 0:
            loc += f":{ref_column}"
    return loc


def _short_fingerprint(fingerprint: str | None, length: int = 16) -> str | None:
    """Shorten fingerprint for display."""
    if not fingerprint:
        return None
    return fingerprint[:length] if len(fingerprint) > length else fingerprint


def _output_fields(result: dict, fields: list[str], as_json: bool) -> None:
    """Output only the specified fields from a result dict.

    For JSON: outputs a filtered dict with only the requested fields.
    For text: outputs each field as "field: value" on its own line.
    """
    # Filter to only requested fields that exist
    filtered = {}
    for field in fields:
        if field in result:
            filtered[field] = result[field]

    if as_json:
        print(json.dumps(filtered, indent=2, default=str))
    else:
        for field in fields:
            if field in result:
                value = result[field]
                # Format the value appropriately
                if value is None:
                    print(f"{field}: null")
                elif isinstance(value, dict):
                    # Inline dict as JSON
                    print(f"{field}: {json.dumps(value, default=str)}")
                elif isinstance(value, list):
                    # Inline list as JSON
                    print(f"{field}: {json.dumps(value, default=str)}")
                else:
                    print(f"{field}: {value}")
            else:
                print(f"{field}: (not found)")


def cmd_event(args: argparse.Namespace) -> None:
    """Show event details by reference.

    If ref is a run reference (e.g., test:24), shows all events from that run.
    If ref is an event reference (e.g., test:24:1), shows that specific event.
    """
    config = BlqConfig.ensure()

    try:
        ref = EventRef.parse(args.ref)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        store = BlqStorage.open(config.lq_dir)

        if ref.is_run_ref:
            # Show all events from this run
            events = store.events(run_id=ref.run_id).df().to_dict(orient="records")

            if not events:
                print(f"No events found for run {ref.run_ref}", file=sys.stderr)
                sys.exit(1)

            output_format = get_output_format(args)
            print(format_errors(events, output_format))
        else:
            # Show single event
            assert ref.event_id is not None  # Guaranteed by not is_run_ref
            event = store.event(ref.run_id, ref.event_id)

            if event is None:
                print(f"Event {args.ref} not found", file=sys.stderr)
                sys.exit(1)

            if getattr(args, "json", False):
                print(json.dumps(event, indent=2, default=str))
            else:
                # Pretty print event details
                print(f"Event: {args.ref}")
                print(f"  Source: {event.get('source_name', '?')}")
                print(f"  Severity: {event.get('severity', '?')}")
                print(f"  File: {_format_location(event)}")

                # Tool info
                tool_name = event.get("tool_name")
                category = event.get("category")
                if tool_name:
                    tool_str = tool_name
                    if category:
                        tool_str += f" ({category})"
                    print(f"  Tool: {tool_str}")

                # Error code
                code = event.get("code") or event.get("rule") or event.get("error_code")
                if code:
                    print(f"  Code: {code}")

                # Message
                print(f"  Message: {event.get('message', '?')}")

                # Fingerprint (shortened for display)
                fingerprint = event.get("fingerprint")
                if fingerprint:
                    print(f"  Fingerprint: {_short_fingerprint(fingerprint)}")

                # Log lines
                if event.get("log_line_start"):
                    print(f"  Log lines: {event.get('log_line_start')}-{event.get('log_line_end')}")

    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_context(args: argparse.Namespace) -> None:
    """Show context lines around an event."""
    config = BlqConfig.ensure()

    try:
        ref = EventRef.parse(args.ref)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Require event reference, not run reference
    if ref.event_id is None:
        print("Error: context requires an event reference (e.g., test:24:1)", file=sys.stderr)
        sys.exit(1)

    try:
        store = BlqStorage.open(config.lq_dir)
        event = store.event(ref.run_id, ref.event_id)

        if event is None:
            print(f"Event {args.ref} not found", file=sys.stderr)
            sys.exit(1)

        log_line_start = event.get("log_line_start")
        log_line_end = event.get("log_line_end") or log_line_start
        source_name = event.get("source_name")
        message = event.get("message")

        if log_line_start is None or log_line_end is None:
            # For structured formats, show message instead
            print(f"Event {args.ref} (from structured format, no log line context)")
            print(f"  Source: {source_name}")
            print(f"  Message: {message}")
            return

        # Read raw log file
        raw_file = config.raw_dir / f"{ref.run_id:03d}.log"
        if not raw_file.exists():
            print(f"Raw log not found: {raw_file}", file=sys.stderr)
            print("Hint: Use --keep-raw or --json/--markdown to save raw logs", file=sys.stderr)
            sys.exit(1)

        lines = raw_file.read_text().splitlines()
        output = format_context(
            lines,
            log_line_start,
            log_line_end,
            context=args.lines,
            ref=args.ref,
        )
        print(output)

    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_inspect(args: argparse.Namespace) -> None:
    """Show comprehensive event details with context and enrichment.

    Shows event metadata plus:
    - Log context (where the error appears in output)
    - Source context (where the error is in the source file) with --source
    - Git context (blame and history) with --git
    - Fingerprint history with --fingerprint
    - All enrichment with --full

    Use -F/--field to output only specific fields.
    """
    config = BlqConfig.ensure()

    try:
        ref = EventRef.parse(args.ref)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Require event reference, not run reference
    if ref.is_run_ref or ref.event_id is None:
        print("Error: inspect requires an event reference (e.g., test:24:1)", file=sys.stderr)
        print("Use 'blq event' to see all events from a run", file=sys.stderr)
        sys.exit(1)

    # Get requested fields (empty list means show all)
    requested_fields = getattr(args, "field", []) or []

    # Determine which enrichments to include
    show_full = getattr(args, "full", False)
    show_source = show_full or getattr(args, "source", False) or config.source_lookup_enabled
    show_git = show_full or getattr(args, "git", False)
    show_fingerprint = show_full or getattr(args, "fingerprint", False)

    try:
        store = BlqStorage.open(config.lq_dir)
        event = store.event(ref.run_id, ref.event_id)

        if event is None:
            print(f"Event {args.ref} not found", file=sys.stderr)
            sys.exit(1)

        # Build full result dict (used for both JSON and field filtering)
        result = dict(event)

        # Add enrichments if requested (or if specific fields need them)
        needs_log_context = not requested_fields or "log_context" in requested_fields
        needs_source_context = (
            not requested_fields or "source_context" in requested_fields
        ) and show_source
        needs_git_context = (
            not requested_fields or "git_context" in requested_fields
        ) and show_git
        needs_fp_history = (
            not requested_fields or "fingerprint_history" in requested_fields
        ) and show_fingerprint

        if needs_log_context:
            result["log_context"] = _get_log_context(config, store, ref, event, args.lines)

        if needs_source_context:
            result["source_context"] = _get_source_context(config, event, args.lines)

        if needs_git_context:
            result["git_context"] = _get_git_context(config, event)

        if needs_fp_history:
            result["fingerprint_history"] = _get_fingerprint_history(store, event)

        # Handle specific field output
        if requested_fields:
            _output_fields(result, requested_fields, getattr(args, "json", False))
            return

        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, default=str))
        else:
            # Pretty print event details
            print(f"Event: {args.ref}")
            print(f"  Severity: {event.get('severity', '?')}")
            print(f"  File: {_format_location(event)}")

            # Tool info
            tool_name = event.get("tool_name")
            category = event.get("category")
            if tool_name:
                tool_str = tool_name
                if category:
                    tool_str += f" ({category})"
                print(f"  Tool: {tool_str}")

            # Error code
            code = event.get("code") or event.get("rule") or event.get("error_code")
            if code:
                print(f"  Code: {code}")

            # Fingerprint (brief)
            fingerprint = event.get("fingerprint")
            if fingerprint and not show_fingerprint:
                print(f"  Fingerprint: {_short_fingerprint(fingerprint)}")

            # Message (last in header section)
            message = event.get("message")
            if message:
                # Truncate long messages
                if len(message) > 200:
                    message = message[:197] + "..."
                print(f"  Message: {message}")

            print()

            # Log context (always shown)
            log_context = _get_log_context(config, store, ref, event, args.lines)
            if log_context:
                print("== Log Context ==")
                print(log_context)
                print()

            # Source context
            if show_source:
                source_context = _get_source_context(config, event, args.lines)
                if source_context:
                    print("== Source Context ==")
                    print(source_context)
                    print()

            # Git context
            if show_git:
                git_context = _get_git_context(config, event)
                if git_context:
                    print("== Git Context ==")
                    print(_format_git_context(git_context))
                    print()

            # Fingerprint history
            if show_fingerprint:
                fp_history = _get_fingerprint_history(store, event)
                if fp_history:
                    print("== Fingerprint History ==")
                    print(_format_fingerprint_history(fp_history))
                    print()

    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _get_log_context(
    config: BlqConfig,
    store: BlqStorage,
    ref: EventRef,
    event: dict,
    context_lines: int,
) -> str | None:
    """Get log context for an event."""
    log_line_start = event.get("log_line_start")
    log_line_end = event.get("log_line_end") or log_line_start

    if log_line_start is None or log_line_end is None:
        return None

    # Try BIRD storage first
    output_bytes = store.get_output(ref.run_id)
    if output_bytes:
        try:
            content = output_bytes.decode("utf-8", errors="replace")
        except Exception:
            content = output_bytes.decode("latin-1")
        lines = content.splitlines()
        return format_context(
            lines,
            log_line_start,
            log_line_end,
            context=context_lines,
            header=f"Line {log_line_start}",
        )

    # Fall back to raw log file
    raw_file = config.raw_dir / f"{ref.run_id:03d}.log"
    if raw_file.exists():
        lines = raw_file.read_text().splitlines()
        return format_context(
            lines,
            log_line_start,
            log_line_end,
            context=context_lines,
            header=f"Line {log_line_start}",
        )

    return None


def _get_source_context(
    config: BlqConfig,
    event: dict,
    context_lines: int,
) -> str | None:
    """Get source file context for an event."""
    ref_file = event.get("ref_file")
    ref_line = event.get("ref_line")

    if not ref_file or not ref_line:
        return None

    return read_source_context(
        ref_file,
        ref_line,
        ref_root=config.ref_root,
        context=context_lines,
    )


def _get_git_context(
    config: BlqConfig,
    event: dict,
    history_limit: int = 2,
) -> dict | None:
    """Get git context for an event (blame and history).

    Returns dict with:
        - blame: who last modified the line (author, commit, modified)
        - recent_commits: most recent commits to the file (default: 2)
    """
    ref_file = event.get("ref_file")
    ref_line = event.get("ref_line")

    if not ref_file:
        return None

    try:
        # Resolve file path relative to ref_root
        from pathlib import Path

        if config.ref_root:
            file_path = str(Path(config.ref_root) / ref_file)
        else:
            file_path = ref_file

        ctx = get_file_context(file_path, line=ref_line, history_limit=history_limit)

        result: dict = {
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
                    "time": c.time.isoformat(),
                    "message": c.message,
                }
                for c in ctx.recent_commits
            ]

        return result
    except Exception:
        return None


def _get_fingerprint_history(
    store: BlqStorage,
    event: dict,
) -> dict | None:
    """Get fingerprint history for an event.

    Returns dict with:
        - fingerprint: the fingerprint value
        - first_seen: first occurrence (run_ref, timestamp)
        - last_seen: most recent occurrence
        - occurrences: total count
        - is_regression: True if was fixed then reappeared
    """
    fingerprint = event.get("fingerprint")
    if not fingerprint:
        return None

    try:
        # Query all occurrences of this fingerprint using parameterized query
        result = store.sql(
            """
            SELECT
                run_serial,
                run_ref,
                timestamp,
                tag
            FROM blq_load_events()
            WHERE fingerprint = ?
            ORDER BY timestamp ASC
            """,
            [fingerprint],
        ).fetchall()

        if not result:
            return None

        first = result[0]
        last = result[-1]

        # Detect regression: check if there are gaps in run_serials
        is_regression = False
        if len(result) >= 2:
            run_serials = [r[0] for r in result]
            # If there's a gap > 1 between consecutive occurrences, it's a regression
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
        return None


def _format_git_context(git_ctx: dict) -> str:
    """Format git context for display."""
    lines = []

    blame = git_ctx.get("blame")
    if blame:
        modified = blame.get("modified", "")
        if modified:
            # Parse ISO date and format nicely
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(modified)
                modified = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        lines.append(f"  Last modified: {modified} by {blame.get('author', '?')}")
        lines.append(f"  Commit: {blame.get('commit', '?')}")

    commits = git_ctx.get("recent_commits", [])
    if commits:
        lines.append("")
        lines.append("  Recent changes:")
        for c in commits[:5]:
            time_str = c.get("time", "")
            if time_str:
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(time_str)
                    time_str = dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass
            msg = c.get("message", "")[:50]
            lines.append(f"    {c.get('hash', '?')} ({time_str}) {msg}")

    return "\n".join(lines)


def _format_fingerprint_history(fp_hist: dict) -> str:
    """Format fingerprint history for display."""
    lines = []

    lines.append(f"  Fingerprint: {fp_hist.get('fingerprint', '?')}")

    first = fp_hist.get("first_seen", {})
    if first:
        ts = first.get("timestamp", "")
        if ts:
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(ts)
                ts = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        lines.append(f"  First seen: {first.get('run_ref', '?')} ({ts})")

    last = fp_hist.get("last_seen", {})
    if last:
        ts = last.get("timestamp", "")
        if ts:
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(ts)
                ts = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        lines.append(f"  Last seen: {last.get('run_ref', '?')} ({ts})")

    lines.append(f"  Occurrences: {fp_hist.get('occurrences', 0)}")

    if fp_hist.get("is_regression"):
        lines.append("  Status: REGRESSION (was fixed, reappeared)")

    return "\n".join(lines)
