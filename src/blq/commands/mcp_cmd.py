"""
MCP (Model Context Protocol) commands for blq.

Commands:
- blq mcp install: Create or update .mcp.json configuration
- blq mcp serve: Start the MCP server
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_mcp_install(args: argparse.Namespace) -> None:
    """Create or update .mcp.json with blq server configuration.

    Optionally installs Claude Code hooks with --hooks flag.
    """
    mcp_file = Path(".mcp.json")
    force = getattr(args, "force", False)
    install_hooks = getattr(args, "hooks", False)

    # Default blq server config
    blq_config = {
        "command": "blq",
        "args": ["mcp", "serve"],
    }

    mcp_updated = False
    if mcp_file.exists():
        # Update existing file
        try:
            with open(mcp_file) as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print(f"Error: {mcp_file} contains invalid JSON", file=sys.stderr)
            sys.exit(1)

        if "mcpServers" not in config:
            config["mcpServers"] = {}

        if "blq" in config["mcpServers"] and not force:
            existing = config["mcpServers"]["blq"]
            if existing != blq_config:
                print("blq server exists with different config. Use --force to overwrite.")
                print(f"  Current: {json.dumps(existing)}")
                print(f"  New:     {json.dumps(blq_config)}")
        else:
            config["mcpServers"]["blq"] = blq_config
            with open(mcp_file, "w") as f:
                json.dump(config, f, indent=2)
            mcp_updated = True
    else:
        # Create new file
        config = {"mcpServers": {"blq": blq_config}}
        with open(mcp_file, "w") as f:
            json.dump(config, f, indent=2)
        mcp_updated = True

    if mcp_updated:
        print(f"Configured blq MCP server in {mcp_file}")
    else:
        print(f"blq server already configured in {mcp_file}")

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
        if not install_hooks:
            print("\nTip: Use 'blq hooks install claude-code' to add Claude Code hooks")


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
