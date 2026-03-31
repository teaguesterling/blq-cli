"""
File-based command lock module for resource contention.

Locks are stored as JSON files in a locks directory (typically .bird/locks/).
PID liveness checks are used to detect and reclaim stale locks.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CommandLock:
    """Represents an acquired command lock."""

    lock_name: str
    pid: int
    attempt_id: str
    command: str
    acquired_at: float  # Unix timestamp

    def to_json(self) -> str:
        """Serialize this lock to a JSON string."""
        return json.dumps(
            {
                "lock_name": self.lock_name,
                "pid": self.pid,
                "attempt_id": self.attempt_id,
                "command": self.command,
                "acquired_at": self.acquired_at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> CommandLock | None:
        """Deserialize a CommandLock from a JSON string.

        Returns None if the input is invalid or missing required fields.
        """
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                lock_name=data["lock_name"],
                pid=data["pid"],
                attempt_id=data["attempt_id"],
                command=data["command"],
                acquired_at=data["acquired_at"],
            )
        except (KeyError, TypeError):
            return None


class LockHeldError(Exception):
    """Raised when a lock is held by a live process."""

    def __init__(self, held_by: CommandLock) -> None:
        self.held_by = held_by
        age = time.time() - held_by.acquired_at
        super().__init__(
            f"Lock '{held_by.lock_name}' is held by PID {held_by.pid} "
            f"(command={held_by.command!r}, attempt_id={held_by.attempt_id!r}, "
            f"age={age:.1f}s)"
        )


def _is_pid_alive(pid: int) -> bool:
    """Return True if the given PID is alive, False if it does not exist."""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True
    except ProcessLookupError:
        return False


def acquire_lock(
    locks_dir: Path,
    lock_name: str,
    pid: int,
    attempt_id: str,
    command: str,
) -> CommandLock:
    """Acquire a named lock.

    Creates locks_dir if it does not exist.
    If the lock is held by a live PID, raises LockHeldError.
    If the lock is held by a dead PID, reclaims it and proceeds.
    Writes the lock file and returns the CommandLock.
    """
    locks_dir = Path(locks_dir)
    locks_dir.mkdir(parents=True, exist_ok=True)

    lock_file = locks_dir / f"{lock_name}.lock"

    if lock_file.exists():
        existing = CommandLock.from_json(lock_file.read_text())
        if existing is not None:
            if _is_pid_alive(existing.pid):
                raise LockHeldError(existing)
            else:
                logger.info(
                    "Reclaiming stale lock '%s' held by dead PID %d (attempt_id=%s)",
                    lock_name,
                    existing.pid,
                    existing.attempt_id,
                )

    lock = CommandLock(
        lock_name=lock_name,
        pid=pid,
        attempt_id=attempt_id,
        command=command,
        acquired_at=time.time(),
    )
    lock_file.write_text(lock.to_json())
    return lock


def release_lock(locks_dir: Path, lock_name: str) -> None:
    """Release a named lock by removing its lock file.

    No-op if the lock file does not exist.
    """
    lock_file = Path(locks_dir) / f"{lock_name}.lock"
    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass


def read_lock(locks_dir: Path, lock_name: str) -> CommandLock | None:
    """Read a lock file without acquiring it.

    Returns None if the lock file does not exist or is invalid.
    """
    lock_file = Path(locks_dir) / f"{lock_name}.lock"
    if not lock_file.exists():
        return None
    return CommandLock.from_json(lock_file.read_text())


def cleanup_stale_locks(locks_dir: Path) -> list[str]:
    """Remove all lock files with dead PIDs.

    Returns the names of locks that were cleaned up.
    Handles missing directory gracefully.
    """
    locks_dir = Path(locks_dir)
    if not locks_dir.exists():
        return []

    cleaned: list[str] = []
    for lock_file in locks_dir.glob("*.lock"):
        lock_name = lock_file.stem
        lock = CommandLock.from_json(lock_file.read_text())
        if lock is None or not _is_pid_alive(lock.pid):
            try:
                lock_file.unlink()
                cleaned.append(lock_name)
                logger.info("Cleaned up stale lock '%s'", lock_name)
            except FileNotFoundError:
                pass

    return cleaned
