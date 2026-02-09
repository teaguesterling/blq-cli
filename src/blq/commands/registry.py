"""
Command registry commands for blq CLI.

Handles listing, registering, and unregistering commands.
"""

from __future__ import annotations

import argparse
import sys

from blq.commands.core import (
    BlqConfig,
    RegisteredCommand,
)
from blq.output import (
    format_commands,
    get_output_format,
)


def cmd_commands(args: argparse.Namespace) -> None:
    """List registered commands."""
    import json

    config = BlqConfig.ensure()
    commands = config.commands

    if not commands:
        print("No commands registered.")
        print("Use 'blq register <name> <command>' to register a command.")
        return

    output_format = get_output_format(args)

    if output_format == "json":
        # JSON: dict keyed by name for backward compatibility
        data = {name: cmd.to_dict() for name, cmd in commands.items()}
        print(json.dumps(data, indent=2))
    else:
        # Table/Markdown: list of dicts
        data = [
            {
                "name": name,
                "cmd": cmd.cmd,
                "description": cmd.description or "",
                "timeout": cmd.timeout,
                "capture": cmd.capture,
            }
            for name, cmd in commands.items()
        ]
        print(format_commands(data, output_format))


def _normalize_cmd(cmd: str) -> str:
    """Normalize command string for comparison (collapse whitespace)."""
    return " ".join(cmd.split())


def cmd_register(args: argparse.Namespace) -> None:
    """Register a new command.

    If a command with the same name or same command string already exists,
    uses the existing command instead of failing. Use --force to overwrite.
    """
    from blq.commands.execution import cmd_run

    config = BlqConfig.ensure()
    commands = config.commands

    name = args.name
    cmd_str = " ".join(args.cmd)
    normalized_cmd = _normalize_cmd(cmd_str)
    run_now = getattr(args, "run", False)

    # Check for existing command with same name
    if name in commands and not args.force:
        existing = commands[name]
        existing_normalized = _normalize_cmd(existing.cmd)

        if existing_normalized == normalized_cmd:
            # Same command, just use it
            print(f"Using existing command '{name}' (identical)")
            if run_now:
                # Create a mock args object for cmd_run
                run_args = argparse.Namespace(
                    command=name,
                    args=[],
                    json=getattr(args, "json", False),
                    quiet=getattr(args, "quiet", False),
                    capture=None,
                    format=None,
                )
                cmd_run(run_args)
            return
        else:
            # Different command with same name
            print(
                f"Command '{name}' already exists with different command.\n"
                f"  Existing: '{existing.cmd}'\n"
                f"  Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Check for existing command with same cmd but different name
    for existing_name, existing in commands.items():
        if _normalize_cmd(existing.cmd) == normalized_cmd and not args.force:
            print(f"Using existing command '{existing_name}' (same command)")
            if run_now:
                run_args = argparse.Namespace(
                    command=existing_name,
                    args=[],
                    json=getattr(args, "json", False),
                    quiet=getattr(args, "quiet", False),
                    capture=None,
                    format=None,
                )
                cmd_run(run_args)
            return

    # Register new command
    capture = not getattr(args, "no_capture", False)
    commands[name] = RegisteredCommand(
        name=name,
        cmd=cmd_str,
        description=args.description or "",
        timeout=args.timeout,
        format=args.format,
        capture=capture,
    )

    config.save_commands()
    capture_note = " (no capture)" if not capture else ""
    print(f"Registered command '{name}': {cmd_str}{capture_note}")

    if run_now:
        run_args = argparse.Namespace(
            command=name,
            args=[],
            json=getattr(args, "json", False),
            quiet=getattr(args, "quiet", False),
            capture=None,
            format=None,
        )
        cmd_run(run_args)


def cmd_unregister(args: argparse.Namespace) -> None:
    """Remove a registered command."""
    config = BlqConfig.ensure()
    commands = config.commands

    if args.name not in commands:
        print(f"Command '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    del commands[args.name]
    config.save_commands()
    print(f"Unregistered command '{args.name}'")
