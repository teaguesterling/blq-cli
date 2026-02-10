"""
MCP server for blq.

Provides tools, resources, and prompts for AI agent integration.

Usage:
    blq mcp serve                    # stdio transport (for Claude Desktop)
    blq mcp serve --transport sse    # SSE transport (for HTTP clients)
    blq mcp serve --safe-mode        # Disable state-modifying tools
    blq mcp serve -D exec,clean      # Disable specific tools

Security:
    Tools can be disabled via CLI flags:
        blq mcp serve --safe-mode    # Disables exec, clean, register_command, unregister_command
        blq mcp serve -D exec,clean  # Disable specific tools

    Or via .lq/config.yaml:
        mcp:
          disabled_tools:
            - exec
            - clean
            - register_command
            - unregister_command

    Or via environment variable:
        BLQ_MCP_DISABLED_TOOLS=exec,clean,register_command
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
from fastmcp import FastMCP

from blq.commands.query_cmd import parse_filter_expression
from blq.output import format_context
from blq.storage import BlqStorage

# ============================================================================
# Security Configuration
# ============================================================================

# Tools that modify state and can be disabled for security (--safe-mode / -S)
# These are the tools disabled when running in safe mode
SAFE_MODE_DISABLED_TOOLS = {
    "exec",               # Can run arbitrary commands
    "clean",              # Can delete data
    "register_command",   # Can modify command registry
    "unregister_command", # Can modify command registry
}

# Cache for disabled tools (loaded once at startup)
_disabled_tools: set[str] | None = None


def _init_disabled_tools(
    cli_disabled: str | None = None,
    safe_mode: bool = False,
) -> None:
    """Initialize disabled tools from CLI arguments.

    This must be called before any tools are invoked, typically at server startup.

    Args:
        cli_disabled: Comma-separated list of tools to disable from --disabled-tools
        safe_mode: If True, disable all tools in SAFE_MODE_DISABLED_TOOLS
    """
    global _disabled_tools

    disabled: set[str] = set()

    # Add safe mode tools first
    if safe_mode:
        disabled.update(SAFE_MODE_DISABLED_TOOLS)

    # Add CLI-specified tools
    if cli_disabled:
        disabled.update(t.strip() for t in cli_disabled.split(",") if t.strip())

    # Check environment variable
    env_disabled = os.environ.get("BLQ_MCP_DISABLED_TOOLS", "")
    if env_disabled:
        disabled.update(t.strip() for t in env_disabled.split(",") if t.strip())

    # Check .lq/config.yaml
    try:
        from blq.cli import BlqConfig
        config = BlqConfig.find()
        if config and hasattr(config, "mcp_config"):
            mcp_config = config.mcp_config or {}
            disabled_list = mcp_config.get("disabled_tools", [])
            if isinstance(disabled_list, list):
                disabled.update(disabled_list)
    except Exception:
        pass

    _disabled_tools = disabled


def _load_disabled_tools() -> set[str]:
    """Get the set of disabled tools.

    If _init_disabled_tools() hasn't been called, loads from config/environment only.
    """
    global _disabled_tools
    if _disabled_tools is not None:
        return _disabled_tools

    # Fallback: initialize without CLI args
    _init_disabled_tools()
    return _disabled_tools or set()


def _check_tool_enabled(tool_name: str) -> None:
    """Check if a tool is enabled. Raises error if disabled."""
    disabled = _load_disabled_tools()
    if tool_name in disabled:
        raise PermissionError(
            f"Tool '{tool_name}' is disabled. "
            f"Enable it by removing from mcp.disabled_tools in .lq/config.yaml "
            f"or BLQ_MCP_DISABLED_TOOLS environment variable."
        )


def _to_json_safe(value: Any) -> Any:
    """Convert pandas NA/NaT values to None and UUID to string for JSON serialization."""
    if pd.isna(value):
        return None
    # Handle UUID objects
    if hasattr(value, "hex") and hasattr(value, "int"):
        return str(value)
    return value


def _safe_int(value: Any) -> int | None:
    """Safely convert a value to int, returning None for NA/null values."""
    if pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compute_status(error_count: int, warning_count: int, exit_code: int | None) -> str:
    """Compute run status from counts and exit code."""
    if exit_code == -1:
        return "TIMEOUT"
    if error_count > 0:
        return "FAIL"
    if exit_code is not None and exit_code != 0:
        return "FAIL"
    if warning_count > 0:
        return "WARN"
    return "OK"


# Create the MCP server
mcp = FastMCP(
    "blq",
    instructions=(
        "Build Log Query - capture and query build/test logs. "
        "Use tools to run builds, query errors, and analyze results. "
        "Read blq://guide for detailed usage instructions. "
        "The database is shared with the CLI - users can run 'blq run build' "
        "and you can query the results, or vice versa. "
        "Start with status() or commands() to see current state."
        "Use errors(), event(ref), and context(ref) to drill down into issues. "
        "Docs: https://blq-cli.readthedocs.io/en/latest/"
    ),
)


def _get_storage() -> BlqStorage:
    """Get BlqStorage for current directory."""
    return BlqStorage.open()


def _parse_ref(ref: str) -> tuple[str | None, int, int]:
    """Parse event reference into (tag, run_serial, event_id).

    Formats:
    - "tag:serial:event" -> (tag, serial, event)
    - "serial:event" -> (None, serial, event)

    Returns:
        Tuple of (tag or None, run_serial, event_id)
    """
    parts = ref.split(":")
    if len(parts) == 2:
        # Format: "serial:event"
        return None, int(parts[0]), int(parts[1])
    elif len(parts) == 3:
        # Format: "tag:serial:event"
        return parts[0], int(parts[1]), int(parts[2])
    else:
        raise ValueError(
            f"Invalid ref format: {ref}. Expected 'serial:event' or 'tag:serial:event'"
        )


# ============================================================================
# Implementation Functions
# (Separated from decorators so they can be called from resources/prompts)
# ============================================================================


def _run_impl(
    command: str,
    args: dict[str, str] | list[str] | None = None,
    extra: list[str] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Implementation of run command (for registered commands).

    Args:
        command: Registered command name
        args: Either a dict of named arguments (recommended) or a list of CLI args
        extra: Passthrough arguments appended to command (when args is a dict)
        timeout: Command timeout in seconds
    """
    # Build command for blq run (registered commands only)
    cmd_parts = ["blq", "run", "--json", "--quiet"]
    if timeout:
        cmd_parts.extend(["--timeout", str(timeout)])
    cmd_parts.append(command)

    if args:
        if isinstance(args, dict):
            # Named arguments: convert to key=value format
            for key, value in args.items():
                cmd_parts.append(f"{key}={value}")
        else:
            # List of CLI args (backward compatible)
            cmd_parts.extend(args)

    # Add passthrough args after :: to ensure they're not parsed as placeholder values
    if extra:
        cmd_parts.append("::")
        cmd_parts.extend(extra)

    try:
        # Add buffer to subprocess timeout so CLI timeout fires first
        subprocess_timeout = timeout + 10 if timeout else None
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )

        # Parse JSON output and return concise response
        if result.stdout.strip():
            try:
                full_result = json.loads(result.stdout)
                # Build concise response with essential fields
                run_id = full_result.get("run_id")
                source_name = full_result.get("source_name") or command
                exit_code = full_result.get("exit_code", 0)
                summary = full_result.get("summary", {})
                errors = full_result.get("errors", [])
                has_errors = exit_code != 0 or len(errors) > 0

                concise: dict[str, Any] = {
                    "run_ref": f"{source_name}:{run_id}" if run_id else None,
                    "status": full_result.get("status"),
                    "exit_code": exit_code,
                    "summary": summary,
                }

                # Only include errors if there are any
                if errors:
                    concise["errors"] = errors

                # Include duration if > 5 seconds
                duration = full_result.get("duration_sec", 0)
                if duration > 5:
                    concise["duration_sec"] = round(duration, 1)

                # Include tail and output_stats for failures
                output_stats = full_result.get("output_stats", {})
                tail = output_stats.get("tail", [])
                total_lines = output_stats.get("lines", 0)

                if has_errors and tail:
                    concise["tail"] = tail
                    if total_lines > len(tail):
                        concise["output_stats"] = {
                            "lines": total_lines,
                            "bytes": output_stats.get("bytes", 0),
                        }

                return concise
            except json.JSONDecodeError:
                pass

        # Check if this was a "not registered" error
        if "is not a registered command" in result.stderr:
            return {
                "run_ref": None,
                "status": "FAIL",
                "exit_code": result.returncode,
                "error": f"'{command}' is not registered. Use exec() for ad-hoc commands.",
                "summary": {"total_events": 0, "errors": 0, "warnings": 0},
            }

        # Fallback: construct basic result
        return {
            "run_ref": None,
            "status": "FAIL" if result.returncode != 0 else "OK",
            "exit_code": result.returncode,
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
        }
    except subprocess.TimeoutExpired:
        return {
            "run_ref": None,
            "status": "FAIL",
            "exit_code": -1,
            "error": f"Command timed out after {timeout} seconds",
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
        }
    except Exception as e:
        return {
            "run_ref": None,
            "status": "FAIL",
            "exit_code": -1,
            "error": str(e),
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
        }


