"""
Output formatting utilities for blq CLI.

Provides consistent, terminal-friendly output formatting for all commands.
Supports table, JSON, and markdown output formats.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence


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
    priority: int = 1  # Lower = more important (shown first)
    format_fn: Any = None  # Optional formatting function

    def __post_init__(self) -> None:
        if self.header is None:
            self.header = self.name


# Standard column definitions for different data types
HISTORY_COLUMNS = [
    Column("run_ref", "Ref", min_width=6, max_width=12, priority=0),
    Column("status", "Status", min_width=4, max_width=8, priority=0),
    Column("error_count", "Err", min_width=3, max_width=5, align="right", priority=0),
    Column("warning_count", "Warn", min_width=4, max_width=5, align="right", priority=1),
    Column("started_at", "Started", min_width=16, max_width=20, priority=1),
    Column("git_branch", "Branch", min_width=6, max_width=20, priority=2),
    Column("git_commit", "Commit", min_width=7, max_width=8, priority=2),
    Column("command", "Command", min_width=10, max_width=60, priority=3),
]

ERRORS_COLUMNS = [
    Column("ref", "Ref", min_width=6, max_width=12, priority=0),
    Column("severity", "Sev", min_width=5, max_width=7, priority=0),
    Column("ref_file", "File", min_width=10, max_width=40, priority=0),
    Column("ref_line", "Line", min_width=4, max_width=6, align="right", priority=1),
    Column("message", "Message", min_width=20, max_width=80, priority=1),
]

STATUS_COLUMNS = [
    Column("source_name", "Source", min_width=6, max_width=20, priority=0),
    Column("status", "Status", min_width=4, max_width=8, priority=0),
    Column("last_run", "Last Run", min_width=16, max_width=20, priority=1),
    Column("error_count", "Errors", min_width=4, max_width=6, align="right", priority=0),
    Column("warning_count", "Warnings", min_width=4, max_width=8, align="right", priority=1),
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
        available_cols = set()
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
    elif output_format == "markdown":
        return format_markdown(data, HISTORY_COLUMNS)
    else:
        return format_table(data, HISTORY_COLUMNS, max_width)


def format_errors(
    data: Sequence[dict[str, Any]],
    output_format: str = "table",
    max_width: int | None = None,
) -> str:
    """Format error/warning data."""
    if output_format == "json":
        return format_json(data)
    elif output_format == "markdown":
        return format_markdown(data, ERRORS_COLUMNS)
    else:
        return format_table(data, ERRORS_COLUMNS, max_width)


def format_status(
    data: Sequence[dict[str, Any]],
    output_format: str = "table",
    max_width: int | None = None,
) -> str:
    """Format status data."""
    if output_format == "json":
        return format_json(data)
    elif output_format == "markdown":
        return format_markdown(data, STATUS_COLUMNS)
    else:
        return format_table(data, STATUS_COLUMNS, max_width)


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


def get_output_format(args: Any) -> str:
    """Get output format from args, defaulting to 'table'."""
    if getattr(args, "json", False):
        return "json"
    elif getattr(args, "markdown", False):
        return "markdown"
    elif getattr(args, "csv", False):
        return "csv"
    else:
        return "table"


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
