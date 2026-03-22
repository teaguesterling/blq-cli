"""Sandbox specifications for registered commands.

Declarative execution environment bounds. A sandbox spec declares the
consequence bounds of a command's execution — regardless of what the command
does internally, what effects can it have on the world?

Phase 1: Declaration and logging only (no enforcement).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# =============================================================================
# Parsing utilities
# =============================================================================

_DURATION_PATTERN = re.compile(r"^(\d+)\s*(s|m|h)$")
_SIZE_PATTERN = re.compile(r"^(\d+)\s*(b|k|m|g)$", re.IGNORECASE)

_SIZE_MULTIPLIERS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def parse_duration(s: str | int) -> int:
    """Parse a duration string to seconds.

    Accepts: "30s", "5m", "1h", or bare int (seconds).
    """
    if isinstance(s, int):
        return s
    m = _DURATION_PATTERN.match(s.strip())
    if not m:
        raise ValueError(f"Invalid duration: {s!r} (expected e.g. '30s', '5m', '1h')")
    value, unit = int(m.group(1)), m.group(2)
    if unit == "s":
        return value
    elif unit == "m":
        return value * 60
    else:  # h
        return value * 3600


def format_duration(seconds: int) -> str:
    """Format seconds as a human-friendly duration string."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def parse_size(s: str | int) -> int:
    """Parse a size string to bytes.

    Accepts: "256m", "2g", "100k", or bare int (bytes).
    """
    if isinstance(s, int):
        return s
    m = _SIZE_PATTERN.match(s.strip())
    if not m:
        raise ValueError(f"Invalid size: {s!r} (expected e.g. '256m', '2g', '100k')")
    value, unit = int(m.group(1)), m.group(2).lower()
    return value * _SIZE_MULTIPLIERS[unit]


def format_size(byte_count: int) -> str:
    """Format bytes as a human-friendly size string."""
    if byte_count >= 1024**3 and byte_count % 1024**3 == 0:
        return f"{byte_count // 1024**3}g"
    if byte_count >= 1024**2 and byte_count % 1024**2 == 0:
        return f"{byte_count // 1024**2}m"
    if byte_count >= 1024 and byte_count % 1024 == 0:
        return f"{byte_count // 1024}k"
    return f"{byte_count}b"


# =============================================================================
# SandboxSpec
# =============================================================================

# Valid values for enum-like fields
NETWORK_VALUES = ("none", "localhost", "allowed_hosts", "unrestricted")
FILESYSTEM_VALUES = ("readonly", "workspace_only", "scoped_write", "unrestricted")
PROCESSES_VALUES = ("isolated", "visible")