def _find_matching_registered_command(full_cmd: str) -> tuple[str, list[str]] | None:
    """Check if command matches a registered command prefix.

    Args:
        full_cmd: Full command string to check

    Returns:
        Tuple of (command_name, extra_args) if match found, None otherwise
    """
    try:
        from blq.cli import BlqConfig

        config = BlqConfig.find()
        if config is None:
            return None

        normalized_full = _normalize_cmd(full_cmd)

        for name, cmd in config.commands.items():
            normalized_registered = _normalize_cmd(cmd.cmd)

            # Check if full command starts with registered command
            if normalized_full.startswith(normalized_registered):
                # Extract extra args
                remainder = normalized_full[len(normalized_registered):].strip()
                if remainder:
                    extra_args = remainder.split()
                else:
                    extra_args = []
                return name, extra_args

        return None
    except Exception:
        return None


def _exec_impl(
    command: str,
    args: list[str] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Implementation of exec command (for ad-hoc shell commands).

    If the command matches a registered command prefix, uses run() instead
    for cleaner refs.
    """
    # Build full command string
    full_cmd = command
    if args:
        full_cmd = f"{command} {' '.join(args)}"

    # Check if this matches a registered command
    match = _find_matching_registered_command(full_cmd)
    if match:
        name, extra_args = match
        result = _run_impl(name, extra=extra_args if extra_args else None, timeout=timeout)
        result["matched_command"] = name
        if extra_args:
            result["extra_args"] = extra_args
        return result

    # No match - run as ad-hoc exec
    # Split command into parts since CLI uses REMAINDER parsing
    cmd_parts = ["blq", "exec", "--json", "--quiet"]
    if timeout:
        cmd_parts.extend(["--timeout", str(timeout)])
    cmd_parts.extend(shlex.split(command))
    if args:
        cmd_parts.extend(args)

    try:
        # Add buffer to subprocess timeout so CLI timeout fires first
        subprocess_timeout = timeout + 10 if timeout else None
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=subprocess_timeout,
        )

        # Parse JSON output and return concise response
        if result.stdout.strip():
            try:
                full_result = json.loads(result.stdout)
                # Build concise response with essential fields
                run_id = full_result.get("run_id")
                source_name = full_result.get("source_name") or "exec"
                exit_code = full_result.get("exit_code", 0)
                summary = full_result.get("summary", {})
                errors = full_result.get("errors", [])
                has_errors = exit_code != 0 or len(errors) > 0

                concise: dict[str, Any] = {
                    "run_ref": f"{source_name}:{run_id}" if run_id else None,
                    "status": full_result.get("status"),
                    "exit_code": exit_code,
                    "summary": summary,
                }

                # Only include errors if there are any
                if errors:
                    concise["errors"] = errors

                # Include duration if > 5 seconds
                duration = full_result.get("duration_sec", 0)
                if duration > 5:
                    concise["duration_sec"] = round(duration, 1)

                # Include tail and output_stats for failures
                output_stats = full_result.get("output_stats", {})
                tail = output_stats.get("tail", [])
                total_lines = output_stats.get("lines", 0)

                if has_errors and tail:
                    concise["tail"] = tail
                    if total_lines > len(tail):
                        concise["output_stats"] = {
                            "lines": total_lines,
                            "bytes": output_stats.get("bytes", 0),
                        }

                return concise
            except json.JSONDecodeError:
                pass

        # Fallback: construct basic result
        return {
            "run_ref": None,
            "status": "FAIL" if result.returncode != 0 else "OK",
            "exit_code": result.returncode,
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
        }
    except subprocess.TimeoutExpired:
        return {
            "run_ref": None,
            "status": "FAIL",
            "exit_code": -1,
            "error": f"Command timed out after {timeout} seconds",
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
        }
    except Exception as e:
        return {
            "run_ref": None,
            "status": "FAIL",
            "exit_code": -1,
            "error": str(e),
            "summary": {"total_events": 0, "errors": 0, "warnings": 0},
            "errors": [],
        }


def _query_impl(
    sql: str | None = None,
    filter: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Implementation of query command.

    Args:
        sql: Raw SQL query
        filter: Simple filter expressions (e.g., "severity=error", "ref_file~test")
        limit: Max rows to return
    """
    try:
        store = _get_storage()
        conn = store.connection

        # Build SQL from filter expressions if provided
        if filter and not sql:
            # Parse filter expressions (comma or space separated)
            expressions = []
            for expr in filter.replace(",", " ").split():
                expr = expr.strip()
                if expr:
                    try:
                        expressions.append(parse_filter_expression(expr))
                    except ValueError as e:
                        return {"columns": [], "rows": [], "row_count": 0, "error": str(e)}

            if expressions:
                where_clause = " AND ".join(expressions)
                sql = f"SELECT * FROM blq_load_events() WHERE {where_clause}"
            else:
                sql = "SELECT * FROM blq_load_events()"

        if not sql:
            return {
                "columns": [], "rows": [], "row_count": 0,
                "error": "Either sql or filter must be provided",
            }

        # Add LIMIT if not present (basic safety)
        sql_upper = sql.upper()
        if "LIMIT" not in sql_upper:
            sql = f"SELECT * FROM ({sql}) LIMIT {limit}"

        result = conn.sql(sql)
        columns = result.columns
        rows = result.fetchall()

        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
        }
    except FileNotFoundError:
        return {"columns": [], "rows": [], "row_count": 0, "error": "No lq repository found"}
    except Exception as e:
        return {"columns": [], "rows": [], "row_count": 0, "error": str(e)}


def _errors_impl(
    limit: int = 20,
    run_id: int | None = None,
    source: str | None = None,
    file_pattern: str | None = None,
) -> dict[str, Any]:
    """Implementation of errors command."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"errors": [], "total_count": 0}

        # Build WHERE conditions
        conditions = ["severity = 'error'"]
        if run_id is not None:
            conditions.append(f"run_serial = {run_id}")
        if source:
            conditions.append(f"source_name = '{source}'")
        if file_pattern:
            conditions.append(f"ref_file LIKE '{file_pattern}'")

        where = " AND ".join(conditions)

        # Get total count
        count_result = storage.sql(
            f"SELECT COUNT(*) FROM blq_load_events() WHERE {where}"
        ).fetchone()
        total_count = count_result[0] if count_result else 0

        # Get errors - use ref column from view
        df = storage.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE {where}
            ORDER BY run_serial DESC, event_id
            LIMIT {limit}
        """).df()

        error_list = []
        for _, row in df.iterrows():
            error_list.append(
                {
                    "ref": _to_json_safe(row.get("ref")),
                    "run_ref": _to_json_safe(row.get("run_ref")),
                    "ref_file": _to_json_safe(row.get("ref_file")),
                    "ref_line": _safe_int(row.get("ref_line")),
                    "ref_column": _safe_int(row.get("ref_column")),
                    "message": _to_json_safe(row.get("message")),
                    "tool_name": _to_json_safe(row.get("tool_name")),
                    "category": _to_json_safe(row.get("category")),
                }
            )

        return {"errors": error_list, "total_count": total_count}
    except FileNotFoundError:
        return {"errors": [], "total_count": 0}


