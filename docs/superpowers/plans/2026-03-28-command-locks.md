# Command Locks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `lock` field to registered commands that prevents concurrent execution of commands sharing the same lock name.

**Architecture:** File-based locks in `.lq/locks/{name}.lock` containing JSON metadata (PID, attempt_id, command, timestamp). Locks are acquired before Window 1 and released after Window 2 in the execution flow. Stale locks (dead PID) are automatically cleaned during acquisition and orphan cleanup.

**Tech Stack:** Python stdlib (`fcntl.flock` for atomic file locking, `os.kill(pid, 0)` for PID liveness), JSON lock files, existing `RegisteredCommand` dataclass.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/blq/locks.py` | Create | Lock acquisition, release, staleness check, cleanup |
| `src/blq/commands/core.py` | Modify | Add `lock` field to `RegisteredCommand`, update `_KNOWN_COMMAND_KEYS` |
| `src/blq/commands/execution.py` | Modify | Acquire/release locks around Window 1/2 execution |
| `src/blq/commands/clean_cmd.py` | Modify | Add lock cleanup to orphan cleanup path |
| `src/blq/bird.py` | Modify | Clean stale locks during `mark_stale_as_orphaned()` |
| `tests/test_locks.py` | Create | Unit tests for lock module |
| `tests/test_execution_locks.py` | Create | Integration tests for lock behavior during execution |

## Design Decisions

1. **File-based, not DB-based**: Locks must work *before* Window 1 (DB access). File locks via `fcntl.flock` provide atomic cross-process coordination without DB contention.
2. **Default behavior: fail immediately**: If a lock is held by a live process, `blq run` exits with a clear error. `--wait-lock SECONDS` opt-in for blocking.
3. **Stale lock cleanup on acquire**: If the lock file's PID is dead, the lock is automatically reclaimed. No separate cleanup step needed for normal operation.
4. **MCP gets it for free**: MCP `run` shells out to `blq run`, so lock semantics flow through automatically.
5. **`--no-lock` bypass**: For manual override when the user knows what they're doing.
6. **Lock names are explicit strings**: No auto-locking by command name. Commands opt in by setting `lock = "name"`.

---

### Task 1: Lock Module Core

**Files:**
- Create: `src/blq/locks.py`
- Create: `tests/test_locks.py`

- [ ] **Step 1: Write failing tests for lock file operations**

```python
# tests/test_locks.py
"""Tests for command lock mechanism."""
import json
import os
import time
from pathlib import Path

import pytest

from blq.locks import CommandLock, LockHeld, acquire_lock, release_lock


class TestCommandLock:
    """Tests for the CommandLock dataclass."""

    def test_to_json(self, tmp_path: Path):
        lock = CommandLock(
            lock_name="build",
            pid=12345,
            attempt_id="abc-123",
            command="make -j8",
            acquired_at=1711641600.0,
        )
        data = json.loads(lock.to_json())
        assert data["lock_name"] == "build"
        assert data["pid"] == 12345
        assert data["attempt_id"] == "abc-123"
        assert data["command"] == "make -j8"

    def test_from_json(self):
        raw = json.dumps({
            "lock_name": "build",
            "pid": 12345,
            "attempt_id": "abc-123",
            "command": "make -j8",
            "acquired_at": 1711641600.0,
        })
        lock = CommandLock.from_json(raw)
        assert lock.lock_name == "build"
        assert lock.pid == 12345

    def test_from_json_invalid_returns_none(self):
        assert CommandLock.from_json("not json") is None
        assert CommandLock.from_json("{}") is None  # missing fields


