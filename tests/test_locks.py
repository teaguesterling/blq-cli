"""Tests for the command lock module."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from blq.locks import CommandLock, LockHeldError, acquire_lock, cleanup_stale_locks, read_lock, release_lock

DEAD_PID = 99999999


class TestCommandLock:
    """Tests for CommandLock dataclass."""

    def _make_lock(self, **kwargs) -> CommandLock:
        defaults = {
            "lock_name": "build",
            "pid": os.getpid(),
            "attempt_id": "abc-123",
            "command": "make build",
            "acquired_at": 1000.0,
        }
        defaults.update(kwargs)
        return CommandLock(**defaults)

    def test_to_json_roundtrip(self):
        """to_json and from_json are inverse operations."""
        lock = self._make_lock()
        raw = lock.to_json()
        assert isinstance(raw, str)
        parsed = json.loads(raw)
        assert parsed["lock_name"] == "build"
        assert parsed["pid"] == os.getpid()
        assert parsed["attempt_id"] == "abc-123"
        assert parsed["command"] == "make build"
        assert parsed["acquired_at"] == 1000.0

    def test_from_json_valid(self):
        """from_json reconstructs a valid CommandLock."""
        lock = self._make_lock()
        raw = lock.to_json()
        restored = CommandLock.from_json(raw)
        assert restored is not None
        assert restored.lock_name == lock.lock_name
        assert restored.pid == lock.pid
        assert restored.attempt_id == lock.attempt_id
        assert restored.command == lock.command
        assert restored.acquired_at == lock.acquired_at

    def test_from_json_missing_fields(self):
        """from_json returns None when required fields are missing."""
        raw = json.dumps({"lock_name": "build", "pid": 123})
        result = CommandLock.from_json(raw)
        assert result is None

    def test_from_json_invalid_json(self):
        """from_json returns None on invalid JSON input."""
        result = CommandLock.from_json("not valid json{{")
        assert result is None

    def test_from_json_empty_string(self):
        """from_json returns None on empty string."""
        result = CommandLock.from_json("")
        assert result is None

    def test_from_json_wrong_type(self):
        """from_json returns None when JSON is not an object."""
        result = CommandLock.from_json("[1, 2, 3]")
        assert result is None


class TestLockHeldError:
    """Tests for LockHeldError exception."""

    def test_attributes(self):
        """LockHeldError carries the holding CommandLock."""
        lock = CommandLock(
            lock_name="test",
            pid=12345,
            attempt_id="xyz",
            command="pytest",
            acquired_at=time.time() - 5.0,
        )
        exc = LockHeldError(lock)
        assert exc.held_by is lock

    def test_message_contains_info(self):
        """LockHeldError message includes lock name, PID, command, attempt_id, and age."""
        lock = CommandLock(
            lock_name="build",
            pid=12345,
            attempt_id="abc-123",
            command="make build",
            acquired_at=time.time() - 10.0,
        )
        exc = LockHeldError(lock)
        msg = str(exc)
        assert "build" in msg
        assert "12345" in msg
        assert "make build" in msg
        assert "abc-123" in msg


class TestAcquireLock:
    """Tests for acquire_lock."""

    def test_creates_lock_file(self, tmp_path):
        """acquire_lock writes a lock file to locks_dir."""
        locks_dir = tmp_path / "locks"
        lock = acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make build")
        lock_file = locks_dir / "build.lock"
        assert lock_file.exists()
        assert lock.lock_name == "build"
        assert lock.pid == os.getpid()
        assert lock.attempt_id == "attempt-1"
        assert lock.command == "make build"

    def test_creates_locks_dir_automatically(self, tmp_path):
        """acquire_lock creates locks_dir if it does not exist."""
        locks_dir = tmp_path / "nested" / "locks"
        assert not locks_dir.exists()
        acquire_lock(locks_dir, "test", os.getpid(), "a1", "pytest")
        assert locks_dir.exists()
        assert (locks_dir / "test.lock").exists()

    def test_raises_when_held_by_live_pid(self, tmp_path):
        """acquire_lock raises LockHeldError when lock is held by a live PID."""
        locks_dir = tmp_path / "locks"
        live_pid = os.getpid()
        # Write a lock held by the current (live) PID
        acquire_lock(locks_dir, "build", live_pid, "first", "make build")

        with pytest.raises(LockHeldError) as exc_info:
            acquire_lock(locks_dir, "build", live_pid, "second", "make build")

        assert exc_info.value.held_by.attempt_id == "first"

    def test_reclaims_stale_lock(self, tmp_path):
        """acquire_lock reclaims a lock held by a dead PID."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        # Write a lock with a dead PID directly
        stale = CommandLock(
            lock_name="build",
            pid=DEAD_PID,
            attempt_id="old-attempt",
            command="make build",
            acquired_at=time.time() - 60.0,
        )
        (locks_dir / "build.lock").write_text(stale.to_json())

        # Should succeed and overwrite the stale lock
        new_lock = acquire_lock(locks_dir, "build", os.getpid(), "new-attempt", "make build")
        assert new_lock.attempt_id == "new-attempt"
        assert new_lock.pid == os.getpid()

        # File should now hold the new lock
        raw = (locks_dir / "build.lock").read_text()
        restored = CommandLock.from_json(raw)
        assert restored is not None
        assert restored.attempt_id == "new-attempt"

    def test_different_lock_names_are_independent(self, tmp_path):
        """Two different lock names do not conflict with each other."""
        locks_dir = tmp_path / "locks"
        live_pid = os.getpid()
        lock_a = acquire_lock(locks_dir, "build", live_pid, "a1", "make build")
        lock_b = acquire_lock(locks_dir, "test", live_pid, "b1", "pytest")
        assert lock_a.lock_name == "build"
        assert lock_b.lock_name == "test"
        assert (locks_dir / "build.lock").exists()
        assert (locks_dir / "test.lock").exists()


