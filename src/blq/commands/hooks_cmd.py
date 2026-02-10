"""
Git hooks integration for blq.

Provides commands to install/remove git pre-commit hooks that
automatically capture build/test output. Also generates portable
hook scripts that can run with or without blq installed.
"""

from __future__ import annotations

import argparse
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
    else:
        print(f"Error: Unknown target '{target}'", file=sys.stderr)
        print("Available targets: git, github, gitlab", file=sys.stderr)
        sys.exit(1)


def _cmd_hooks_install_legacy(args: argparse.Namespace) -> None:
    """Legacy install behavior: use PRECOMMIT_HOOK_TEMPLATE."""
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
    workflow_path = workflow_dir / "blq-checks.yml"

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
        ci_content += f"""{cmd_name}:
  script:
    - .lq/hooks/{cmd_name}.sh --via=standalone --metadata=footer

"""

    ci_path.write_text(ci_content)
    print(f"Generated {ci_path}")
    print("Include in .gitlab-ci.yml with: include: '.gitlab-ci.blq.yml'")


def _cmd_needs_python(cmd) -> bool:
    """Check if a command likely needs Python setup."""
    template = cmd.tpl or cmd.cmd or ""
    python_indicators = ["pytest", "python", "pip", "ruff", "mypy", "black", "flake8"]
    return any(ind in template.lower() for ind in python_indicators)


def cmd_hooks_remove(args: argparse.Namespace) -> None:
    """Remove git pre-commit hook."""
    git_dir = _find_git_dir()
    if git_dir is None:
        print("Error: Not in a git repository.", file=sys.stderr)
        sys.exit(1)

    hook_path = git_dir / "hooks" / "pre-commit"

    if not hook_path.exists():
        print("No pre-commit hook installed.")
        return

    if not _is_blq_hook(hook_path):
        print("Error: Pre-commit hook was not created by blq.", file=sys.stderr)
        print("Remove manually if needed:", hook_path, file=sys.stderr)
        sys.exit(1)

    hook_path.unlink()
    print("Removed pre-commit hook.")


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
    github_workflow = Path(".github/workflows/blq-checks.yml")
    gitlab_ci = Path(".gitlab-ci.blq.yml")

    if github_workflow.exists():
        print(f"  github       [installed] {github_workflow}")
    else:
        print("  github       [not installed]")

    if gitlab_ci.exists():
        print(f"  gitlab       [installed] {gitlab_ci}")
    else:
        print("  gitlab       [not installed]")

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