class TestAcquireLock:
    """Tests for lock acquisition."""

    def test_acquire_creates_lock_file(self, tmp_path: Path):
        lock = acquire_lock(
            locks_dir=tmp_path,
            lock_name="build",
            pid=os.getpid(),
            attempt_id="test-attempt",
            command="make",
        )
        assert lock is not None
        lock_file = tmp_path / "build.lock"
        assert lock_file.exists()
        data = json.loads(lock_file.read_text())
        assert data["pid"] == os.getpid()

    def test_acquire_fails_when_held_by_live_process(self, tmp_path: Path):
        # First acquisition succeeds
        acquire_lock(tmp_path, "build", os.getpid(), "attempt-1", "make")

        # Second acquisition fails (same PID is still alive)
        with pytest.raises(LockHeld) as exc_info:
            acquire_lock(tmp_path, "build", os.getpid(), "attempt-2", "make")
        assert exc_info.value.held_by.attempt_id == "attempt-1"

    def test_acquire_reclaims_stale_lock(self, tmp_path: Path):
        # Write a lock with a dead PID
        dead_pid = 99999999  # Almost certainly not running
        lock_file = tmp_path / "build.lock"
        lock_file.write_text(json.dumps({
            "lock_name": "build",
            "pid": dead_pid,
            "attempt_id": "old-attempt",
            "command": "make",
            "acquired_at": time.time() - 3600,
        }))

        # Should reclaim because PID is dead
        lock = acquire_lock(tmp_path, "build", os.getpid(), "new-attempt", "make")
        assert lock is not None
        data = json.loads(lock_file.read_text())
        assert data["attempt_id"] == "new-attempt"

    def test_acquire_creates_locks_dir(self, tmp_path: Path):
        locks_dir = tmp_path / "locks"
        assert not locks_dir.exists()
        acquire_lock(locks_dir, "build", os.getpid(), "test", "make")
        assert locks_dir.exists()

    def test_different_lock_names_are_independent(self, tmp_path: Path):
        acquire_lock(tmp_path, "build", os.getpid(), "attempt-1", "make")
        # Different lock name should succeed
        lock = acquire_lock(tmp_path, "test", os.getpid(), "attempt-2", "pytest")
        assert lock is not None


class TestReleaseLock:
    """Tests for lock release."""

    def test_release_removes_lock_file(self, tmp_path: Path):
        acquire_lock(tmp_path, "build", os.getpid(), "test", "make")
        lock_file = tmp_path / "build.lock"
        assert lock_file.exists()
        release_lock(tmp_path, "build")
        assert not lock_file.exists()

    def test_release_nonexistent_is_noop(self, tmp_path: Path):
        # Should not raise
        release_lock(tmp_path, "build")

    def test_release_only_removes_named_lock(self, tmp_path: Path):
        acquire_lock(tmp_path, "build", os.getpid(), "a1", "make")
        acquire_lock(tmp_path, "test", os.getpid(), "a2", "pytest")
        release_lock(tmp_path, "build")
        assert not (tmp_path / "build.lock").exists()
        assert (tmp_path / "test.lock").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_locks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq.locks'`

- [ ] **Step 3: Implement lock module**

```python
# src/blq/locks.py
"""File-based command locks for resource contention.

Locks are stored as JSON files in .lq/locks/{name}.lock.
Uses PID liveness checks to detect and reclaim stale locks.
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
    """Metadata for a held lock."""

    lock_name: str
    pid: int
    attempt_id: str
    command: str
    acquired_at: float

    def to_json(self) -> str:
        return json.dumps({
            "lock_name": self.lock_name,
            "pid": self.pid,
            "attempt_id": self.attempt_id,
            "command": self.command,
            "acquired_at": self.acquired_at,
        })

    @classmethod
    def from_json(cls, raw: str) -> CommandLock | None:
        try:
            data = json.loads(raw)
            return cls(
                lock_name=data["lock_name"],
                pid=data["pid"],
                attempt_id=data["attempt_id"],
                command=data["command"],
                acquired_at=data["acquired_at"],
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None


class LockHeld(Exception):
    """Raised when a lock is held by another live process."""

    def __init__(self, held_by: CommandLock):
        self.held_by = held_by
        age = time.time() - held_by.acquired_at
        super().__init__(
            f"Lock '{held_by.lock_name}' held by PID {held_by.pid} "
            f"(command: {held_by.command}, attempt: {held_by.attempt_id}, "
            f"age: {age:.0f}s)"
        )


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we can't signal it


def _lock_path(locks_dir: Path, lock_name: str) -> Path:
    return locks_dir / f"{lock_name}.lock"


def acquire_lock(
    locks_dir: Path,
    lock_name: str,
    pid: int,
    attempt_id: str,
    command: str,
) -> CommandLock:
    """Acquire a named lock. Reclaims stale locks automatically.

    Args:
        locks_dir: Directory for lock files (e.g., .lq/locks/)
        lock_name: Lock name (shared across commands)
        pid: Current process PID
        attempt_id: Attempt UUID for debugging
        command: Command string for debugging

    Returns:
        CommandLock on success

    Raises:
        LockHeld: If lock is held by a live process
    """
    locks_dir.mkdir(parents=True, exist_ok=True)
    path = _lock_path(locks_dir, lock_name)

    # Check existing lock
    if path.exists():
        try:
            existing = CommandLock.from_json(path.read_text())
        except OSError:
            existing = None

        if existing is not None and _is_pid_alive(existing.pid):
            raise LockHeld(existing)

        # Stale lock — reclaim it
        if existing is not None:
            logger.info(
                f"Reclaiming stale lock '{lock_name}' "
                f"(was PID {existing.pid}, attempt {existing.attempt_id})"
            )

    lock = CommandLock(
        lock_name=lock_name,
        pid=pid,
        attempt_id=attempt_id,
        command=command,
        acquired_at=time.time(),
    )

    path.write_text(lock.to_json())
    return lock


def release_lock(locks_dir: Path, lock_name: str) -> None:
    """Release a named lock by removing its lock file."""
    path = _lock_path(locks_dir, lock_name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def read_lock(locks_dir: Path, lock_name: str) -> CommandLock | None:
    """Read a lock file without acquiring. Returns None if not held."""
    path = _lock_path(locks_dir, lock_name)
    if not path.exists():
        return None
    try:
        return CommandLock.from_json(path.read_text())
    except OSError:
        return None


def cleanup_stale_locks(locks_dir: Path) -> list[str]:
    """Remove all lock files with dead PIDs. Returns names of cleaned locks."""
    if not locks_dir.exists():
        return []
    cleaned = []
    for lock_file in locks_dir.glob("*.lock"):
        lock = CommandLock.from_json(lock_file.read_text())
        if lock is not None and not _is_pid_alive(lock.pid):
            logger.info(f"Cleaning stale lock: {lock.lock_name} (PID {lock.pid})")
            lock_file.unlink(missing_ok=True)
            cleaned.append(lock.lock_name)
    return cleaned
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_locks.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq/locks.py tests/test_locks.py
git commit -m "feat: add command lock module for resource contention (#23)"
```

