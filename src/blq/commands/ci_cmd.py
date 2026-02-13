"""
CI integration commands for blq CLI.

Provides commands for CI/CD workflows:
- blq ci check: Compare current run against baseline, exit 0/1
- blq ci comment: Post error summary as PR comment
- blq ci generate: Generate standalone shell scripts for CI
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blq.commands.core import BlqConfig, RegisteredCommand, get_store_for_args


@dataclass
class DiffResult:
    """Result of comparing two runs."""

    baseline_run_id: int | None
    current_run_id: int | None
    baseline_errors: int
    current_errors: int
    fixed: list[dict] = field(default_factory=list)
    new_errors: list[dict] = field(default_factory=list)

    @property
    def has_new_errors(self) -> bool:
        """Return True if there are new errors."""
        return len(self.new_errors) > 0

    @property
    def delta(self) -> int:
        """Net change in error count (positive = more errors)."""
        return self.current_errors - self.baseline_errors


def _find_baseline_run(store, baseline: str | None) -> int | None:
    """Find baseline run by run ID, branch name, or commit SHA.

    Resolution order:
    1. If baseline is numeric: use as run ID
    2. If baseline looks like a commit SHA: find run with matching git_commit
    3. If baseline is a branch name: find latest run on that branch
    4. If no baseline: try "main", then "master"

    Args:
        store: BlqStorage instance
        baseline: Baseline specifier (run ID, branch, or commit)

    Returns:
        Run ID of baseline, or None if not found
    """
    runs = store.runs().df()
    if runs.empty:
        return None

    # If baseline specified, try to resolve it
    if baseline:
        # Try as run ID (numeric)
        if baseline.isdigit():
            run_id = int(baseline)
            if run_id in runs["run_id"].values:
                return run_id
            return None

        # Try as commit SHA (40 hex chars or prefix)
        if re.match(r"^[a-f0-9]{7,40}$", baseline.lower()):
            for _, row in runs.iterrows():
                commit = row.get("git_commit")
                if commit and commit.lower().startswith(baseline.lower()):
                    return int(row["run_id"])

        # Try as branch name
        matching = runs[runs["git_branch"] == baseline]
        if not matching.empty:
            return int(matching.iloc[0]["run_id"])

        return None

    # No baseline specified - try main, then master
    for default_branch in ["main", "master"]:
        matching = runs[runs["git_branch"] == default_branch]
        if not matching.empty:
            return int(matching.iloc[0]["run_id"])

    return None


def _find_current_run(store) -> int | None:
    """Find current run (latest or by git commit).

    First tries to match the current git commit, then falls back to latest run.

    Args:
        store: BlqStorage instance

    Returns:
        Run ID of current run, or None if no runs
    """
    runs = store.runs().df()
    if runs.empty:
        return None

    # Try to find run matching current git commit
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            current_commit = result.stdout.strip()
            for _, row in runs.iterrows():
                commit = row.get("git_commit")
                if commit and commit == current_commit:
                    return int(row["run_id"])
    except Exception:
        pass

    # Fall back to latest run
    return int(runs.iloc[0]["run_id"])


def _compute_diff(store, baseline_id: int | None, current_id: int | None) -> DiffResult:
    """Compute diff between two runs using fingerprints.

    Errors are matched by fingerprint to determine which are fixed vs new.

    Args:
        store: BlqStorage instance
        baseline_id: Baseline run ID (None = no baseline)
        current_id: Current run ID (None = no current run)

    Returns:
        DiffResult with comparison details
    """
    # Get baseline errors
    baseline_errors: list[dict[str, Any]] = []
    if baseline_id is not None:
        baseline_df = store.errors(run_id=baseline_id, limit=10000).df()
        baseline_errors = baseline_df.to_dict("records") if not baseline_df.empty else []

    # Get current errors
    current_errors: list[dict[str, Any]] = []
    if current_id is not None:
        current_df = store.errors(run_id=current_id, limit=10000).df()
        current_errors = current_df.to_dict("records") if not current_df.empty else []

    # Build fingerprint sets for comparison
    baseline_fps = {e.get("fingerprint") for e in baseline_errors if e.get("fingerprint")}
    current_fps = {e.get("fingerprint") for e in current_errors if e.get("fingerprint")}

    # Find fixed errors (in baseline but not in current)
    fixed = [e for e in baseline_errors if e.get("fingerprint") in (baseline_fps - current_fps)]

    # Find new errors (in current but not in baseline)
    new = [e for e in current_errors if e.get("fingerprint") in (current_fps - baseline_fps)]

    return DiffResult(
        baseline_run_id=baseline_id,
        current_run_id=current_id,
        baseline_errors=len(baseline_errors),
        current_errors=len(current_errors),
        fixed=fixed,
        new_errors=new,
    )


def _format_location(error: dict) -> str:
    """Format error location as file:line string."""
    ref_file = error.get("ref_file")
    if not ref_file:
        return "?"
    ref_line = error.get("ref_line")
    if ref_line:
        return f"{ref_file}:{ref_line}"
    return str(ref_file)


def _format_pr_comment(diff: DiffResult, include_fixed: bool = True) -> str:
    """Format diff as GitHub-flavored markdown for PR comment.

    Args:
        diff: DiffResult to format
        include_fixed: Include fixed errors section

    Returns:
        Markdown string for PR comment
    """
    lines = ["## Build Log Analysis", ""]

    # Summary table
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    if diff.baseline_run_id is not None:
        lines.append(f"| Baseline run | #{diff.baseline_run_id} |")
        lines.append(f"| Baseline errors | {diff.baseline_errors} |")
    lines.append(f"| Current run | #{diff.current_run_id} |")
    lines.append(f"| Current errors | {diff.current_errors} |")
    if diff.baseline_run_id is not None:
        lines.append(f"| Fixed | {len(diff.fixed)} |")
        lines.append(f"| New | {len(diff.new_errors)} |")
    lines.append("")

    # New errors section
    if diff.new_errors:
        lines.append("### New Errors")
        lines.append("")
        for error in diff.new_errors[:20]:
            loc = _format_location(error)
            msg = (error.get("message") or "")[:100]
            lines.append(f"- `{loc}` - {msg}")
        if len(diff.new_errors) > 20:
            lines.append(f"- ... and {len(diff.new_errors) - 20} more")
        lines.append("")

    # Fixed errors section (collapsible)
    if include_fixed and diff.fixed:
        lines.append("<details>")
        lines.append(f"<summary>Fixed Errors ({len(diff.fixed)})</summary>")
        lines.append("")
        for error in diff.fixed[:20]:
            loc = _format_location(error)
            msg = (error.get("message") or "")[:100]
            lines.append(f"- `{loc}` - {msg}")
        if len(diff.fixed) > 20:
            lines.append(f"- ... and {len(diff.fixed) - 20} more")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Status badge
    if diff.has_new_errors:
        lines.append("**Status:** :x: New errors introduced")
    elif diff.current_errors == 0:
        lines.append("**Status:** :white_check_mark: No errors")
    elif len(diff.fixed) > 0:
        lines.append("**Status:** :white_check_mark: Errors fixed, none new")
    else:
        lines.append("**Status:** :white_check_mark: No new errors")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by [blq](https://github.com/teaguesterling/blq)*")

    return "\n".join(lines)


def _format_json_output(diff: DiffResult) -> str:
    """Format diff as JSON."""
    data = {
        "baseline_run_id": diff.baseline_run_id,
        "current_run_id": diff.current_run_id,
        "baseline_errors": diff.baseline_errors,
        "current_errors": diff.current_errors,
        "fixed_count": len(diff.fixed),
        "new_count": len(diff.new_errors),
        "has_new_errors": diff.has_new_errors,
        "delta": diff.delta,
        "new_errors": [
            {
                "ref_file": e.get("ref_file"),
                "ref_line": e.get("ref_line"),
                "message": e.get("message"),
                "error_code": e.get("error_code"),
                "fingerprint": e.get("fingerprint"),
            }
            for e in diff.new_errors[:50]
        ],
        "fixed": [
            {
                "ref_file": e.get("ref_file"),
                "ref_line": e.get("ref_line"),
                "message": e.get("message"),
                "fingerprint": e.get("fingerprint"),
            }
            for e in diff.fixed[:50]
        ],
    }
    return json.dumps(data, indent=2)


def cmd_ci_check(args: argparse.Namespace) -> None:
    """Check for new errors compared to baseline.

    Exit codes:
        0: No new errors (check passed)
        1: New errors introduced
    """
    store = get_store_for_args(args)

    # Find current run
    current_id = _find_current_run(store)
    if current_id is None:
        print("Error: No runs found.", file=sys.stderr)
        sys.exit(1)

    # Handle --fail-on-any (no baseline comparison)
    if getattr(args, "fail_on_any", False):
        current_errors = store.error_count(current_id)

        if getattr(args, "json", False):
            data = {
                "current_run_id": current_id,
                "current_errors": current_errors,
                "has_errors": current_errors > 0,
            }
            print(json.dumps(data, indent=2))
        else:
            if current_errors > 0:
                print(f"FAIL: {current_errors} errors in run #{current_id}")
            else:
                print(f"OK: No errors in run #{current_id}")

        sys.exit(1 if current_errors > 0 else 0)

    # Find baseline run
    baseline_spec = getattr(args, "baseline", None)
    baseline_id = _find_baseline_run(store, baseline_spec)

    if baseline_id is None:
        if baseline_spec:
            print(f"Warning: Baseline '{baseline_spec}' not found.", file=sys.stderr)
        else:
            print("Warning: No baseline found (no main/master branch runs).", file=sys.stderr)
        print("Running without baseline comparison.", file=sys.stderr)

    # Compute diff
    diff = _compute_diff(store, baseline_id, current_id)

    # Output
    if getattr(args, "json", False):
        print(_format_json_output(diff))
    else:
        if baseline_id is not None:
            print(f"Comparing run #{current_id} against baseline #{baseline_id}")
            print(f"  Baseline errors: {diff.baseline_errors}")
            print(f"  Current errors:  {diff.current_errors}")
            print(f"  Fixed: {len(diff.fixed)}, New: {len(diff.new_errors)}")
        else:
            print(f"Run #{current_id}: {diff.current_errors} errors (no baseline)")

        if diff.has_new_errors:
            print(f"\nFAIL: {len(diff.new_errors)} new errors introduced")
            for error in diff.new_errors[:10]:
                loc = _format_location(error)
                msg = (error.get("message") or "")[:80]
                print(f"  - {loc}: {msg}")
            if len(diff.new_errors) > 10:
                print(f"  ... and {len(diff.new_errors) - 10} more")
        else:
            print("\nOK: No new errors")

    # Exit code based on new errors
    sys.exit(1 if diff.has_new_errors else 0)


def _get_github_context() -> tuple[str | None, int | None]:
    """Get GitHub repository and PR number from environment.

    Returns:
        Tuple of (repo, pr_number) or (None, None) if not in PR context
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None, None

    # Try to get PR number from GITHUB_REF (refs/pull/123/merge)
    ref = os.environ.get("GITHUB_REF", "")
    match = re.match(r"refs/pull/(\d+)/", ref)
    if match:
        return repo, int(match.group(1))

    # Try GITHUB_PR_NUMBER (set by some workflows)
    pr_num = os.environ.get("GITHUB_PR_NUMBER")
    if pr_num and pr_num.isdigit():
        return repo, int(pr_num)

    return repo, None


