"""Parse strace output to extract file, network, and process access patterns."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# openat(AT_FDCWD, "/path", FLAGS[, mode]) = N
_RE_OPENAT = re.compile(
    r'openat\([^,]+,\s*"([^"]+)",\s*([^)]+)\)\s*=\s*(-?\d+)'
)

# access("/path", MODE) = N
_RE_ACCESS = re.compile(
    r'access\("([^"]+)",[^)]+\)\s*=\s*(-?\d+)'
)

# execve("/path", [...]) = N
_RE_EXECVE = re.compile(
    r'execve\("([^"]+)"'
)

# connect(..., {sa_family=AF_INET, sin_port=htons(PORT), sin_addr=inet_addr("ADDR")}, ...) = N
_RE_CONNECT_INET = re.compile(
    r'connect\([^,]+,\s*\{sa_family=AF_INET,'
    r'.*?sin_port=htons\((\d+)\),'
    r'.*?sin_addr=inet_addr\("([^"]+)"\)'
    r'.*?\}\s*,\s*\d+\)\s*=\s*(-?\d+)',
    re.DOTALL,
)

# connect(..., {sa_family=AF_INET6, sin6_port=htons(PORT), ..., inet_pton(AF_INET6, "ADDR"), ...}) = N
_RE_CONNECT_INET6 = re.compile(
    r'connect\([^,]+,\s*\{sa_family=AF_INET6,'
    r'.*?sin6_port=htons\((\d+)\),'
    r'.*?inet_pton\(AF_INET6,\s*"([^"]+)"\)'
    r'.*?\}\s*,\s*\d+\)\s*=\s*(-?\d+)',
    re.DOTALL,
)

# clone or clone3 call
_RE_CLONE = re.compile(r'\bclone3?\(')

# Write-mode openat flags
_WRITE_FLAGS = frozenset(["O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"])


def _flags_are_write(flags: str) -> bool:
    """Return True if any write-indicating flag is present in the flags string."""
    return any(f in flags for f in _WRITE_FLAGS)


# ---------------------------------------------------------------------------
# StraceProfile dataclass
# ---------------------------------------------------------------------------


@dataclass
class StraceProfile:
    """Access patterns extracted from strace output."""

    files_read: set[str] = field(default_factory=set)
    files_written: set[str] = field(default_factory=set)
    network_connections: set[tuple[str, int]] = field(default_factory=set)
    executables: set[str] = field(default_factory=set)
    process_spawns: int = 0

    @property
    def has_network(self) -> bool:
        return bool(self.network_connections)

    @property
    def has_writes(self) -> bool:
        return bool(self.files_written)

    @property
    def has_spawns(self) -> bool:
        return self.process_spawns > 0

    def read_directories(self) -> set[str]:
        """Return the set of unique parent directories of all read files."""
        return {os.path.dirname(p) for p in self.files_read}

    def write_directories(self) -> set[str]:
        """Return the set of unique parent directories of all written files."""
        return {os.path.dirname(p) for p in self.files_written}

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_read": sorted(self.files_read),
            "files_written": sorted(self.files_written),
            "network_connections": [list(c) for c in sorted(self.network_connections)],
            "executables": sorted(self.executables),
            "process_spawns": self.process_spawns,
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_strace_output(output: str) -> StraceProfile:
    """Parse strace -f output and return a StraceProfile.

    Each line has the format: PID syscall(args) = result
    Lines that don't match any known pattern are silently ignored.
    """
    profile = StraceProfile()

    for line in output.splitlines():
        # Strip leading PID (digits + optional whitespace)
        # The rest is the syscall expression.
        stripped = line.lstrip()

        # ------------------------------------------------------------------
        # clone / clone3  →  process_spawns
        # ------------------------------------------------------------------
        if _RE_CLONE.search(stripped):
            profile.process_spawns += 1
            continue

        # ------------------------------------------------------------------
        # execve  →  executables
        # ------------------------------------------------------------------
        m = _RE_EXECVE.search(stripped)
        if m:
            profile.executables.add(m.group(1))
            continue

        # ------------------------------------------------------------------
        # openat  →  files_read or files_written
        # ------------------------------------------------------------------
        m = _RE_OPENAT.search(stripped)
        if m:
            path, flags, result = m.group(1), m.group(2), m.group(3)
            if int(result) == -1:
                continue  # failed open — ignore
            if _flags_are_write(flags):
                profile.files_written.add(path)
            else:
                profile.files_read.add(path)
            continue

        # ------------------------------------------------------------------
        # access  →  files_read (only on success, result == 0)
        # ------------------------------------------------------------------
        m = _RE_ACCESS.search(stripped)
        if m:
            path, result = m.group(1), m.group(2)
            if int(result) == 0:
                profile.files_read.add(path)
            continue

        # ------------------------------------------------------------------
        # connect AF_INET  →  network_connections
        # ------------------------------------------------------------------
        m = _RE_CONNECT_INET.search(stripped)
        if m:
            port, addr, result = int(m.group(1)), m.group(2), m.group(3)
            if int(result) != -1:
                profile.network_connections.add((addr, port))
            continue

        # ------------------------------------------------------------------
        # connect AF_INET6  →  network_connections
        # ------------------------------------------------------------------
        m = _RE_CONNECT_INET6.search(stripped)
        if m:
            port, addr, result = int(m.group(1)), m.group(2), m.group(3)
            if int(result) != -1:
                profile.network_connections.add((addr, port))
            continue

    return profile