---

### Task 2: Add `lock` Field to RegisteredCommand

**Files:**
- Modify: `src/blq/commands/core.py:1073-1084` (RegisteredCommand fields)
- Modify: `src/blq/commands/core.py:1350-1353` (_KNOWN_COMMAND_KEYS)
- Modify: `src/blq/commands/core.py:1155-1181` (to_dict)
- Create: `tests/test_command_lock_field.py`

- [ ] **Step 1: Write failing tests for lock field**

```python
# tests/test_command_lock_field.py
"""Tests for the lock field on RegisteredCommand."""
from blq.commands.core import RegisteredCommand


class TestRegisteredCommandLockField:
    def test_default_lock_is_none(self):
        cmd = RegisteredCommand(name="build", cmd="make")
        assert cmd.lock is None

    def test_lock_field_set(self):
        cmd = RegisteredCommand(name="build", cmd="make", lock="build")
        assert cmd.lock == "build"

    def test_lock_in_to_dict(self):
        cmd = RegisteredCommand(name="build", cmd="make", lock="build")
        d = cmd.to_dict()
        assert d["lock"] == "build"

    def test_lock_omitted_from_to_dict_when_none(self):
        cmd = RegisteredCommand(name="build", cmd="make")
        d = cmd.to_dict()
        assert "lock" not in d

    def test_lock_shared_across_commands(self):
        build = RegisteredCommand(name="build", cmd="make", lock="compile")
        test = RegisteredCommand(name="test", cmd="pytest", lock="compile")
        assert build.lock == test.lock == "compile"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_command_lock_field.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'lock'`

- [ ] **Step 3: Add lock field to RegisteredCommand**

In `src/blq/commands/core.py`, add `lock` field to `RegisteredCommand` (after `lines`):

```python
    lines: str | None = None  # Default line selection for run/exec output (e.g., "+20-")
    lock: str | None = None  # Lock name for resource contention (shared across commands)
    _extra: dict[str, Any] = field(default_factory=dict)
```

Add `"lock"` to `_KNOWN_COMMAND_KEYS`:

```python
_KNOWN_COMMAND_KEYS = {
    "name", "cmd", "tpl", "defaults", "description", "timeout",
    "format", "capture", "capture_env", "suppress", "lines", "lock",
}
```

Add serialization in `to_dict()`, after the `lines` block:

```python
        if self.lines is not None:
            d["lines"] = self.lines
        if self.lock is not None:
            d["lock"] = self.lock
```

Add deserialization in `_load_commands_impl()`, in the `RegisteredCommand(...)` constructor call, add:

