"""
Management commands for blq CLI.

Handles status, errors, warnings, summary, history, and prune operations.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime, timedelta

import duckdb

from blq.bird import BirdStore
from blq.commands.core import (
    BlqConfig,
    EventRef,
    get_all_suppressed_fingerprints,
    get_store_for_args,
)
from blq.output import (
    format_errors,
    format_history,
    format_run_details,
    format_status,
    get_default_limit,
    get_output_format,
)


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of all sources."""
    try:
        store = get_store_for_args(args)
        conn = store.connection

        if getattr(args, "verbose", False):
            result = conn.execute("FROM blq_status_verbose()").fetchdf()
        else:
            result = conn.execute("FROM blq_status()").fetchdf()

        data = result.to_dict(orient="records")
        output_format = get_output_format(args)
        print(format_status(data, output_format))
    except duckdb.Error:
        # Fallback if macros aren't working
        store = get_store_for_args(args)
        result = store.events(limit=10).df()
        data = result.to_dict(orient="records")
        output_format = get_output_format(args)
        print(format_errors(data, output_format))


def cmd_info(args: argparse.Namespace) -> None:
    """Show detailed information about a specific run.

    Accepts a run ref (e.g., 'test:5') or invocation_id (UUID).
    Supports --tail, --head for output viewing and --follow for live streaming.
    """
    ref_arg = args.ref
    tail_lines = getattr(args, "tail", None)
    head_lines = getattr(args, "head", None)
    follow = getattr(args, "follow", False)

    try:
        store = get_store_for_args(args)
        config = BlqConfig.find()

        # Check if it's a UUID (invocation_id) or a run ref
        is_uuid = len(ref_arg) == 36 and ref_arg.count("-") == 4

        # First try to find in completed runs (invocations)
        if is_uuid:
            result = store.sql(f"""
                SELECT * FROM blq_load_runs()
                WHERE invocation_id = '{ref_arg}'
            """).df()
            attempt_id = ref_arg
        else:
            try:
                ref = EventRef.parse(ref_arg)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

            result = store.sql(f"""
                SELECT * FROM blq_load_runs()
                WHERE run_id = {ref.run_id}
            """).df()

        # Check if run is completed or still pending
        run_status = "completed"
        attempt_id = None

        if result.empty:
            # Not in completed runs - check pending attempts
            if is_uuid:
                attempt_result = store.sql(f"""
                    SELECT * FROM blq_load_attempts()
                    WHERE attempt_id = '{ref_arg}'
                """).df()
            else:
                attempt_result = store.sql(f"""
                    SELECT * FROM blq_load_attempts()
                    WHERE run_id = {ref.run_id}
                """).df()

            if attempt_result.empty:
                print(f"Run {ref_arg} not found", file=sys.stderr)
                sys.exit(1)

            run_data = attempt_result.to_dict(orient="records")[0]
            run_status = run_data.get("status", "pending")
            attempt_id = str(run_data.get("attempt_id"))
        else:
            run_data = result.to_dict(orient="records")[0]
            invocation_id = run_data.get("invocation_id")
            attempt_id = str(invocation_id) if invocation_id else None

            # Get output details for this run
            if invocation_id:
                outputs_result = store.sql(f"""
                    SELECT stream, byte_length
                    FROM outputs
                    WHERE invocation_id = '{invocation_id}'
                    ORDER BY stream
                """).fetchall()
                if outputs_result:
                    run_data["outputs"] = [
                        {"stream": row[0], "bytes": row[1]} for row in outputs_result
                    ]

        # Add status to run_data for display
        run_data["status"] = run_status

        # Handle --tail, --head, or --follow
        if tail_lines or head_lines or follow:
            _show_run_output(
                config=config,
                attempt_id=attempt_id,
                run_status=run_status,
                run_data=run_data,
                tail_lines=tail_lines,
                head_lines=head_lines,
                follow=follow,
            )
        else:
            # Just show run details
            output_format = get_output_format(args)
            detailed = getattr(args, "details", False) or getattr(args, "verbose", False)
            print(format_run_details(run_data, output_format, detailed=detailed))

    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _show_run_output(
    config: BlqConfig | None,
    attempt_id: str | None,
    run_status: str,
    run_data: dict,
    tail_lines: int | None,
    head_lines: int | None,
    follow: bool,
) -> None:
    """Show run output (tail, head, or follow mode).

    For running commands, reads from live output directory.
    For completed commands, reads from blob storage.
    """
    if not config:
        print("Error: Not in a blq project", file=sys.stderr)
        sys.exit(1)

    if not attempt_id:
        print("Error: No attempt ID found for this run", file=sys.stderr)
        sys.exit(1)

    bird_store = BirdStore.open(config.lq_dir)

    try:
        if run_status == "pending":
            # Running command - read from live output
            _show_live_output(bird_store, attempt_id, run_data, tail_lines, head_lines, follow)
        else:
            # Completed command - read from blob storage
            if follow:
                print("Error: --follow only works for running commands", file=sys.stderr)
                sys.exit(1)
            _show_stored_output(bird_store, attempt_id, tail_lines, head_lines)
    finally:
        bird_store.close()


