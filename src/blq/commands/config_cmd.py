"""
User configuration commands for blq.

Commands:
- blq config: Show non-default settings
- blq config --all: Show all settings
- blq config get <key>: Get a specific value
- blq config set <key> <value>: Set a value
- blq config unset <key>: Remove a setting
- blq config --path: Show config file path
- blq config --edit: Open in $EDITOR
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from blq.user_config import UserConfig

# Config schema: maps dotted keys to type and default
# Default of None means dynamic default (computed at runtime)
CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "init.auto_mcp": {
        "type": "bool",
        "default": None,  # Dynamic: True if fastmcp installed
        "attr": "auto_mcp",
        "section": "init",
        "key": "auto_mcp",
        "description": "Create .mcp.json on init",
    },
    "init.auto_gitignore": {
        "type": "bool",
        "default": True,
        "attr": "auto_gitignore",
        "section": "init",
        "key": "auto_gitignore",
        "description": "Add .lq/ to .gitignore",
    },
    "init.default_storage": {
        "type": "str",
        "default": "bird",
        "attr": "default_storage",
        "section": "init",
        "key": "default_storage",
        "description": "Default storage mode",
    },
    "init.auto_detect": {
        "type": "bool",
        "default": False,
        "attr": "auto_detect",
        "section": "init",
        "key": "auto_detect",
        "description": "Auto-detect commands on init",
    },
    "register.auto_init": {
        "type": "bool",
        "default": False,
        "attr": "auto_init",
        "section": "register",
        "key": "auto_init",
        "description": "Auto-init on register if not initialized",
    },
    "output.default_format": {
        "type": "str",
        "default": "table",
        "attr": "default_format",
        "section": "output",
        "key": "default_format",
        "description": "Default output format (table, json, markdown)",
    },
    "output.default_limit": {
        "type": "int",
        "default": 20,
        "attr": "default_limit",
        "section": "output",
        "key": "default_limit",
        "description": "Default limit for history, errors, etc.",
    },
    "run.show_summary": {
        "type": "bool",
        "default": False,
        "attr": "show_summary",
        "section": "run",
        "key": "show_summary",
        "description": "Always show summary after runs",
    },
    "run.keep_raw": {
        "type": "bool",
        "default": False,
        "attr": "keep_raw",
        "section": "run",
        "key": "keep_raw",
        "description": "Always keep raw output",
    },
    "mcp.safe_mode": {
        "type": "bool",
        "default": False,
        "attr": "mcp_safe_mode",
        "section": "mcp",
        "key": "safe_mode",
        "description": "MCP server safe mode",
    },
    "storage.auto_prune": {
        "type": "bool",
        "default": False,
        "attr": "auto_prune",
        "section": "storage",
        "key": "auto_prune",
        "description": "Enable automatic pruning",
    },
    "storage.prune_days": {
        "type": "int",
        "default": 30,
        "attr": "prune_days",
        "section": "storage",
        "key": "prune_days",
        "description": "Auto-prune logs older than N days",
    },
    "hooks.auto_claude_code": {
        "type": "bool",
        "default": False,
        "attr": "hooks_auto_claude_code",
        "section": "hooks",
        "key": "auto_claude_code",
        "description": "Auto-install Claude Code hooks with mcp install",
    },
    "defaults.extra_capture_env": {
        "type": "list[str]",
        "default": [],
        "attr": "extra_capture_env",
        "section": "defaults",
        "key": "extra_capture_env",
        "description": "Additional env vars to capture",
    },
}


def _get_default(key: str) -> Any:
    """Get the default value for a config key.

    Handles dynamic defaults like init.auto_mcp.
    """
    schema = CONFIG_SCHEMA.get(key)
    if not schema:
        return None

    default = schema["default"]
    if default is None:
        # Dynamic default
        if key == "init.auto_mcp":
            return UserConfig.mcp_available()
    return default


def _parse_bool(value: str) -> bool:
    """Parse a boolean value from string."""
    if value.lower() in ("true", "yes", "1", "on"):
        return True
    if value.lower() in ("false", "no", "0", "off"):
        return False
    raise ValueError(f"Invalid boolean value '{value}'")


def _parse_int(value: str) -> int:
    """Parse an integer value from string."""
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid integer value '{value}'")


def _parse_list(value: str) -> list[str]:
    """Parse a list value from comma-separated string."""
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_value(key: str, value: str) -> Any:
    """Parse a value according to the expected type for a key."""
    schema = CONFIG_SCHEMA.get(key)
    if not schema:
        raise ValueError(f"Unknown config key '{key}'")

    value_type = schema["type"]

    if value_type == "bool":
        try:
            return _parse_bool(value)
        except ValueError:
            raise ValueError(
                f"Invalid boolean value '{value}' for {key}\n"
                "Valid values: true, false, yes, no, 1, 0"
            )
    elif value_type == "int":
        try:
            return _parse_int(value)
        except ValueError:
            raise ValueError(f"Invalid integer value '{value}' for {key}")
    elif value_type == "list[str]":
        return _parse_list(value)
    else:  # str
        return value


def _format_value(value: Any) -> str:
    """Format a value for display."""
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, list):
        if not value:
            return "[]"
        return json.dumps(value)
    elif isinstance(value, str):
        return f'"{value}"'
    else:
        return str(value)


def _is_default(key: str, value: Any) -> bool:
    """Check if a value is the default for a key."""
    default = _get_default(key)
    return bool(value == default)


def cmd_config(args: argparse.Namespace) -> None:
    """Handle the blq config command."""
    show_path = getattr(args, "path", False)
    edit = getattr(args, "edit", False)
    show_all = getattr(args, "all", False)
    output_json = getattr(args, "json", False)
    subcommand = getattr(args, "config_subcommand", None)

    if show_path:
        print(UserConfig.config_path())
        return

    if edit:
        _edit_config()
        return

    if subcommand == "get":
        _cmd_get(args)
        return

    if subcommand == "set":
        _cmd_set(args)
        return

    if subcommand == "unset":
        _cmd_unset(args)
        return

    # Default: show config
    _cmd_show(show_all, output_json)


def _cmd_show(show_all: bool, output_json: bool) -> None:
    """Show configuration values."""
    config = UserConfig.load()

    # Collect values
    values: dict[str, Any] = {}
    for key, schema in CONFIG_SCHEMA.items():
        attr = schema["attr"]
        value = getattr(config, attr)
        default = _get_default(key)
        is_default = value == default

        if show_all or not is_default:
            values[key] = {"value": value, "is_default": is_default}

    if output_json:
        # JSON output: just key-value pairs
        output = {k: v["value"] for k, v in values.items()}
        print(json.dumps(output, indent=2))
        return

    if not values:
        config_path = UserConfig.config_path()
        if config_path.exists():
            print("All settings are at default values.")
        else:
            print("Using defaults (no config file).")
        print(f"\nConfig path: {config_path}")
        return

    # Group by section
    sections: dict[str, list[tuple[str, Any, bool]]] = {}
    for key, info in values.items():
        section = key.split(".")[0]
        if section not in sections:
            sections[section] = []
        sections[section].append((key, info["value"], info["is_default"]))

    # Print grouped output
    print(f"# User config: {UserConfig.config_path()}\n")

    for section in ["init", "register", "output", "run", "mcp", "storage", "hooks", "defaults"]:
        if section not in sections:
            continue

        print(f"# [{section}]")
        for key, value, is_default in sections[section]:
            formatted = _format_value(value)
            if is_default:
                print(f"{key} = {formatted}  # (default)")
            else:
                print(f"{key} = {formatted}")
        print()


def _cmd_get(args: argparse.Namespace) -> None:
    """Get a specific config value."""
    key = args.key
    output_json = getattr(args, "json", False)

    if key not in CONFIG_SCHEMA:
        print(f"Error: Unknown config key '{key}'", file=sys.stderr)
        print("\nAvailable keys:", file=sys.stderr)
        for k in sorted(CONFIG_SCHEMA.keys()):
            print(f"  {k}", file=sys.stderr)
        sys.exit(1)

    config = UserConfig.load()
    schema = CONFIG_SCHEMA[key]
    value = getattr(config, schema["attr"])

    if output_json:
        print(json.dumps({key: value}))
    else:
        # Raw value output for scripting
        if isinstance(value, bool):
            print("true" if value else "false")
        elif isinstance(value, list):
            print(",".join(value) if value else "")
        else:
            print(value)


def _cmd_set(args: argparse.Namespace) -> None:
    """Set a config value."""
    key = args.key
    value_str = args.value

    if key not in CONFIG_SCHEMA:
        print(f"Error: Unknown config key '{key}'", file=sys.stderr)
        print("\nAvailable keys:", file=sys.stderr)
        for k in sorted(CONFIG_SCHEMA.keys()):
            desc = CONFIG_SCHEMA[k].get("description", "")
            print(f"  {k:<30} {desc}", file=sys.stderr)
        sys.exit(1)

    try:
        value = _parse_value(key, value_str)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Load, update, save
    config = UserConfig.load()
    schema = CONFIG_SCHEMA[key]
    setattr(config, schema["attr"], value)
    config.save()

    formatted = _format_value(value)
    print(f"Set {key} = {formatted}")


def _cmd_unset(args: argparse.Namespace) -> None:
    """Unset a config value (revert to default)."""
    key = args.key

    if key not in CONFIG_SCHEMA:
        print(f"Error: Unknown config key '{key}'", file=sys.stderr)
        sys.exit(1)

    # Load config, set to default, save
    config = UserConfig.load()
    schema = CONFIG_SCHEMA[key]
    default = _get_default(key)

    setattr(config, schema["attr"], default)
    config.save()

    formatted = _format_value(default)
    print(f"Unset {key} (default: {formatted})")


def _edit_config() -> None:
    """Open config file in editor."""
    config_path = UserConfig.config_path()

    # Create template if file doesn't exist
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        template = _generate_template()
        config_path.write_text(template)
        print(f"Created config template at {config_path}")

    # Find editor
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"

    try:
        subprocess.run([editor, str(config_path)], check=True)
    except FileNotFoundError:
        print(f"Error: Editor '{editor}' not found", file=sys.stderr)
        print(f"Set $EDITOR environment variable or edit manually: {config_path}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)

    # Validate after editing
    try:
        from blq.config_format import load_toml

        load_toml(config_path)
        print("Config validated successfully.")
    except Exception as e:
        print(f"Warning: Config may have syntax errors: {e}", file=sys.stderr)


def _generate_template() -> str:
    """Generate a commented config template."""
    lines = [
        "# blq user configuration",
        "# See: blq config --all (to view current settings)",
        "#      blq config set <key> <value> (to change settings)",
        "",
    ]

    current_section = None
    for key in sorted(CONFIG_SCHEMA.keys()):
        schema = CONFIG_SCHEMA[key]
        section = key.split(".")[0]
        key_name = key.split(".")[1]

        if section != current_section:
            if current_section is not None:
                lines.append("")
            lines.append(f"# [{section}]")
            current_section = section

        default = _get_default(key)
        formatted = _format_value(default)
        desc = schema.get("description", "")

        lines.append(f"# {key_name} = {formatted}  # {desc}")

    lines.append("")
    return "\n".join(lines)
