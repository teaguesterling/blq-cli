"""
MCP (Model Context Protocol) commands for blq.

Commands:
- blq mcp install: Create or update .mcp.json configuration
- blq mcp serve: Start the MCP server

Shared utilities:
- ensure_mcp_config(): Merge blq_mcp entry into .mcp.json
- ensure_claude_md(): Inject blq agent instructions into CLAUDE.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MCP_SERVER_KEY = "blq_mcp"

BLQ_MCP_CONFIG = {
    "command": "blq",
    "args": ["mcp", "serve"],
}

CLAUDE_MD_START_MARKER = "<!-- blq:agent-instructions -->"
CLAUDE_MD_END_MARKER = "<!-- /blq:agent-instructions -->"

CLAUDE_MD_INSTRUCTIONS = """\
<!-- blq:agent-instructions -->
## blq - Build Log Query

Run builds and tests via blq MCP tools, not via Bash directly:
- `mcp__blq_mcp__commands` - list available commands
- `mcp__blq_mcp__run` - run a registered command (e.g., `run(command="test")`)
- `mcp__blq_mcp__register_command` - register new commands
- `mcp__blq_mcp__status` - check current build/test status
- `mcp__blq_mcp__errors` - view errors from runs
- `mcp__blq_mcp__info` - detailed run info (supports relative refs like `+1`, `latest`)
<!-- /blq:agent-instructions -->"""


def ensure_mcp_config(mcp_file: Path, force: bool = False) -> bool:
    """Add or update blq_mcp entry in .mcp.json, preserving other servers.

    Args:
        mcp_file: Path to .mcp.json
        force: If True, overwrite existing blq_mcp entry even if different

    Returns:
        True if the file was created or updated, False if already correct
    """
    if mcp_file.exists():
        try:
            config = json.loads(mcp_file.read_text())
        except json.JSONDecodeError:
            print(f"Error: {mcp_file} contains invalid JSON", file=sys.stderr)
            sys.exit(1)

        if "mcpServers" not in config:
            config["mcpServers"] = {}

        existing = config["mcpServers"].get(MCP_SERVER_KEY)
        if existing == BLQ_MCP_CONFIG:
            return False  # Already correct

        if existing and not force:
            print(f"{MCP_SERVER_KEY} server exists with different config. Use --force to overwrite.")
            print(f"  Current: {json.dumps(existing)}")
            print(f"  New:     {json.dumps(BLQ_MCP_CONFIG)}")
            return False

        config["mcpServers"][MCP_SERVER_KEY] = BLQ_MCP_CONFIG
    else:
        config = {"mcpServers": {MCP_SERVER_KEY: BLQ_MCP_CONFIG}}

    mcp_file.write_text(json.dumps(config, indent=2) + "\n")
    return True


def ensure_claude_md(cwd: Path) -> bool:
    """Add blq instructions to CLAUDE.md if not already present.

    Uses marker comments for idempotent updates:
    - If markers found, replaces the existing section
    - If no markers, appends to end of file
    - If no CLAUDE.md, creates it

    Args:
        cwd: Directory containing (or to contain) CLAUDE.md

    Returns:
        True if CLAUDE.md was created or updated, False if already correct
    """
    claude_md = cwd / "CLAUDE.md"
    marker_pattern = re.compile(
        re.escape(CLAUDE_MD_START_MARKER) + r".*?" + re.escape(CLAUDE_MD_END_MARKER),
        re.DOTALL,
    )

    if claude_md.exists():
        content = claude_md.read_text()

        if CLAUDE_MD_START_MARKER in content:
            # Replace existing section
            new_content = marker_pattern.sub(CLAUDE_MD_INSTRUCTIONS, content)
            if new_content == content:
                return False  # Already correct
            claude_md.write_text(new_content)
            return True

        # Append to end
        if content and not content.endswith("\n"):
            content += "\n"
        content += "\n" + CLAUDE_MD_INSTRUCTIONS + "\n"
        claude_md.write_text(content)
        return True

    # Create new file
    claude_md.write_text(CLAUDE_MD_INSTRUCTIONS + "\n")
    return True


def cmd_mcp_install(args: argparse.Namespace) -> None:
    """Create or update .mcp.json with blq server configuration.

    Optionally installs Claude Code hooks:
    - --hooks: explicitly install hooks
    - --no-hooks: explicitly skip hooks
    - Neither: use hooks.auto_claude_code from user config
    """
    from blq.user_config import UserConfig

    cwd = Path.cwd()
    mcp_file = cwd / ".mcp.json"
    force = getattr(args, "force", False)

    # Determine whether to install hooks
    # args.hooks is None if neither --hooks nor --no-hooks was passed
    hooks_arg = getattr(args, "hooks", None)
    if hooks_arg is None:
        # Neither flag passed, use user config
        user_config = UserConfig.load()
        install_hooks = user_config.hooks_auto_claude_code
    else:
        # Explicit flag passed
        install_hooks = hooks_arg

    # Merge blq_mcp into .mcp.json
    mcp_updated = ensure_mcp_config(mcp_file, force=force)

    if mcp_updated:
        print(f"Configured {MCP_SERVER_KEY} MCP server in {mcp_file.name}")
    else:
        print(f"{MCP_SERVER_KEY} server already configured in {mcp_file.name}")

    # Add agent instructions to CLAUDE.md
    claude_updated = ensure_claude_md(cwd)
    if claude_updated:
        print("Updated CLAUDE.md with blq agent instructions")

    # Install Claude Code hooks if requested
    hook_installed = False
    if install_hooks:
        from blq.commands.hooks_cmd import _install_claude_code_hooks

        hook_installed = _install_claude_code_hooks(force)

    if mcp_updated or hook_installed:
        print("\nblq MCP integration installed:")
        print("  - MCP server: blq mcp serve")
        if hook_installed:
            print("  - Claude Code hook: suggests using blq MCP tools for registered commands")
        elif not install_hooks:
            print("\nTip: Use 'blq hooks install claude-code' to add Claude Code hooks")
            print("     Or set hooks.auto_claude_code = true in user config")


def cmd_mcp_serve(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    from blq.serve import serve
    from blq.user_config import UserConfig

    # Load user config for defaults
    user_config = UserConfig.load()

    transport = getattr(args, "transport", "stdio")
    port = getattr(args, "port", 8080)
    disabled_tools = getattr(args, "disabled_tools", None)
    # Use user config default for safe_mode if not explicitly set
    safe_mode = getattr(args, "safe_mode", False) or user_config.mcp_safe_mode

    serve(
        transport=transport,
        port=port,
        disabled_tools=disabled_tools,
        safe_mode=safe_mode,
    )
