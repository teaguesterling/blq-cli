"""
Hook script generation for blq.

Generates portable shell scripts from registered commands that can run
with or without blq installed.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader

if TYPE_CHECKING:
    from blq.commands.core import RegisteredCommand

# Package version - imported lazily to avoid circular imports
_blq_version: str | None = None


def get_blq_version() -> str:
    """Get blq version string."""
    global _blq_version
    if _blq_version is None:
        try:
            from importlib.metadata import version

            _blq_version = version("blq-cli")
        except Exception:
            _blq_version = "dev"
    return _blq_version


def get_template_env() -> Environment:
    """Get Jinja2 environment with templates loaded."""
    return Environment(
        loader=PackageLoader("blq", "templates"),
        autoescape=False,  # Shell scripts, not HTML
        keep_trailing_newline=True,
    )


def compute_command_checksum(cmd: RegisteredCommand) -> str:
    """Compute a checksum of the command definition.

    Used to detect when a command has changed since script generation.
    """
    # Include all fields that affect the generated script
    parts = [
        cmd.name,
        cmd.cmd or "",
        cmd.tpl or "",
        str(sorted(cmd.defaults.items())),
    ]
    content = "|".join(parts)
    return hashlib.sha256(content.encode()).hexdigest()[:12]


def render_standalone_cmd_template(tpl: str) -> str:
    """Convert a blq template to a shell-variable-interpolated string.

    Example:
        "pytest {path} -v" -> "pytest ${path} -v"
    """
    # Replace {param} with ${param}
    return re.sub(r"\{(\w+)\}", r"${\1}", tpl)


def generate_hook_script(cmd: RegisteredCommand) -> str:
    """Generate a hook script for a registered command.

    Args:
        cmd: The registered command to generate a script for.

    Returns:
        The generated shell script content.
    """
    env = get_template_env()
    template = env.get_template("hook_script.sh.j2")

    # Prepare template context
    checksum = compute_command_checksum(cmd)
    defaults = cmd.defaults.copy()

    # Format defaults as string for comment
    defaults_str = ", ".join(f"{k}={v}" for k, v in defaults.items())

    # Build context
    context = {
        "command_name": cmd.name,
        "blq_version": get_blq_version(),
        "checksum": checksum,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "defaults": defaults,
        "defaults_str": defaults_str,
    }

    if cmd.tpl:
        # Template command
        context["template"] = cmd.tpl
        context["cmd"] = None
        context["standalone_cmd_template"] = render_standalone_cmd_template(cmd.tpl)
    else:
        # Simple command
        context["template"] = None
        context["cmd"] = cmd.cmd

    return template.render(**context)


def generate_git_hook(
    commands: list[str],
    hook_name: str = "pre-commit",
) -> str:
    """Generate a git hook script that runs multiple hook scripts.

    Args:
        commands: List of command names to run.
        hook_name: Git hook name (pre-commit, pre-push, etc.)

    Returns:
        The generated git hook script content.
    """
    env = get_template_env()
    template = env.get_template("git_hook.sh.j2")

    return template.render(
        blq_version=get_blq_version(),
        hook_name=hook_name,
        commands=commands,
    )


def get_hooks_dir(lq_path: Path) -> Path:
    """Get the hooks directory path, creating if needed."""
    hooks_dir = lq_path / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    return hooks_dir


def write_hook_script(
    cmd: RegisteredCommand,
    lq_path: Path,
    force: bool = False,
) -> tuple[Path, bool]:
    """Generate and write a hook script for a command.

    Args:
        cmd: The registered command.
        lq_path: Path to .lq directory.
        force: Overwrite existing script even if unchanged.

    Returns:
        Tuple of (script_path, was_written).
    """
    hooks_dir = get_hooks_dir(lq_path)
    script_path = hooks_dir / f"{cmd.name}.sh"

    # Generate new content
    new_content = generate_hook_script(cmd)

    # Check if we need to write
    if script_path.exists() and not force:
        existing_content = script_path.read_text()
        if existing_content == new_content:
            return script_path, False

        # Check if script was manually modified (different checksum)
        existing_checksum = extract_checksum_from_script(existing_content)
        expected_checksum = compute_command_checksum(cmd)
        if existing_checksum and existing_checksum != expected_checksum:
            # Script exists but command changed - warn but overwrite
            pass

    # Write the script
    script_path.write_text(new_content)
    script_path.chmod(0o755)  # Make executable

    return script_path, True


def extract_checksum_from_script(content: str) -> str | None:
    """Extract the checksum from an existing script.

    Returns None if not found.
    """
    match = re.search(r'^# Checksum: ([a-f0-9]+)', content, re.MULTILINE)
    if match:
        return match.group(1)

    # Also check for embedded variable
    match = re.search(r'^BLQ_CHECKSUM="([a-f0-9]+)"', content, re.MULTILINE)
    if match:
        return match.group(1)

    return None


def check_script_staleness(
    cmd: RegisteredCommand,
    lq_path: Path,
) -> tuple[bool, str | None]:
    """Check if a hook script is stale (command changed since generation).

    Args:
        cmd: The registered command.
        lq_path: Path to .lq directory.

    Returns:
        Tuple of (is_stale, script_checksum).
        is_stale is True if the script exists but has different checksum.
        script_checksum is the checksum in the existing script, or None if not found.
    """
    hooks_dir = get_hooks_dir(lq_path)
    script_path = hooks_dir / f"{cmd.name}.sh"

    if not script_path.exists():
        return False, None

    content = script_path.read_text()
    script_checksum = extract_checksum_from_script(content)
    expected_checksum = compute_command_checksum(cmd)

    if script_checksum is None:
        # Can't determine staleness without checksum
        return False, None

    return script_checksum != expected_checksum, script_checksum
