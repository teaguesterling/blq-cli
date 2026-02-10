"""
Execution commands for blq CLI.

Handles running commands, importing logs, and capturing stdin.
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import queue
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from blq.bird import write_bird_invocation
from blq.commands.core import (
    RAW_DIR,
    BlqConfig,
    EventSummary,
    RunResult,
    capture_ci_info,
    capture_environment,
    capture_git_info,
    expand_command,
    find_executable,
    format_command_help,
    get_next_run_id,
    parse_log_content,
    write_run_parquet,
)

# Logger for lq status messages
logger = logging.getLogger("blq-cli")


def _print_run_summary(
    result: RunResult,
    source_name: str,
    show_events: bool = True,
    max_events: int = 10,
) -> None:
    """Print a formatted run summary to stderr.

    Args:
        result: The run result to summarize
        source_name: Name of the source (e.g., "test-all")
        show_events: Whether to show individual events
        max_events: Maximum number of events to show
    """
    # Status indicators
    status_icons = {
        "OK": "\033[32m✓\033[0m",  # Green checkmark
        "FAIL": "\033[31m✗\033[0m",  # Red X
        "WARN": "\033[33m⚠\033[0m",  # Yellow warning
        "TIMEOUT": "\033[35m⏱\033[0m",  # Magenta clock
    }
    icon = status_icons.get(result.status, "?")

    # Build run ref
    run_ref = f"{source_name}:{result.run_id}"

    # Build counts string
    counts = []
    error_count = result.summary.get("errors", 0)
    warning_count = result.summary.get("warnings", 0)
    if error_count > 0:
        counts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
    if warning_count > 0:
        counts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
    counts_str = ", ".join(counts) if counts else "no issues"

    # Print header line
    print(
        f"\n{icon} {run_ref} | {result.status} | {result.duration_sec:.1f}s | {counts_str}",
        file=sys.stderr,
    )

    # Print events if requested and there are any
    if show_events and (result.errors or result.warnings):
        events = result.errors[:max_events]
        if result.warnings and len(events) < max_events:
            events.extend(result.warnings[: max_events - len(events)])

        for e in events:
            # Truncate message to fit on one line
            msg = (e.message or "")[:60]
            if len(e.message or "") > 60:
                msg += "..."
            loc = e.location()
            # Format: ref  location  message
            print(f"  {e.ref:<12} {loc:<30} {msg}", file=sys.stderr)

        remaining = error_count + warning_count - len(events)
        if remaining > 0:
            print(f"  ... and {remaining} more", file=sys.stderr)

    print(file=sys.stderr)  # Blank line after summary


def _make_event_summary(run_id: int, e: dict) -> EventSummary:
    """Create an EventSummary from an event dict."""
    return EventSummary(
        ref=f"{run_id}:{e.get('event_id', 0)}",
        severity=e.get("severity"),
        ref_file=e.get("ref_file"),
        ref_line=e.get("ref_line"),
        ref_column=e.get("ref_column"),
        message=e.get("message"),
        error_code=e.get("error_code"),
        fingerprint=e.get("fingerprint"),
        test_name=e.get("test_name"),
        log_line_start=e.get("log_line_start"),
        log_line_end=e.get("log_line_end"),
    )


def _execute_command(
    command: str,
    source_name: str,
    source_type: str,
    config: BlqConfig,
    format_hint: str = "auto",
    quiet: bool = False,
    keep_raw: bool | None = None,
    error_limit: int = 50,
    session_id: str | None = None,
    capture_env_vars: list[str] | None = None,
    timeout: int | None = None,
) -> RunResult:
    """Execute a command and capture its output.

    This is the core execution function used by cmd_run, cmd_exec, and cmd_watch.
    Unlike the CLI commands, this function returns a RunResult instead of calling
    sys.exit, allowing callers to handle the result.

    Args:
        command: The shell command to execute
        source_name: Name to use for this run in the logs
        source_type: Type of source ("run", "exec", "watch")
        config: BlqConfig with project settings
        format_hint: Log format hint for parsing
        quiet: If True, don't stream command output
        keep_raw: If True, save raw log output. If None, uses config.keep_raw setting.
        error_limit: Maximum number of errors to include in result
        session_id: Optional session ID for grouping related runs (watch mode)
        capture_env_vars: Environment variables to capture (default: config.capture_env)
        timeout: Timeout in seconds. If None, no timeout is applied.

    Returns:
        RunResult with execution details and parsed events
    """
    # Resolve keep_raw from config if not explicitly set
    if keep_raw is None:
        keep_raw = config.keep_raw
    lq_dir = config.lq_dir

    if capture_env_vars is None:
        capture_env_vars = config.capture_env.copy()

    run_id = get_next_run_id(lq_dir)
    started_at = datetime.now()

    # Capture execution context
    cwd = os.getcwd()
    executable_path = find_executable(command)
    environment = capture_environment(capture_env_vars)
    hostname = socket.gethostname()
    platform_name = platform.system()
    arch = platform.machine()
    git_info = capture_git_info()
    ci_info = capture_ci_info()

    logger.debug(f"Running: {command}")
    logger.debug(f"Run ID: {run_id}")
    if timeout:
        logger.debug(f"Timeout: {timeout}s")

    # Run command, capturing output with timeout support
    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    output_lines: list[str] = []
    timed_out = False
    assert process.stdout is not None  # stdout=PIPE ensures this

    if timeout is None:
        # No timeout - simple synchronous read
        for line in process.stdout:
            if not quiet:
                sys.stdout.write(line)
                sys.stdout.flush()
            output_lines.append(line)
        exit_code = process.wait()
    else:
        # Timeout enabled - use threading to read output while monitoring time
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            """Read output lines and put them in queue."""
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    output_queue.put(line)
            finally:
                output_queue.put(None)  # Signal done

        reader_thread = threading.Thread(target=read_output, daemon=True)
        reader_thread.start()

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break

            try:
                queue_line = output_queue.get(timeout=min(remaining, 0.5))
                if queue_line is None:  # Reader finished
                    break
                if not quiet:
                    sys.stdout.write(queue_line)
                    sys.stdout.flush()
                output_lines.append(queue_line)
            except queue.Empty:
                # Check if process has finished
                if process.poll() is not None:
                    # Drain remaining output
                    while True:
                        try:
                            drain_line = output_queue.get_nowait()
                            if drain_line is None:
                                break
                            if not quiet:
                                sys.stdout.write(drain_line)
                                sys.stdout.flush()
                            output_lines.append(drain_line)
                        except queue.Empty:
                            break
                    break

        if timed_out:
            # Kill the process and drain any remaining output
            process.kill()
            if not quiet:
                sys.stdout.write(f"\n[TIMEOUT after {timeout}s]\n")
                sys.stdout.flush()
            # Give reader thread a moment to finish
            reader_thread.join(timeout=1.0)
            # Drain any remaining output that was captured
            while True:
                try:
                    timeout_line = output_queue.get_nowait()
                    if timeout_line is None:
                        break
                    output_lines.append(timeout_line)
                except queue.Empty:
                    break
            exit_code = -1  # Indicate timeout
        else:
            exit_code = process.wait()
            reader_thread.join(timeout=1.0)

    completed_at = datetime.now()
    output = "".join(output_lines)
    duration_sec = (completed_at - started_at).total_seconds()

    # Save raw output if requested
    if keep_raw:
        raw_file = lq_dir / RAW_DIR / f"{run_id:03d}.log"
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_text(output)

    # Parse output
    events = parse_log_content(output, format_hint)

    # Build run metadata
    # tag is the logical command name (how user refers to it, e.g., "build", "test")
    run_meta = {
        "run_id": run_id,
        "source_name": source_name,
        "source_type": source_type,
        "tag": source_name,  # Logical command name for easy lookup
        "command": command,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "exit_code": exit_code,
        "cwd": cwd,
        "executable_path": executable_path,
        "environment": environment or None,
        "hostname": hostname,
        "platform": platform_name,
        "arch": arch,
        "git_commit": git_info.commit,
        "git_branch": git_info.branch,
        "git_dirty": git_info.dirty,
        "ci": ci_info,
        "session_id": session_id,
        "timed_out": timed_out,
    }

    # Write using appropriate storage backend
    if config.use_bird:
        # BIRD storage mode - write to DuckDB tables
        output_bytes = output.encode("utf-8") if keep_raw else None
        inv_id, filepath = write_bird_invocation(events, run_meta, lq_dir, output_bytes)
        # For BIRD mode, we use a sequential run number for display
        # but the actual ID is a UUID stored in inv_id
    else:
        # Legacy parquet storage mode
        filepath = write_run_parquet(events, run_meta, lq_dir)

    # Build structured result
    error_events = [e for e in events if e.get("severity") == "error"]
    warning_events = [e for e in events if e.get("severity") == "warning"]

    # Determine status
    if timed_out:
        status = "TIMEOUT"
    elif error_events:
        status = "FAIL"
    elif warning_events:
        status = "WARN"
    elif exit_code != 0:
        status = "FAIL"
    else:
        status = "OK"

    # Build output stats for visibility when no events are parsed
    tail_lines = 5
    max_line_length = 120  # Truncate long lines to conserve context

    def _truncate_line(ln: str) -> str:
        stripped = ln.rstrip("\n\r")
        if len(stripped) > max_line_length:
            return stripped[:max_line_length] + "..."
        return stripped

    output_stats: dict[str, int | list[str]] = {
        "lines": len(output_lines),
        "bytes": len(output),
        "tail": [_truncate_line(ln) for ln in output_lines[-tail_lines:]],
    }

    return RunResult(
        run_id=run_id,
        command=command,
        status=status,
        exit_code=exit_code,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_sec=duration_sec,
        summary={
            "total_events": len(events),
            "errors": len(error_events),
            "warnings": len(warning_events),
        },
        errors=[_make_event_summary(run_id, e) for e in error_events[:error_limit]],
        warnings=[_make_event_summary(run_id, e) for e in warning_events[:error_limit]],
        parquet_path=str(filepath),
        output_stats=output_stats,
    )


def _find_similar_commands(name: str, registered: list[str], max_results: int = 3) -> list[str]:
    """Find registered commands similar to the given name.

    Uses simple heuristics: prefix match, suffix match, and substring match.
    """
    if not registered:
        return []

    name_lower = name.lower()
    similar = []

    # Exact prefix match (e.g., "tes" -> "test")
    for cmd in registered:
        if cmd.lower().startswith(name_lower) or name_lower.startswith(cmd.lower()):
            similar.append(cmd)

    # Suffix match (e.g., "tests" ends with "test" pattern)
    if not similar:
        for cmd in registered:
            if cmd.lower().endswith(name_lower) or name_lower.endswith(cmd.lower()):
                similar.append(cmd)

    # Substring match
    if not similar:
        for cmd in registered:
            if name_lower in cmd.lower() or cmd.lower() in name_lower:
                similar.append(cmd)

    # Simple edit distance for close matches (off by one character)
    if not similar:
        for cmd in registered:
            if abs(len(cmd) - len(name)) <= 2:
                # Check if only differs by 1-2 chars
                matches = sum(a == b for a, b in zip(name_lower, cmd.lower()))
                if matches >= min(len(name), len(cmd)) - 2:
                    similar.append(cmd)

    return similar[:max_results]


def _parse_command_args(
    cli_args: list[str],
    positional_limit: int | None = None,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Parse CLI arguments into named args, positional args, and extra args.

    Args:
        cli_args: List of CLI arguments after the command name
        positional_limit: If set, only use this many positional args for placeholders

    Returns:
        Tuple of (named_args, positional_args, extra_args)
        - named_args: Dict of key=value arguments
        - positional_args: List of positional arguments for placeholders
        - extra_args: List of passthrough arguments
    """
    named_args: dict[str, str] = {}
    positional_args: list[str] = []
    extra_args: list[str] = []

    # Check for :: separator
    if "::" in cli_args:
        separator_idx = cli_args.index("::")
        main_args = cli_args[:separator_idx]
        extra_args = cli_args[separator_idx + 1 :]
    else:
        main_args = cli_args

    # Parse main args into named and positional
    for arg in main_args:
        if "=" in arg and not arg.startswith("-"):
            # Named argument: key=value
            key, value = arg.split("=", 1)
            named_args[key] = value
        else:
            # Positional argument
            positional_args.append(arg)

    # Apply positional limit if specified
    if positional_limit is not None and positional_limit < len(positional_args):
        extra_args = positional_args[positional_limit:] + extra_args
        positional_args = positional_args[:positional_limit]

    return named_args, positional_args, extra_args


