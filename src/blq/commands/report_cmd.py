"""
Report generation commands for blq CLI.

Generates markdown reports summarizing build/test results.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime

from blq.commands.core import get_store_for_args


@dataclass
class ReportData:
    """Data collected for report generation."""

    run_id: int | None = None
    source_name: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    total_errors: int = 0
    total_warnings: int = 0
    errors_by_file: list[dict] = field(default_factory=list)
    warnings_by_file: list[dict] = field(default_factory=list)
    top_errors: list[dict] = field(default_factory=list)
    top_warnings: list[dict] = field(default_factory=list)
    # Comparison data
    baseline_run_id: int | None = None
    baseline_errors: int = 0
    baseline_warnings: int = 0
    new_errors: list[dict] = field(default_factory=list)
    fixed_errors: list[dict] = field(default_factory=list)


def _collect_report_data(
    store,
    run_id: int | None = None,
    baseline_id: int | None = None,
    error_limit: int = 20,
    file_limit: int = 10,
) -> ReportData:
    """Collect data for report generation.

    Args:
        store: LogStore instance
        run_id: Specific run ID (None = latest)
        baseline_id: Baseline run ID for comparison
        error_limit: Max errors to include
        file_limit: Max files to show in breakdown

    Returns:
        ReportData with collected information
    """
    data = ReportData()

    # Get run metadata
    runs_df = store.runs()
    if runs_df.empty:
        return data

    if run_id is None:
        run_row = runs_df.iloc[0]
        run_id = int(run_row["run_id"])
    else:
        matching = runs_df[runs_df["run_id"] == run_id]
        if matching.empty:
            return data
        run_row = matching.iloc[0]

    data.run_id = run_id
    data.source_name = run_row.get("source_name")
    data.started_at = run_row.get("started_at")
    data.completed_at = run_row.get("completed_at")
    data.exit_code = run_row.get("exit_code")
    data.git_branch = run_row.get("git_branch")
    data.git_commit = run_row.get("git_commit")

    # Get error/warning counts
    errors_df = store.run(run_id).filter(severity="error").df()
    warnings_df = store.run(run_id).filter(severity="warning").df()

    data.total_errors = len(errors_df)
    data.total_warnings = len(warnings_df)

    # Errors by file
    if not errors_df.empty and "file_path" in errors_df.columns:
        file_counts = errors_df.groupby("file_path").size().reset_index(name="count")
        file_counts = file_counts.sort_values("count", ascending=False).head(file_limit)
        data.errors_by_file = file_counts.to_dict("records")

    # Warnings by file
    if not warnings_df.empty and "file_path" in warnings_df.columns:
        file_counts = warnings_df.groupby("file_path").size().reset_index(name="count")
        file_counts = file_counts.sort_values("count", ascending=False).head(file_limit)
        data.warnings_by_file = file_counts.to_dict("records")

    # Top errors with details
    if not errors_df.empty:
        cols = ["file_path", "line_number", "message", "error_code", "fingerprint"]
        available_cols = [c for c in cols if c in errors_df.columns]
        data.top_errors = errors_df[available_cols].head(error_limit).to_dict("records")

    # Top warnings with details
    if not warnings_df.empty:
        cols = ["file_path", "line_number", "message", "error_code", "fingerprint"]
        available_cols = [c for c in cols if c in warnings_df.columns]
        data.top_warnings = warnings_df[available_cols].head(error_limit).to_dict("records")

    # Baseline comparison
    if baseline_id is not None:
        data.baseline_run_id = baseline_id
        baseline_errors_df = store.run(baseline_id).filter(severity="error").df()
        data.baseline_errors = len(baseline_errors_df)
        baseline_warnings_df = store.run(baseline_id).filter(severity="warning").df()
        data.baseline_warnings = len(baseline_warnings_df)

        # Compare fingerprints
        if not errors_df.empty and "fingerprint" in errors_df.columns:
            current_fps = set(errors_df["fingerprint"].dropna())
            baseline_fps = set()
            if not baseline_errors_df.empty and "fingerprint" in baseline_errors_df.columns:
                baseline_fps = set(baseline_errors_df["fingerprint"].dropna())

            new_fps = current_fps - baseline_fps
            fixed_fps = baseline_fps - current_fps

            data.new_errors = [
                e for e in data.top_errors if e.get("fingerprint") in new_fps
            ][:error_limit]
            data.fixed_errors = (
                baseline_errors_df[baseline_errors_df["fingerprint"].isin(fixed_fps)]
                .head(error_limit)
                .to_dict("records")
            )

    return data


def _format_location(error: dict) -> str:
    """Format error location as file:line string."""
    file_path = error.get("file_path")
    if not file_path:
        return "?"
    line_number = error.get("line_number")
    if line_number:
        return f"{file_path}:{line_number}"
    return str(file_path)


def _generate_markdown_report(
    data: ReportData,
    include_warnings: bool = False,
    include_details: bool = True,
) -> str:
    """Generate markdown report from collected data.

    Args:
        data: ReportData to format
        include_warnings: Include warning details
        include_details: Include individual error/warning details

    Returns:
        Markdown formatted report
    """
    lines = []

    # Title
    if data.source_name:
        lines.append(f"# Build Report: {data.source_name}")
    else:
        lines.append("# Build Report")
    lines.append("")

    # Summary section
    lines.append("## Summary")
    lines.append("")

    # Status badge
    if data.total_errors == 0:
        status = ":white_check_mark: **PASSED**"
    else:
        status = f":x: **FAILED** ({data.total_errors} errors)"
    lines.append(f"**Status:** {status}")
    lines.append("")

    # Metadata table
    lines.append("| Field | Value |")
    lines.append("|-------|-------|")
    if data.run_id:
        lines.append(f"| Run ID | #{data.run_id} |")
    if data.started_at:
        ts = (
            data.started_at.strftime("%Y-%m-%d %H:%M:%S")
            if hasattr(data.started_at, "strftime")
            else str(data.started_at)
        )
        lines.append(f"| Started | {ts} |")
    if data.git_branch:
        lines.append(f"| Branch | `{data.git_branch}` |")
    if data.git_commit:
        short_commit = str(data.git_commit)[:7] if data.git_commit else ""
        lines.append(f"| Commit | `{short_commit}` |")
    lines.append(f"| Errors | {data.total_errors} |")
    lines.append(f"| Warnings | {data.total_warnings} |")
    if data.exit_code is not None:
        lines.append(f"| Exit Code | {data.exit_code} |")
    lines.append("")

    # Comparison section
    if data.baseline_run_id is not None:
        lines.append("## Comparison vs Baseline")
        lines.append("")
        lines.append(f"Comparing against run #{data.baseline_run_id}")
        lines.append("")

        error_delta = data.total_errors - data.baseline_errors
        warning_delta = data.total_warnings - data.baseline_warnings

        lines.append("| Metric | Baseline | Current | Delta |")
        lines.append("|--------|----------|---------|-------|")

        error_delta_str = f"+{error_delta}" if error_delta > 0 else str(error_delta)
        warning_delta_str = (
            f"+{warning_delta}" if warning_delta > 0 else str(warning_delta)
        )

        lines.append(
            f"| Errors | {data.baseline_errors} | {data.total_errors} "
            f"| {error_delta_str} |"
        )
        lines.append(
            f"| Warnings | {data.baseline_warnings} | {data.total_warnings} "
            f"| {warning_delta_str} |"
        )
        lines.append("")

        # New errors
        if data.new_errors:
            lines.append(f"### New Errors ({len(data.new_errors)})")
            lines.append("")
            for error in data.new_errors[:10]:
                loc = _format_location(error)
                msg = (error.get("message") or "")[:80]
                lines.append(f"- `{loc}` - {msg}")
            if len(data.new_errors) > 10:
                lines.append(f"- ... and {len(data.new_errors) - 10} more")
            lines.append("")

        # Fixed errors
        if data.fixed_errors:
            lines.append("<details>")
            lines.append(f"<summary>Fixed Errors ({len(data.fixed_errors)})</summary>")
            lines.append("")
            for error in data.fixed_errors[:10]:
                loc = _format_location(error)
                msg = (error.get("message") or "")[:80]
                lines.append(f"- `{loc}` - {msg}")
            if len(data.fixed_errors) > 10:
                lines.append(f"- ... and {len(data.fixed_errors) - 10} more")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # Errors by file
    if data.errors_by_file:
        lines.append("## Errors by File")
        lines.append("")
        lines.append("| File | Count |")
        lines.append("|------|-------|")
        for item in data.errors_by_file:
            file_path = item.get("file_path", "?")
            count = item.get("count", 0)
            lines.append(f"| `{file_path}` | {count} |")
        lines.append("")

    # Error details
    if include_details and data.top_errors:
        lines.append("## Error Details")
        lines.append("")
        for error in data.top_errors:
            loc = _format_location(error)
            msg = error.get("message") or ""
            code = error.get("error_code")
            if code:
                lines.append(f"- **`{loc}`** [{code}]: {msg}")
            else:
                lines.append(f"- **`{loc}`**: {msg}")
        lines.append("")

    # Warnings section
    if include_warnings:
        if data.warnings_by_file:
            lines.append("## Warnings by File")
            lines.append("")
            lines.append("| File | Count |")
            lines.append("|------|-------|")
            for item in data.warnings_by_file:
                file_path = item.get("file_path", "?")
                count = item.get("count", 0)
                lines.append(f"| `{file_path}` | {count} |")
            lines.append("")

        if include_details and data.top_warnings:
            lines.append("<details>")
            lines.append(f"<summary>Warning Details ({len(data.top_warnings)})</summary>")
            lines.append("")
            for warning in data.top_warnings:
                loc = _format_location(warning)
                msg = warning.get("message") or ""
                code = warning.get("error_code")
                if code:
                    lines.append(f"- `{loc}` [{code}]: {msg}")
                else:
                    lines.append(f"- `{loc}`: {msg}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # Footer
    lines.append("---")
    lines.append("*Generated by [blq](https://github.com/teaguesterling/blq)*")

    return "\n".join(lines)


def _find_baseline_run(store, baseline: str | None) -> int | None:
    """Find baseline run by run ID or branch name.

    Args:
        store: LogStore instance
        baseline: Baseline specifier (run ID or branch name)

    Returns:
        Run ID of baseline, or None if not found
    """
    if baseline is None:
        return None

    runs = store.runs()
    if runs.empty:
        return None

    # Try as run ID
    if baseline.isdigit():
        run_id = int(baseline)
        if run_id in runs["run_id"].values:
            return run_id
        return None

    # Try as branch name
    matching = runs[runs["git_branch"] == baseline]
    if not matching.empty:
        return int(matching.iloc[0]["run_id"])

    return None


def cmd_report(args: argparse.Namespace) -> None:
    """Generate a markdown report of build/test results.

    Outputs to stdout by default, or to a file with --output.
    """
    store = get_store_for_args(args)

    # Determine run ID
    run_id = getattr(args, "run", None)
    if run_id is not None:
        run_id = int(run_id)

    # Find baseline
    baseline_spec = getattr(args, "baseline", None)
    baseline_id = _find_baseline_run(store, baseline_spec)

    if baseline_spec and baseline_id is None:
        print(f"Warning: Baseline '{baseline_spec}' not found.", file=sys.stderr)

    # Collect data
    error_limit = getattr(args, "error_limit", 20)
    file_limit = getattr(args, "file_limit", 10)

    data = _collect_report_data(
        store,
        run_id=run_id,
        baseline_id=baseline_id,
        error_limit=error_limit,
        file_limit=file_limit,
    )

    if data.run_id is None:
        print("Error: No runs found.", file=sys.stderr)
        sys.exit(1)

    # Generate report
    include_warnings = getattr(args, "warnings", False)
    include_details = not getattr(args, "summary_only", False)

    report = _generate_markdown_report(
        data,
        include_warnings=include_warnings,
        include_details=include_details,
    )

    # Output
    output_file = getattr(args, "output", None)
    if output_file:
        with open(output_file, "w") as f:
            f.write(report)
        print(f"Report written to {output_file}")
    else:
        print(report)