@dataclass
class SandboxSpec:
    """Declarative execution environment bounds for a command.

    Each dimension is independently characterizable — the Harness can decide,
    before execution, exactly what the command can and can't do.

    Phase 1: declaration and logging only. No enforcement.
    """

    network: str = "unrestricted"
    filesystem: str = "unrestricted"
    timeout: int | None = None  # seconds
    memory: int | None = None  # bytes
    cpu: int | None = None  # cpu-seconds
    processes: str = "visible"
    tmpfs: int | None = None  # bytes
    paths_readable: list[str] = field(default_factory=list)
    paths_hidden: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.network not in NETWORK_VALUES:
            raise ValueError(
                f"Invalid network value: {self.network!r}. "
                f"Expected one of: {', '.join(NETWORK_VALUES)}"
            )
        if self.filesystem not in FILESYSTEM_VALUES:
            raise ValueError(
                f"Invalid filesystem value: {self.filesystem!r}. "
                f"Expected one of: {', '.join(FILESYSTEM_VALUES)}"
            )
        if self.processes not in PROCESSES_VALUES:
            raise ValueError(
                f"Invalid processes value: {self.processes!r}. "
                f"Expected one of: {', '.join(PROCESSES_VALUES)}"
            )

    @property
    def grade_w(self) -> str:
        """Compute world coupling level from spec.

        Returns one of: sealed, pinhole, scoped, broad, open.
        """
        if self.network == "unrestricted" and self.filesystem == "unrestricted":
            return "open"
        if self.network != "none":
            return "broad"
        if self.filesystem in ("workspace_only", "scoped_write"):
            return "scoped"
        if self.filesystem == "readonly":
            return "pinhole"
        return "sealed"

    @property
    def effects_ceiling(self) -> int:
        """Maximum computation level the sandbox's effect constraints allow.

        This is a ceiling, not the actual level. The actual risk depends on
        the tool running inside:
        - A level 1 tool in a level 7 sandbox: effective risk = 1
          (the tool doesn't use the sandbox's full allowance)
        - A level 4 tool in a level 2 sandbox: effective risk = 2
          (the sandbox constrains the consequences)

        The sandbox bounds effects (filesystem, network, processes).
        It cannot bound semantics (is this write data or code?).
        The level 3/4 distinction (data vs executable specification)
        is a property of the tool interface, not the sandbox.
        """
        if self.network != "none":
            return 8  # can reach external services
        if self.processes == "visible" and self.filesystem not in ("readonly",):
            return 7  # can spawn persistent subprocesses + write
        if self.filesystem not in ("readonly",):
            return 4  # can write; sandbox can't distinguish data from code
        return 2  # read + compute only (effects bounded)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for TOML/JSON.

        Uses human-friendly strings for sizes and durations.
        Omits fields that match the unrestricted defaults.
        """
        d: dict[str, Any] = {}

        if self.network != "unrestricted":
            d["network"] = self.network
        if self.filesystem != "unrestricted":
            d["filesystem"] = self.filesystem
        if self.timeout is not None:
            d["timeout"] = format_duration(self.timeout)
        if self.memory is not None:
            d["memory"] = format_size(self.memory)
        if self.cpu is not None:
            d["cpu"] = format_duration(self.cpu)
        if self.processes != "visible":
            d["processes"] = self.processes
        if self.tmpfs is not None:
            d["tmpfs"] = format_size(self.tmpfs)
        if self.paths_readable:
            d["paths_readable"] = list(self.paths_readable)
        if self.paths_hidden:
            d["paths_hidden"] = list(self.paths_hidden)

        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SandboxSpec:
        """Create from a TOML/JSON dict with human-friendly values."""
        kwargs: dict[str, Any] = {}

        if "network" in d:
            kwargs["network"] = d["network"]
        if "filesystem" in d:
            kwargs["filesystem"] = d["filesystem"]
        if "timeout" in d:
            kwargs["timeout"] = parse_duration(d["timeout"])
        if "memory" in d:
            kwargs["memory"] = parse_size(d["memory"])
        if "cpu" in d:
            kwargs["cpu"] = parse_duration(d["cpu"])
        if "processes" in d:
            kwargs["processes"] = d["processes"]
        if "tmpfs" in d:
            kwargs["tmpfs"] = parse_size(d["tmpfs"])
        if "paths_readable" in d:
            kwargs["paths_readable"] = list(d["paths_readable"])
        if "paths_hidden" in d:
            kwargs["paths_hidden"] = list(d["paths_hidden"])

        return cls(**kwargs)

    @classmethod
    def from_preset(cls, name: str) -> SandboxSpec:
        """Create from a named preset.

        Raises ValueError if the preset name is unknown.
        """
        if name not in PRESETS:
            valid = ", ".join(sorted(PRESETS.keys()))
            raise ValueError(f"Unknown sandbox preset: {name!r}. Valid presets: {valid}")
        return PRESETS[name]

    def matching_preset(self) -> str | None:
        """Return the preset name if this spec matches one exactly, else None."""
        for name, preset in PRESETS.items():
            if self == preset:
                return name
        return None

    def active_dimensions(self) -> set[str]:
        """Return the set of dimensions that differ from unrestricted defaults."""
        dims: set[str] = set()
        if self.network != "unrestricted":
            dims.add("network")
        if self.filesystem != "unrestricted":
            dims.add("filesystem")
        if self.memory is not None:
            dims.add("memory")
        if self.cpu is not None:
            dims.add("cpu")
        if self.processes != "visible":
            dims.add("processes")
        if self.tmpfs is not None:
            dims.add("tmpfs")
        if self.paths_readable:
            dims.add("paths_readable")
        if self.paths_hidden:
            dims.add("paths_hidden")
        return dims


# =============================================================================
# Presets
# =============================================================================

# From the design doc table (lines 91-98):
#
# | Preset       | network      | filesystem     | timeout | memory | cpu |
# |--------------|--------------|----------------|---------|--------|-----|
# | readonly     | none         | readonly       | 30s     | 256m   | 15s |
# | test         | none         | readonly       | 60s     | 512m   | 30s |
# | build        | none         | workspace_only | 5m      | 2g     | 2m  |
# | integration  | localhost    | workspace_only | 10m     | 4g     | 5m  |
# | unrestricted | unrestricted | unrestricted   | 30m     | —      | —   |
# | none         | unrestricted | unrestricted   | —       | —      | —   |

PRESETS: dict[str, SandboxSpec] = {
    "readonly": SandboxSpec(
        network="none",
        filesystem="readonly",
        timeout=30,
        memory=parse_size("256m"),
        cpu=15,
        processes="isolated",
    ),
    "test": SandboxSpec(
        network="none",
        filesystem="readonly",
        timeout=60,
        memory=parse_size("512m"),
        cpu=30,
        processes="isolated",
    ),
    "build": SandboxSpec(
        network="none",
        filesystem="workspace_only",
        timeout=300,
        memory=parse_size("2g"),
        cpu=120,
        processes="isolated",
    ),
    "integration": SandboxSpec(
        network="localhost",
        filesystem="workspace_only",
        timeout=600,
        memory=parse_size("4g"),
        cpu=300,
    ),
    "unrestricted": SandboxSpec(
        network="unrestricted",
        filesystem="unrestricted",
        timeout=1800,
    ),
    "none": SandboxSpec(
        network="unrestricted",
        filesystem="unrestricted",
    ),
}


# =============================================================================
# Resolver
# =============================================================================


def resolve_sandbox(value: str | dict[str, Any] | SandboxSpec | None) -> SandboxSpec | None:
    """Resolve a sandbox spec from various input forms.

    Args:
        value: A preset name (str), a config dict, a SandboxSpec, or None.

    Returns:
        A SandboxSpec instance, or None if value is None.
    """
    if value is None:
        return None
    if isinstance(value, SandboxSpec):
        return value
    if isinstance(value, str):
        return SandboxSpec.from_preset(value)
    if isinstance(value, dict):
        return SandboxSpec.from_dict(value)
    raise TypeError(f"Cannot resolve sandbox spec from {type(value).__name__}: {value!r}")