def cmd_ci_comment(args: argparse.Namespace) -> None:
    """Post error summary as PR comment.

    Requires GITHUB_TOKEN environment variable for authentication.
    """
    # Import GitHub client
    try:
        from blq.github import GitHubClient, GitHubError
    except ImportError:
        print("Error: GitHub client not available.", file=sys.stderr)
        sys.exit(1)

    # Get GitHub token
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN environment variable not set.", file=sys.stderr)
        sys.exit(1)

    # Get GitHub context
    repo, pr_number = _get_github_context()
    if not repo:
        print(
            "Error: GITHUB_REPOSITORY not set. Are you running in GitHub Actions?",
            file=sys.stderr,
        )
        sys.exit(1)
    if pr_number is None:
        print(
            "Error: Could not determine PR number. Is this a pull_request event?",
            file=sys.stderr,
        )
        sys.exit(1)

    store = get_store_for_args(args)

    # Find current run
    current_id = _find_current_run(store)
    if current_id is None:
        print("Error: No runs found.", file=sys.stderr)
        sys.exit(1)

    # Find baseline if --diff requested
    baseline_id = None
    if getattr(args, "diff", False):
        baseline_spec = getattr(args, "baseline", None)
        baseline_id = _find_baseline_run(store, baseline_spec)
        if baseline_id is None and baseline_spec:
            print(f"Warning: Baseline '{baseline_spec}' not found.", file=sys.stderr)

    # Compute diff (or just current errors if no baseline)
    diff = _compute_diff(store, baseline_id, current_id)

    # Format comment
    comment_body = _format_pr_comment(diff, include_fixed=bool(baseline_id))

    # Create or update comment
    client = GitHubClient(token)
    marker = "<!-- blq-ci-comment -->"

    try:
        if getattr(args, "update", False):
            # Try to find and update existing comment
            existing_id = client.find_comment(repo, pr_number, marker)
            if existing_id:
                client.update_comment(repo, existing_id, f"{marker}\n{comment_body}")
                print(f"Updated comment on PR #{pr_number}")
            else:
                client.create_comment(repo, pr_number, f"{marker}\n{comment_body}")
                print(f"Created comment on PR #{pr_number}")
        else:
            client.create_comment(repo, pr_number, f"{marker}\n{comment_body}")
            print(f"Created comment on PR #{pr_number}")

    except GitHubError as e:
        print(f"Error posting comment: {e}", file=sys.stderr)
        sys.exit(1)