def _run_no_capture(command: str, quiet: bool = False) -> int:
    """Run a command without capturing output to parquet.

    Args:
        command: Shell command to run
        quiet: If True, don't stream output

    Returns:
        Exit code from the command
    """
    started_at = datetime.now()

    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    assert process.stdout is not None  # stdout=PIPE ensures this
    for line in process.stdout:
        if not quiet:
            sys.stdout.write(line)
            sys.stdout.flush()

    exit_code = process.wait()
    duration_sec = (datetime.now() - started_at).total_seconds()
    logger.debug(f"Completed in {duration_sec:.1f}s (exit code {exit_code})")
    return exit_code


def cmd_run(args: argparse.Namespace) -> None:
    """Run a registered command and capture its output.

    Unlike exec, this command only runs registered commands from the registry.
    Use --register to register a new command while running it.

    Command templates can have placeholders:
    - {name} - keyword-only, required
    - {name=default} - keyword-only, optional
    - {name:} - positional-able, required
    - {name:=default} - positional-able, optional
    """
    from blq.commands.core import RegisteredCommand

    # Get unified config (finds .lq, loads settings and commands)
    config = BlqConfig.ensure()

    # Check if first argument is a registered command name
    registered_commands = config.commands
    cmd_name = args.command[0]
    cmd_args = args.command[1:]  # Arguments after the command name

    # Build list of env vars to capture (config defaults + command-specific)
    capture_env_vars = config.capture_env.copy()

    # Default capture setting (can be overridden by command config)
    should_capture = True

    if cmd_name in registered_commands:
        # Use registered command
        reg_cmd = registered_commands[cmd_name]
        source_name = args.name or cmd_name
        format_hint = args.format if args.format != "auto" else reg_cmd.format
        should_capture = reg_cmd.capture
        # Add command-specific env vars
        for var in reg_cmd.capture_env:
            if var not in capture_env_vars:
                capture_env_vars.append(var)

        # Parse command arguments
        positional_limit = getattr(args, "positional_args", None)
        named_args, positional_args, extra_args = _parse_command_args(cmd_args, positional_limit)

        # Merge command defaults with provided args (defaults first, then user args override)
        merged_args = {**reg_cmd.defaults, **named_args}

        # Expand command template with arguments
        # Use template property which returns tpl for template commands, cmd otherwise
        try:
            command = expand_command(reg_cmd.template, merged_args, positional_args, extra_args)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            print("", file=sys.stderr)
            print(format_command_help(reg_cmd), file=sys.stderr)
            sys.exit(1)

    elif getattr(args, "register", False):
        # --register flag: register this command and run it
        cmd_str = " ".join(args.command)
        registered_commands[cmd_name] = RegisteredCommand(
            name=cmd_name,
            cmd=cmd_str,
            description="",
            timeout=300,
            format=args.format,
            capture=True,
        )
        config.save_commands()
        logger.warning(f"Registered command '{cmd_name}': {cmd_str}")

        command = cmd_str
        source_name = cmd_name
        format_hint = args.format
    else:
        # Command not found - error out with suggestions
        similar = _find_similar_commands(cmd_name, list(registered_commands.keys()))
        print(f"Error: '{cmd_name}' is not a registered command.", file=sys.stderr)
        if similar:
            print(f"Did you mean: {', '.join(similar)}?", file=sys.stderr)
        print("", file=sys.stderr)
        print("Options:", file=sys.stderr)
        print(f"  blq run -R {' '.join(args.command)}  # Register and run", file=sys.stderr)
        print(f"  blq exec {' '.join(args.command)}    # Run without registering", file=sys.stderr)
        print("  blq commands                         # List registered commands", file=sys.stderr)
        sys.exit(1)

    # Runtime flag overrides command config
    if args.capture is not None:
        should_capture = args.capture

    # Determine output mode
    structured_output = args.json or args.markdown
    show_summary = getattr(args, "summary", False)
    verbose = getattr(args, "verbose", False)
    quiet = args.quiet or structured_output

    # Configure logger based on verbosity
    if verbose:
        logger.setLevel(logging.DEBUG)
    elif show_summary:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)

    # No-capture mode: just run and exit with the command's exit code
    if not should_capture:
        exit_code = _run_no_capture(command, quiet)
        sys.exit(exit_code)

    # Determine timeout: CLI flag overrides command config
    timeout = getattr(args, "timeout", None)
    if timeout is None and cmd_name in registered_commands:
        timeout = registered_commands[cmd_name].timeout

    # Execute command with capture
    result = _execute_command(
        command=command,
        source_name=source_name,
        source_type="run",
        config=config,
        format_hint=format_hint,
        quiet=quiet,
        keep_raw=True if (args.keep_raw or structured_output) else None,
        error_limit=args.error_limit,
        capture_env_vars=capture_env_vars,
        timeout=timeout,
    )

    # Output based on format
    if args.json:
        print(result.to_json(include_warnings=args.include_warnings))
    elif args.markdown:
        print(result.to_markdown(include_warnings=args.include_warnings))
    else:
        # Show summary only in verbose mode
        if verbose:
            _print_run_summary(result, source_name, show_events=True, max_events=10)

    sys.exit(result.exit_code)


