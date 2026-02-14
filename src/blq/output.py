"""
Output formatting utilities for blq CLI.

Provides consistent, terminal-friendly output formatting for all commands.
Supports table, JSON, and markdown output formats.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def format_age(age_str: str) -> str:
    """Format age string to a compact human-readable format.

    Converts "0 days 03:13:38.171..." to "3h" or "5m" etc.
    """
    if not age_str:
        return ""

    # Parse "X days HH:MM:SS.microseconds" format
    match = re.match(r"(\d+)\s+days?\s+(\d+):(\d+):(\d+)", str(age_str))
    if match:
        days = int(match.group(1))
        hours = int(match.group(2))
        minutes = int(match.group(3))

        if days > 0:
            return f"{days}d"
        elif hours > 0:
            return f"{hours}h"
        elif minutes > 0:
            return f"{minutes}m"
        else:
            return "<1m"

    # Return as-is if can't parse
    return str(age_str)[:10]


def format_relative_time(timestamp_str: str) -> str:
    """Format timestamp as relative time (e.g., '5m ago', '2d ago').

    Args:
        timestamp_str: ISO format or similar timestamp string

    Returns:
        Relative time string like "5m ago", "2h ago", "3d ago"
    """
    if not timestamp_str:
        return ""

    try:
        # Try parsing various formats
        ts_str = str(timestamp_str).replace("T", " ")
        # Remove microseconds if present
        if "." in ts_str:
            ts_str = ts_str.split(".")[0]

        ts = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        delta = now - ts

        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "now"
        elif seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            days = seconds // 86400
            return f"{days}d ago"
    except (ValueError, TypeError):
        # Return shortened timestamp if can't parse
        return str(timestamp_str)[:16]


def format_file_location(row: dict) -> str:
    """Format file:line showing end of path.

    Converts "very/long/path/to/tests/test_foo.py" line 20 to
    ".../tests/test_foo.py:20"
    """
    ref_file = row.get("ref_file") or ""
    ref_line = row.get("ref_line")

    if not ref_file:
        return ""

    # Show last 2-3 path components
    parts = ref_file.replace("\\", "/").split("/")
    if len(parts) > 3:
        short_path = ".../" + "/".join(parts[-2:])
    elif len(parts) > 1:
        short_path = "/".join(parts[-2:])
    else:
        short_path = ref_file

    # Append line number if present
    if ref_line:
        return f"{short_path}:{ref_line}"
    return short_path


@dataclass
class Column:
    """Column definition for table output."""

    name: str
    header: str | None = None  # Display header (defaults to name)
    width: int | None = None  # Fixed width (None = auto)
    min_width: int = 3
    max_width: int = 50
    align: str = "left"  # left, right, center
    truncate: bool = True  # Truncate long values
    truncate_left: bool = False  # Truncate from left (show end) instead of right
    priority: int = 1  # Lower = more important (shown first)
    format_fn: Any = None  # Optional formatting function

    def __post_init__(self) -> None:
        if self.header is None:
            self.header = self.name


# Standard column definitions for different data types
HISTORY_COLUMNS = [
    Column("run_ref", "· Ref", min_width=6, max_width=18, priority=0, truncate=False),
    Column("counts", "E/W", min_width=3, max_width=7, align="right", priority=0),
    Column("when", "When", min_width=6, max_width=10, priority=1),
    Column("git_ref", "Git", min_width=10, max_width=20, priority=2),
    Column("command", "Command", min_width=10, max_width=50, priority=3),
]

ERRORS_COLUMNS = [
    Column("source_name", "Source", min_width=6, max_width=12, priority=0),
    Column("ref", "Ref", min_width=10, max_width=18, priority=0, truncate=False),
    Column("code", "Code", min_width=4, max_width=12, priority=2),
    Column("severity", "Sev", min_width=5, max_width=7, priority=1),
    Column("location", "Location", min_width=15, max_width=30, priority=0, truncate_left=True),
    Column("message", "Message", min_width=15, max_width=50, priority=1),
]

STATUS_COLUMNS = [
    Column("badge", "Status", min_width=6, max_width=8, priority=0),
    Column("source_name", "Source", min_width=8, max_width=25, priority=0),
    Column("counts", "E/W", min_width=3, max_width=7, align="right", priority=0),
    Column("age", "Age", min_width=4, max_width=8, priority=1, format_fn=format_age),
]

COMMANDS_COLUMNS = [
    Column("name", "Name", min_width=6, max_width=20, priority=0),
    Column("cmd", "Command", min_width=20, max_width=60, priority=0),
    Column("description", "Description", min_width=10, max_width=40, priority=1),
    Column("timeout", "Timeout", min_width=4, max_width=8, align="right", priority=2),
]


@dataclass
class TableFormatter:
    """Formats data as a terminal-friendly table."""

    columns: list[Column] = field(default_factory=list)
    max_width: int | None = None  # None = auto-detect terminal width
    min_width: int = 60
    border: bool = False
    header_separator: bool = True

    def __post_init__(self) -> None:
        if self.max_width is None:
            try:
                self.max_width = shutil.get_terminal_size().columns
            except Exception:
                self.max_width = 120
        # Clamp to reasonable range
        self.max_width = max(self.min_width, min(self.max_width, 200))

    def format(self, data: Sequence[dict[str, Any]]) -> str:
        """Format data as a table string."""
        if not data:
            return "(no data)"

        # Determine which columns to show based on available width
        visible_columns = self._select_columns(data)
        if not visible_columns:
            return "(no columns)"

        # Calculate column widths
        col_widths = self._calculate_widths(data, visible_columns)

        # Build output
        lines = []

        # Header
        header_parts = []
        for col in visible_columns:
            w = col_widths[col.name]
            text = (col.header or col.name)[:w]
            header_parts.append(self._align(text, w, col.align))

        header_line = "  ".join(header_parts)
        lines.append(header_line)

        # Separator
        if self.header_separator:
            sep_parts = []
            for col in visible_columns:
                w = col_widths[col.name]
                sep_parts.append("-" * w)
            lines.append("  ".join(sep_parts))

        # Data rows
        for row in data:
            row_parts = []
            for col in visible_columns:
                w = col_widths[col.name]
                value = self._format_value(row.get(col.name), col, w)
                row_parts.append(self._align(value, w, col.align))
            lines.append("  ".join(row_parts))

        return "\n".join(lines)

    def _select_columns(self, data: Sequence[dict[str, Any]]) -> list[Column]:
        """Select columns to display based on priority and available width."""
        if not self.columns:
            # Auto-generate columns from data
            if data:
                keys = list(data[0].keys())
                self.columns = [Column(k) for k in keys]

        # Filter to columns that exist in data
        available_cols: set[str] = set()
        for row in data:
            available_cols.update(row.keys())

        columns = [c for c in self.columns if c.name in available_cols]

        # Sort by priority
        columns.sort(key=lambda c: c.priority)

        # Select columns that fit
        selected = []
        used_width = 0
        max_width = self.max_width or 120

        for col in columns:
            col_width = col.min_width + 2  # +2 for spacing
            if used_width + col_width <= max_width:
                selected.append(col)
                used_width += col_width
            elif col.priority == 0:
                # Always include priority 0 columns
                selected.append(col)
                used_width += col_width

        return selected

    def _calculate_widths(
        self, data: Sequence[dict[str, Any]], columns: list[Column]
    ) -> dict[str, int]:
        """Calculate optimal column widths."""
        widths: dict[str, int] = {}

        for col in columns:
            # Start with header width
            header_width = len(col.header or col.name)

            # Sample data to find max content width
            max_content = header_width
            for row in data[:50]:  # Sample first 50 rows
                value = row.get(col.name)
                if value is not None:
                    str_val = self._to_string(value, col)
                    max_content = max(max_content, len(str_val))

            # Apply constraints
            if col.width:
                widths[col.name] = col.width
            else:
                w = max(col.min_width, min(max_content, col.max_width))
                widths[col.name] = w

        # Distribute remaining width to flexible columns
        total_width = sum(widths.values()) + (len(columns) - 1) * 2  # +2 for spacing
        max_width = self.max_width or 120

        if total_width < max_width:
            # Find columns that can expand
            expandable = [c for c in columns if c.max_width > widths[c.name]]
            extra = max_width - total_width
            if expandable:
                per_col = extra // len(expandable)
                for col in expandable:
                    widths[col.name] = min(widths[col.name] + per_col, col.max_width)

        return widths

    def _to_string(self, value: Any, col: Column) -> str:
        """Convert value to string for display."""
        if value is None:
            return ""
        if col.format_fn:
            return str(col.format_fn(value))
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M")
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, dict):
            return json.dumps(value)
        return str(value)

    def _format_value(self, value: Any, col: Column, width: int) -> str:
        """Format a value for display in a column."""
        text = self._to_string(value, col)

        # Truncate if needed
        if col.truncate and len(text) > width:
            if col.truncate_left:
                # Show end of text (useful for file paths)
                text = "…" + text[-(width - 1) :]
            else:
                text = text[: width - 1] + "…"

        return text

    def _align(self, text: str, width: int, align: str) -> str:
        """Align text within width."""
        if len(text) >= width:
            return text[:width]
        if align == "right":
            return text.rjust(width)
        if align == "center":
            return text.center(width)
        return text.ljust(width)


def format_table(
    data: Sequence[dict[str, Any]],
    columns: list[Column] | None = None,
    max_width: int | None = None,
) -> str:
    """Format data as a terminal-friendly table.

    Args:
        data: List of dicts to format
        columns: Column definitions (auto-generated if None)
        max_width: Maximum table width (auto-detect if None)

    Returns:
        Formatted table string
    """
    formatter = TableFormatter(columns=columns or [], max_width=max_width)
    return formatter.format(data)


def format_json(data: Any, indent: int = 2) -> str:
    """Format data as JSON.

    Args:
        data: Data to format
        indent: Indentation level

    Returns:
        JSON string
    """
    return json.dumps(data, indent=indent, default=str)


def format_markdown(
    data: Sequence[dict[str, Any]],
    columns: list[Column] | None = None,
) -> str:
    """Format data as a markdown table.

    Args:
        data: List of dicts to format
        columns: Column definitions (auto-generated if None)

    Returns:
        Markdown table string
    """
    if not data:
        return "(no data)"

    # Determine columns
    if columns:
        cols = [c for c in columns if c.name in data[0]]
    else:
        cols = [Column(k) for k in data[0].keys()]

    if not cols:
        return "(no columns)"

    # Build header
    headers = [c.header or c.name for c in cols]
    lines = ["| " + " | ".join(headers) + " |"]

    # Separator with alignment
    sep_parts = []
    for col in cols:
        if col.align == "right":
            sep_parts.append("---:")
        elif col.align == "center":
            sep_parts.append(":---:")
        else:
            sep_parts.append("---")
    lines.append("| " + " | ".join(sep_parts) + " |")

    # Data rows
    for row in data:
        values = []
        for col in cols:
            val = row.get(col.name)
            if val is None:
                values.append("")
            elif isinstance(val, datetime):
                values.append(val.strftime("%Y-%m-%d %H:%M"))
            elif isinstance(val, bool):
                values.append("Yes" if val else "No")
            else:
                # Escape pipes in values
                values.append(str(val).replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")

    return "\n".join(lines)


# Convenience functions for specific data types


def format_history(
    data: Sequence[dict[str, Any]],
    output_format: str = "table",
    max_width: int | None = None,
) -> str:
    """Format run history data."""
    if output_format == "json":
        return format_json(data)

    # Preprocess: add run_ref, counts, relative time, and combined git_ref
    processed = []
    for row in data:
        new_row = dict(row)
        # Create run_ref from tag:run_id or just run_id
        tag = row.get("tag") or row.get("source_name") or ""
        run_id = row.get("run_id") or row.get("run_serial") or ""
        if tag and run_id:
            base_ref = f"{tag}:{run_id}"
        else:
            base_ref = str(run_id)

        # Add status prefix: ▶ running, ⊘ orphaned, ✗ failed, (space) success
        status = row.get("status")
        errors = row.get("error_count") or 0
        exit_code = row.get("exit_code")

        if status == "pending":
            new_row["run_ref"] = f"▶ {base_ref}"
        elif status == "orphaned":
            new_row["run_ref"] = f"⊘ {base_ref}"
        elif errors > 0 or (exit_code is not None and exit_code != 0):
            new_row["run_ref"] = f"✗ {base_ref}"
        else:
            new_row["run_ref"] = f"  {base_ref}"

        # Create sparse counts: ✓ if clean, … if pending with no events, else errors/warnings
        status = row.get("status")
        errors = row.get("error_count") or 0
        warnings = row.get("warning_count") or 0

        if status == "pending":
            # Show … until events are detected, then show actual counts
            if errors == 0 and warnings == 0:
                new_row["counts"] = "…"
            else:
                new_row["counts"] = f"{errors}/{warnings}"
        elif status == "orphaned":
            # Orphaned runs never completed - show ? or partial counts
            if errors == 0 and warnings == 0:
                new_row["counts"] = "?"
            else:
                new_row["counts"] = f"{errors}/{warnings}"
        elif errors == 0 and warnings == 0:
            new_row["counts"] = "✓"
        else:
            new_row["counts"] = f"{errors}/{warnings}"

        # Create relative time
        new_row["when"] = format_relative_time(row.get("started_at", ""))

        # Create combined git_ref: *hash(branch) or hash(branch)
        # Smart truncation: keep full hash, truncate long branch names
        commit = row.get("git_commit") or ""
        branch = row.get("git_branch") or ""
        dirty = row.get("git_dirty") or False
        dirty_prefix = "*" if dirty else ""

        if commit:
            # Shorten commit to 7 chars
            short_commit = commit[:7] if len(commit) > 7 else commit
            if branch:
                # Max 18 chars total: *1234567(branch...)
                # prefix (0-1) + hash (7) + parens (2) = 9-10 chars
                # Leave ~8 chars for branch, truncate if longer
                max_branch_len = 8
                if len(branch) > max_branch_len:
                    branch = branch[: max_branch_len - 1] + "…"
                new_row["git_ref"] = f"{dirty_prefix}{short_commit}({branch})"
            else:
                new_row["git_ref"] = f"{dirty_prefix}{short_commit}"
        elif branch:
            # No commit, just branch
            max_branch_len = 15
            if len(branch) > max_branch_len:
                branch = branch[: max_branch_len - 1] + "…"
            new_row["git_ref"] = f"{dirty_prefix}({branch})"
        else:
            new_row["git_ref"] = ""

        processed.append(new_row)

    if output_format == "markdown":
        return format_markdown(processed, HISTORY_COLUMNS)
    else:
        return format_table(processed, HISTORY_COLUMNS, max_width)


def format_errors(
    data: Sequence[dict[str, Any]],
    output_format: str = "table",
    max_width: int | None = None,
) -> str:
    """Format error/warning data."""
    if output_format == "json":
        return format_json(data)

    # Preprocess: add combined location column and normalize code field
    processed = []
    for row in data:
        new_row = dict(row)
        new_row["location"] = format_file_location(row)
        # Normalize code field (can come from code, rule, or error_code)
        code = row.get("code") or row.get("rule") or row.get("error_code")
        new_row["code"] = code if code else ""
        processed.append(new_row)

    if output_format == "markdown":
        return format_markdown(processed, ERRORS_COLUMNS)
    else:
        return format_table(processed, ERRORS_COLUMNS, max_width)


def format_status(
    data: Sequence[dict[str, Any]],
    output_format: str = "table",
    max_width: int | None = None,
) -> str:
    """Format status data."""
    if output_format == "json":
        return format_json(data)

    # Transform data to add computed counts field with unique counts
    processed = []
    for row in data:
        new_row = dict(row)
        status = row.get("status")

        # Handle pending/running status
        if status == "pending":
            new_row["counts"] = "…"
        else:
            errors = row.get("error_count") or row.get("errors") or 0
            warnings = row.get("warning_count") or row.get("warnings") or 0
            unique_errors = row.get("unique_error_count")
            unique_warnings = row.get("unique_warning_count")

            if errors == 0 and warnings == 0:
                new_row["counts"] = "✓"
            else:
                # Show unique count if different from total: "3 (2)" means 3 errors, 2 unique
                error_str = str(errors)
                if unique_errors is not None and unique_errors != errors:
                    error_str = f"{errors}({unique_errors})"
                warning_str = str(warnings)
                if unique_warnings is not None and unique_warnings != warnings:
                    warning_str = f"{warnings}({unique_warnings})"
                new_row["counts"] = f"{error_str}/{warning_str}"
        processed.append(new_row)

    if output_format == "markdown":
        return format_markdown(processed, STATUS_COLUMNS)
    else:
        return format_table(processed, STATUS_COLUMNS, max_width)


def format_commands(
    data: Sequence[dict[str, Any]],
    output_format: str = "table",
    max_width: int | None = None,
) -> str:
    """Format registered commands data."""
    if output_format == "json":
        return format_json(data)
    elif output_format == "markdown":
        return format_markdown(data, COMMANDS_COLUMNS)
    else:
        return format_table(data, COMMANDS_COLUMNS, max_width)


RUN_DETAIL_COLUMNS = [
    Column("field", "Field", min_width=15, max_width=20, priority=0),
    Column("value", "Value", min_width=20, max_width=80, priority=0),
]


def format_run_details(
    run: dict[str, Any],
    output_format: str = "table",
    detailed: bool = False,
) -> str:
    """Format detailed run information.

    Args:
        run: Run data dictionary
        output_format: One of 'table', 'json', 'markdown'
        detailed: Show all fields (vs. just key fields)

    Returns:
        Formatted string
    """
    if output_format == "json":
        return format_json(run)

    # Key fields to show by default (order matters)
    key_fields = [
        "run_ref",
        "source_name",
        "command",
        "status",
        "error_count",
        "warning_count",
        "started_at",
        "duration",
        "exit_code",
        "git_branch",
        "git_commit",
        "invocation_id",  # UUID at the end
    ]

    # Additional fields for detailed view (all remaining fields)
    extra_fields = [
        "git_dirty",
        "cwd",
        "executable_path",
        "hostname",
        "platform",
        "arch",
        "run_id",
        "run_serial",
        "session_id",
        "info_count",
        "event_count",
        "completed_at",
        "log_date",
        "tag",
        "source_type",
    ]

    # Build run_ref if not present
    if "run_ref" not in run:
        tag = run.get("tag") or run.get("source_name") or ""
        run_id = run.get("run_id") or run.get("run_serial") or ""
        if tag and run_id:
            run["run_ref"] = f"{tag}:{run_id}"
        else:
            run["run_ref"] = str(run_id)

    # Collect fields to display
    fields_to_show = key_fields.copy()
    if detailed:
        fields_to_show.extend(extra_fields)

    # Build key-value pairs
    data = []
    for field_name in fields_to_show:
        if field_name in run and run[field_name] is not None:
            value = run[field_name]
            # Format special fields
            if field_name == "started_at":
                value = f"{value} ({format_relative_time(str(value))})"
            elif field_name == "git_dirty":
                value = "Yes" if value else "No"
            elif field_name == "duration":
                # Format duration nicely
                if isinstance(value, (int, float)):
                    if value < 1:
                        value = f"{value * 1000:.0f}ms"
                    elif value < 60:
                        value = f"{value:.1f}s"
                    else:
                        mins = int(value // 60)
                        secs = value % 60
                        value = f"{mins}m {secs:.0f}s"
            data.append(
                {
                    "field": field_name.replace("_", " ").title(),
                    "value": str(value),
                }
            )

    # Add output streams info (always show if available)
    if run.get("outputs"):
        outputs = run["outputs"]
        for out in outputs:
            stream = out.get("stream", "?")
            byte_len = out.get("bytes", 0)
            # Format bytes nicely
            if byte_len < 1024:
                size_str = f"{byte_len} bytes"
            elif byte_len < 1024 * 1024:
                size_str = f"{byte_len / 1024:.1f} KB"
            else:
                size_str = f"{byte_len / (1024 * 1024):.1f} MB"
            data.append({"field": f"Output ({stream})", "value": size_str})

    # Add environment and CI info if detailed
    if detailed:
        if run.get("environment"):
            env = run["environment"]
            if isinstance(env, dict):
                env_str = ", ".join(
                    f"{k}={v[:20]}..." if len(str(v)) > 20 else f"{k}={v}"
                    for k, v in list(env.items())[:5]
                )
                if len(env) > 5:
                    env_str += f" (+{len(env) - 5} more)"
                data.append({"field": "Environment", "value": env_str})
        if run.get("ci"):
            ci = run["ci"]
            if isinstance(ci, dict):
                data.append({"field": "CI Provider", "value": ci.get("provider", "?")})

    if output_format == "markdown":
        return format_markdown(data, RUN_DETAIL_COLUMNS)
    else:
        return format_table(data, RUN_DETAIL_COLUMNS)


def get_output_format(args: Any) -> str:
    """Get output format from args, using user config default.

    Priority: explicit flags > user config default > 'table'
    """
    if getattr(args, "json", False):
        return "json"
    elif getattr(args, "markdown", False):
        return "markdown"
    elif getattr(args, "csv", False):
        return "csv"
    else:
        # Use user config default
        from blq.user_config import UserConfig

        user_config = UserConfig.load()
        return user_config.default_format


def get_default_limit(args: Any, fallback: int = 20) -> int:
    """Get limit from args, using user config default if not specified.

    Args:
        args: Parsed arguments with optional 'limit' attribute
        fallback: Fallback if neither args nor user config specify

    Returns:
        The limit to use
    """
    # Check if limit was explicitly set in args
    limit = getattr(args, "limit", None)
    if limit is not None:
        return int(limit)

    # Use user config default
    from blq.user_config import UserConfig

    user_config = UserConfig.load()
    return user_config.default_limit


def print_output(
    data: Any,
    output_format: str = "table",
    columns: list[Column] | None = None,
    max_width: int | None = None,
    file: Any = None,
) -> None:
    """Print formatted output to stdout or file.

    Args:
        data: Data to print
        output_format: One of 'table', 'json', 'markdown'
        columns: Column definitions for table/markdown
        max_width: Maximum width for table
        file: File to write to (default: stdout)
    """
    if file is None:
        file = sys.stdout

    if output_format == "json":
        output = format_json(data)
    elif output_format == "markdown":
        output = format_markdown(data, columns)
    else:
        output = format_table(data, columns, max_width)

    print(output, file=file)


def read_source_context(
    ref_file: str,
    ref_line: int,
    ref_root: Any = None,
    context: int = 5,
) -> str | None:
    """Read source file and return formatted context lines around ref_line.

    Args:
        ref_file: Path to source file (relative or absolute)
        ref_line: 1-indexed line number of interest
        ref_root: Root path for resolving relative paths (default: current dir)
        context: Number of context lines before/after

    Returns:
        Formatted context string with line numbers and markers, or None if file not found
    """
    from pathlib import Path

    if ref_root is None:
        ref_root = Path.cwd()
    else:
        ref_root = Path(ref_root)

    # Try to resolve the file path
    file_path = ref_root / ref_file
    if not file_path.exists():
        # Try absolute path
        abs_path = Path(ref_file)
        if abs_path.exists():
            file_path = abs_path
        else:
            return None

    try:
        content = file_path.read_text(errors="replace")
        lines = content.splitlines()

        if ref_line < 1 or ref_line > len(lines):
            return None

        # Format context around the line
        return format_context(
            lines,
            ref_line,
            ref_line,
            context=context,
            ref=None,
            header=f"Source: {ref_file}:{ref_line}",
        )
    except OSError:
        return None


def format_context(
    lines: list[str],
    log_line_start: int,
    log_line_end: int,
    context: int = 5,
    ref: str | None = None,
    header: str | None = None,
) -> str:
    """Format log context around an event.

    Args:
        lines: All lines from the log file
        log_line_start: 1-indexed start line of the event
        log_line_end: 1-indexed end line of the event
        context: Number of context lines before/after
        ref: Optional event reference for header (deprecated, use header)
        header: Optional custom header text

    Returns:
        Formatted context string with line numbers and markers
    """
    start = max(0, log_line_start - context - 1)  # 1-indexed to 0-indexed
    end = min(len(lines), log_line_end + context)

    output_lines = []

    # Header
    if header:
        output_lines.append(header)
    elif ref:
        output_lines.append(f"Context for event {ref} (lines {start + 1}-{end}):")

    if output_lines:
        output_lines.append("-" * 60)

    # Context lines with markers
    for i in range(start, end):
        line_num = i + 1
        prefix = ">>> " if log_line_start <= line_num <= log_line_end else "    "
        output_lines.append(f"{prefix}{line_num:4d} | {lines[i]}")

    output_lines.append("-" * 60)

    return "\n".join(output_lines)
