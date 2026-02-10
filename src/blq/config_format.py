"""
Configuration format utilities for blq.

Uses TOML as the standard configuration format (per BIRD spec).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# TOML reading: use stdlib tomllib (3.11+) or tomli (3.10)
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

# TOML writing: always use tomli_w
import tomli_w


def load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file.

    Args:
        path: Path to the TOML file

    Returns:
        Parsed TOML as a dictionary

    Raises:
        FileNotFoundError: If file doesn't exist
        tomllib.TOMLDecodeError: If TOML is invalid
    """
    with open(path, "rb") as f:
        return tomllib.load(f)


def save_toml(path: Path, data: dict[str, Any]) -> None:
    """Save data to a TOML file.

    Args:
        path: Path to write to
        data: Dictionary to serialize as TOML
    """
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


# File name constants
CONFIG_FILE = "config.toml"
COMMANDS_FILE = "commands.toml"