def _show_live_output(
    store: BirdStore,
    attempt_id: str,
    run_data: dict,
    tail_lines: int | None,
    head_lines: int | None,
    follow: bool,
) -> None:
    """Show output from live directory (for running commands)."""
    # Print run info header
    source_name = run_data.get("source_name", "unknown")
    cmd = run_data.get("command", "")
    started_at = run_data.get("started_at", "")
    print(f"[{source_name}] {cmd}", file=sys.stderr)
    print(f"Started: {started_at} | Status: running", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    if follow:
        # Stream output like tail -f
        live_path = store.get_live_output_path(attempt_id, "combined")
        if not live_path.exists():
            print("(no output yet)", file=sys.stderr)
            return

        try:
            with open(live_path) as f:
                # First print existing content
                if tail_lines:
                    # Read all, then show tail
                    lines = f.readlines()
                    for line in lines[-tail_lines:]:
                        sys.stdout.write(line)
                else:
                    # Print all existing content
                    for line in f:
                        sys.stdout.write(line)
                sys.stdout.flush()

                # Then follow new content
                print("\n--- Following output (Ctrl+C to stop) ---", file=sys.stderr)
                while True:
                    line = f.readline()
                    if line:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                    else:
                        # Check if command is still running
                        status = store.get_attempt_status(attempt_id)
                        if status != "pending":
                            print(f"\n--- Command finished (status: {status}) ---", file=sys.stderr)
                            break
                        time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n--- Stopped following ---", file=sys.stderr)
    else:
        # Just read current content
        content = store.read_live_output(attempt_id, "combined", tail=tail_lines)
        if content:
            if head_lines:
                lines = content.split("\n")[:head_lines]
                print("\n".join(lines))
            else:
                print(content, end="")
        else:
            print("(no output yet)")


def _show_stored_output(
    store: BirdStore,
    attempt_id: str,
    tail_lines: int | None,
    head_lines: int | None,
) -> None:
    """Show output from blob storage (for completed commands)."""
    # Get output from blob storage
    content_bytes = store.read_output(attempt_id, "combined")
    if not content_bytes:
        # Try stdout if combined not available
        content_bytes = store.read_output(attempt_id, "stdout")

    if not content_bytes:
        print("(no output stored)")
        return

    content = content_bytes.decode("utf-8", errors="replace")

    if tail_lines:
        lines = content.split("\n")
        # Handle trailing newline
        if lines and lines[-1] == "":
            lines = lines[:-1]
        output_lines = lines[-tail_lines:]
        print("\n".join(output_lines))
    elif head_lines:
        lines = content.split("\n")[:head_lines]
        print("\n".join(lines))
    else:
        print(content, end="")


def cmd_last(args: argparse.Namespace) -> None:
    """Show information about the most recent run.

    Provides a quick way to see what happened in the last command execution,
    with options to show output and/or events.
    """
    import json as json_module

    try:
        store = get_store_for_args(args)

        # Get the most recent run
        result = store.sql("""
            SELECT * FROM blq_load_runs()
            ORDER BY run_id DESC
            LIMIT 1
        """).df()

        if result.empty:
            print("No runs found", file=sys.stderr)
            sys.exit(1)

        run_data = result.to_dict(orient="records")[0]
        run_id = run_data.get("run_id")
        invocation_id = run_data.get("invocation_id")

        # Collect output data for JSON mode
        json_output = {}
        if getattr(args, "json", False):
            json_output["run"] = run_data

        # Get output details
        if invocation_id:
            outputs_result = store.sql(f"""
                SELECT stream, byte_length
                FROM outputs
                WHERE invocation_id = '{invocation_id}'
                ORDER BY stream
            """).fetchall()
            if outputs_result:
                run_data["outputs"] = [
                    {"stream": row[0], "bytes": row[1]} for row in outputs_result
                ]

        # Show run info (unless --quiet)
        if not getattr(args, "quiet", False):
            if getattr(args, "json", False):
                pass  # Will output at end
            else:
                output_format = get_output_format(args)
                print(format_run_details(run_data, output_format))
                print()

        # Show output if requested
        show_output = getattr(args, "output", False)
        head_lines = getattr(args, "head", None)
        tail_lines = getattr(args, "tail", None)

        # Default to showing tail if --output but no head/tail specified
        if show_output and head_lines is None and tail_lines is None:
            tail_lines = 20

        if head_lines is not None or tail_lines is not None:
            output_bytes = store.get_output(run_id)
            if output_bytes:
                try:
                    content = output_bytes.decode("utf-8", errors="replace")
                except Exception:
                    content = output_bytes.decode("latin-1")

                lines = content.splitlines()

                if getattr(args, "json", False):
                    if head_lines is not None:
                        json_output["head"] = lines[:head_lines]
                    if tail_lines is not None:
                        json_output["tail"] = lines[-tail_lines:] if tail_lines else lines
                else:
                    if head_lines is not None:
                        print(f"== Output (first {head_lines} lines) ==")
                        for line in lines[:head_lines]:
                            print(line)
                        print()

                    if tail_lines is not None:
                        print(f"== Output (last {tail_lines} lines) ==")
                        for line in lines[-tail_lines:]:
                            print(line)
                        print()
            elif not getattr(args, "json", False):
                print("(no output captured - enable with storage.keep_raw config)")
                print()

        # Show events if requested
        severity = getattr(args, "severity", None)
        show_errors = getattr(args, "errors", False)
        show_warnings = getattr(args, "warnings", False)
        event_limit = get_default_limit(args)

        # Determine severity filter
        if show_errors and show_warnings:
            severity = "error,warning"
        elif show_errors:
            severity = "error"
        elif show_warnings:
            severity = "warning"

        if severity:
            conditions = [f"run_serial = {run_id}"]

            if "," in severity:
                severities = [s.strip() for s in severity.split(",")]
                severity_list = ", ".join(f"'{s}'" for s in severities)
                conditions.append(f"severity IN ({severity_list})")
            else:
                conditions.append(f"severity = '{severity}'")

            where = " AND ".join(conditions)

            events_result = store.sql(f"""
                SELECT * FROM blq_load_events()
                WHERE {where}
                ORDER BY event_id
                LIMIT {event_limit}
            """).df()

            events_data = events_result.to_dict(orient="records")

            if getattr(args, "json", False):
                json_output["events"] = events_data
            else:
                if events_data:
                    label = severity.replace(",", "/").title() + "s"
                    print(f"== {label} ==")
                    output_format = get_output_format(args)
                    print(format_errors(events_data, output_format))
                else:
                    print(f"No {severity} events in this run")

        # Output JSON if requested
        if getattr(args, "json", False):
            print(json_module.dumps(json_output, indent=2, default=str))

    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_events(args: argparse.Namespace) -> None:
    """Show events with optional severity filter.

    This is the main event viewing command. `blq errors` and `blq warnings`
    are aliases that set --severity appropriately.
    """
    try:
        store = get_store_for_args(args)
        config = BlqConfig.find()

        # Build SQL query with filters
        conditions = []

        # Severity filter (can be single value or comma-separated list)
        severity = getattr(args, "severity", None)
        if severity:
            if "," in severity:
                # Multiple severities
                severities = [s.strip() for s in severity.split(",")]
                severity_list = ", ".join(f"'{s}'" for s in severities)
                conditions.append(f"severity IN ({severity_list})")
            else:
                conditions.append(f"severity = '{severity}'")

        # Source filter
        if getattr(args, "source", None):
            conditions.append(f"source_name = '{args.source}'")

        # Suppression filter (unless --include-suppressed is set)
        include_suppressed = getattr(args, "include_suppressed", False)
        if not include_suppressed and config:
            suppressed = get_all_suppressed_fingerprints(config)
            if suppressed:
                fp_list = ", ".join(f"'{fp}'" for fp in suppressed)
                conditions.append(f"(fingerprint IS NULL OR fingerprint NOT IN ({fp_list}))")

        where = " AND ".join(conditions) if conditions else "1=1"

        # Always get full columns for formatting, select happens in formatter
        limit = get_default_limit(args)
        result = store.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE {where}
            ORDER BY run_id DESC, event_id
            LIMIT {limit}
        """).df()

        data = result.to_dict(orient="records")
        output_format = get_output_format(args)
        print(format_errors(data, output_format))
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_errors(args: argparse.Namespace) -> None:
    """Show recent errors (alias for `blq events --severity error`)."""
    args.severity = "error"
    # Ensure include_suppressed attribute exists
    if not hasattr(args, "include_suppressed"):
        args.include_suppressed = False
    cmd_events(args)


def cmd_warnings(args: argparse.Namespace) -> None:
    """Show recent warnings (alias for `blq events --severity warning`)."""
    args.severity = "warning"
    # Ensure include_suppressed attribute exists
    if not hasattr(args, "include_suppressed"):
        args.include_suppressed = False
    cmd_events(args)


def cmd_summary(args: argparse.Namespace) -> None:
    """Show aggregate summary."""
    try:
        store = get_store_for_args(args)
        conn = store.connection

        if args.latest:
            result = conn.execute("FROM blq_summary_latest()").fetchdf()
        else:
            result = conn.execute("FROM blq_summary()").fetchdf()

        data = result.to_dict(orient="records")
        output_format = get_output_format(args)
        print(format_status(data, output_format))  # Similar format to status
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_history(args: argparse.Namespace) -> None:
    """Show run history.

    Can filter by tag/ref (e.g., 'blq history test' or 'blq history -t test').
    Can filter by status (e.g., 'blq history --status=running').
    """
    try:
        store = get_store_for_args(args)

        # Get filter from positional arg or --tag flag
        tag_filter = getattr(args, "ref", None) or getattr(args, "tag", None)
        status_filter = getattr(args, "status", None)
        limit = get_default_limit(args)

        # Map CLI status names to database status values
        # "running" is user-friendly alias for "pending"
        status_map = {"running": "pending", "completed": "completed", "orphaned": "orphaned"}
        if status_filter and status_filter != "all":
            db_status = status_map.get(status_filter)
        else:
            db_status = None

        if db_status:
            # Use blq_history_status() macro for status filtering
            if db_status:
                status_sql = f"'{db_status}'"
            else:
                status_sql = "NULL"

            if tag_filter:
                result = store.sql(f"""
                    SELECT * FROM blq_history_status({status_sql}, {limit})
                    WHERE source_name = '{tag_filter}'
                """).df()
            else:
                result = store.sql(f"""
                    SELECT * FROM blq_history_status({status_sql}, {limit})
                """).df()
        elif tag_filter:
            # Filter by tag/source_name only
            result = store.sql(f"""
                SELECT * FROM blq_load_runs()
                WHERE tag = '{tag_filter}' OR source_name = '{tag_filter}'
                ORDER BY run_id DESC
                LIMIT {limit}
            """).df()
        else:
            result = store.runs(limit=limit).df()

        # Convert to list of dicts for formatting
        data = result.to_dict(orient="records")

        if not data:
            if status_filter:
                print(f"No {status_filter} runs found", file=sys.stderr)
            elif tag_filter:
                print(f"No runs found for '{tag_filter}'", file=sys.stderr)
            return

        # Format and print
        output_format = get_output_format(args)
        print(format_history(data, output_format))
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_prune(args: argparse.Namespace) -> None:
    """Remove old log files."""
    config = BlqConfig.ensure()
    logs_dir = config.logs_dir

    cutoff = datetime.now() - timedelta(days=args.older_than)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    removed = 0
    for date_dir in logs_dir.glob("date=*"):
        date_str = date_dir.name.replace("date=", "")
        if date_str < cutoff_str:
            if args.dry_run:
                print(f"Would remove: {date_dir}")
            else:
                shutil.rmtree(date_dir)
                print(f"Removed: {date_dir}")
            removed += 1

    if removed == 0:
        print(f"No logs older than {args.older_than} days")
    elif args.dry_run:
        print(f"\nDry run: would remove {removed} date partitions")


def cmd_formats(args: argparse.Namespace) -> None:
    """List available log formats."""
    conn = duckdb.connect(":memory:")

    # Try to load duck_hunt
    try:
        conn.execute("LOAD duck_hunt")
    except duckdb.Error:
        print("duck_hunt extension not available.", file=sys.stderr)
        print("\nBuilt-in formats (fallback parser):", file=sys.stderr)
        print("  auto    - Automatic detection of common formats")
        print("  generic - Generic file:line:col: message pattern")
        sys.exit(1)

    # Get formats from duck_hunt
    try:
        result = conn.execute("SELECT * FROM duck_hunt_formats()").fetchall()
    except duckdb.Error as e:
        print(f"Error querying formats: {e}", file=sys.stderr)
        sys.exit(1)

    # Group by category
    categories: dict[str, list[tuple]] = {}
    for row in result:
        name, desc, category, *_ = row
        if category not in categories:
            categories[category] = []
        categories[category].append((name, desc))

    # Display order
    category_order = [
        "meta",
        "build_system",
        "test_framework",
        "linting_tool",
        "python_tool",
        "security_tool",
        "ci_system",
        "infrastructure_tool",
        "debugging_tool",
        "structured_log",
        "system_log",
        "web_access",
        "cloud_audit",
    ]

    # Nice category names
    category_names = {
        "meta": "Meta",
        "build_system": "Build Systems",
        "test_framework": "Test Frameworks",
        "linting_tool": "Linting Tools",
        "python_tool": "Python Tools",
        "security_tool": "Security Tools",
        "ci_system": "CI/CD Systems",
        "infrastructure_tool": "Infrastructure",
        "debugging_tool": "Debugging",
        "structured_log": "Structured Logs",
        "system_log": "System Logs",
        "web_access": "Web Access Logs",
        "cloud_audit": "Cloud Audit Logs",
    }

    print(f"Available log formats ({len(result)} total):\n")

    for cat in category_order:
        if cat not in categories:
            continue
        formats = categories[cat]
        cat_name = category_names.get(cat, cat)
        print(f"  {cat_name}:")
        for name, desc in sorted(formats):
            print(f"    {name:24} {desc}")
        print()

    # Any remaining categories
    for cat, formats in categories.items():
        if cat not in category_order:
            print(f"  {cat}:")
            for name, desc in sorted(formats):
                print(f"    {name:24} {desc}")
            print()


def cmd_completions(args: argparse.Namespace) -> None:
    """Generate shell completion scripts."""
    shell = args.shell

    if shell == "bash":
        print(_bash_completion())
    elif shell == "zsh":
        print(_zsh_completion())
    elif shell == "fish":
        print(_fish_completion())
    else:
        print(f"Unsupported shell: {shell}", file=sys.stderr)
        print("Supported shells: bash, zsh, fish", file=sys.stderr)
        sys.exit(1)


def _bash_completion() -> str:
    """Generate bash completion script."""
    return r"""# blq bash completion
# Add to ~/.bashrc or ~/.bash_completion:
#   eval "$(blq completions bash)"
# Or save to a file:
#   blq completions bash > /etc/bash_completion.d/blq

_blq_completions() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Main commands
    commands="init run r exec e import capture status errors warnings"
    commands="$commands summary history sql shell prune formats event"
    commands="$commands context commands register unregister sync"
    commands="$commands query q filter f serve completions"

    # Complete commands
    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${commands}" -- "${cur}") )
        return 0
    fi

    # Command-specific completions
    case "${COMP_WORDS[1]}" in
        run|r)
            # Complete registered command names
            if [[ -f .lq/commands.toml ]]; then
                local registered
                registered=$(grep -oP '(?<=^\[commands\.)[^]]+' .lq/commands.toml 2>/dev/null)
                COMPREPLY=( $(compgen -W "${registered}" -- "${cur}") )
            fi
            ;;
        exec|e)
            # Complete files and common options
            if [[ "${cur}" == -* ]]; then
                local opts="--name --format --keep-raw --json --markdown"
                opts="$opts --quiet --summary --verbose --include-warnings"
                opts="$opts --error-limit --no-capture"
                COMPREPLY=( $(compgen -W "$opts" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -f -- "${cur}") )
            fi
            ;;
        import)
            # Complete log files
            COMPREPLY=( $(compgen -f -X "!*.log" -- "${cur}") )
            COMPREPLY+=( $(compgen -f -X "!*.txt" -- "${cur}") )
            COMPREPLY+=( $(compgen -d -- "${cur}") )
            ;;
        query|q|filter|f)
            # Complete log files and options
            if [[ "${cur}" == -* ]]; then
                local opts="--select --filter --order --limit --json --csv --markdown"
                COMPREPLY=( $(compgen -W "$opts" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -f -- "${cur}") )
            fi
            ;;
        event|context)
            # No specific completions for refs
            ;;
        errors|warnings)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=( $(compgen -W "--source --limit --compact --json" -- "${cur}") )
            fi
            ;;
        register)
            if [[ "${cur}" == -* ]]; then
                local opts="--description --timeout --format --no-capture --force"
                COMPREPLY=( $(compgen -W "$opts" -- "${cur}") )
            fi
            ;;
        completions)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "${cur}") )
            ;;
        *)
            # Default to file completion
            COMPREPLY=( $(compgen -f -- "${cur}") )
            ;;
    esac
}

