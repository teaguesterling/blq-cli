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
        rows = [
            {
                "name": name,
                "cmd": cmd.cmd,
                "description": cmd.description or "",
                "timeout": cmd.timeout,
                "capture": cmd.capture,
            }
            for name, cmd in commands.items()
        ]
        print(format_commands(rows, output_format))


def _normalize_cmd(cmd: str) -> str:
    """Normalize command string for comparison (collapse whitespace)."""
    return " ".join(cmd.split())


def _parse_defaults(default_args: list[str]) -> dict[str, str]:
    """Parse --default KEY=VALUE args into a dict."""
    defaults = {}
    for item in default_args:
        if "=" not in item:
            print(f"Warning: ignoring invalid default '{item}' (expected KEY=VALUE)")
            continue
        key, value = item.split("=", 1)
        defaults[key] = value
    return defaults


def _auto_init() -> BlqConfig | None:
    """Auto-initialize with sensible defaults from user config.

    Returns:
        BlqConfig if successful, None otherwise.
    """
    import argparse

    from blq.commands.init_cmd import cmd_init
    from blq.user_config import UserConfig

    user_config = UserConfig.load()

    init_args = argparse.Namespace(
        mcp=user_config.auto_mcp,
        no_mcp=False,
        detect=False,
        detect_mode="none",
        yes=True,
        force=False,
        parquet=user_config.default_storage == "parquet",
        namespace=None,
        project=None,
        gitignore=user_config.auto_gitignore,
    )
    cmd_init(init_args)

    # Return the newly created config
    return BlqConfig.find()


def cmd_register(args: argparse.Namespace) -> None:
    """Register a new command.

    If a command with the same name or same command string already exists,
    uses the existing command instead of failing. Use --force to overwrite.

    Supports two modes:
    - Simple command: blq commands register build make -j8
    - Template command: blq commands register test -t pytest {path} -D path=tests/

    If the project is not initialized and auto_init is enabled in user config,
    the project will be auto-initialized first.
    """
    from blq.commands.execution import cmd_run
    from blq.user_config import UserConfig

    # Try to find existing config
    config = BlqConfig.find()

    if config is None:
        user_config = UserConfig.load()

        if user_config.auto_init:
            # Auto-init with notice
            print("Project not initialized. Auto-initializing...", file=sys.stderr)
            config = _auto_init()
            if config is None:
                print("Error: Failed to initialize project.", file=sys.stderr)
                sys.exit(1)
        else:
            print("Error: .lq not initialized. Run 'blq init' first.", file=sys.stderr)
            print(
                "Tip: Set auto_init = true in ~/.config/blq/config.toml to auto-init",
                file=sys.stderr,
            )
            sys.exit(1)

    commands = config.commands

    name = args.name
    cmd_str = " ".join(args.cmd)
    normalized_cmd = _normalize_cmd(cmd_str)
    run_now = getattr(args, "run", False)
    is_template = getattr(args, "template", False)
    default_args = getattr(args, "default", []) or []

    # Check for existing command with same name
    if name in commands and not args.force:
        existing = commands[name]
        existing_template = existing.tpl if existing.is_template else existing.cmd
        existing_normalized = _normalize_cmd(existing_template or "")

        if existing_normalized == normalized_cmd:
            # Same command, just use it
            print(f"Using existing command '{name}' (identical)")
            if run_now:
                # Create a mock args object for cmd_run
                run_args = argparse.Namespace(
                    command=[name],
                    name=None,
                    json=getattr(args, "json", False),
                    quiet=getattr(args, "quiet", False),
                    capture=None,
                    format=None,
                    timeout=None,
                    keep_raw=False,
                    summary=False,
                    verbose=False,
                    include_warnings=False,
                    error_limit=20,
                    register=False,
                    positional_args=None,
                    dry_run=False,
                    markdown=False,
                    csv=False,
                )
                cmd_run(run_args)
            return
        else:
            # Different command with same name
            print(
                f"Command '{name}' already exists with different command.\n"
                f"  Existing: '{existing_template}'\n"
                f"  Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Check for existing command with same cmd but different name (skip for templates)
    if not is_template:
        for existing_name, existing in commands.items():
            existing_cmd = existing.cmd if not existing.is_template else None
            if existing_cmd and _normalize_cmd(existing_cmd) == normalized_cmd and not args.force:
                print(f"Using existing command '{existing_name}' (same command)")
                if run_now:
                    run_args = argparse.Namespace(
                        command=[existing_name],
                        name=None,
                        json=getattr(args, "json", False),
                        quiet=getattr(args, "quiet", False),
                        capture=None,
                        format=None,
                        timeout=None,
                        keep_raw=False,
                        summary=False,
                        verbose=False,
                        include_warnings=False,
                        error_limit=20,
                        register=False,
                        positional_args=None,
                        dry_run=False,
                        markdown=False,
                        csv=False,
                    )
                    cmd_run(run_args)
                return

    # Register new command
    capture = not getattr(args, "no_capture", False)

    if is_template:
        # Template command
        defaults = _parse_defaults(default_args)
        commands[name] = RegisteredCommand(
            name=name,
            cmd=None,
            tpl=cmd_str,
            defaults=defaults,
            description=args.description or "",
            timeout=args.timeout,
            format=args.format,
            capture=capture,
        )
        defaults_note = f" (defaults: {defaults})" if defaults else ""
        print(f"Registered template '{name}': {cmd_str}{defaults_note}")
    else:
        # Simple command
        commands[name] = RegisteredCommand(
            name=name,
            cmd=cmd_str,
            description=args.description or "",
            timeout=args.timeout,
            format=args.format,
            capture=capture,
        )
        capture_note = " (no capture)" if not capture else ""
        print(f"Registered command '{name}': {cmd_str}{capture_note}")

    # Save commands to disk
    config.save_commands()

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