```python
                lock=config.get("lock"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_command_lock_field.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `.venv/bin/pytest tests/ -x -q --tb=short`
Expected: All pass (909+)

- [ ] **Step 6: Commit**

```bash
git add src/blq/commands/core.py tests/test_command_lock_field.py
git commit -m "feat: add lock field to RegisteredCommand (#23)"
```

---

### Task 3: Integrate Locks into Execution Flow

**Files:**
- Modify: `src/blq/commands/execution.py:226-250` (before Window 1)
- Modify: `src/blq/commands/execution.py:1057-1200` (cmd_run CLI flags)
- Create: `tests/test_execution_locks.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/test_execution_locks.py
"""Integration tests for command locks during execution."""
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from blq.locks import CommandLock, LockHeld, acquire_lock


class TestExecutionLockIntegration:
    """Test that execution respects lock field."""

    @pytest.fixture
    def lq_dir(self, tmp_path: Path) -> Path:
        """Create a minimal .lq directory."""
        lq = tmp_path / ".lq"
        lq.mkdir()
        (lq / "locks").mkdir()
        return lq

    def test_lock_blocks_concurrent_same_lock(self, lq_dir: Path):
        """Two commands with the same lock cannot run concurrently."""
        locks_dir = lq_dir / "locks"

        # Simulate a held lock from a live process
        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make")

        # Second attempt should fail
        with pytest.raises(LockHeld) as exc_info:
            acquire_lock(locks_dir, "build", os.getpid(), "attempt-2", "pytest")
        assert "build" in str(exc_info.value)

    def test_lock_allows_different_lock_names(self, lq_dir: Path):
        """Commands with different lock names can run concurrently."""
        locks_dir = lq_dir / "locks"

        acquire_lock(locks_dir, "build", os.getpid(), "attempt-1", "make")
        # Different lock name should succeed
        lock2 = acquire_lock(locks_dir, "lint", os.getpid(), "attempt-2", "ruff")
        assert lock2 is not None

    def test_no_lock_field_skips_locking(self, lq_dir: Path):
        """Commands without a lock field don't create lock files."""
        locks_dir = lq_dir / "locks"
        # No lock acquired, directory stays empty
        assert list(locks_dir.glob("*.lock")) == []

    def test_lock_released_after_execution(self, lq_dir: Path):
        """Lock file is removed after command completes."""
        locks_dir = lq_dir / "locks"
        from blq.locks import release_lock

        acquire_lock(locks_dir, "build", os.getpid(), "test", "make")
        assert (locks_dir / "build.lock").exists()
        release_lock(locks_dir, "build")
        assert not (locks_dir / "build.lock").exists()

    def test_lock_released_on_failure(self, lq_dir: Path):
        """Lock is released even if the command fails."""
        locks_dir = lq_dir / "locks"
        from blq.locks import release_lock

        acquire_lock(locks_dir, "build", os.getpid(), "test", "make")

        # Simulate failure path — lock should still be released
        try:
            raise RuntimeError("command failed")
        finally:
            release_lock(locks_dir, "build")

        assert not (locks_dir / "build.lock").exists()
```

- [ ] **Step 2: Run tests to verify they pass (these test the lock module directly)**

Run: `.venv/bin/pytest tests/test_execution_locks.py -v`
Expected: All PASS (they test the lock API which already exists from Task 1)

- [ ] **Step 3: Add `--no-lock` and `--wait-lock` CLI flags to `cmd_run`**

In `src/blq/commands/execution.py`, find the `cmd_run()` argument parser setup. The args come from `cli.py`.

In `src/blq/cli.py`, find the `blq run` subparser and add these arguments:

```python
    p_run.add_argument(
        "--no-lock", action="store_true",
        help="Bypass command lock (run even if lock is held)",
    )
    p_run.add_argument(
        "--wait-lock", type=int, metavar="SECONDS", default=None,
        help="Wait up to SECONDS for lock to be released (default: fail immediately)",
    )