# ============================================================================
# CI Script Generation
# ============================================================================


def _generate_simple_script(cmd: RegisteredCommand, shell: str = "bash") -> str:
    """Generate a script for a simple (non-template) command.

    Args:
        cmd: The registered command
        shell: Shell to use (bash, sh, zsh)

    Returns:
        Shell script content
    """
    shebang = f"#!/usr/bin/env {shell}"
    command = cmd.cmd or ""
    name = cmd.name

    script = f"""{shebang}
# Generated by blq ci generate
# Command: {name}
# Description: {cmd.description or "(no description)"}
#
# Usage: ./{name}.sh [extra args...]

set -euo pipefail

# Check if blq is available for log capture
if command -v blq &> /dev/null && [ -d ".lq" ]; then
    # Use blq to capture and parse output
    if [ $# -gt 0 ]; then
        blq run {name} -- "$@"
    else
        blq run {name}
    fi
else
    # Fallback: run command directly
    CMD={_shell_quote(command)}
    if [ $# -gt 0 ]; then
        CMD="$CMD $*"
    fi
    echo "Running: $CMD"
    eval "$CMD"
fi
"""
    return script


def _generate_template_script(cmd: RegisteredCommand, shell: str = "bash") -> str:
    """Generate a script for a parameterized template command.

    Args:
        cmd: The registered command (must be a template)
        shell: Shell to use (bash, sh, zsh)

    Returns:
        Shell script content with argument parsing
    """
    shebang = f"#!/usr/bin/env {shell}"
    template = cmd.tpl or ""

    # Find all parameters
    all_params = set(re.findall(r"\{(\w+)\}", template))
    required_params = all_params - set(cmd.defaults.keys())

    # Build usage string
    usage_parts = []
    for param in sorted(all_params):
        if param in required_params:
            usage_parts.append(f"--{param} <value>")
        else:
            default = cmd.defaults.get(param, "")
            usage_parts.append(f"[--{param} <value>]  (default: {default})")

    usage_str = " ".join(usage_parts)

    # Build default assignments
    default_lines = []
    for param in sorted(all_params):
        if param in cmd.defaults:
            default_lines.append(f"{param}={_shell_quote(cmd.defaults[param])}")
        else:
            default_lines.append(f'{param}=""')

    defaults_block = "\n".join(default_lines)

    # Build argument parser
    parse_cases = []
    for param in sorted(all_params):
        parse_cases.append(f"""\
        --{param})
            {param}="$2"
            shift 2
            ;;""")

    parse_block = "\n".join(parse_cases)

    # Build required checks
    required_checks = []
    for param in sorted(required_params):
        required_checks.append(f"""\
if [ -z "${param}" ]; then
    echo "Error: --{param} is required" >&2
    usage
    exit 1
fi""")

    required_block = "\n".join(required_checks)

    # Build substitution (escape braces for shell)
    # We'll use sed to replace {param} with $param value
    subst_lines = [f"CMD={_shell_quote(template)}"]
    for param in sorted(all_params):
        # Use sed to replace {param} with the shell variable value
        subst_lines.append(f'CMD=$(echo "$CMD" | sed "s/{{{param}}}/${param}/g")')

    subst_block = "\n".join(subst_lines)

    script = f"""{shebang}
# Generated by blq ci generate
# Command: {cmd.name}
# Description: {cmd.description or "(no description)"}
# Template: {template}
#
# Usage: ./{cmd.name}.sh {usage_str} [-- extra args...]

set -euo pipefail

usage() {{
    echo "Usage: $0 {usage_str} [-- extra args...]"
    echo ""
    echo "Parameters:"
"""

    # Add parameter descriptions to usage
    for param in sorted(all_params):
        if param in required_params:
            script += f'    echo "  --{param}    (required)"\n'
        else:
            default = cmd.defaults.get(param, "")
            script += f'    echo "  --{param}    (default: {default})"\n'

    script += f"""}}

# Defaults
{defaults_block}

# Extra args to append (after --)
EXTRA_ARGS=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
{parse_block}
        --)
            shift
            EXTRA_ARGS="$*"
            break
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

# Check required parameters
{required_block}

# Check if blq is available for log capture
if command -v blq &> /dev/null && [ -d ".lq" ]; then
    # Build blq run command with parameters
    BLQ_ARGS="{cmd.name}"
"""

    # Add parameter forwarding for blq
    for param in sorted(all_params):
        script += f'    BLQ_ARGS="$BLQ_ARGS --{param} ${param}"\n'

    script += """
    # Append extra args
    if [ -n "$EXTRA_ARGS" ]; then
        BLQ_ARGS="$BLQ_ARGS -- $EXTRA_ARGS"
    fi

    blq run $BLQ_ARGS
else
    # Fallback: run command directly
"""
    script += f"    {subst_block}\n"
    script += """
    # Append extra args
    if [ -n "$EXTRA_ARGS" ]; then
        CMD="$CMD $EXTRA_ARGS"
    fi

    echo "Running: $CMD"
    eval "$CMD"
fi
"""
    return script