def cmd_exec(args: argparse.Namespace) -> None:
    """Execute an ad-hoc command and capture its output.

    Unlike cmd_run, this always treats the command as a shell command
    and never looks up the command registry.
    """
    # Get unified config (finds .lq, loads settings)
    config = BlqConfig.ensure()

    # Handle command args (REMAINDER may include leading '--')
    cmd_args = args.command
    if cmd_args and cmd_args[0] == "--":
        cmd_args = cmd_args[1:]

    if not cmd_args:
        print("Error: No command specified", file=sys.stderr)
        sys.exit(1)

    # Build command from args - properly quote for shell
    import shlex

    command = shlex.join(cmd_args)
    # Use provided name, or extract basename of first command token
    if args.name:
        source_name = args.name
    else:
        import os

        first_token = cmd_args[0]
        source_name = os.path.basename(first_token)

    # Determine capture mode (default: capture)
    should_capture = not args.no_capture

    # Determine output mode
    structured_output = args.json or args.markdown
    show_summary = getattr(args, "summary", False)
    verbose = getattr(args, "verbose", False)
    quiet = args.quiet or structured_output

    # Configure logger based on verbosity
    if verbose:
        logger.setLevel(logging.DEBUG)
    elif show_summary:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)

    # No-capture mode: just run and exit with the command's exit code
    if not should_capture:
        exit_code = _run_no_capture(command, quiet)
        sys.exit(exit_code)

    # Execute command with capture
    timeout = getattr(args, "timeout", None)
    result = _execute_command(
        command=command,
        source_name=source_name,
        source_type="exec",
        config=config,
        format_hint=args.format,
        quiet=quiet,
        keep_raw=True if (args.keep_raw or structured_output) else None,
        error_limit=args.error_limit,
        timeout=timeout,
    )

    # Output based on format
    if args.json:
        print(result.to_json(include_warnings=args.include_warnings))
    elif args.markdown:
        print(result.to_markdown(include_warnings=args.include_warnings))
    else:
        # Show summary only in verbose mode
        if verbose:
            _print_run_summary(result, source_name, show_events=True, max_events=10)

    sys.exit(result.exit_code)