```

- [ ] **Step 4: Integrate lock acquire/release into `_execute_with_live_output()`**

In `src/blq/commands/execution.py`, add the import at the top:

```python
from blq.locks import LockHeld, acquire_lock, release_lock
```

Add `lock_name`, `no_lock`, and `wait_lock` parameters to `_execute_with_live_output()`:

```python
def _execute_with_live_output(
    command: str,
    source_name: str,
    source_type: str,
    config: BlqConfig,
    format_hint: str = "auto",
    quiet: bool = False,
    keep_raw: bool | None = None,
    error_limit: int = 50,
    session_id: str | None = None,
    capture_env_vars: list[str] | None = None,
    timeout: int | None = None,
    lock_name: str | None = None,
    no_lock: bool = False,
    wait_lock: int | None = None,
) -> RunResult:
```

Add lock acquisition **before** Window 1 (before the `with BirdStore.open_with_retry(...)` block at line 324). The lock must wrap the entire execution including both DB windows:

```python
    # =========================================================================
    # Lock acquisition (before DB access)
    # =========================================================================
    locks_dir = config.lq_dir / "locks"
    held_lock_name: str | None = None

    if lock_name and not no_lock:
        if wait_lock is not None:
            deadline = time.time() + wait_lock
            while True:
                try:
                    acquire_lock(locks_dir, lock_name, os.getpid(), attempt.id, command)
                    held_lock_name = lock_name
                    break
                except LockHeld as e:
                    if time.time() >= deadline:
                        raise
                    remaining = deadline - time.time()
                    wait_time = min(1.0, remaining)
                    if not quiet:
                        logger.info(f"Lock '{lock_name}' held, waiting ({remaining:.0f}s remaining)...")
                    time.sleep(wait_time)
        else:
            acquire_lock(locks_dir, lock_name, os.getpid(), attempt.id, command)
            held_lock_name = lock_name

    try:
        # ... existing Window 1, subprocess, Window 2 code ...
        # (indent the existing code into this try block)
    finally:
        if held_lock_name:
            release_lock(locks_dir, held_lock_name)
```

Wrap the entire body from Window 1 through the `return RunResult(...)` in the `try` block so the lock is always released.

- [ ] **Step 5: Pass lock parameters from `cmd_run()` to `_execute_with_live_output()`**

In `cmd_run()`, after resolving the `RegisteredCommand`, extract the lock name and pass it through:

```python
    lock_name = reg_cmd.lock if reg_cmd else None
    no_lock = getattr(args, "no_lock", False)
    wait_lock = getattr(args, "wait_lock", None)
```

Then pass these to `_execute_command()` / `_execute_with_live_output()`:

```python
    lock_name=lock_name,
    no_lock=no_lock,
    wait_lock=wait_lock,
```

- [ ] **Step 6: Handle LockHeld in `cmd_run()` error path**

In `cmd_run()`, wrap the execution call to catch `LockHeld` and print a user-friendly message:

```python
    from blq.locks import LockHeld

    try:
        result = _execute_command(...)
    except LockHeld as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Use --no-lock to bypass or --wait-lock SECONDS to wait.", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/pytest tests/ -x -q --tb=short`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/blq/commands/execution.py src/blq/cli.py tests/test_execution_locks.py
git commit -m "feat: integrate command locks into execution flow (#23)"
```

---

### Task 4: Lock Cleanup in Orphan Detection

**Files:**
- Modify: `src/blq/bird.py:1134` (mark_stale_as_orphaned)
- Modify: `src/blq/commands/clean_cmd.py` (clean command)
- Modify: `tests/test_locks.py` (add cleanup tests)

- [ ] **Step 1: Add cleanup test**

Append to `tests/test_locks.py`:

```python
class TestCleanupStaleLocks:
    def test_removes_locks_with_dead_pids(self, tmp_path: Path):
        from blq.locks import cleanup_stale_locks

        # Write a lock with a dead PID
        lock_file = tmp_path / "build.lock"
        lock_file.write_text(json.dumps({
            "lock_name": "build",
            "pid": 99999999,
            "attempt_id": "old",
            "command": "make",
            "acquired_at": time.time() - 3600,
        }))

        cleaned = cleanup_stale_locks(tmp_path)
        assert "build" in cleaned
        assert not lock_file.exists()

    def test_keeps_locks_with_live_pids(self, tmp_path: Path):
        from blq.locks import cleanup_stale_locks

        lock_file = tmp_path / "mylock.lock"
        lock_file.write_text(json.dumps({
            "lock_name": "mylock",
            "pid": os.getpid(),  # This process is alive
            "attempt_id": "current",
            "command": "make",
            "acquired_at": time.time(),
        }))

        cleaned = cleanup_stale_locks(tmp_path)
        assert cleaned == []
        assert lock_file.exists()

    def test_handles_empty_dir(self, tmp_path: Path):
        from blq.locks import cleanup_stale_locks

        cleaned = cleanup_stale_locks(tmp_path)
        assert cleaned == []

    def test_handles_missing_dir(self, tmp_path: Path):
        from blq.locks import cleanup_stale_locks

        cleaned = cleanup_stale_locks(tmp_path / "nonexistent")
        assert cleaned == []