def _warnings_impl(
    limit: int = 20,
    run_id: int | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Implementation of warnings command."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"warnings": [], "total_count": 0}

        # Build WHERE conditions
        conditions = ["severity = 'warning'"]
        if run_id is not None:
            conditions.append(f"run_serial = {run_id}")
        if source:
            conditions.append(f"source_name = '{source}'")

        where = " AND ".join(conditions)

        # Get total count
        count_result = storage.sql(
            f"SELECT COUNT(*) FROM blq_load_events() WHERE {where}"
        ).fetchone()
        total_count = count_result[0] if count_result else 0

        # Get warnings - use ref column from view
        df = storage.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE {where}
            ORDER BY run_serial DESC, event_id
            LIMIT {limit}
        """).df()

        warning_list = []
        for _, row in df.iterrows():
            warning_list.append(
                {
                    "ref": _to_json_safe(row.get("ref")),
                    "run_ref": _to_json_safe(row.get("run_ref")),
                    "ref_file": _to_json_safe(row.get("ref_file")),
                    "ref_line": _safe_int(row.get("ref_line")),
                    "ref_column": _safe_int(row.get("ref_column")),
                    "message": _to_json_safe(row.get("message")),
                    "tool_name": _to_json_safe(row.get("tool_name")),
                    "category": _to_json_safe(row.get("category")),
                }
            )

        return {"warnings": warning_list, "total_count": total_count}
    except FileNotFoundError:
        return {"warnings": [], "total_count": 0}


def _events_impl(
    limit: int = 20,
    run_id: int | None = None,
    source: str | None = None,
    severity: str | None = None,
    file_pattern: str | None = None,
) -> dict[str, Any]:
    """Implementation of events command."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"events": [], "total_count": 0}

        # Build WHERE conditions
        conditions: list[str] = []
        if run_id is not None:
            conditions.append(f"run_serial = {run_id}")
        if source:
            conditions.append(f"source_name = '{source}'")
        if file_pattern:
            conditions.append(f"ref_file LIKE '{file_pattern}'")

        # Severity filter (can be single value or comma-separated list)
        if severity:
            if "," in severity:
                severities = [s.strip() for s in severity.split(",")]
                severity_list = ", ".join(f"'{s}'" for s in severities)
                conditions.append(f"severity IN ({severity_list})")
            else:
                conditions.append(f"severity = '{severity}'")

        where = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        count_result = storage.sql(
            f"SELECT COUNT(*) FROM blq_load_events() WHERE {where}"
        ).fetchone()
        total_count = count_result[0] if count_result else 0

        # Get events - use ref column from view
        df = storage.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE {where}
            ORDER BY run_serial DESC, event_id
            LIMIT {limit}
        """).df()

        event_list = []
        for _, row in df.iterrows():
            event_list.append(
                {
                    "ref": _to_json_safe(row.get("ref")),
                    "run_ref": _to_json_safe(row.get("run_ref")),
                    "severity": _to_json_safe(row.get("severity")),
                    "ref_file": _to_json_safe(row.get("ref_file")),
                    "ref_line": _safe_int(row.get("ref_line")),
                    "ref_column": _safe_int(row.get("ref_column")),
                    "message": _to_json_safe(row.get("message")),
                    "tool_name": _to_json_safe(row.get("tool_name")),
                    "category": _to_json_safe(row.get("category")),
                    "log_line": _safe_int(row.get("log_line_start")),
                }
            )

        return {"events": event_list, "total_count": total_count}
    except FileNotFoundError:
        return {"events": [], "total_count": 0}


def _event_impl(ref: str) -> dict[str, Any] | None:
    """Implementation of event command."""
    try:
        tag, run_serial, event_id = _parse_ref(ref)
        store = _get_storage()

        # Build query using run_serial and event_id
        if tag is not None:
            where = f"tag = '{tag}' AND run_serial = {run_serial} AND event_id = {event_id}"
        else:
            where = f"run_serial = {run_serial} AND event_id = {event_id}"

        result = store.sql(f"SELECT * FROM blq_load_events() WHERE {where}").fetchone()

        if result is None:
            return None

        columns = store.sql("SELECT * FROM blq_load_events() LIMIT 0").columns
        event_data = dict(zip(columns, result))

        # Environment is now stored as MAP, convert to dict if needed
        environment = event_data.get("environment")
        if environment is not None and not isinstance(environment, dict):
            # Handle legacy JSON format
            try:
                environment = json.loads(environment)
            except (json.JSONDecodeError, TypeError):
                environment = None

        return {
            "ref": _to_json_safe(event_data.get("ref")),
            "run_ref": _to_json_safe(event_data.get("run_ref")),
            "run_serial": run_serial,
            "event_id": event_id,
            "severity": event_data.get("severity"),
            "ref_file": event_data.get("ref_file"),
            "ref_line": event_data.get("ref_line"),
            "ref_column": event_data.get("ref_column"),
            "message": event_data.get("message"),
            "tool_name": event_data.get("tool_name"),
            "category": event_data.get("category"),
            "fingerprint": event_data.get("fingerprint"),
            "raw_text": event_data.get("raw_text"),
            "log_line_start": event_data.get("log_line_start"),
            "log_line_end": event_data.get("log_line_end"),
            # Execution context
            "cwd": event_data.get("cwd"),
            "executable_path": event_data.get("executable_path"),
            "environment": environment,
            # System context
            "hostname": event_data.get("hostname"),
            "platform": event_data.get("platform"),
            "arch": event_data.get("arch"),
            # Git context
            "git_commit": event_data.get("git_commit"),
            "git_branch": event_data.get("git_branch"),
            "git_dirty": event_data.get("git_dirty"),
            # CI context
            "ci": event_data.get("ci"),
        }
    except (ValueError, FileNotFoundError):
        return None


def _context_impl(ref: str, lines: int = 5) -> dict[str, Any]:
    """Implementation of context command.

    Returns formatted text showing log context around the event,
    matching the CLI output format.
    """
    try:
        tag, run_serial, event_id = _parse_ref(ref)
        storage = _get_storage()

        # Build query using run_serial and event_id
        if tag is not None:
            where = f"tag = '{tag}' AND run_serial = {run_serial} AND event_id = {event_id}"
        else:
            where = f"run_serial = {run_serial} AND event_id = {event_id}"

        result = storage.sql(f"SELECT * FROM blq_load_events() WHERE {where}").fetchone()

        if result is None:
            return {"error": f"Event {ref} not found"}

        columns = storage.sql("SELECT * FROM blq_load_events() LIMIT 0").columns
        event_data = dict(zip(columns, result))

        log_line_start = event_data.get("log_line_start")
        log_line_end = event_data.get("log_line_end") or log_line_start

        if log_line_start is None:
            # For structured formats without line info, return message
            source_name = event_data.get("source_name")
            message = event_data.get("message")
            return {
                "context": f"Event {ref} (from structured format, no log line context)\n"
                f"  Source: {source_name}\n"
                f"  Message: {message}",
            }

        # Get raw output for this run
        output_bytes = storage.get_output(run_serial)
        if output_bytes is None:
            return {"error": "Raw log not available for this run"}

        # Decode output
        try:
            content = output_bytes.decode("utf-8", errors="replace")
        except Exception:
            content = output_bytes.decode("latin-1")

        log_lines = content.splitlines()

        # Format using shared function
        formatted = format_context(
            log_lines,
            log_line_start,
            log_line_end,
            context=lines,
            ref=ref,
        )

        return {"context": formatted}
    except (ValueError, FileNotFoundError) as e:
        return {"error": f"Event not found: {e}"}


def _inspect_impl(ref: str, lines: int = 5) -> dict[str, Any]:
    """Implementation of inspect command.

    Returns comprehensive event details with both log context and source context.
    Source context is only included when source_lookup is enabled in config.

    Args:
        ref: Event reference in format "tag:serial:event" or "serial:event"
        lines: Lines of context before/after (default: 5)

    Returns:
        Event details with log_context and source_context fields
    """
    from blq.cli import BlqConfig
    from blq.output import read_source_context

    try:
        tag, run_serial, event_id = _parse_ref(ref)
        storage = _get_storage()

        # Build query using run_serial and event_id
        if tag is not None:
            where = f"tag = '{tag}' AND run_serial = {run_serial} AND event_id = {event_id}"
        else:
            where = f"run_serial = {run_serial} AND event_id = {event_id}"

        result = storage.sql(f"SELECT * FROM blq_load_events() WHERE {where}").fetchone()

        if result is None:
            return {"error": f"Event {ref} not found"}

        columns = storage.sql("SELECT * FROM blq_load_events() LIMIT 0").columns
        event_data = dict(zip(columns, result))

        # Build response
        response: dict[str, Any] = {
            "ref": _to_json_safe(event_data.get("ref")),
            "run_ref": _to_json_safe(event_data.get("run_ref")),
            "severity": _to_json_safe(event_data.get("severity")),
            "ref_file": _to_json_safe(event_data.get("ref_file")),
            "ref_line": _safe_int(event_data.get("ref_line")),
            "ref_column": _safe_int(event_data.get("ref_column")),
            "message": _to_json_safe(event_data.get("message")),
            "tool_name": _to_json_safe(event_data.get("tool_name")),
            "category": _to_json_safe(event_data.get("category")),
            "code": _to_json_safe(event_data.get("code") or event_data.get("rule")),
            "fingerprint": _to_json_safe(event_data.get("fingerprint")),
        }

        # Log context
        log_line_start = event_data.get("log_line_start")
        log_line_end = event_data.get("log_line_end") or log_line_start
        log_context = None

        if log_line_start is not None:
            output_bytes = storage.get_output(run_serial)
            if output_bytes is not None:
                try:
                    content = output_bytes.decode("utf-8", errors="replace")
                except Exception:
                    content = output_bytes.decode("latin-1")
                log_lines = content.splitlines()
                log_context = format_context(
                    log_lines,
                    log_line_start,
                    log_line_end,
                    context=lines,
                    header=f"Line {log_line_start}",
                )

        response["log_context"] = log_context

        # Source context (if enabled)
        source_context = None
        config = BlqConfig.find()
        if config is not None and config.source_lookup_enabled:
            ref_file = event_data.get("ref_file")
            ref_line = event_data.get("ref_line")
            if ref_file and ref_line:
                source_context = read_source_context(
                    ref_file,
                    ref_line,
                    ref_root=config.ref_root,
                    context=lines,
                )

        response["source_context"] = source_context

        return response
    except (ValueError, FileNotFoundError) as e:
        return {"error": f"Event not found: {e}"}


def _output_impl(
    run_id: int,
    stream: str | None = None,
    tail: int | None = None,
    head: int | None = None,
) -> dict[str, Any]:
    """Implementation of output command - get raw output for a run.

    Args:
        run_id: Run serial number
        stream: Stream name ('stdout', 'stderr', 'combined') or None for any
        tail: Return only last N lines
        head: Return only first N lines

    Returns:
        Output content and metadata
    """
    try:
        storage = _get_storage()

        # Get output info first
        info = storage.get_output_info(run_id)
        if not info:
            return {
                "run_id": run_id,
                "error": "No output found for this run",
                "streams": [],
            }

        # Get the raw output
        output_bytes = storage.get_output(run_id, stream)
        if output_bytes is None:
            return {
                "run_id": run_id,
                "error": "Output content not available",
                "streams": [s["stream"] for s in info],
            }

        # Decode and optionally truncate
        try:
            content = output_bytes.decode("utf-8", errors="replace")
        except Exception:
            content = output_bytes.decode("latin-1")

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # Apply head/tail
        if tail is not None and tail > 0:
            lines = lines[-tail:]
        elif head is not None and head > 0:
            lines = lines[:head]

        content = "".join(lines)

        return {
            "run_id": run_id,
            "stream": stream or info[0]["stream"] if info else "combined",
            "byte_length": len(output_bytes),
            "total_lines": total_lines,
            "returned_lines": len(lines),
            "content": content,
            "streams": [s["stream"] for s in info],
        }
    except FileNotFoundError:
        return {"run_id": run_id, "error": "No lq repository found", "streams": []}
    except Exception as e:
        return {"run_id": run_id, "error": str(e), "streams": []}


def _status_impl() -> dict[str, Any]:
    """Implementation of status command."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"sources": []}

        # Get status for each source (blq_load_runs includes error_count/warning_count)
        runs_df = storage.runs().df()
        sources = []

        for _, row in runs_df.iterrows():
            error_count = _safe_int(row.get("error_count")) or 0
            warning_count = _safe_int(row.get("warning_count")) or 0

            if error_count > 0:
                status_str = "FAIL"
            elif warning_count > 0:
                status_str = "WARN"
            else:
                status_str = "OK"

            # Build run_ref from tag and run_id (serial number from blq_load_runs)
            tag = _to_json_safe(row.get("tag"))
            run_serial = _safe_int(row.get("run_id")) or 0
            if tag:
                run_ref = f"{tag}:{run_serial}"
            else:
                run_ref = str(run_serial)

            sources.append(
                {
                    "name": _to_json_safe(row.get("source_name")) or "unknown",
                    "status": status_str,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "last_run": str(row.get("started_at", "")),
                    "run_ref": run_ref,
                    "run_serial": run_serial,
                }
            )

        return {"sources": sources}
    except FileNotFoundError:
        return {"sources": []}


def _info_impl(ref: str) -> dict[str, Any]:
    """Implementation of info command - get detailed run info."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"error": "No data available"}

        # Check if it's a UUID (invocation_id) or a run ref
        is_uuid = len(ref) == 36 and ref.count("-") == 4

        if is_uuid:
            # Query by invocation_id
            df = storage.sql(f"""
                SELECT * FROM blq_load_runs()
                WHERE invocation_id = '{ref}'
            """).df()
        else:
            # Parse as run ref
            tag, run_serial, _ = _parse_ref(ref + ":0")  # Add dummy event_id
            if tag is not None:
                df = storage.sql(f"""
                    SELECT * FROM blq_load_runs()
                    WHERE tag = '{tag}' AND run_id = {run_serial}
                """).df()
            else:
                df = storage.sql(f"""
                    SELECT * FROM blq_load_runs()
                    WHERE run_id = {run_serial}
                """).df()

        if df.empty:
            return {"error": f"Run {ref} not found"}

        row = df.iloc[0]
        invocation_id = _to_json_safe(row.get("invocation_id"))

        # Build run_ref
        tag = _to_json_safe(row.get("tag"))
        run_serial = _safe_int(row.get("run_id")) or 0
        if tag:
            run_ref = f"{tag}:{run_serial}"
        else:
            run_ref = str(run_serial)

        # Get output details
        outputs = []
        if invocation_id:
            outputs_result = storage.sql(f"""
                SELECT stream, byte_length
                FROM outputs
                WHERE invocation_id = '{invocation_id}'
                ORDER BY stream
            """).fetchall()
            outputs = [
                {"stream": r[0], "bytes": r[1]}
                for r in outputs_result
            ]

        return {
            "run_ref": run_ref,
            "run_serial": run_serial,
            "invocation_id": invocation_id,
            "source_name": _to_json_safe(row.get("source_name")),
            "source_type": _to_json_safe(row.get("source_type")),
            "command": _to_json_safe(row.get("command")),
            "status": _compute_status(
                _safe_int(row.get("error_count")) or 0,
                _safe_int(row.get("warning_count")) or 0,
                _safe_int(row.get("exit_code")),
            ),
            "exit_code": _safe_int(row.get("exit_code")),
            "error_count": _safe_int(row.get("error_count")) or 0,
            "warning_count": _safe_int(row.get("warning_count")) or 0,
            "info_count": _safe_int(row.get("info_count")) or 0,
            "event_count": _safe_int(row.get("event_count")) or 0,
            "started_at": str(row.get("started_at", "")),
            "completed_at": str(row.get("completed_at", "")),
            "cwd": _to_json_safe(row.get("cwd")),
            "executable_path": _to_json_safe(row.get("executable_path")),
            "hostname": _to_json_safe(row.get("hostname")),
            "platform": _to_json_safe(row.get("platform")),
            "arch": _to_json_safe(row.get("arch")),
            "git_branch": _to_json_safe(row.get("git_branch")),
            "git_commit": _to_json_safe(row.get("git_commit")),
            "git_dirty": bool(row.get("git_dirty")),
            "outputs": outputs,
        }
    except FileNotFoundError:
        return {"error": "No lq repository found"}
    except Exception as e:
        return {"error": str(e)}


def _history_impl(limit: int = 20, source: str | None = None) -> dict[str, Any]:
    """Implementation of history command."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"runs": []}

        # Build query with optional source filter
        if source:
            runs_df = storage.sql(f"""
                SELECT * FROM blq_load_runs()
                WHERE source_name = '{source}'
                ORDER BY run_id DESC
                LIMIT {limit}
            """).df()
        else:
            runs_df = storage.runs(limit=limit).df()

        runs = []

        for _, row in runs_df.iterrows():
            error_count = _safe_int(row.get("error_count")) or 0
            warning_count = _safe_int(row.get("warning_count")) or 0

            if error_count > 0:
                status_str = "FAIL"
            elif warning_count > 0:
                status_str = "WARN"
            else:
                status_str = "OK"

            # Build run_ref from tag and run_id (serial number from blq_load_runs)
            tag = _to_json_safe(row.get("tag"))
            run_serial = _safe_int(row.get("run_id")) or 0
            if tag:
                run_ref = f"{tag}:{run_serial}"
            else:
                run_ref = str(run_serial)

            runs.append(
                {
                    "run_ref": run_ref,
                    "run_serial": run_serial,
                    "source_name": _to_json_safe(row.get("source_name")) or "unknown",
                    "status": status_str,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "started_at": str(row.get("started_at", "")),
                    "exit_code": _safe_int(row.get("exit_code")),
                    "command": _to_json_safe(row.get("command")),
                    "cwd": _to_json_safe(row.get("cwd")),
                    "executable_path": _to_json_safe(row.get("executable_path")),
                    "hostname": _to_json_safe(row.get("hostname")),
                    "platform": _to_json_safe(row.get("platform")),
                    "arch": _to_json_safe(row.get("arch")),
                    "git_commit": _to_json_safe(row.get("git_commit")),
                    "git_branch": _to_json_safe(row.get("git_branch")),
                    "git_dirty": _to_json_safe(row.get("git_dirty")),
                    "ci": _to_json_safe(row.get("ci")),
                }
            )
        return {"runs": runs}
    except FileNotFoundError:
        return {"runs": []}


def _diff_impl(run1: int, run2: int) -> dict[str, Any]:
    """Implementation of diff command.

    Args:
        run1: First run serial number (baseline)
        run2: Second run serial number (comparison)
    """
    try:
        storage = _get_storage()

        # Get errors from each run using run_serial
        errors1 = storage.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE severity = 'error' AND run_serial = {run1}
            LIMIT 1000
        """).df()
        errors2 = storage.sql(f"""
            SELECT * FROM blq_load_events()
            WHERE severity = 'error' AND run_serial = {run2}
            LIMIT 1000
        """).df()

        # Use fingerprints for comparison if available, else use file+line+message
        def get_error_key(row):
            fp = row.get("fingerprint")
            if fp:
                return fp
            return f"{row.get('ref_file')}:{row.get('ref_line')}:{row.get('message', '')[:50]}"

        keys1 = set(get_error_key(row) for _, row in errors1.iterrows())
        keys2 = set(get_error_key(row) for _, row in errors2.iterrows())

        fixed_keys = keys1 - keys2
        new_keys = keys2 - keys1
        unchanged_keys = keys1 & keys2

        # Build fixed and new error lists
        fixed = []
        for _, row in errors1.iterrows():
            if get_error_key(row) in fixed_keys:
                fixed.append(
                    {
                        "ref_file": row.get("ref_file"),
                        "message": row.get("message"),
                    }
                )

        new_errors = []
        for _, row in errors2.iterrows():
            if get_error_key(row) in new_keys:
                new_errors.append(
                    {
                        "ref": _to_json_safe(row.get("ref")),
                        "ref_file": row.get("ref_file"),
                        "ref_line": row.get("ref_line"),
                        "message": row.get("message"),
                    }
                )

        return {
            "summary": {
                "run1_errors": len(errors1),
                "run2_errors": len(errors2),
                "fixed": len(fixed_keys),
                "new": len(new_keys),
                "unchanged": len(unchanged_keys),
            },
            "fixed": fixed,
            "new": new_errors,
        }
    except FileNotFoundError:
        return {
            "summary": {"run1_errors": 0, "run2_errors": 0, "fixed": 0, "new": 0, "unchanged": 0},
            "fixed": [],
            "new": [],
            "error": "No lq repository found",
        }


def _normalize_cmd(cmd: str) -> str:
    """Normalize command string for comparison (collapse whitespace)."""
    return " ".join(cmd.split())


def _register_command_impl(
    name: str,
    cmd: str,
    description: str = "",
    timeout: int = 300,
    capture: bool = True,
    force: bool = False,
    format: str | None = None,
    run_now: bool = False,
) -> dict[str, Any]:
    """Implementation of register_command."""
    try:
        from blq.cli import BlqConfig, RegisteredCommand
        from blq.commands.core import detect_format_from_command

        config = BlqConfig.find()

        if config is None:
            return {"success": False, "error": "No lq repository found. Run 'blq init' first."}

        commands = config.commands
        normalized_cmd = _normalize_cmd(cmd)

        # Check for existing command with same name
        if name in commands and not force:
            existing = commands[name]
            existing_normalized = _normalize_cmd(existing.cmd)

            if existing_normalized == normalized_cmd:
                # Same command, just use it
                result: dict[str, Any] = {
                    "success": True,
                    "message": f"Using existing command '{name}' (identical)",
                    "existing": True,
                    "command": {
                        "name": name,
                        "cmd": existing.cmd,
                        "description": existing.description,
                        "timeout": existing.timeout,
                        "capture": existing.capture,
                        "format": existing.format,
                    },
                }
                if run_now:
                    run_result = _run_impl(name, timeout=timeout)
                    result["run"] = run_result
                return result
            else:
                # Different command with same name
                return {
                    "success": False,
                    "error": (
                        f"Command '{name}' already exists with different command. "
                        f"Existing: '{existing.cmd}'. Use force=true to overwrite."
                    ),
                }

        # Check for existing command with same cmd but different name
        for existing_name, existing in commands.items():
            if _normalize_cmd(existing.cmd) == normalized_cmd and not force:
                result = {
                    "success": True,
                    "message": f"Using existing command '{existing_name}' (same command)",
                    "existing": True,
                    "matched_name": existing_name,
                    "command": {
                        "name": existing_name,
                        "cmd": existing.cmd,
                        "description": existing.description,
                        "timeout": existing.timeout,
                        "capture": existing.capture,
                        "format": existing.format,
                    },
                }
                if run_now:
                    run_result = _run_impl(existing_name, timeout=timeout)
                    result["run"] = run_result
                return result

        # Auto-detect format if not specified
        if format is None:
            format = detect_format_from_command(cmd)

        commands[name] = RegisteredCommand(
            name=name,
            cmd=cmd,
            description=description,
            timeout=timeout,
            capture=capture,
            format=format,
        )
        config.save_commands()

        result = {
            "success": True,
            "message": f"Registered command '{name}': {cmd}",
            "existing": False,
            "command": {
                "name": name,
                "cmd": cmd,
                "description": description,
                "timeout": timeout,
                "capture": capture,
                "format": format,
            },
        }
        if run_now:
            run_result = _run_impl(name, timeout=timeout)
            result["run"] = run_result
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def _unregister_command_impl(name: str) -> dict[str, Any]:
    """Implementation of unregister_command."""
    try:
        from blq.cli import BlqConfig

        config = BlqConfig.find()

        if config is None:
            return {"success": False, "error": "No lq repository found."}

        commands = config.commands

        if name not in commands:
            return {"success": False, "error": f"Command '{name}' not found."}

        del commands[name]
        config.save_commands()

        return {"success": True, "message": f"Unregistered command '{name}'"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _commands_impl() -> dict[str, Any]:
    """Implementation of commands listing."""
    try:
        from blq.cli import BlqConfig

        config = BlqConfig.find()

        if config is None:
            return {"commands": []}

        commands = config.commands

        return {
            "commands": [
                {
                    "name": name,
                    "cmd": cmd.cmd,
                    "description": cmd.description,
                    "timeout": cmd.timeout,
                    "capture": cmd.capture,
                    "format": cmd.format,
                }
                for name, cmd in commands.items()
            ]
        }
    except Exception as e:
        return {"commands": [], "error": str(e)}


# ============================================================================
# Tools (thin wrappers around implementations)
# ============================================================================


@mcp.tool()
def run(
    command: str,
    args: dict[str, str] | list[str] | None = None,
    extra: list[str] | None = None,
    timeout: int = 300,
    # Batch mode parameters
    commands: list[str] | None = None,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    """Run a registered command and capture its output.

    Can run a single command or multiple commands in sequence (batch mode).

    Args:
        command: Registered command name (use the exec tool for ad-hoc commands)
        args: Command arguments - either a dict of named args (recommended)
              or a list of CLI args for backward compatibility
        extra: Passthrough arguments appended to command
        timeout: Timeout in seconds (default: 300)
        commands: List of command names for batch mode (overrides `command`)
        stop_on_failure: In batch mode, stop after first failure (default: true)

    Returns:
        Run result with status, errors, and warnings.
        In batch mode, returns results for each command with overall status.
    """
    # Batch mode: run multiple commands in sequence
    if commands is not None:
        results = []
        overall_status = "OK"

        for cmd in commands:
            result = _run_impl(cmd, timeout=timeout)
            results.append({"command": cmd, "result": result})

            if result.get("status") == "FAIL":
                overall_status = "FAIL"
                if stop_on_failure:
                    break
            elif result.get("status") == "WARN" and overall_status == "OK":
                overall_status = "WARN"

        return {
            "status": overall_status,
            "results": results,
            "commands_run": len(results),
            "commands_requested": len(commands),
        }

    # Single command mode
    return _run_impl(command, args, extra, timeout)


@mcp.tool()
def exec(
    command: str,
    args: list[str] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Execute an ad-hoc shell command and capture its output.

    If the command matches a registered command prefix, automatically uses
    run() instead for cleaner refs. For example, if 'test' is registered as
    'pytest tests/', then exec('pytest tests/ -v') will run as
    run(command='test', extra=['-v']).

    Note: This tool can be disabled via mcp.disabled_tools config.

    Args:
        command: Shell command to run
        args: Additional arguments to append
        timeout: Timeout in seconds (default: 300)

    Returns:
        Run result with status, errors, and warnings. If a registered command
        was matched, includes 'matched_command' and optionally 'extra_args'.
    """
    _check_tool_enabled("exec")
    return _exec_impl(command, args, timeout)


@mcp.tool()
def query(
    sql: str | None = None,
    filter: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Query stored log events with SQL or simple filter expressions.

    Use either `sql` for raw SQL queries or `filter` for simple expressions.

    Filter syntax:
        key=value      -> exact match (key = 'value')
        key=v1,v2      -> multiple values (key IN ('v1', 'v2'))
        key~pattern    -> contains (key ILIKE '%pattern%')
        key!=value     -> not equal (key != 'value')

    Multiple filters are AND'd together (space or comma separated).

    Args:
        sql: SQL query against blq_load_events() or other blq macros
        filter: Simple filter expressions (e.g., "severity=error ref_file~test")
        limit: Max rows to return (default: 100)

    Returns:
        Query results with columns, rows, and row_count

    Examples:
        query(sql="SELECT * FROM blq_load_events() WHERE severity = 'error'")
        query(filter="severity=error")
        query(filter="severity=error,warning ref_file~test")
        query(filter="tool_name=pytest", limit=50)
    """
    return _query_impl(sql=sql, filter=filter, limit=limit)


@mcp.tool()
def events(
    limit: int = 20,
    run_id: int | None = None,
    source: str | None = None,
    severity: str | None = None,
    file_pattern: str | None = None,
    # Batch mode
    run_ids: list[int] | None = None,
    limit_per_run: int = 10,
) -> dict[str, Any]:
    """Get events with optional severity filter.

    For errors only: use severity="error"
    For warnings only: use severity="warning"

    Args:
        limit: Max events to return (default: 20)
        run_id: Filter to specific run (by serial number, e.g., 1, 2, 3)
        source: Filter to specific source name
        severity: Filter by severity. Can be a single value (error, warning, info)
                  or comma-separated list (e.g., "error,warning")
        file_pattern: Filter by file path pattern (SQL LIKE)
        run_ids: List of run IDs for batch mode (returns events grouped by run)
        limit_per_run: Max events per run in batch mode (default: 10)

    Returns:
        Events list with total count. Each event includes a 'ref' field
        in format "tag:serial:event" or "serial:event".
        In batch mode, returns events grouped by run_id.
    """
    # Batch mode: get events from multiple runs
    if run_ids:
        runs = []
        total_events = 0

        for rid in run_ids:
            result = _events_impl(
                limit=limit_per_run,
                run_id=rid,
                source=source,
                severity=severity,
                file_pattern=file_pattern,
            )
            event_count = len(result.get("events", []))
            total_events += event_count
            runs.append({
                "run_id": rid,
                "event_count": event_count,
                "events": result.get("events", []),
            })

        return {
            "runs": runs,
            "total_events": total_events,
            "run_count": len(run_ids),
        }

    # Single run/all runs mode
    return _events_impl(limit, run_id, source, severity, file_pattern)


@mcp.tool()
def inspect(
    ref: str,
    lines: int = 5,
    include_log_context: bool = True,
    include_source_context: bool = True,
    # Batch mode
    refs: list[str] | None = None,
) -> dict[str, Any]:
    """Get comprehensive event details with context.

    Returns event details with optional log context (where the error appears
    in command output) and source context (where it is in source files).

    For basic event details only, use include_log_context=False and
    include_source_context=False.

    Args:
        ref: Event reference in format "tag:serial:event" (e.g., "build:1:3")
             or "serial:event" (e.g., "1:3")
        lines: Lines of context before/after (default: 5)
        include_log_context: Include surrounding log lines (default: true)
        include_source_context: Include source file context (default: true)
        refs: List of event references for batch mode (overrides `ref`)

    Returns:
        Event details with ref, severity, ref_file, ref_line, ref_column,
        message, tool_name, category, code, fingerprint, log_context,
        and source_context (based on include_* flags).
        In batch mode, returns list of event details.
    """
    # Batch mode: get details for multiple events
    if refs:
        events_list = []
        found = 0

        for r in refs:
            result = _inspect_impl(r, lines)
            if "error" not in result:
                found += 1
                # Optionally strip context based on flags
                if not include_log_context:
                    result.pop("log_context", None)
                if not include_source_context:
                    result.pop("source_context", None)
                events_list.append({"ref": r, "event": result})
            else:
                events_list.append({"ref": r, "event": None, "error": result.get("error")})

        return {
            "events": events_list,
            "found": found,
            "total": len(refs),
        }

    # Single event mode
    result = _inspect_impl(ref, lines)

    # Strip context if not requested
    if not include_log_context:
        result.pop("log_context", None)
    if not include_source_context:
        result.pop("source_context", None)

    return result


@mcp.tool()
def output(
    run_id: int,
    stream: str | None = None,
    tail: int | None = None,
    head: int | None = None,
) -> dict[str, Any]:
    """Get raw output for a run.

    Retrieves the captured stdout/stderr from a command execution.
    Use tail or head to limit output size for large logs.

    Args:
        run_id: Run serial number (e.g., 1, 2, 3)
        stream: Stream name ('stdout', 'stderr', 'combined') or None for default
        tail: Return only last N lines
        head: Return only first N lines

    Returns:
        Output content and metadata including byte_length, total_lines, etc.
    """
    return _output_impl(run_id, stream, tail, head)


@mcp.tool()
def status() -> dict[str, Any]:
    """Get current status summary of all sources.

    Returns:
        Status summary with sources list
    """
    return _status_impl()


@mcp.tool()
def info(
    ref: str | None = None,
    head: int | None = None,
    tail: int | None = None,
    errors: bool = False,
    warnings: bool = False,
    severity: str | None = None,
    limit: int = 20,
    context: int | None = None,
) -> dict[str, Any]:
    """Get detailed information about a specific run.

    If ref is not provided, returns info about the most recent run.

    Args:
        ref: Run reference (e.g., 'test:5') or invocation_id (UUID).
             If not provided, uses the most recent run.
        head: Return first N lines of output
        tail: Return last N lines of output
        errors: Include error events
        warnings: Include warning events
        severity: Filter events by severity (e.g., 'error', 'error,warning')
        limit: Max events to return (default: 20)
        context: Show N lines of log context around each event (distinct from head/tail)

    Returns:
        Run info with optional output and events
    """
    # If no ref provided, get the most recent run
    if ref is None:
        return _last_impl(head, tail, errors, warnings, severity, limit, context)

    # Get info for specific run
    result = _info_impl(ref)

    # If additional output/events requested, fetch them
    needs_extra = (
        head is not None or tail is not None or errors or warnings
        or severity or context is not None
    )
    if needs_extra:
        run_serial = result.get("run_serial")

        if run_serial:
            try:
                storage = _get_storage()
                output_bytes = None
                log_lines = None

                # Load output if needed for head/tail or context
                if head is not None or tail is not None or context is not None:
                    output_bytes = storage.get_output(run_serial)
                    if output_bytes:
                        try:
                            content = output_bytes.decode("utf-8", errors="replace")
                        except Exception:
                            content = output_bytes.decode("latin-1")
                        log_lines = content.splitlines()

                        if head is not None:
                            result["head"] = log_lines[:head]
                        if tail is not None:
                            result["tail"] = log_lines[-tail:] if tail else log_lines

                # Get events if requested
                if errors or warnings or severity or context is not None:
                    if errors and warnings:
                        sev_filter = "error,warning"
                    elif errors:
                        sev_filter = "error"
                    elif warnings:
                        sev_filter = "warning"
                    elif context is not None:
                        # If context requested but no severity, default to errors
                        sev_filter = "error"
                    else:
                        sev_filter = severity

                    events_result = _events_impl(
                        limit=limit,
                        run_id=run_serial,
                        severity=sev_filter,
                    )
                    raw_events = events_result.get("events", [])

                    # Transform events based on whether context is requested
                    if context is not None and log_lines is not None:
                        # Compact format with context
                        events_list = []
                        errors_by_category: dict[str, int] = {}
                        for event in raw_events:
                            full_ref = event.get("ref") or ""
                            ref_file = event.get("ref_file")
                            ref_line = event.get("ref_line")
                            log_line = event.get("log_line")
                            category = event.get("category") or "other"

                            # Track category counts
                            errors_by_category[category] = (
                                errors_by_category.get(category, 0) + 1
                            )

                            # Use short ref: "test:47:242" -> "47:242"
                            parts = full_ref.split(":")
                            short_ref = (
                                ":".join(parts[-2:]) if len(parts) >= 2 else full_ref
                            )

                            # Build location
                            location = (
                                f"{ref_file}:{ref_line}"
                                if ref_file and ref_line
                                else ref_file
                            )

                            compact_event: dict[str, Any] = {
                                "ref": short_ref,
                                "location": location,
                            }

                            # Add context lines
                            if log_line is not None:
                                start = max(0, log_line - context - 1)
                                end = min(len(log_lines), log_line + context)
                                context_lines = []
                                for i in range(start, end):
                                    prefix = ">>> " if i == log_line - 1 else "    "
                                    context_lines.append(
                                        f"{prefix}{i + 1:4d} | {log_lines[i]}"
                                    )
                                compact_event["context"] = "\n".join(context_lines)

                            events_list.append(compact_event)

                        result["events"] = events_list
                        result["errors_by_category"] = errors_by_category
                    else:
                        # Full format without context
                        result["events"] = raw_events
            except Exception:
                pass  # Silently skip if can't fetch additional data

    return result


def _last_impl(
    head: int | None = None,
    tail: int | None = None,
    errors: bool = False,
    warnings: bool = False,
    severity: str | None = None,
    limit: int = 20,
    context: int | None = None,
) -> dict[str, Any]:
    """Implementation of last command - get info about most recent run."""
    try:
        storage = _get_storage()
        if not storage.has_data():
            return {"error": "No data available"}

        # Get most recent run
        df = storage.sql("""
            SELECT * FROM blq_load_runs()
            ORDER BY run_id DESC
            LIMIT 1
        """).df()

        if df.empty:
            return {"error": "No runs found"}

        row = df.iloc[0]
        run_serial = _safe_int(row.get("run_id")) or 0
        invocation_id = _to_json_safe(row.get("invocation_id"))

        # Build run_ref
        tag = _to_json_safe(row.get("tag"))
        if tag:
            run_ref = f"{tag}:{run_serial}"
        else:
            run_ref = str(run_serial)

        result: dict[str, Any] = {
            "run_ref": run_ref,
            "run_serial": run_serial,
            "invocation_id": invocation_id,
            "source_name": _to_json_safe(row.get("source_name")),
            "command": _to_json_safe(row.get("command")),
            "status": _to_json_safe(row.get("status")),
            "exit_code": _safe_int(row.get("exit_code")),
            "error_count": _safe_int(row.get("error_count")) or 0,
            "warning_count": _safe_int(row.get("warning_count")) or 0,
            "started_at": _to_json_safe(row.get("started_at")),
            "git_branch": _to_json_safe(row.get("git_branch")),
            "git_commit": _to_json_safe(row.get("git_commit")),
        }

        # Load output if needed
        log_lines = None
        if head is not None or tail is not None or context is not None:
            output_bytes = storage.get_output(run_serial)
            if output_bytes:
                try:
                    content = output_bytes.decode("utf-8", errors="replace")
                except Exception:
                    content = output_bytes.decode("latin-1")
                log_lines = content.splitlines()

                if head is not None:
                    result["head"] = log_lines[:head]
                if tail is not None:
                    result["tail"] = log_lines[-tail:] if tail else log_lines

        # Get events if requested
        if errors or warnings or severity or context is not None:
            # Determine severity filter
            if errors and warnings:
                sev_filter = "error,warning"
            elif errors:
                sev_filter = "error"
            elif warnings:
                sev_filter = "warning"
            elif context is not None:
                # If context requested but no severity, default to errors
                sev_filter = "error"
            else:
                sev_filter = severity

            conditions = [f"run_serial = {run_serial}"]
            if sev_filter and "," in sev_filter:
                severities = [s.strip() for s in sev_filter.split(",")]
                severity_list = ", ".join(f"'{s}'" for s in severities)
                conditions.append(f"severity IN ({severity_list})")
            elif sev_filter:
                conditions.append(f"severity = '{sev_filter}'")

            where = " AND ".join(conditions)
            events_df = storage.sql(f"""
                SELECT * FROM blq_load_events()
                WHERE {where}
                ORDER BY event_id
                LIMIT {limit}
            """).df()

            events_list = []
            errors_by_category: dict[str, int] = {}
            for _, erow in events_df.iterrows():
                full_ref = _to_json_safe(erow.get("ref")) or ""
                ref_file = _to_json_safe(erow.get("ref_file"))
                ref_line = _safe_int(erow.get("ref_line"))
                log_line = _safe_int(erow.get("log_line_start"))
                category = _to_json_safe(erow.get("category")) or "other"

                # Track category counts
                errors_by_category[category] = errors_by_category.get(category, 0) + 1

                if context is not None and log_lines is not None:
                    # Compact format when context is present
                    # Use short ref (strip tag prefix): "test:47:242" -> "47:242"
                    parts = full_ref.split(":")
                    short_ref = ":".join(parts[-2:]) if len(parts) >= 2 else full_ref

                    # Build location from file:line
                    location = f"{ref_file}:{ref_line}" if ref_file and ref_line else ref_file

                    event: dict[str, Any] = {
                        "ref": short_ref,
                        "location": location,
                    }

                    # Add context lines
                    if log_line is not None:
                        start = max(0, log_line - context - 1)
                        end = min(len(log_lines), log_line + context)
                        context_lines = []
                        for i in range(start, end):
                            prefix = ">>> " if i == log_line - 1 else "    "
                            context_lines.append(f"{prefix}{i + 1:4d} | {log_lines[i]}")
                        event["context"] = "\n".join(context_lines)
                else:
                    # Full format when no context
                    event = {
                        "ref": full_ref,
                        "severity": _to_json_safe(erow.get("severity")),
                        "ref_file": ref_file,
                        "ref_line": ref_line,
                        "message": _to_json_safe(erow.get("message")),
                        "log_line": log_line,
                    }

                events_list.append(event)

            result["events"] = events_list
            if context is not None:
                result["errors_by_category"] = errors_by_category

        return result

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def history(limit: int = 20, source: str | None = None) -> dict[str, Any]:
    """Get run history.

    Args:
        limit: Max runs to return (default: 20)
        source: Filter to specific source name

    Returns:
        Run history list
    """
    return _history_impl(limit, source)


@mcp.tool()
def diff(run1: int, run2: int) -> dict[str, Any]:
    """Compare errors between two runs.

    Args:
        run1: First run serial number (baseline)
        run2: Second run serial number (comparison)

    Returns:
        Diff summary with fixed and new errors
    """
    return _diff_impl(run1, run2)


@mcp.tool()
def register_command(
    name: str,
    cmd: str,
    description: str = "",
    timeout: int = 300,
    capture: bool = True,
    force: bool = False,
    format: str | None = None,
    run_now: bool = False,
) -> dict[str, Any]:
    """Register a new command.

    If a command with the same name or same command string already exists,
    returns the existing command (and runs it if run_now=True) instead of
    failing. Use force=True to overwrite an existing command.

    Note: This tool can be disabled via mcp.disabled_tools config.

    Args:
        name: Command name (e.g., 'build', 'test')
        cmd: Command to run
        description: Command description
        timeout: Timeout in seconds (default: 300)
        capture: Whether to capture and parse logs (default: true)
        force: Overwrite existing command if it exists
        format: Log format for parsing (auto-detected from command if not specified)
        run_now: Run the command immediately after registering (default: false)

    Returns:
        Success status and registered command details. If run_now=True,
        also includes 'run' key with the run result.
    """
    _check_tool_enabled("register_command")
    return _register_command_impl(name, cmd, description, timeout, capture, force, format, run_now)


@mcp.tool()
def unregister_command(name: str) -> dict[str, Any]:
    """Remove a registered command.

    Note: This tool can be disabled via mcp.disabled_tools config.

    Args:
        name: Command name to remove

    Returns:
        Success status
    """
    _check_tool_enabled("unregister_command")
    return _unregister_command_impl(name)


@mcp.tool()
def commands() -> dict[str, Any]:
    """List all registered commands.

    Returns:
        List of registered commands with their configuration
    """
    return _commands_impl()


def _clean_impl(
    mode: str = "data",
    confirm: bool = False,
    days: int | None = None,
) -> dict[str, Any]:
    """Implementation of clean command."""
    import shutil
    from datetime import datetime, timedelta
    from pathlib import Path

    valid_modes = ["data", "prune", "schema", "full"]

    if mode not in valid_modes:
        return {
            "success": False,
            "error": f"Invalid mode '{mode}'. Valid modes: {', '.join(valid_modes)}",
        }

    if mode == "prune" and days is None:
        return {
            "success": False,
            "error": "Prune mode requires 'days' parameter",
        }

    if not confirm:
        return {
            "success": False,
            "error": "Clean requires confirm=true to proceed. This is a destructive operation.",
            "mode": mode,
            "description": {
                "data": "Clear run data but keep config and commands",
                "prune": f"Remove data older than {days} days",
                "schema": "Recreate database schema (clears data, keeps config)",
                "full": "Delete and recreate entire .lq directory",
            }.get(mode, "Unknown mode"),
        }

    try:
        # Find .lq directory
        lq_dir = None
        current = Path.cwd()
        while current != current.parent:
            if (current / ".lq").exists():
                lq_dir = current / ".lq"
                break
            current = current.parent

        if lq_dir is None:
            return {"success": False, "error": "No .lq directory found"}

        if mode == "data":
            # Clear data tables but keep schema and config
            db_path = lq_dir / "blq.duckdb"
            if db_path.exists():
                import duckdb
                conn = duckdb.connect(str(db_path))
                conn.execute("DELETE FROM events")
                conn.execute("DELETE FROM outputs")
                conn.execute("DELETE FROM invocations")
                conn.execute("DELETE FROM sessions")
                conn.close()

            # Clear blobs
            blobs_dir = lq_dir / "blobs"
            if blobs_dir.exists():
                shutil.rmtree(blobs_dir)
                blobs_dir.mkdir()
                (blobs_dir / "content").mkdir()

            return {
                "success": True,
                "message": "Cleared all run data. Config and commands preserved.",
                "mode": mode,
            }

        elif mode == "prune":
            # Remove data older than N days
            cutoff = datetime.now() - timedelta(days=days)
            cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

            db_path = lq_dir / "blq.duckdb"
            if not db_path.exists():
                return {"success": False, "error": "No database found", "mode": mode}

            import duckdb
            conn = duckdb.connect(str(db_path))

            # Count what will be removed
            result = conn.execute(
                "SELECT COUNT(*) FROM invocations WHERE timestamp < ?", [cutoff_str]
            ).fetchone()
            invocation_count = result[0] if result else 0

            if invocation_count == 0:
                conn.close()
                return {
                    "success": True,
                    "message": f"No data older than {days} days found.",
                    "mode": mode,
                    "removed": {"invocations": 0, "events": 0},
                }

            result = conn.execute("""
                SELECT COUNT(*) FROM events e
                JOIN invocations i ON e.invocation_id = i.id
                WHERE i.timestamp < ?
            """, [cutoff_str]).fetchone()
            event_count = result[0] if result else 0

            # Delete events first
            conn.execute("""
                DELETE FROM events WHERE invocation_id IN (
                    SELECT id FROM invocations WHERE timestamp < ?
                )
            """, [cutoff_str])

            # Delete outputs
            conn.execute("""
                DELETE FROM outputs WHERE invocation_id IN (
                    SELECT id FROM invocations WHERE timestamp < ?
                )
            """, [cutoff_str])

            # Delete invocations
            conn.execute("DELETE FROM invocations WHERE timestamp < ?", [cutoff_str])

            # Clean up orphaned sessions
            conn.execute("""
                DELETE FROM sessions WHERE id NOT IN (
                    SELECT DISTINCT session_id FROM invocations WHERE session_id IS NOT NULL
                )
            """)

            conn.close()

            # Clean up orphaned blobs
            from blq.bird import BirdStore
            store = BirdStore.open(lq_dir)
            blobs_deleted, bytes_freed = store.cleanup_orphaned_blobs()
            store.close()

            return {
                "success": True,
                "message": f"Removed data older than {days} days.",
                "mode": mode,
                "removed": {
                    "invocations": invocation_count,
                    "events": event_count,
                    "blobs": blobs_deleted,
                    "bytes_freed": bytes_freed,
                },
            }

        elif mode == "schema":
            # Recreate database with fresh schema
            db_path = lq_dir / "blq.duckdb"
            if db_path.exists():
                db_path.unlink()

            # Clear blobs
            blobs_dir = lq_dir / "blobs"
            if blobs_dir.exists():
                shutil.rmtree(blobs_dir)
                blobs_dir.mkdir()
                (blobs_dir / "content").mkdir()

            # Recreate database with schema
            from blq.bird import BirdStore
            store = BirdStore.open(lq_dir)
            store.close()

            return {
                "success": True,
                "message": "Recreated database schema. Config files preserved.",
                "mode": mode,
            }

        elif mode == "full":
            # Full reinitialize
            shutil.rmtree(lq_dir)

            # Run init
            result = subprocess.run(
                ["blq", "init"],
                capture_output=True,
                text=True,
                cwd=lq_dir.parent,
            )

            if result.returncode == 0:
                return {
                    "success": True,
                    "message": "Fully reinitialized .lq directory.",
                    "mode": mode,
                }
            else:
                return {
                    "success": False,
                    "error": f"Init failed: {result.stderr}",
                    "mode": mode,
                }

    except Exception as e:
        return {"success": False, "error": str(e), "mode": mode}


@mcp.tool()
def clean(
    mode: str = "data",
    confirm: bool = False,
    days: int | None = None,
) -> dict[str, Any]:
    """Database cleanup and maintenance.

    Note: This tool can be disabled via mcp.disabled_tools config.

    Args:
        mode: Cleanup mode:
            - "data": Clear all run data but keep config and commands
            - "prune": Remove data older than N days (requires `days` param)
            - "schema": Recreate database schema (clears data, keeps config files)
            - "full": Delete and recreate entire .lq directory
        confirm: Must be true to proceed (safety check)
        days: For prune mode, remove data older than this many days

    Returns:
        Success status and message
    """
    _check_tool_enabled("clean")
    return _clean_impl(mode, confirm, days)




# ============================================================================
# Resources
# ============================================================================


@mcp.resource("blq://status")
def resource_status() -> str:
    """Current status of all sources."""
    result = _status_impl()
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://runs")
def resource_runs() -> str:
    """List of all runs."""
    result = _history_impl(limit=100)
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://events")
def resource_events() -> str:
    """All stored events."""
    result = _errors_impl(limit=100)
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://event/{ref}")
def resource_event(ref: str) -> str:
    """Single event details."""
    result = _event_impl(ref)
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://errors")
def resource_errors() -> str:
    """Recent errors across all runs."""
    result = _errors_impl(limit=50)
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://errors/{run_serial}")
def resource_errors_for_run(run_serial: str) -> str:
    """Errors for a specific run."""
    try:
        run_id = int(run_serial)
        result = _errors_impl(limit=100, run_id=run_id)
    except ValueError:
        result = {"errors": [], "total_count": 0, "error": f"Invalid run serial: {run_serial}"}
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://warnings")
def resource_warnings() -> str:
    """Recent warnings across all runs."""
    result = _warnings_impl(limit=50)
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://warnings/{run_serial}")
def resource_warnings_for_run(run_serial: str) -> str:
    """Warnings for a specific run."""
    try:
        run_id = int(run_serial)
        result = _warnings_impl(limit=100, run_id=run_id)
    except ValueError:
        result = {"warnings": [], "total_count": 0, "error": f"Invalid run serial: {run_serial}"}
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://context/{ref}")
def resource_context(ref: str) -> str:
    """Log context around a specific event."""
    result = _context_impl(ref, lines=5)
    return json.dumps(result, indent=2, default=str)


@mcp.resource("blq://commands")
def resource_commands() -> str:
    """Registered commands."""
    try:
        from blq.cli import BlqConfig

        config = BlqConfig.find()
        if config is not None:
            commands = config.commands
            return json.dumps({"commands": commands}, indent=2, default=str)
    except Exception:
        pass
    return json.dumps({"commands": []}, indent=2)


@mcp.resource("blq://guide")
def resource_guide() -> str:
    """Agent usage guide for blq MCP tools."""
    try:
        from importlib import resources
        guide = resources.files("blq").joinpath("SKILL.md").read_text()
        return guide
    except Exception:
        return """# blq Quick Reference

## Key Tools
- status() - Overview of all sources
- commands() - Registered commands
- events(severity="error") - Get errors
- inspect(ref) - Error details with context (ref like "build:1:3")
- diff(run1, run2) - Compare runs
- run(command) - Run registered command
- info() - Most recent run details (or info(ref) for specific run)
- reset(mode, confirm) - Clear data

## Workflow
1. commands() or status() to see current state
2. events(severity="error") to get recent errors
3. inspect(ref) to understand issues with full context
4. After fixes: diff(run1, run2) to verify

Docs: https://blq-cli.readthedocs.io/en/latest/
"""


# ============================================================================
# Prompts
# ============================================================================


@mcp.prompt(name="fix-errors")
def fix_errors(run_id: int | None = None, file_pattern: str | None = None) -> str:
    """Guide through fixing build errors systematically."""
    # Get current errors
    error_result = _errors_impl(limit=20, run_id=run_id, file_pattern=file_pattern)
    status_result = _status_impl()

    # Build status table
    status_lines = [
        "| Source | Status | Errors | Warnings |",
        "|--------|--------|--------|----------|",
    ]
    for src in status_result.get("sources", []):
        status_lines.append(
            f"| {src['name']} | {src['status']} | {src['error_count']} | {src['warning_count']} |"
        )
    status_table = "\n".join(status_lines)

    # Build error list
    error_lines = []
    for i, err in enumerate(error_result.get("errors", []), 1):
        loc = f"{err.get('ref_file', '?')}:{err.get('ref_line', '?')}"
        if err.get("ref_column"):
            loc += f":{err['ref_column']}"
        error_lines.append(
            f"{i}. **ref: {err['ref']}** `{loc}`\n   ```\n   {err.get('message', '')}\n   ```"
        )
    error_list = "\n\n".join(error_lines) if error_lines else "No errors found."

    return f"""You are helping fix build errors in a software project.

## Current Status

{status_table}

## Errors to Fix

{error_list}

## Instructions

1. Read each error and understand the root cause
2. Use `event(ref="...")` for full context if the message is unclear
3. Use `context(ref="...")` to see surrounding log lines
4. Fix errors in dependency order:
   - Missing includes/declarations first
   - Then type errors
   - Then syntax errors
5. After fixing, run `run(command="...")` to verify
6. Repeat until build passes

Focus on fixing the root cause, not just suppressing warnings."""


@mcp.prompt(name="analyze-regression")
def analyze_regression(good_run: int | None = None, bad_run: int | None = None) -> str:
    """Help identify why a build started failing between two runs."""
    # Get run history to find good/bad runs if not specified
    hist = _history_impl(limit=10)
    runs = hist.get("runs", [])

    if not runs:
        return 'No runs found. Run a build first with `run(command="...")`.'

    if bad_run is None:
        bad_run = runs[0]["run_serial"] if runs else 1
    if good_run is None:
        # Find last passing run
        for r in runs[1:]:
            if r["status"] == "OK":
                good_run = r["run_serial"]
                break
        if good_run is None:
            good_run = bad_run - 1 if bad_run > 1 else 1

    # Get diff
    diff_result = _diff_impl(good_run, bad_run)
    summary = diff_result.get("summary", {})

    # Build new errors list
    new_error_lines = []
    for err in diff_result.get("new", []):
        loc = f"{err.get('ref_file', '?')}:{err.get('ref_line', '?')}"
        new_error_lines.append(f"- **ref: {err['ref']}** `{loc}`\n  {err.get('message', '')}")
    new_errors = "\n".join(new_error_lines) if new_error_lines else "None"

    return f"""You are analyzing why a build started failing.

## Run Comparison

| Metric | Run {good_run} (good) | Run {bad_run} (bad) | Delta |
|--------|--------------|-------------|-------|
| Errors | {summary.get("run1_errors", 0)} | {summary.get("run2_errors", 0)} | \
+{summary.get("new", 0)} |

## New Errors (not in Run {good_run})

{new_errors}

## Instructions

1. Review the new errors that appeared
2. Look for patterns (same file, same error type)
3. Use `event(ref="...")` for full error context
4. Identify the root cause
5. Suggest the minimal fix to restore the build"""


@mcp.prompt(name="summarize-run")
def summarize_run(run_id: int | None = None, format: str = "brief") -> str:
    """Generate a concise summary of a build/test run."""
    hist = _history_impl(limit=1)
    runs = hist.get("runs", [])

    if not runs:
        return 'No runs found. Run a build first with `run(command="...")`.'

    if run_id is None:
        run_id = runs[0]["run_serial"]

    # Get run info
    run_info = None
    for r in runs:
        if r["run_serial"] == run_id:
            run_info = r
            break

    if not run_info:
        run_info = runs[0]

    error_result = _errors_impl(limit=10, run_id=run_id)

    # Build error details
    error_lines = []
    for err in error_result.get("errors", []):
        loc = f"{err.get('ref_file', '?')}:{err.get('ref_line', '?')}"
        error_lines.append(f"- `{loc}` - {err.get('message', '')[:80]}")
    error_details = "\n".join(error_lines) if error_lines else "No errors"

    return f"""Summarize this build/test run.

## Run Details

- **Run:** {run_info["run_ref"]}
- **Status:** {run_info["status"]}
- **Errors:** {run_info.get("error_count", 0)}
- **Warnings:** {run_info.get("warning_count", 0)}

## Error Details

{error_details}

## Instructions

Generate a summary suitable for a GitHub PR comment:
- Lead with pass/fail status
- List the key errors (not all warnings)
- Suggest what might have caused the failure
- Keep it concise"""


@mcp.prompt(name="investigate-flaky")
def investigate_flaky(test_pattern: str | None = None, lookback: int = 10) -> str:
    """Help investigate intermittently failing tests."""
    hist = _history_impl(limit=lookback)
    runs = hist.get("runs", [])

    if not runs:
        return 'No runs found. Run tests first with `run(command="...")`.'

    # Build history table
    history_lines = ["| Run | Status | Errors |", "|-----|--------|--------|"]
    for r in runs:
        history_lines.append(f"| {r['run_ref']} | {r['status']} | {r.get('error_count', 0)} |")
    history_table = "\n".join(history_lines)

    return f"""You are investigating flaky (intermittently failing) tests.

## Test History (last {lookback} runs)

{history_table}

## Instructions

1. Look for patterns in failures
2. Use `errors(run_id=N)` to see errors for specific runs
3. Use `event(ref="...")` for detailed failure output
4. Look for:
   - Race conditions (concurrent, parallel, thread)
   - Timing issues (timeout, sleep, wait)
   - Resource contention (connection, file, lock)
5. Suggest fixes to make tests more deterministic"""


# ============================================================================
# Entry point
# ============================================================================


def serve(
    transport: str = "stdio",
    port: int = 8080,
    disabled_tools: str | None = None,
    safe_mode: bool = False,
) -> None:
    """Start the MCP server.

    Args:
        transport: Transport type ("stdio" or "sse")
        port: Port for SSE transport
        disabled_tools: Comma-separated list of tools to disable
        safe_mode: If True, disable state-modifying tools (exec, clean, register/unregister)
    """
    # Initialize disabled tools before starting the server
    _init_disabled_tools(cli_disabled=disabled_tools, safe_mode=safe_mode)

    if transport == "stdio":
        mcp.run()
    elif transport == "sse":
        mcp.run(transport="sse", port=port)
    else:
        raise ValueError(f"Unknown transport: {transport}")


if __name__ == "__main__":
    serve()