def cmd_import(args: argparse.Namespace) -> None:
    """Import an existing log file."""
    config = BlqConfig.ensure()
    lq_dir = config.lq_dir

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    source_name = args.name or filepath.stem
    run_id = get_next_run_id(lq_dir)
    now = datetime.now().isoformat()

    content = filepath.read_text()
    events = parse_log_content(content, args.format)

    run_meta = {
        "run_id": run_id,
        "source_name": source_name,
        "source_type": "import",
        "tag": source_name,
        "command": f"import {filepath}",
        "started_at": now,
        "completed_at": now,
        "exit_code": 0,
    }

    outpath = write_run_parquet(events, run_meta, lq_dir)

    errors = sum(1 for e in events if e.get("severity") == "error")
    warnings = sum(1 for e in events if e.get("severity") == "warning")
    print(f"Imported {len(events)} events ({errors} errors, {warnings} warnings)")
    print(f"Saved to {outpath}")


def cmd_capture(args: argparse.Namespace) -> None:
    """Capture from stdin."""
    config = BlqConfig.ensure()
    lq_dir = config.lq_dir

    source_name = args.name or "stdin"
    run_id = get_next_run_id(lq_dir)
    started_at = datetime.now().isoformat()

    content = sys.stdin.read()
    completed_at = datetime.now().isoformat()

    events = parse_log_content(content, args.format)

    run_meta = {
        "run_id": run_id,
        "source_name": source_name,
        "source_type": "capture",
        "tag": source_name,
        "command": "stdin",
        "started_at": started_at,
        "completed_at": completed_at,
        "exit_code": 0,
    }

    outpath = write_run_parquet(events, run_meta, lq_dir)

    errors = sum(1 for e in events if e.get("severity") == "error")
    warnings = sum(1 for e in events if e.get("severity") == "warning")
    print(f"Captured {len(events)} events ({errors} errors, {warnings} warnings)", file=sys.stderr)
    print(f"Saved to {outpath}", file=sys.stderr)