```

- [ ] **Step 2: Run cleanup tests**

Run: `.venv/bin/pytest tests/test_locks.py::TestCleanupStaleLocks -v`
Expected: All PASS (cleanup_stale_locks already implemented in Task 1)

- [ ] **Step 3: Call `cleanup_stale_locks()` from orphan cleanup**

In `src/blq/bird.py`, in `mark_stale_as_orphaned()`, add lock cleanup after marking orphans:

```python
from blq.locks import cleanup_stale_locks

# At the end of mark_stale_as_orphaned(), after cleaning up live dirs:
locks_dir = self.lq_dir / "locks"
cleanup_stale_locks(locks_dir)
```

In `src/blq/commands/clean_cmd.py`, in the orphan cleanup path, add the same:

```python
from blq.locks import cleanup_stale_locks

# After mark_stale_as_orphaned() call:
locks_dir = config.lq_dir / "locks"
cleaned_locks = cleanup_stale_locks(locks_dir)
if cleaned_locks:
    print(f"Cleaned {len(cleaned_locks)} stale lock(s): {', '.join(cleaned_locks)}")
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest tests/ -x -q --tb=short`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/blq/bird.py src/blq/commands/clean_cmd.py tests/test_locks.py
git commit -m "feat: clean stale locks during orphan detection (#23)"
```

---

### Task 5: MCP `register_command` Lock Support

**Files:**
- Modify: `src/blq/serve.py` (register_command tool)

- [ ] **Step 1: Add `lock` parameter to MCP `register_command` tool**

In `src/blq/serve.py`, find the `register_command` tool function and add a `lock` parameter:

```python
@mcp.tool()
def register_command(
    name: str,
    cmd: str,
    ...,
    lock: str | None = None,
    ...
) -> str:
```

Pass `lock=lock` to the `RegisteredCommand(...)` constructor.

- [ ] **Step 2: Verify MCP run tool reports lock errors**

The MCP `run` tool shells out to `blq run --json`. When a `LockHeld` error occurs, `cmd_run()` exits with code 1 and prints to stderr. The MCP `run` handler already captures stderr in error cases. Verify this path handles it by checking that `proc.returncode != 0` leads to an error response including the lock message.

If the error path doesn't capture stderr, add it:

```python
# In the run tool's error handling for non-zero exit:
if proc.returncode != 0 and not proc.stdout.strip():
    return json.dumps({
        "status": "FAIL",
        "exit_code": proc.returncode,
        "error": proc.stderr.strip() if proc.stderr else f"Command failed with exit code {proc.returncode}",
    })
```

- [ ] **Step 3: Run all tests**

Run: `.venv/bin/pytest tests/ -x -q --tb=short`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add src/blq/serve.py
git commit -m "feat: add lock parameter to MCP register_command (#23)"
```

---

### Task 6: Documentation and Issue Closure

**Files:**
- Modify: `CLAUDE.md` (completed features)
- Modify: `docs/commands/run.md` (CLI flags)
- Modify: `docs/commands/registry.md` (lock field)
- Modify: `docs/mcp.md` (register_command lock param)

- [ ] **Step 1: Update CLAUDE.md completed list**

Add to the Completed section:

```
- **Command locks** for resource contention (`lock` field in commands.toml) - Issue #23
```

- [ ] **Step 2: Update docs/commands/run.md**

Add `--no-lock` and `--wait-lock` to the options table.

- [ ] **Step 3: Update docs/commands/registry.md**

Add `lock` field documentation with example:

```toml
[commands.build]
cmd = "make -j8"
lock = "build"

[commands.test]
cmd = "pytest"
lock = "build"  # Shares lock — can't run while build is running

[commands.lint]
cmd = "ruff check"
# No lock — runs concurrently with anything
```

- [ ] **Step 4: Update docs/mcp.md register_command section**

Add `lock` parameter to the example:

```json
{"name": "test", "cmd": "pytest", "lock": "build"}
```

- [ ] **Step 5: Run full test suite one final time**

Run: `.venv/bin/pytest tests/ -q --tb=short`
Expected: All pass

- [ ] **Step 6: Commit docs**

```bash
git add CLAUDE.md docs/
git commit -m "docs: add command locks documentation (#23)"
```

- [ ] **Step 7: Close issue**

```bash
gh issue close 23 --comment "Implemented in main. Commands can now declare a \`lock\` field to prevent concurrent execution."
```
