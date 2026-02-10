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
    """Show comprehensive event details with dual context display.

    Shows event metadata plus both log context (where the error appears in output)
    and source context (where the error is in the source file) when enabled.
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

    try:
        store = BlqStorage.open(config.lq_dir)
        event = store.event(ref.run_id, ref.event_id)

        if event is None:
            print(f"Event {args.ref} not found", file=sys.stderr)
            sys.exit(1)

        if getattr(args, "json", False):
            # Include context in JSON output
            result = dict(event)
            result["log_context"] = _get_log_context(config, store, ref, event, args.lines)
            if config.source_lookup_enabled:
                result["source_context"] = _get_source_context(config, event, args.lines)
            else:
                result["source_context"] = None
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

            # Fingerprint
            fingerprint = event.get("fingerprint")
            if fingerprint:
                print(f"  Fingerprint: {_short_fingerprint(fingerprint)}")

            # Message (last in header section)
            message = event.get("message")
            if message:
                # Truncate long messages
                if len(message) > 200:
                    message = message[:197] + "..."
                print(f"  Message: {message}")

            print()

            # Log context
            log_context = _get_log_context(config, store, ref, event, args.lines)
            if log_context:
                print("== Log Context ==")
                print(log_context)
                print()

            # Source context (if enabled)
            if config.source_lookup_enabled:
                source_context = _get_source_context(config, event, args.lines)
                if source_context:
                    print("== Source Context ==")
                    print(source_context)

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