complete -F _blq_completions blq
"""


def _zsh_completion() -> str:
    """Generate zsh completion script."""
    return r"""#compdef blq
# blq zsh completion
# Add to ~/.zshrc:
#   eval "$(blq completions zsh)"
# Or save to a file in your fpath:
#   blq completions zsh > ~/.zsh/completions/_blq

_blq() {
    local -a commands
    commands=(
        'init:Initialize .lq directory'
        'run:Run registered command (alias: r)'
        'r:Run registered command'
        'exec:Execute ad-hoc command (alias: e)'
        'e:Execute ad-hoc command'
        'import:Import existing log file'
        'capture:Capture from stdin'
        'status:Show status of all sources'
        'errors:Show recent errors'
        'warnings:Show recent warnings'
        'summary:Aggregate summary'
        'history:Show run history'
        'sql:Run arbitrary SQL'
        'shell:Interactive SQL shell'
        'prune:Remove old logs'
        'formats:List available log formats'
        'event:Show event details by reference'
        'context:Show context lines around event'
        'commands:List registered commands'
        'register:Register a command'
        'unregister:Remove a registered command'
        'sync:Sync logs to central location'
        'query:Query log files or stored events (alias: q)'
        'q:Query log files or stored events'
        'filter:Filter with simple syntax (alias: f)'
        'f:Filter with simple syntax'
        'serve:Start MCP server'
        'completions:Generate shell completions'
    )

    _arguments -C \\
        '-V[Show version]' \\
        '--version[Show version]' \\
        '-F[Log format]:format:' \\
        '--log-format[Log format]:format:' \\
        '-g[Query global store]' \\
        '--global[Query global store]' \\
        '-d[Database path]:path:_files' \\
        '--database[Database path]:path:_files' \\
        '1:command:->command' \\
        '*::args:->args'

    case "$state" in
        command)
            _describe -t commands 'blq command' commands
            ;;
        args)
            case "${words[1]}" in
                run|r)
                    # Complete registered commands
                    if [[ -f .lq/commands.toml ]]; then
                        local -a registered
                        local cmd="grep -oP '(?<=^\[commands\.)[^]]+' .lq/commands.toml"
                        registered=(${(f)"$(eval $cmd 2>/dev/null)"})
                        _describe -t registered 'registered command' registered
                    fi
                    ;;
                exec|e)
                    _arguments \\
                        '-n[Source name]:name:' \\
                        '--name[Source name]:name:' \\
                        '-f[Parse format]:format:' \\
                        '--format[Parse format]:format:' \\
                        '-r[Keep raw output]' \\
                        '--keep-raw[Keep raw output]' \\
                        '-j[JSON output]' \\
                        '--json[JSON output]' \\
                        '-m[Markdown output]' \\
                        '--markdown[Markdown output]' \\
                        '-q[Quiet mode]' \\
                        '--quiet[Quiet mode]' \\
                        '-s[Show summary]' \\
                        '--summary[Show summary]' \\
                        '-v[Verbose mode]' \\
                        '--verbose[Verbose mode]' \\
                        '-w[Include warnings]' \\
                        '--include-warnings[Include warnings]' \\
                        '-N[Skip capture]' \\
                        '--no-capture[Skip capture]' \\
                        '*:command:_command_names -e'
                    ;;
                import)
                    _arguments \\
                        '-n[Source name]:name:' \\
                        '--name[Source name]:name:' \\
                        '*:file:_files -g "*.log *.txt"'
                    ;;
                query|q)
                    _arguments \\
                        '-s[Select columns]:columns:' \\
                        '--select[Select columns]:columns:' \\
                        '-f[Filter]:filter:' \\
                        '--filter[Filter]:filter:' \\
                        '-o[Order by]:column:' \\
                        '--order[Order by]:column:' \\
                        '-l[Limit]:number:' \\
                        '--limit[Limit]:number:' \\
                        '-j[JSON output]' \\
                        '--json[JSON output]' \\
                        '-c[CSV output]' \\
                        '--csv[CSV output]' \\
                        '-m[Markdown output]' \\
                        '--markdown[Markdown output]' \\
                        '*:file:_files'
                    ;;
                errors|warnings)
                    _arguments \\
                        '-s[Filter by source]:source:' \\
                        '--source[Filter by source]:source:' \\
                        '-n[Max results]:number:' \\
                        '--limit[Max results]:number:' \\
                        '-c[Compact format]' \\
                        '--compact[Compact format]' \\
                        '-j[JSON output]' \\
                        '--json[JSON output]'
                    ;;
                completions)
                    _arguments '1:shell:(bash zsh fish)'
                    ;;
                *)
                    _files
                    ;;
            esac
            ;;
    esac
}

