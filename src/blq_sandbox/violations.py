"""Detect sandbox violation patterns in command output."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SandboxViolation:
    """A detected sandbox violation."""

    dimension: str  # "filesystem", "network", "processes"
    pattern: str  # The matched pattern description
    line: str  # The line that matched
    line_number: int | None  # Line number in output (if available)


# Patterns: (compiled_regex, dimension, description)
VIOLATION_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Filesystem
    (re.compile(r"Read-only file system", re.IGNORECASE),
     "filesystem", "write to read-only filesystem"),
    (re.compile(r"cannot (?:touch|create|open)\b.*(?:Permission denied|Read-only)",
                re.IGNORECASE), "filesystem", "write blocked"),
    (re.compile(r"Permission denied", re.IGNORECASE), "filesystem", "permission denied"),
    # Network
    (re.compile(r"Network is unreachable", re.IGNORECASE), "network", "network unreachable"),
    (re.compile(r"Name or service not known", re.IGNORECASE), "network", "DNS blocked"),
    (re.compile(r"Could not resolve host", re.IGNORECASE), "network", "DNS blocked"),
    (re.compile(r"Connection refused", re.IGNORECASE), "network", "connection refused"),
]


def detect_violations(output: str, sandbox_spec: dict[str, str]) -> list[SandboxViolation]:
    """Scan output for sandbox violation patterns.

    Only reports violations for dimensions that are actually restricted
    in the sandbox spec. E.g., "Permission denied" is only a sandbox
    violation if filesystem != "unrestricted".

    Args:
        output: Combined stdout/stderr from the command.
        sandbox_spec: The sandbox spec dict (from extension_data).

    Returns:
        List of detected violations (at most one per dimension).
    """
    violations: list[SandboxViolation] = []
    restricted_dims: set[str] = set()

    # Determine which dimensions are restricted
    if sandbox_spec.get("network", "unrestricted") != "unrestricted":
        restricted_dims.add("network")
    if sandbox_spec.get("filesystem", "unrestricted") != "unrestricted":
        restricted_dims.add("filesystem")
    if sandbox_spec.get("processes", "visible") == "isolated":
        restricted_dims.add("processes")

    if not restricted_dims:
        return []

    seen_dims: set[str] = set()  # Only report first violation per dimension

    for line_num, line in enumerate(output.splitlines(), 1):
        for pattern, dimension, description in VIOLATION_PATTERNS:
            if dimension not in restricted_dims:
                continue
            if dimension in seen_dims:
                continue
            if pattern.search(line):
                violations.append(
                    SandboxViolation(
                        dimension=dimension,
                        pattern=description,
                        line=line.strip(),
                        line_number=line_num,
                    )
                )
                seen_dims.add(dimension)
                break  # Only first match per line

    return violations
