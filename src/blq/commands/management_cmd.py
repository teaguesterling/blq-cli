"""
Commands management handlers for blq CLI.

Provides the `blq commands config` subcommand for modifying command settings.
"""

from __future__ import annotations

import argparse
import json
import sys

from blq.commands.core import BlqConfig, EventRef
from blq.storage import BlqStorage


def cmd_commands_config(args: argparse.Namespace) -> None:
    """Configure a registered command's settings.

    Supports:
    - Adding fingerprints to suppress list (from event refs or directly)
    - Removing fingerprints from suppress list
    - Listing current suppress configuration
    - Clearing all suppressed fingerprints
    """
    config = BlqConfig.ensure()

    # Check if command exists
    command_name = args.name
    if command_name not in config.commands:
        print(f"Error: Command '{command_name}' is not registered", file=sys.stderr)
        print("Use 'blq commands list' to see registered commands", file=sys.stderr)
        sys.exit(1)

    cmd = config.commands[command_name]

    # Handle --list
    if getattr(args, "list", False):
        _show_suppress_list(cmd, getattr(args, "json", False))
        return

    # Handle --clear-suppress
    if getattr(args, "clear_suppress", False):
        old_count = len(cmd.suppress)
        cmd.suppress.clear()
        config.save_commands()
        print(f"Cleared {old_count} suppressed fingerprints from '{command_name}'")
        return

    # Collect fingerprints to add
    fingerprints_to_add: list[str] = []

    # Handle --suppress-event: look up event refs and get their fingerprints
    event_refs = getattr(args, "suppress_event", []) or []
    if event_refs:
        try:
            store = BlqStorage.open(config.lq_dir)
            for ref_str in event_refs:
                try:
                    ref = EventRef.parse(ref_str)
                    if ref.event_id is None:
                        print(
                            f"Warning: '{ref_str}' is a run ref, not an event ref. "
                            f"Use format like {ref_str}:1",
                            file=sys.stderr,
                        )
                        continue

                    # Get the event
                    event = store.event(ref.run_id, ref.event_id)
                    if event is None:
                        print(f"Warning: Event '{ref_str}' not found", file=sys.stderr)
                        continue

                    fp = event.get("fingerprint")
                    if not fp:
                        print(
                            f"Warning: Event '{ref_str}' has no fingerprint",
                            file=sys.stderr,
                        )
                        continue

                    fingerprints_to_add.append(fp)
                    print(f"Found fingerprint for {ref_str}: {fp[:16]}...")
                except ValueError as e:
                    print(f"Warning: Invalid event ref '{ref_str}': {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error accessing storage: {e}", file=sys.stderr)
            sys.exit(1)

    # Handle --suppress-fp: add fingerprints directly
    direct_fps = getattr(args, "suppress_fp", []) or []
    fingerprints_to_add.extend(direct_fps)

    # Handle --unsuppress-fp: remove fingerprints
    fps_to_remove = getattr(args, "unsuppress_fp", []) or []

    # Apply changes
    added_count = 0
    removed_count = 0

    # Add fingerprints (avoid duplicates)
    for fp in fingerprints_to_add:
        if fp not in cmd.suppress:
            cmd.suppress.append(fp)
            added_count += 1

    # Remove fingerprints
    for fp in fps_to_remove:
        if fp in cmd.suppress:
            cmd.suppress.remove(fp)
            removed_count += 1
        else:
            # Try partial match (for shortened fingerprints)
            matches = [f for f in cmd.suppress if f.startswith(fp)]
            if len(matches) == 1:
                cmd.suppress.remove(matches[0])
                removed_count += 1
            elif len(matches) > 1:
                print(
                    f"Warning: '{fp}' matches multiple fingerprints: "
                    f"{[m[:16] + '...' for m in matches]}",
                    file=sys.stderr,
                )

    # Save if changes were made
    if added_count > 0 or removed_count > 0:
        config.save_commands()
        if added_count > 0:
            print(f"Added {added_count} fingerprint(s) to suppress list")
        if removed_count > 0:
            print(f"Removed {removed_count} fingerprint(s) from suppress list")
        print(f"Total suppressed: {len(cmd.suppress)}")
    elif not event_refs and not direct_fps and not fps_to_remove:
        # No action specified, show current state
        _show_suppress_list(cmd, getattr(args, "json", False))


def _show_suppress_list(cmd, as_json: bool) -> None:
    """Show current suppress list for a command."""
    if as_json:
        print(
            json.dumps(
                {
                    "command": cmd.name,
                    "suppress_count": len(cmd.suppress),
                    "suppress": cmd.suppress,
                },
                indent=2,
            )
        )
    else:
        if cmd.suppress:
            print(f"Suppressed fingerprints for '{cmd.name}' ({len(cmd.suppress)}):")
            for fp in cmd.suppress:
                # Show shortened fingerprint
                short = fp[:16] + "..." if len(fp) > 16 else fp
                print(f"  {short}")
        else:
            print(f"No suppressed fingerprints for '{cmd.name}'")
