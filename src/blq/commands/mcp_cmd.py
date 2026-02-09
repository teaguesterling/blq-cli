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
    """Create or update .mcp.json with blq server configuration."""
    mcp_file = Path(".mcp.json")

    # Default blq server config
    blq_config = {
        "command": "blq",
        "args": ["mcp", "serve"],
    }

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

        if "blq" in config["mcpServers"] and not args.force:
            existing = config["mcpServers"]["blq"]
            if existing == blq_config:
                print(f"blq server already configured in {mcp_file}")
                return
            else:
                print(f"blq server exists with different config. Use --force to overwrite.")
                print(f"  Current: {json.dumps(existing)}")
                print(f"  New:     {json.dumps(blq_config)}")
                return

        config["mcpServers"]["blq"] = blq_config

        with open(mcp_file, "w") as f:
            json.dump(config, f, indent=2)

        print(f"Updated {mcp_file} with blq server")
    else:
        # Create new file
        config = {
            "mcpServers": {
                "blq": blq_config
            }
        }

        with open(mcp_file, "w") as f:
            json.dump(config, f, indent=2)

        print(f"Created {mcp_file} with blq server")

    print(f"\nblq MCP server configured:")
    print(f"  command: blq mcp serve")


def cmd_mcp_serve(args: argparse.Namespace) -> None:
    """Start the MCP server."""
    from blq.serve import serve

    transport = getattr(args, "transport", "stdio")
    port = getattr(args, "port", 8080)

    serve(transport=transport, port=port)