_blq "$@"
"""


def _fish_completion() -> str:
    """Generate fish completion script."""
    return """# blq fish completion
# Save to ~/.config/fish/completions/blq.fish:
#   blq completions fish > ~/.config/fish/completions/blq.fish

# Disable file completion by default
complete -c blq -f

# Commands
complete -c blq -n "__fish_use_subcommand" -a init -d "Initialize .lq directory"
complete -c blq -n "__fish_use_subcommand" -a run -d "Run registered command"
complete -c blq -n "__fish_use_subcommand" -a r -d "Run registered command (alias)"
complete -c blq -n "__fish_use_subcommand" -a exec -d "Execute ad-hoc command"
complete -c blq -n "__fish_use_subcommand" -a e -d "Execute ad-hoc command (alias)"
complete -c blq -n "__fish_use_subcommand" -a import -d "Import existing log file"
complete -c blq -n "__fish_use_subcommand" -a capture -d "Capture from stdin"
complete -c blq -n "__fish_use_subcommand" -a status -d "Show status of all sources"
complete -c blq -n "__fish_use_subcommand" -a errors -d "Show recent errors"
complete -c blq -n "__fish_use_subcommand" -a warnings -d "Show recent warnings"
complete -c blq -n "__fish_use_subcommand" -a summary -d "Aggregate summary"
complete -c blq -n "__fish_use_subcommand" -a history -d "Show run history"
complete -c blq -n "__fish_use_subcommand" -a sql -d "Run arbitrary SQL"
complete -c blq -n "__fish_use_subcommand" -a shell -d "Interactive SQL shell"
complete -c blq -n "__fish_use_subcommand" -a prune -d "Remove old logs"
complete -c blq -n "__fish_use_subcommand" -a formats -d "List available log formats"
complete -c blq -n "__fish_use_subcommand" -a event -d "Show event details"
complete -c blq -n "__fish_use_subcommand" -a context -d "Show context around event"
complete -c blq -n "__fish_use_subcommand" -a commands -d "List registered commands"
complete -c blq -n "__fish_use_subcommand" -a register -d "Register a command"
complete -c blq -n "__fish_use_subcommand" -a unregister -d "Remove a registered command"
complete -c blq -n "__fish_use_subcommand" -a sync -d "Sync logs to central location"
complete -c blq -n "__fish_use_subcommand" -a query -d "Query log files"
complete -c blq -n "__fish_use_subcommand" -a q -d "Query log files (alias)"
complete -c blq -n "__fish_use_subcommand" -a filter -d "Filter with simple syntax"
complete -c blq -n "__fish_use_subcommand" -a f -d "Filter with simple syntax (alias)"
complete -c blq -n "__fish_use_subcommand" -a serve -d "Start MCP server"
complete -c blq -n "__fish_use_subcommand" -a completions -d "Generate shell completions"

