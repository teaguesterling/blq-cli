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
    """Create or update .mcp.json with blq server configuration and install Claude Code hooks."""
    mcp_file = Path(".mcp.json")
    force = getattr(args, "force", False)

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

    # Install Claude Code suggest hook
    hook_installed = _install_suggest_hook(force)

    if mcp_updated or hook_installed:
        print("\nblq MCP integration installed:")
        print("  - MCP server: blq mcp serve")
        if hook_installed:
            print("  - Claude Code hook: suggests using blq MCP tools for registered commands")
    elif not mcp_updated:
        print(f"blq server already configured in {mcp_file}")


def _install_suggest_hook(force: bool = False) -> bool:
    """Install the blq-suggest Claude Code hook.

    Returns True if hook was installed/updated.
    """
    hooks_dir = Path(".claude/hooks")
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_file = hooks_dir / "blq-suggest.sh"
    settings_file = Path(".claude/settings.json")

    # Hook script content
    hook_script = """#!/bin/bash
# Claude Code PostToolUse hook for Bash commands
# Suggests using blq MCP run tool when a matching registered command is found
# Installed by: blq mcp install

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

    # Write hook script
    hook_existed = hook_file.exists()
    if not hook_existed or force:
        hook_file.write_text(hook_script)
        hook_file.chmod(0o755)

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

    return not hook_existed or settings_updated


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
