"""Integration tests for command locks during execution."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from blq.locks import CommandLock, LockHeld, acquire_lock, release_lock


class TestExecutionLockIntegration:
    @pytest.fixture
    def locks_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "locks"
        d.mkdir()
        return d

    def test_lock_blocks_concurrent_same_lock(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make")
        with pytest.raises(LockHeld) as exc_info:
            acquire_lock(locks_dir, "build", os.getpid(), "attempt-2", "pytest")
        assert "build" in str(exc_info.value)

    def test_lock_allows_different_lock_names(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make")
        lock2 = acquire_lock(locks_dir, "lint", os.getpid(), "attempt-2", "ruff")
        assert lock2 is not None

    def test_lock_released_after_use(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "test", "make")
        assert (locks_dir / "build.lock").exists()
        release_lock(locks_dir, "build")
        assert not (locks_dir / "build.lock").exists()

    def test_lock_released_on_exception(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "test", "make")
        try:
            raise RuntimeError("command failed")
        except RuntimeError:
            release_lock(locks_dir, "build")
        assert not (locks_dir / "build.lock").exists()

    def test_no_lock_field_means_no_locking(self, locks_dir: Path) -> None:
        """Commands without lock field should not create lock files."""
        assert list(locks_dir.glob("*.lock")) == []

    def test_lock_file_contains_valid_json(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make -j8")
        lock_file = locks_dir / "build.lock"
        data = json.loads(lock_file.read_text())
        assert data["lock_name"] == "build"
        assert data["pid"] == os.getpid()
        assert data["attempt_id"] == "attempt-1"
        assert data["command"] == "make -j8"
        assert "acquired_at" in data

    def test_lock_reacquirable_after_release(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make")
        release_lock(locks_dir, "build")
        lock2 = acquire_lock(locks_dir, "build", os.getpid(), "attempt-2", "make")
        assert lock2 is not None
        assert lock2.attempt_id == "attempt-2"
        release_lock(locks_dir, "build")

    def test_lock_held_error_contains_holder_info(self, locks_dir: Path) -> None:
        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make")
        with pytest.raises(LockHeld) as exc_info:
            acquire_lock(locks_dir, "build", os.getpid(), "attempt-2", "make")
        err = exc_info.value
        assert err.held_by.lock_name == "build"
        assert err.held_by.pid == os.getpid()
        assert err.held_by.attempt_id == "attempt-1"
        assert err.held_by.command == "make"

    def test_release_nonexistent_lock_is_noop(self, locks_dir: Path) -> None:
        """Releasing a lock that doesn't exist should not raise."""
        release_lock(locks_dir, "nonexistent")