# Global options
complete -c blq -s V -l version -d "Show version"
complete -c blq -s F -l log-format -d "Log format for parsing"
complete -c blq -s g -l global -d "Query global store"
complete -c blq -s d -l database -d "Database path"

# completions subcommand
complete -c blq -n "__fish_seen_subcommand_from completions" -a "bash zsh fish" -d "Shell type"

# exec options
complete -c blq -n "__fish_seen_subcommand_from exec e" -s n -l name -d "Source name"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s f -l format -d "Parse format"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s r -l keep-raw -d "Keep raw output"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s j -l json -d "JSON output"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s q -l quiet -d "Quiet mode"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s s -l summary -d "Show summary"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s v -l verbose -d "Verbose mode"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s N -l no-capture -d "Skip capture"

# errors/warnings options
complete -c blq -n "__fish_seen_subcommand_from errors warnings" \\
    -s s -l source -d "Filter by source"
complete -c blq -n "__fish_seen_subcommand_from errors warnings" -s n -l limit -d "Max results"
complete -c blq -n "__fish_seen_subcommand_from errors warnings" -s c -l compact -d "Compact format"
complete -c blq -n "__fish_seen_subcommand_from errors warnings" -s j -l json -d "JSON output"

# import - complete log files
complete -c blq -n "__fish_seen_subcommand_from import" -F -d "Log file"

# query/filter - complete files
complete -c blq -n "__fish_seen_subcommand_from query q filter f" -F -d "Log file"
"""
