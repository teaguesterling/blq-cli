"""
Hooks integration for blq.

Provides commands to install/remove hooks for various targets:
- git: pre-commit hooks that capture build/test output
- github/gitlab/drone: CI workflow files
- claude-code: Claude Code hooks for agent integration

Also generates portable hook scripts that can run with or without blq.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from blq.commands.core import BlqConfig
from blq.commands.hooks_gen import (
    check_script_staleness,
    compute_command_checksum,
    generate_git_hook,
    get_hooks_dir,
    write_hook_script,
)

# Marker to identify blq-managed hooks
HOOK_MARKER = "# blq-managed-hook"

# Pre-commit hook script template
PRECOMMIT_HOOK_TEMPLATE = f"""#!/bin/sh
{HOOK_MARKER}
# blq pre-commit hook - auto-generated
# To remove: blq hooks remove

# Run configured pre-commit commands
blq hooks run

# Always exit 0 (non-blocking mode)
# Future: exit with error count if block_on_new_errors is enabled
exit 0
"""


def _find_git_dir() -> Path | None:
    """Find .git directory from cwd or parents.

    Returns:
        Path to .git directory, or None if not in a git repository.
    """
    cwd = Path.cwd()
    for p in [cwd, *list(cwd.parents)]:
        git_dir = p / ".git"
        if git_dir.is_dir():
            return git_dir
    return None


def _is_blq_hook(hook_path: Path) -> bool:
    """Check if a hook file was created by blq.

    Args:
        hook_path: Path to the hook file.

    Returns:
        True if the hook contains our marker.
    """
    if not hook_path.exists():
        return False
    try:
        content = hook_path.read_text()
        return HOOK_MARKER in content
    except (OSError, UnicodeDecodeError):
        return False


def _get_precommit_commands(config: BlqConfig) -> list[str]:
    """Get list of commands configured for pre-commit hook.

    Args:
        config: BlqConfig instance.

    Returns:
        List of command names to run.
    """
    hooks_config = config.hooks_config
    if not hooks_config:
        return []
    precommit = hooks_config.get("pre-commit", [])
    if isinstance(precommit, list):
        return precommit
    return []


def cmd_hooks_generate(args: argparse.Namespace) -> None:
    """Generate hook scripts from registered commands.

    Creates portable shell scripts in .lq/hooks/ that can run
    with or without blq installed.
    """
    config = BlqConfig.ensure()
    force = getattr(args, "force", False)
    command_names = getattr(args, "commands", [])

    if not command_names:
        print("Error: No commands specified.", file=sys.stderr)
        print("Usage: blq hooks generate <command> [command...]", file=sys.stderr)
        sys.exit(1)

    # Validate commands exist
    missing = [c for c in command_names if c not in config.commands]
    if missing:
        print(f"Error: Commands not registered: {', '.join(missing)}", file=sys.stderr)
        print("Register commands first with: blq register <name> <cmd>", file=sys.stderr)
        sys.exit(1)

    hooks_dir = get_hooks_dir(config.lq_dir)
    generated = []
    skipped = []
    stale_warnings = []

    for cmd_name in command_names:
        cmd = config.commands[cmd_name]

        # Check for staleness before overwriting
        is_stale, old_checksum = check_script_staleness(cmd, config.lq_dir)
        if is_stale and not force:
            new_checksum = compute_command_checksum(cmd)
            stale_warnings.append(
                f"  {cmd_name}: command changed (was: {old_checksum}, now: {new_checksum})"
            )

        script_path, was_written = write_hook_script(cmd, config.lq_dir, force=force)

        if was_written:
            generated.append(cmd_name)
        else:
            skipped.append(cmd_name)

    # Report results
    if generated:
        print(f"Generated {len(generated)} hook script(s) in {hooks_dir}/")
        for name in generated:
            print(f"  {name}.sh")

    if skipped:
        print(f"Skipped {len(skipped)} unchanged script(s): {', '.join(skipped)}")

    if stale_warnings:
        print("\nWarning: Some scripts were stale and have been regenerated:")
        for warning in stale_warnings:
            print(warning)


def cmd_hooks_install(args: argparse.Namespace) -> None:
    """Install hooks to a target (git, github, gitlab).

    For git: installs a pre-commit hook that calls .lq/hooks/*.sh scripts.
    For github/gitlab: generates workflow files.
    """
    config = BlqConfig.ensure()
    target = getattr(args, "target", "git")
    force = getattr(args, "force", False)
    command_names = getattr(args, "commands", [])
    hook_name = getattr(args, "hook", "pre-commit")

    # Claude Code hooks don't need command names
    if target == "claude-code":
        _install_claude_code_hooks(force)
        return

    # If no commands specified, fall back to legacy behavior for git
    if target == "git" and not command_names:
        # Legacy mode: use config-based pre-commit commands
        _cmd_hooks_install_legacy(args)
        return

    if not command_names:
        print("Error: No commands specified.", file=sys.stderr)
        print(f"Usage: blq hooks install {target} <command> [command...]", file=sys.stderr)
        sys.exit(1)

    # Validate commands exist
    missing = [c for c in command_names if c not in config.commands]
    if missing:
        print(f"Error: Commands not registered: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Generate hook scripts first (if not already generated)
    for cmd_name in command_names:
        cmd = config.commands[cmd_name]
        script_path = get_hooks_dir(config.lq_dir) / f"{cmd_name}.sh"
        if not script_path.exists() or force:
            write_hook_script(cmd, config.lq_dir, force=force)
            print(f"Generated .lq/hooks/{cmd_name}.sh")

    # Install to target
    if target == "git":
        _install_git_hook(config, command_names, hook_name, force)
    elif target == "github":
        _install_github_workflow(config, command_names, force)
    elif target == "gitlab":
        _install_gitlab_ci(config, command_names, force)
    elif target == "drone":
        _install_drone_ci(config, command_names, force)
    elif target == "claude-code":
        # Claude Code hooks don't need command names
        _install_claude_code_hooks(force)
    else:
        print(f"Error: Unknown target '{target}'", file=sys.stderr)
        print("Available targets: git, github, gitlab, drone, claude-code", file=sys.stderr)
        sys.exit(1)


def _cmd_hooks_install_legacy(args: argparse.Namespace) -> None:
    """Legacy install behavior: use PRECOMMIT_HOOK_TEMPLATE.

    DEPRECATED: This mode is deprecated. Use:
        blq hooks install git <command> [command...]
    instead.
    """
    import warnings

    warnings.warn(
        "Legacy hooks install is deprecated. Use: blq hooks install git <commands...>",
        DeprecationWarning,
        stacklevel=2,
    )
    print(
        "Note: Consider using 'blq hooks install git <commands>' for portable hook scripts.",
        file=sys.stderr,
    )
    config = BlqConfig.ensure()

    # Find git directory
    git_dir = _find_git_dir()
    if git_dir is None:
        print("Error: Not in a git repository.", file=sys.stderr)
        sys.exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    # Check if hook exists
    if hook_path.exists():
        if _is_blq_hook(hook_path):
            if not getattr(args, "force", False):
                print("blq pre-commit hook already installed.")
                print("Use --force to reinstall.")
                return
        else:
            if not getattr(args, "force", False):
                print("Error: Pre-commit hook exists but was not created by blq.", file=sys.stderr)
                print("Use --force to overwrite (existing hook will be lost).", file=sys.stderr)
                sys.exit(1)
            print("Warning: Overwriting existing pre-commit hook.")

    # Write hook script
    hook_path.write_text(PRECOMMIT_HOOK_TEMPLATE)
    hook_path.chmod(0o755)

    # Show status
    commands = _get_precommit_commands(config)
    print(f"Installed pre-commit hook at {hook_path}")
    if commands:
        print(f"Configured commands: {', '.join(commands)}")
    else:
        print("No commands configured yet.")
        print("Add commands to .lq/config.toml:")
        print("  [hooks]")
        print('  pre-commit = ["lint", "test"]')


def _install_git_hook(
    config: BlqConfig,
    commands: list[str],
    hook_name: str,
    force: bool,
) -> None:
    """Install a git hook that calls .lq/hooks/*.sh scripts."""
    git_dir = _find_git_dir()
    if git_dir is None:
        print("Error: Not in a git repository.", file=sys.stderr)
        sys.exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / hook_name

    # Check if hook exists
    if hook_path.exists():
        if _is_blq_hook(hook_path):
            if not force:
                print(f"blq {hook_name} hook already installed.")
                print("Use --force to reinstall.")
                return
        else:
            if not force:
                print(
                    f"Error: {hook_name} hook exists but was not created by blq.",
                    file=sys.stderr,
                )
                print("Use --force to overwrite.", file=sys.stderr)
                sys.exit(1)
            print(f"Warning: Overwriting existing {hook_name} hook.")

    # Generate the git hook content
    hook_content = generate_git_hook(commands, hook_name)
    hook_path.write_text(hook_content)
    hook_path.chmod(0o755)

    print(f"Installed {hook_name} hook at {hook_path}")
    print(f"Commands: {', '.join(commands)}")


def _install_github_workflow(
    config: BlqConfig,
    commands: list[str],
    force: bool,
) -> None:
    """Generate a GitHub Actions workflow file."""
    workflow_dir = Path(".github/workflows")
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflow_dir / "blq.yml"

    if workflow_path.exists() and not force:
        print(f"Error: {workflow_path} already exists.", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    # Generate workflow content
    from blq.commands.hooks_gen import get_blq_version

    jobs = []
    for cmd_name in commands:
        cmd = config.commands[cmd_name]
        # Determine if this needs Python setup
        needs_python = _cmd_needs_python(cmd)
        jobs.append(
            {
                "name": cmd_name,
                "needs_python": needs_python,
            }
        )

    # Simple template for now
    workflow_content = f"""# Generated by blq v{get_blq_version()}
# Regenerate with: blq hooks install github {" ".join(commands)} --force
name: blq checks

on: [push, pull_request]

jobs:
"""
    for job in jobs:
        workflow_content += f"""  {job["name"]}:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""
        if job["needs_python"]:
            workflow_content += """      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install dependencies
        run: pip install -e ".[dev]"
"""
        workflow_content += f"""      - name: Run {job["name"]}
        run: .lq/hooks/{job["name"]}.sh --via=standalone --metadata=footer
"""

    workflow_path.write_text(workflow_content)
    print(f"Generated {workflow_path}")
    print(f"Commands: {', '.join(commands)}")


def _install_gitlab_ci(
    config: BlqConfig,
    commands: list[str],
    force: bool,
) -> None:
    """Generate a GitLab CI fragment file."""
    ci_path = Path(".gitlab-ci.blq.yml")

    if ci_path.exists() and not force:
        print(f"Error: {ci_path} already exists.", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    from blq.commands.hooks_gen import get_blq_version

    ci_content = f"""# Generated by blq v{get_blq_version()}
# Include in .gitlab-ci.yml: include: '.gitlab-ci.blq.yml'
# Regenerate with: blq hooks install gitlab {" ".join(commands)} --force

"""
    for cmd_name in commands:
        ci_content += f"""blq-{cmd_name}:
  script:
    - .lq/hooks/{cmd_name}.sh --via=standalone --metadata=footer

"""

    ci_path.write_text(ci_content)
    print(f"Generated {ci_path}")
    print("Include in .gitlab-ci.yml with: include: '.gitlab-ci.blq.yml'")


def _install_drone_ci(
    config: BlqConfig,
    commands: list[str],
    force: bool,
) -> None:
    """Generate a Drone CI configuration file."""
    ci_path = Path(".drone.blq.yml")

    if ci_path.exists() and not force:
        print(f"Error: {ci_path} already exists.", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    from blq.commands.hooks_gen import get_blq_version

    ci_content = f"""# Generated by blq v{get_blq_version()}
# Include in .drone.yml or use directly as .drone.yml
# Regenerate with: blq hooks install drone {" ".join(commands)} --force

kind: pipeline
type: docker
name: blq-checks

steps:
"""
    for cmd_name in commands:
        ci_content += f"""  - name: {cmd_name}
    image: alpine
    commands:
      - .lq/hooks/{cmd_name}.sh --via=standalone --metadata=footer
"""

    ci_path.write_text(ci_content)
    print(f"Generated {ci_path}")
    print("Include in .drone.yml or use directly as .drone.yml")


def _cmd_needs_python(cmd) -> bool:
    """Check if a command likely needs Python setup."""
    template = cmd.tpl or cmd.cmd or ""
    python_indicators = ["pytest", "python", "pip", "ruff", "mypy", "black", "flake8"]
    return any(ind in template.lower() for ind in python_indicators)


def cmd_hooks_remove(args: argparse.Namespace) -> None:
    """Remove git pre-commit hook (legacy, delegates to uninstall)."""
    # Delegate to uninstall with git target
    args.target = "git"
    cmd_hooks_uninstall(args)


def cmd_hooks_uninstall(args: argparse.Namespace) -> None:
    """Uninstall hooks from a target (git, github, gitlab, drone, claude-code)."""
    target = getattr(args, "target", "git")
    hook_name = getattr(args, "hook", "pre-commit")

    if target == "git":
        _uninstall_git_hook(hook_name)
    elif target == "github":
        _uninstall_github_workflow()
    elif target == "gitlab":
        _uninstall_gitlab_ci()
    elif target == "drone":
        _uninstall_drone_ci()
    elif target == "claude-code":
        _uninstall_claude_code_hooks()
    else:
        print(f"Error: Unknown target '{target}'", file=sys.stderr)
        print("Available targets: git, github, gitlab, drone, claude-code", file=sys.stderr)
        sys.exit(1)


def _uninstall_git_hook(hook_name: str = "pre-commit") -> None:
    """Remove a git hook."""
    git_dir = _find_git_dir()
    if git_dir is None:
        print("Error: Not in a git repository.", file=sys.stderr)
        sys.exit(1)

    hook_path = git_dir / "hooks" / hook_name

    if not hook_path.exists():
        print(f"No {hook_name} hook installed.")
        return

    if not _is_blq_hook(hook_path):
        print(f"Error: {hook_name} hook was not created by blq.", file=sys.stderr)
        print("Remove manually if needed:", hook_path, file=sys.stderr)
        sys.exit(1)

    hook_path.unlink()
    print(f"Removed {hook_name} hook.")


def _uninstall_github_workflow() -> None:
    """Remove GitHub Actions workflow file."""
    workflow_path = Path(".github/workflows/blq.yml")

    if not workflow_path.exists():
        print("GitHub workflow not installed.")
        return

    workflow_path.unlink()
    print(f"Removed {workflow_path}")


def _uninstall_gitlab_ci() -> None:
    """Remove GitLab CI configuration file."""
    ci_path = Path(".gitlab-ci.blq.yml")

    if not ci_path.exists():
        print("GitLab CI configuration not installed.")
        return

    ci_path.unlink()
    print(f"Removed {ci_path}")


def _uninstall_drone_ci() -> None:
    """Remove Drone CI configuration file."""
    ci_path = Path(".drone.blq.yml")

    if not ci_path.exists():
        print("Drone CI configuration not installed.")
        return

    ci_path.unlink()
    print(f"Removed {ci_path}")


def cmd_hooks_status(args: argparse.Namespace) -> None:
    """Show hook status including generated scripts and installations."""
    config = BlqConfig.find()
    if config is None:
        print("blq not initialized.")
        return

    # Show generated hook scripts
    hooks_dir = config.lq_dir / "hooks"
    print("Hook Scripts (.lq/hooks/):")
    if hooks_dir.exists():
        scripts = sorted(hooks_dir.glob("*.sh"))
        if scripts:
            for script in scripts:
                cmd_name = script.stem
                if cmd_name in config.commands:
                    cmd = config.commands[cmd_name]
                    is_stale, _ = check_script_staleness(cmd, config.lq_dir)
                    status = "[stale]" if is_stale else "[ok]"
                    template = cmd.tpl or cmd.cmd or ""
                    # Truncate template for display
                    if len(template) > 40:
                        template = template[:37] + "..."
                    checksum = compute_command_checksum(cmd)[:8]
                    print(f"  {script.name:<20} {status:<8} {template} ({checksum})")
                else:
                    print(f"  {script.name:<20} [orphan] (command not registered)")
        else:
            print("  (none)")
    else:
        print("  (none)")

    # Show commands that could have scripts generated
    print()
    print("Registered Commands (no script yet):")
    commands_without_scripts = []
    for cmd_name in config.commands:
        script_path = hooks_dir / f"{cmd_name}.sh" if hooks_dir.exists() else None
        if script_path is None or not script_path.exists():
            commands_without_scripts.append(cmd_name)

    if commands_without_scripts:
        for cmd_name in commands_without_scripts[:5]:  # Show first 5
            cmd = config.commands[cmd_name]
            template = cmd.tpl or cmd.cmd or ""
            if len(template) > 40:
                template = template[:37] + "..."
            print(f"  {cmd_name}: {template}")
        if len(commands_without_scripts) > 5:
            print(f"  ... and {len(commands_without_scripts) - 5} more")
    else:
        print("  (all commands have scripts)")

    # Show git hook status
    print()
    git_dir = _find_git_dir()
    print("Git Hooks:")
    if git_dir is None:
        print("  (not a git repository)")
    else:
        for hook_name in ["pre-commit", "pre-push"]:
            hook_path = git_dir / "hooks" / hook_name
            if hook_path.exists():
                if _is_blq_hook(hook_path):
                    # Try to extract commands from hook
                    content = hook_path.read_text()
                    import re

                    matches = re.findall(r"\.lq/hooks/(\w+)\.sh", content)
                    cmds = ", ".join(matches) if matches else "?"
                    print(f"  {hook_name:<12} [installed] {cmds}")
                else:
                    print(f"  {hook_name:<12} [external]")
            else:
                print(f"  {hook_name:<12} [not installed]")

    # Show CI workflow status
    print()
    print("CI Workflows:")
    github_workflow = Path(".github/workflows/blq.yml")
    gitlab_ci = Path(".gitlab-ci.blq.yml")
    drone_ci = Path(".drone.blq.yml")

    if github_workflow.exists():
        print(f"  github       [installed] {github_workflow}")
    else:
        print("  github       [not installed]")

    if gitlab_ci.exists():
        print(f"  gitlab       [installed] {gitlab_ci}")
    else:
        print("  gitlab       [not installed]")

    if drone_ci.exists():
        print(f"  drone        [installed] {drone_ci}")
    else:
        print("  drone        [not installed]")

    # Claude Code hooks status
    print()
    print("Claude Code Hooks:")
    claude_hook = Path(".claude/hooks/blq-suggest.sh")
    if claude_hook.exists():
        print(f"  suggest      [installed] {claude_hook}")
    else:
        print("  suggest      [not installed]")

    # Legacy config-based hooks
    legacy_commands = _get_precommit_commands(config)
    if legacy_commands:
        print()
        print("Legacy Config (hooks.pre-commit in config.toml):")
        for cmd in legacy_commands:
            print(f"  - {cmd}")


def cmd_hooks_run(args: argparse.Namespace) -> None:
    """Run pre-commit hook commands.

    This is called by the git hook script. It runs all configured
    commands and displays a summary.
    """
    config = BlqConfig.find()
    if config is None:
        # Silently exit if not in a blq project
        return

    commands = _get_precommit_commands(config)
    if not commands:
        return

    print("blq: Running pre-commit checks...")
    print()

    results: list[tuple[str, bool, int]] = []  # (name, success, error_count)

    for cmd_name in commands:
        if cmd_name not in config.commands:
            print(f"  {cmd_name}: (not registered, skipping)")
            continue

        # Run the command via blq run
        result = subprocess.run(
            ["blq", "run", "--quiet", "--json", cmd_name],
            capture_output=True,
            text=True,
        )

        # Parse result
        try:
            import json

            data = json.loads(result.stdout) if result.stdout.strip() else {}
            status = data.get("status", "FAIL" if result.returncode != 0 else "OK")
            error_count = len(data.get("errors", []))
            success = status == "OK"
        except json.JSONDecodeError:
            success = result.returncode == 0
            error_count = 0 if success else 1
            status = "OK" if success else "FAIL"

        results.append((cmd_name, success, error_count))

        # Print status
        if success:
            print(f"  {cmd_name}: OK")
        else:
            print(f"  {cmd_name}: FAIL ({error_count} errors)")

    print()

    # Summary
    failed = [r for r in results if not r[1]]
    if failed:
        total_errors = sum(r[2] for r in failed)
        print(f"Pre-commit: {len(failed)} command(s) failed, {total_errors} error(s)")
        print("Run 'blq errors' to see details.")
    else:
        print("Pre-commit: all checks passed")


def cmd_hooks_add(args: argparse.Namespace) -> None:
    """Add a command to the pre-commit hook."""
    config = BlqConfig.ensure()

    cmd_name = args.command

    # Verify command is registered
    if cmd_name not in config.commands:
        print(f"Warning: '{cmd_name}' is not a registered command.", file=sys.stderr)
        print('Register it first with: blq register {cmd_name} "<command>"', file=sys.stderr)

    # Load current hooks config
    hooks_config = config.hooks_config.copy() if config.hooks_config else {}
    precommit = hooks_config.get("pre-commit", [])
    if not isinstance(precommit, list):
        precommit = []

    if cmd_name in precommit:
        print(f"'{cmd_name}' is already in pre-commit hooks.")
        return

    precommit.append(cmd_name)
    hooks_config["pre-commit"] = precommit
    config._hooks_config = hooks_config
    config.save()

    print(f"Added '{cmd_name}' to pre-commit hooks.")


def cmd_hooks_list(args: argparse.Namespace) -> None:
    """List commands configured for pre-commit hook."""
    config = BlqConfig.find()
    if config is None:
        return

    commands = _get_precommit_commands(config)
    for cmd in commands:
        print(cmd)


# =============================================================================
# Claude Code Hooks
# =============================================================================

# Hook script content for Claude Code suggest hook
CLAUDE_SUGGEST_HOOK = """#!/bin/bash
# Claude Code PostToolUse hook for Bash commands
# Suggests using blq MCP run tool when a matching registered command is found
# Installed by: blq hooks install claude-code

set -e

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Skip if no command, blq not available, or MCP not configured
[[ -z "$COMMAND" ]] && exit 0
command -v blq >/dev/null 2>&1 || exit 0
[[ ! -d .lq ]] && exit 0
[[ ! -f .mcp.json ]] && exit 0

# Get suggestion from blq
SUGGESTION=$(blq commands suggest "$COMMAND" --json 2>/dev/null || true)

if [[ -n "$SUGGESTION" ]]; then
    TIP=$(echo "$SUGGESTION" | jq -r '.tip // empty')
    MCP_TOOL=$(echo "$SUGGESTION" | jq -r '.mcp_tool // empty')

    jq -n --arg tip "$TIP" --arg mcp "$MCP_TOOL" '{
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: "Tip: Use blq MCP tool \\($mcp) instead. \\($tip)"
        }
    }'
fi

exit 0
"""


def _install_claude_code_hooks(force: bool = False) -> bool:
    """Install Claude Code hooks for blq integration.

    Installs:
    - .claude/hooks/blq-suggest.sh: PostToolUse hook that suggests using blq MCP tools

    Returns True if any hooks were installed/updated.
    """
    hooks_dir = Path(".claude/hooks")
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "blq-suggest.sh"
    settings_file = Path(".claude/settings.json")

    # Write hook script
    hook_existed = hook_file.exists()
    if not hook_existed or force:
        hook_file.write_text(CLAUDE_SUGGEST_HOOK)
        hook_file.chmod(0o755)
        print(f"{'Updated' if hook_existed else 'Created'} {hook_file}")

    # Update .claude/settings.json
    hook_config = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": ".claude/hooks/blq-suggest.sh"}],
    }

    settings_updated = False
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = json.load(f)
        except json.JSONDecodeError:
            settings = {}
    else:
        settings = {}

    if "hooks" not in settings:
        settings["hooks"] = {}
    if "PostToolUse" not in settings["hooks"]:
        settings["hooks"]["PostToolUse"] = []

    # Check if hook already registered
    post_hooks = settings["hooks"]["PostToolUse"]
    blq_hook_exists = any(
        h.get("matcher") == "Bash"
        and any(hh.get("command", "").endswith("blq-suggest.sh") for hh in h.get("hooks", []))
        for h in post_hooks
    )

    if not blq_hook_exists:
        post_hooks.append(hook_config)
        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=2)
        settings_updated = True
        print(f"Registered hook in {settings_file}")
    elif not hook_existed:
        print(f"Hook already registered in {settings_file}")

    if not hook_existed or settings_updated:
        print("\nClaude Code hooks installed:")
        print("  - blq-suggest.sh: suggests using blq MCP tools for registered commands")
        return True

    print("Claude Code hooks already installed. Use --force to reinstall.")
    return False


def _uninstall_claude_code_hooks() -> None:
    """Remove Claude Code hooks."""
    hook_file = Path(".claude/hooks/blq-suggest.sh")
    settings_file = Path(".claude/settings.json")

    removed_any = False

    if hook_file.exists():
        hook_file.unlink()
        print(f"Removed {hook_file}")
        removed_any = True

    # Remove from settings.json
    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = json.load(f)

            if "hooks" in settings and "PostToolUse" in settings["hooks"]:
                post_hooks = settings["hooks"]["PostToolUse"]
                original_len = len(post_hooks)
                settings["hooks"]["PostToolUse"] = [
                    h
                    for h in post_hooks
                    if not (
                        h.get("matcher") == "Bash"
                        and any(
                            hh.get("command", "").endswith("blq-suggest.sh")
                            for hh in h.get("hooks", [])
                        )
                    )
                ]
                if len(settings["hooks"]["PostToolUse"]) < original_len:
                    with open(settings_file, "w") as f:
                        json.dump(settings, f, indent=2)
                    print(f"Removed hook registration from {settings_file}")
                    removed_any = True
        except json.JSONDecodeError:
            pass

    if not removed_any:
        print("Claude Code hooks not installed.")