class TestReleaseLock:
    """Tests for release_lock."""

    def test_removes_lock_file(self, tmp_path):
        """release_lock removes the named lock file."""
        locks_dir = tmp_path / "locks"
        acquire_lock(locks_dir, "build", os.getpid(), "a1", "make build")
        assert (locks_dir / "build.lock").exists()
        release_lock(locks_dir, "build")
        assert not (locks_dir / "build.lock").exists()

    def test_nonexistent_is_noop(self, tmp_path):
        """release_lock on a nonexistent lock does not raise."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        # Should not raise
        release_lock(locks_dir, "nonexistent")

    def test_only_removes_named_lock(self, tmp_path):
        """release_lock removes only the specified lock, not others."""
        locks_dir = tmp_path / "locks"
        live_pid = os.getpid()
        acquire_lock(locks_dir, "build", live_pid, "a1", "make build")
        acquire_lock(locks_dir, "test", live_pid, "b1", "pytest")
        release_lock(locks_dir, "build")
        assert not (locks_dir / "build.lock").exists()
        assert (locks_dir / "test.lock").exists()


class TestCleanupStaleLocks:
    """Tests for cleanup_stale_locks."""

    def test_removes_dead_pid_locks(self, tmp_path):
        """cleanup_stale_locks removes locks with dead PIDs."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        stale = CommandLock(
            lock_name="build",
            pid=DEAD_PID,
            attempt_id="old",
            command="make build",
            acquired_at=time.time() - 60.0,
        )
        (locks_dir / "build.lock").write_text(stale.to_json())

        cleaned = cleanup_stale_locks(locks_dir)
        assert "build" in cleaned
        assert not (locks_dir / "build.lock").exists()

    def test_keeps_live_pid_locks(self, tmp_path):
        """cleanup_stale_locks does not remove locks with live PIDs."""
        locks_dir = tmp_path / "locks"
        acquire_lock(locks_dir, "build", os.getpid(), "a1", "make build")

        cleaned = cleanup_stale_locks(locks_dir)
        assert "build" not in cleaned
        assert (locks_dir / "build.lock").exists()

    def test_handles_empty_dir(self, tmp_path):
        """cleanup_stale_locks returns empty list for an empty directory."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        cleaned = cleanup_stale_locks(locks_dir)
        assert cleaned == []

    def test_handles_missing_dir(self, tmp_path):
        """cleanup_stale_locks returns empty list when directory doesn't exist."""
        locks_dir = tmp_path / "nonexistent" / "locks"
        cleaned = cleanup_stale_locks(locks_dir)
        assert cleaned == []

    def test_returns_names_of_cleaned_locks(self, tmp_path):
        """cleanup_stale_locks returns the names (not filenames) of cleaned locks."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        for name in ("build", "test", "lint"):
            stale = CommandLock(
                lock_name=name,
                pid=DEAD_PID,
                attempt_id="old",
                command="cmd",
                acquired_at=time.time() - 30.0,
            )
            (locks_dir / f"{name}.lock").write_text(stale.to_json())

        cleaned = cleanup_stale_locks(locks_dir)
        assert sorted(cleaned) == ["build", "lint", "test"]


class TestReadLock:
    """Tests for read_lock."""

    def test_returns_lock_when_exists(self, tmp_path):
        """read_lock returns the CommandLock when lock file exists."""
        locks_dir = tmp_path / "locks"
        acquire_lock(locks_dir, "build", os.getpid(), "a1", "make build")
        lock = read_lock(locks_dir, "build")
        assert lock is not None
        assert lock.lock_name == "build"
        assert lock.attempt_id == "a1"

    def test_returns_none_when_missing(self, tmp_path):
        """read_lock returns None when no lock file exists."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        lock = read_lock(locks_dir, "build")
        assert lock is None

    def test_does_not_acquire_lock(self, tmp_path):
        """read_lock does not create a lock file."""
        locks_dir = tmp_path / "locks"
        locks_dir.mkdir()
        read_lock(locks_dir, "build")
        assert not (locks_dir / "build.lock").exists()
