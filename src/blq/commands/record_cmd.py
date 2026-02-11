"""
Record invocation commands for passive command tracking.

These commands enable tracking of commands via Claude Code hooks without
changing how commands are invoked. The attempt/outcome pattern allows
accurate duration tracking via pre/post hooks.

Usage with Claude Code hooks:
    # PreToolUse hook records attempt at start
    ATTEMPT=$(blq record-invocation attempt --command "$command" --json)
    ATTEMPT_ID=$(echo "$ATTEMPT" | jq -r '.attempt_id')

    # PostToolUse hook records outcome at completion
    echo "$output" | blq record-invocation outcome \\
        --attempt $ATTEMPT_ID --exit $exit_code --parse --json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from datetime import datetime
from pathlib import Path

from blq.bird import (
    AttemptRecord,
    BirdStore,
    InvocationRecord,
    OutcomeRecord,
)
from blq.commands.core import (
    BlqConfig,
    capture_ci_info,
    capture_environment,
    capture_git_info,
    detect_format_from_command,
    find_executable,
    parse_log_content,
)


def _extract_tag_from_command(command: str) -> str:
    """Extract a tag name from a command string.

    Uses the first word of the command as the tag.

    Args:
        command: Command string

    Returns:
        Tag name (first word, sanitized)
    """
    # Get first word
    parts = command.split()
    if not parts:
        return "unknown"

    first_word = parts[0]

    # Remove path components (e.g., /usr/bin/pytest -> pytest)
    if "/" in first_word:
        first_word = first_word.rsplit("/", 1)[-1]

    # Sanitize: keep only alphanumeric and hyphens/underscores
    sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in first_word)

    return sanitized[:50] or "unknown"


def cmd_record_attempt(args: argparse.Namespace) -> None:
    """Record an invocation attempt (command start).

    This is called before a command executes to record that it's starting.
    Returns an attempt_id that should be passed to record-invocation outcome.
    """
    config = BlqConfig.find()
    if config is None:
        print(
            json.dumps({"error": "Not initialized. Run 'blq init' first."}),
            file=sys.stderr,
        )
        sys.exit(1)

    store = BirdStore.open(config.lq_dir)

    command = args.command
    tag = getattr(args, "tag", None) or _extract_tag_from_command(command)
    format_hint = getattr(args, "format", None) or detect_format_from_command(command)
    cwd = getattr(args, "cwd", None) or os.getcwd()
    pid = getattr(args, "pid", None)

    # Collect metadata
    started_at = datetime.now()
    git_info = capture_git_info()
    ci_info = capture_ci_info()
    environment = capture_environment(config.capture_env)

    # Ensure session exists
    client_id = "blq-record"
    session_id = f"record-{tag}"
    store.ensure_session(
        session_id=session_id,
        client_id=client_id,
        invoker="blq",
        invoker_type="record",
        cwd=cwd,
    )

    # Create attempt record
    attempt = AttemptRecord(
        id=AttemptRecord.generate_id(),
        session_id=session_id,
        cmd=command,
        cwd=cwd,
        client_id=client_id,
        timestamp=started_at,
        executable=find_executable(command),
        format_hint=format_hint if format_hint != "auto" else None,
        hostname=socket.gethostname(),
        pid=pid,
        tag=tag,
        source_name=tag,
        source_type="record",
        environment=environment or None,
        platform=platform.system(),
        arch=platform.machine(),
        git_commit=git_info.commit,
        git_branch=git_info.branch,
        git_dirty=git_info.dirty,
        ci=ci_info,
    )

    attempt_id = store.write_attempt(attempt)

    # Store PID in live metadata if provided
    if pid is not None:
        live_meta = {
            "pid": pid,
            "cmd": command,
            "tag": tag,
            "started_at": started_at.isoformat(),
        }
        store.create_live_dir(attempt_id, live_meta)

    store.close()

    result = {
        "attempt_id": attempt_id,
        "command": command,
        "tag": tag,
        "started_at": started_at.isoformat(),
    }
    if pid is not None:
        result["pid"] = pid

    if getattr(args, "json", False):
        print(json.dumps(result))
    else:
        print(f"Recorded attempt: {attempt_id}")


def cmd_record_outcome(args: argparse.Namespace) -> None:
    """Record an invocation outcome (command completion).

    This is called after a command completes to record its exit status
    and optionally parse output for events.
    """
    config = BlqConfig.find()
    if config is None:
        print(
            json.dumps({"error": "Not initialized. Run 'blq init' first."}),
            file=sys.stderr,
        )
        sys.exit(1)

    store = BirdStore.open(config.lq_dir)

    attempt_id = getattr(args, "attempt", None)
    command = getattr(args, "command", None)
    exit_code = getattr(args, "exit", 0)
    do_parse = getattr(args, "parse", False)
    format_hint = getattr(args, "format", None)
    tag = getattr(args, "tag", None)
    output_file = getattr(args, "output", None)
    duration_arg = getattr(args, "duration", None)
    pid = getattr(args, "pid", None)

    # Either --attempt or --command is required
    if not attempt_id and not command:
        print(
            json.dumps({"error": "Either --attempt or --command is required."}),
            file=sys.stderr,
        )
        sys.exit(1)

    # Read output from file or stdin
    output_content: bytes = b""
    if output_file:
        output_path = Path(output_file)
        if output_path.exists():
            output_content = output_path.read_bytes()
        else:
            print(
                json.dumps({"error": f"Output file not found: {output_file}"}),
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        # Read from stdin (non-blocking check)
        if not sys.stdin.isatty():
            output_content = sys.stdin.buffer.read()

    completed_at = datetime.now()

    # If no prior attempt, create one now (standalone mode)
    if not attempt_id:
        assert command is not None  # Required if no attempt_id
        tag = tag or _extract_tag_from_command(command)
        format_hint = format_hint or detect_format_from_command(command)
        cwd = os.getcwd()

        # Collect metadata
        started_at = completed_at  # Best we can do - unknown actual start
        git_info = capture_git_info()
        ci_info = capture_ci_info()
        environment = capture_environment(config.capture_env)

        # Ensure session
        client_id = "blq-record"
        session_id = f"record-{tag}"
        store.ensure_session(
            session_id=session_id,
            client_id=client_id,
            invoker="blq",
            invoker_type="record",
            cwd=cwd,
        )

        # Create attempt record
        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id=session_id,
            cmd=command,
            cwd=cwd,
            client_id=client_id,
            timestamp=started_at,
            executable=find_executable(command),
            format_hint=format_hint if format_hint != "auto" else None,
            hostname=socket.gethostname(),
            pid=pid,
            tag=tag,
            source_name=tag,
            source_type="record",
            environment=environment or None,
            platform=platform.system(),
            arch=platform.machine(),
            git_commit=git_info.commit,
            git_branch=git_info.branch,
            git_dirty=git_info.dirty,
            ci=ci_info,
        )

        attempt_id = store.write_attempt(attempt)
        # Use provided duration or 0 (unknown) in standalone mode
        duration_ms = duration_arg if duration_arg is not None else 0
    else:
        # Get attempt info to calculate duration
        attempt_info = store.connection.execute(
            "SELECT timestamp, format_hint, tag FROM attempts WHERE id = ?",
            [attempt_id],
        ).fetchone()

        if not attempt_info:
            print(
                json.dumps({"error": f"Attempt not found: {attempt_id}"}),
                file=sys.stderr,
            )
            store.close()
            sys.exit(1)

        started_at_str = attempt_info[0]
        if isinstance(started_at_str, str):
            started_at = datetime.fromisoformat(started_at_str)
        else:
            started_at = started_at_str

        # Use format from attempt if not overridden
        if not format_hint and attempt_info[1]:
            format_hint = attempt_info[1]

        # Use tag from attempt if not overridden
        if not tag and attempt_info[2]:
            tag = attempt_info[2]

        # Use provided duration or calculate from timestamps
        if duration_arg is not None:
            duration_ms = duration_arg
        else:
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

    # Write outcome record
    outcome = OutcomeRecord(
        attempt_id=attempt_id,
        completed_at=completed_at,
        exit_code=exit_code,
        duration_ms=duration_ms,
    )
    store.write_outcome(outcome)

    # Get the command from attempt if not already known
    if not command:
        cmd_result = store.connection.execute(
            "SELECT cmd FROM attempts WHERE id = ?", [attempt_id]
        ).fetchone()
        command = cmd_result[0] if cmd_result else "unknown"

    # Also write invocation record for backward compatibility with blq_events_flat
    # Get attempt details for invocation
    attempt_details = store.connection.execute(
        """
        SELECT session_id, cwd, executable, hostname, tag, source_name,
               source_type, environment, platform, arch, git_commit,
               git_branch, git_dirty, ci, pid
        FROM attempts WHERE id = ?
        """,
        [attempt_id],
    ).fetchone()

    if attempt_details:
        # Use pid from outcome args if provided, otherwise from attempt record
        effective_pid = pid if pid is not None else attempt_details[14]
        invocation = InvocationRecord(
            id=attempt_id,  # Same ID so events can join
            session_id=attempt_details[0],
            cmd=command,
            cwd=attempt_details[1],
            client_id="blq-record",
            timestamp=started_at,
            duration_ms=duration_ms,
            exit_code=exit_code,
            executable=attempt_details[2],
            format_hint=format_hint if format_hint and format_hint != "auto" else None,
            hostname=attempt_details[3],
            pid=effective_pid,
            tag=attempt_details[4] or tag,
            source_name=attempt_details[5] or tag,
            source_type=attempt_details[6] or "record",
            environment=json.loads(attempt_details[7]) if attempt_details[7] else None,
            platform=attempt_details[8],
            arch=attempt_details[9],
            git_commit=attempt_details[10],
            git_branch=attempt_details[11],
            git_dirty=attempt_details[12],
            ci=json.loads(attempt_details[13]) if attempt_details[13] else None,
        )
        store.write_invocation(invocation)

    # Get run_id (count of invocations)
    run_id = store.get_next_run_number() - 1  # Already written, so -1

    # Write output if we have content
    output_bytes = len(output_content)
    if output_content:
        store.write_output(attempt_id, "combined", output_content)

    # Parse events if requested
    events_summary = {"total": 0, "errors": 0, "warnings": 0}
    if do_parse and output_content:
        output_text = output_content.decode("utf-8", errors="replace")
        effective_format = format_hint or detect_format_from_command(command)
        events = parse_log_content(output_text, effective_format)

        if events:
            # Get hostname from attempt for denormalization
            hostname = (
                store.connection.execute(
                    "SELECT hostname FROM attempts WHERE id = ?", [attempt_id]
                ).fetchone()
                or (None,)
            )[0]

            store.write_events(
                attempt_id,
                events,
                client_id="blq-record",
                format_used=effective_format if effective_format != "auto" else None,
                hostname=hostname,
            )

            events_summary["total"] = len(events)
            events_summary["errors"] = sum(1 for e in events if e.get("severity") == "error")
            events_summary["warnings"] = sum(1 for e in events if e.get("severity") == "warning")

    store.close()

    result = {
        "recorded": True,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "output_bytes": output_bytes,
        "events": events_summary,
    }

    if getattr(args, "json", False):
        print(json.dumps(result))
    else:
        status = "OK" if exit_code == 0 else "FAIL"
        events_str = f"{events_summary['errors']} errors, {events_summary['warnings']} warnings"
        print(f"Recorded outcome: {status} | {duration_ms}ms | {events_str}")


def cmd_record_help(args: argparse.Namespace) -> None:
    """Show help for record-invocation command."""
    print("""blq record-invocation - Record invocation metadata for passive tracking

Subcommands:
  attempt   Record command start (returns attempt_id)
  outcome   Record command completion

Usage with Claude Code hooks:
  # PreToolUse hook
  ATTEMPT=$(blq record-invocation attempt --command "$command" --json)
  ATTEMPT_ID=$(echo "$ATTEMPT" | jq -r '.attempt_id')

  # PostToolUse hook
  echo "$output" | blq record-invocation outcome \\
      --attempt $ATTEMPT_ID --exit $exit_code --parse --json

Examples:
  # Record attempt
  blq record-invocation attempt --command "pytest tests/" --json

  # Record outcome with parsing
  echo "test output" | blq record-invocation outcome --attempt $ID --exit 0 --parse --json

  # Standalone mode (no prior attempt)
  echo "output" | blq record-invocation outcome --command "make build" --exit 1 --parse
""")