def _shell_quote(s: str) -> str:
    """Quote a string for shell use."""
    # Use single quotes, escaping any single quotes in the string
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _generate_script(cmd: RegisteredCommand, shell: str = "bash") -> str:
    """Generate a shell script for a registered command.

    Args:
        cmd: The registered command
        shell: Shell to use (bash, sh, zsh)

    Returns:
        Shell script content
    """
    if cmd.is_template:
        return _generate_template_script(cmd, shell)
    else:
        return _generate_simple_script(cmd, shell)


def cmd_ci_generate(args: argparse.Namespace) -> None:
    """Generate standalone shell scripts for CI environments.

    Creates executable shell scripts from registered commands that can
    run without blq installed. Supports both simple commands and
    parameterized template commands.
    """
    config = BlqConfig.find()
    if config is None:
        print("Error: .lq not initialized. Run 'blq init' first.", file=sys.stderr)
        sys.exit(1)

    commands = config.commands
    if not commands:
        print("No registered commands found.", file=sys.stderr)
        sys.exit(1)

    # Get command names to generate (all if not specified)
    command_names = getattr(args, "commands", None) or list(commands.keys())
    shell = getattr(args, "shell", "bash")
    output_dir = Path(getattr(args, "output", "."))
    force = getattr(args, "force", False)
    dry_run = getattr(args, "dry_run", False)

    # Validate command names
    missing = set(command_names) - set(commands.keys())
    if missing:
        print(f"Error: Unknown commands: {', '.join(sorted(missing))}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(commands.keys()))}", file=sys.stderr)
        sys.exit(1)

    # Create output directory if needed
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    skipped = []

    for name in sorted(command_names):
        cmd = commands[name]
        script_path = output_dir / f"{name}.sh"

        if script_path.exists() and not force:
            skipped.append(name)
            continue

        script_content = _generate_script(cmd, shell)

        if dry_run:
            print(f"\n{'=' * 60}")
            print(f"# {script_path}")
            print(f"{'=' * 60}")
            print(script_content)
            generated.append(name)
        else:
            script_path.write_text(script_content)
            script_path.chmod(0o755)  # Make executable
            generated.append(name)

    # Summary
    if not dry_run:
        if generated:
            print(f"Generated {len(generated)} script(s) in {output_dir}/:")
            for name in generated:
                cmd = commands[name]
                marker = " (template)" if cmd.is_template else ""
                print(f"  {name}.sh{marker}")

        if skipped:
            print(f"\nSkipped {len(skipped)} existing script(s) (use --force to overwrite):")
            for name in skipped:
                print(f"  {name}.sh")
    else:
        print(f"\n[DRY RUN] Would generate {len(generated)} script(s)")
