"""
Watch mode for continuous capture.

Monitors file system changes and automatically re-runs commands,
capturing output for each run.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import signal
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from blq.commands.core import BlqConfig, RunResult
from blq.commands.execution import _execute_command


@dataclass
class WatchSession:
    """Tracks state for a watch mode session."""

    session_id: str
    commands: list[str]
    include_patterns: list[str]
    exclude_patterns: list[str]
    debounce_ms: int
    started_at: datetime
    run_count: int = 0
    failed_count: int = 0
    last_result: RunResult | None = None

    @classmethod
    def create(
        cls,
        commands: list[str],
        include_patterns: list[str],
        exclude_patterns: list[str],
        debounce_ms: int = 500,
    ) -> WatchSession:
        """Create a new watch session with a unique ID."""
        return cls(
            session_id=str(uuid.uuid4())[:8],  # Short ID for display
            commands=commands,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            debounce_ms=debounce_ms,
            started_at=datetime.now(),
        )


def _matches_pattern(path: str, patterns: list[str]) -> bool:
    """Check if a path matches any of the given glob patterns.

    Supports ** for recursive directory matching.
    """
    for pattern in patterns:
        # Handle ** patterns by converting to fnmatch-compatible form
        if "**" in pattern:
            # Replace ** with * for fnmatch (less precise but works)
            simple_pattern = pattern.replace("**/", "").replace("/**", "")
            if fnmatch.fnmatch(path, simple_pattern):
                return True
            if fnmatch.fnmatch(os.path.basename(path), simple_pattern):
                return True
            # Also try matching with a more relaxed pattern
            parts = pattern.split("**/")
            if len(parts) == 2:
                prefix, suffix = parts
                # Check if path starts with prefix and ends matching suffix
                if path.startswith(prefix.rstrip("/")) or not prefix:
                    if fnmatch.fnmatch(path, "*" + suffix):
                        return True
                    if fnmatch.fnmatch(os.path.basename(path), suffix.lstrip("/")):
                        return True
        else:
            if fnmatch.fnmatch(path, pattern):
                return True
            # Also check just the filename
            if fnmatch.fnmatch(os.path.basename(path), pattern):
                return True
    return False


class DebounceHandler(FileSystemEventHandler):
    """Watchdog handler with debouncing and pattern filtering.

    Collects file change events, filters by include/exclude patterns,
    and calls a callback after a debounce delay.
    """

    def __init__(
        self,
        callback: Callable[[set[str]], None],
        debounce_ms: int,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ):
        super().__init__()
        self._callback = callback
        self._debounce_ms = debounce_ms
        self._include_patterns = include_patterns
        self._exclude_patterns = exclude_patterns
        self._pending_timer: threading.Timer | None = None
        self._pending_files: set[str] = set()
        self._lock = threading.Lock()

    def _should_include(self, path: str) -> bool:
        """Check if a file should trigger a rebuild."""
        # Check exclude patterns first
        if self._exclude_patterns and _matches_pattern(path, self._exclude_patterns):
            return False

        # If include patterns specified, path must match one
        if self._include_patterns:
            return _matches_pattern(path, self._include_patterns)

        # No include patterns = include everything not excluded
        return True

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle any file system event."""
        # Skip directory events
        if event.is_directory:
            return

        # Skip non-modification events
        if event.event_type not in ("created", "modified", "deleted", "moved"):
            return

        src_path = event.src_path if isinstance(event.src_path, str) else event.src_path.decode()
        if not self._should_include(src_path):
            return

        with self._lock:
            self._pending_files.add(src_path)
            # Cancel existing timer and start a new one
            if self._pending_timer:
                self._pending_timer.cancel()
            self._pending_timer = threading.Timer(
                self._debounce_ms / 1000.0,
                self._fire_callback,
            )
            self._pending_timer.daemon = True
            self._pending_timer.start()

    def _fire_callback(self) -> None:
        """Fire the callback with accumulated files."""
        with self._lock:
            files = self._pending_files.copy()
            self._pending_files.clear()
            self._pending_timer = None

        if files:
            self._callback(files)


class WatchController:
    """Controls the watch loop with queue-next-run behavior.

    State machine:
        IDLE -> RUNNING (on file change)
        RUNNING -> QUEUED (if file change during run)
        RUNNING -> IDLE (if no changes during run)
        QUEUED -> RUNNING (after current run completes)
    """

    def __init__(
        self,
        session: WatchSession,
        config: BlqConfig,
        quiet: bool = False,
        clear: bool = False,
    ):
        self._session = session
        self._config = config
        self._quiet = quiet
        self._clear = clear
        self._state: Literal["idle", "running", "queued"] = "idle"
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._changed_files: set[str] = set()

    def on_files_changed(self, files: set[str]) -> None:
        """Handle file change notification from DebounceHandler."""
        with self._state_lock:
            self._changed_files.update(files)
            if self._state == "idle":
                self._state = "running"
                # Start run in a new thread
                thread = threading.Thread(target=self._run_loop, daemon=True)
                thread.start()
            elif self._state == "running":
                # Queue next run
                self._state = "queued"

    def _run_loop(self) -> None:
        """Run commands, possibly multiple times if queued."""
        while not self._stop_event.is_set():
            # Get changed files and clear
            with self._state_lock:
                changed = self._changed_files.copy()
                self._changed_files.clear()

            # Run all commands
            self._execute_run(changed)

            with self._state_lock:
                if self._state == "queued":
                    # More changes came in, run again
                    self._state = "running"
                    continue
                else:
                    self._state = "idle"
                    break

    def _execute_run(self, changed_files: set[str]) -> None:
        """Execute all configured commands."""
        if self._clear:
            # Clear screen
            print("\033[2J\033[H", end="")

        # Show what changed
        timestamp = datetime.now().strftime("%H:%M:%S")
        if len(changed_files) <= 3:
            files_str = ", ".join(os.path.basename(f) for f in changed_files)
        else:
            files_str = f"{len(changed_files)} files"
        print(f"[{timestamp}] Changes detected: {files_str}")

        all_success = True
        total_errors = 0

        for cmd_name in self._session.commands:
            # Check if command is registered
            if cmd_name not in self._config.commands:
                print(f"  {cmd_name}: (not registered, skipping)")
                continue

            reg_cmd = self._config.commands[cmd_name]
            print(f"[{timestamp}] Running {cmd_name}...")

            try:
                result = _execute_command(
                    command=reg_cmd.cmd,
                    source_name=cmd_name,
                    source_type="watch",
                    config=self._config,
                    format_hint=reg_cmd.format,
                    quiet=self._quiet,
                    session_id=self._session.session_id,
                )

                self._session.run_count += 1
                self._session.last_result = result

                # Show result
                if result.status == "OK":
                    print(f"  {cmd_name}: OK ({result.duration_sec:.1f}s)")
                else:
                    print(
                        f"  {cmd_name}: {result.status} ({result.duration_sec:.1f}s) "
                        f"- {result.summary.get('errors', 0)} errors"
                    )
                    all_success = False
                    total_errors += result.summary.get("errors", 0)
                    self._session.failed_count += 1

            except Exception as e:
                print(f"  {cmd_name}: ERROR - {e}")
                all_success = False
                self._session.failed_count += 1

        # Print session summary
        print()
        if all_success:
            print(f"Session {self._session.session_id}: {self._session.run_count} runs, all passed")
        else:
            print(
                f"Session {self._session.session_id}: {self._session.run_count} runs, "
                f"{self._session.failed_count} failed, {total_errors} total errors"
            )
        print()

    def stop(self) -> None:
        """Signal the controller to stop."""
        self._stop_event.set()


def cmd_watch(args: argparse.Namespace) -> None:
    """Watch for file changes and re-run commands.

    Monitors the current directory for file changes and automatically
    re-runs configured commands when files are modified.
    """
    config = BlqConfig.ensure()

    # Determine commands to run
    if args.commands:
        commands = args.commands
    else:
        # Run all registered commands
        commands = list(config.commands.keys())

    if not commands:
        print("Error: No commands to run.", file=sys.stderr)
        print("Either specify commands or register some first.", file=sys.stderr)
        sys.exit(1)

    # Validate commands exist
    missing = [c for c in commands if c not in config.commands]
    if missing:
        print(f"Warning: Commands not registered: {', '.join(missing)}", file=sys.stderr)
        print("These will be skipped.", file=sys.stderr)

    # Get watch config
    watch_config = config.watch_config

    # Merge CLI args with config defaults
    include_patterns = args.include if args.include else watch_config.include
    exclude_patterns = args.exclude if args.exclude else watch_config.exclude
    debounce_ms = args.debounce if args.debounce else watch_config.debounce_ms
    quiet = args.quiet or watch_config.quiet
    clear = args.clear or watch_config.clear_screen

    # Create session
    session = WatchSession.create(
        commands=commands,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        debounce_ms=debounce_ms,
    )

    # Create controller
    controller = WatchController(
        session=session,
        config=config,
        quiet=quiet,
        clear=clear,
    )

    # Create handler
    handler = DebounceHandler(
        callback=controller.on_files_changed,
        debounce_ms=debounce_ms,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )

    # Start observer
    observer = Observer()
    observer.schedule(handler, ".", recursive=True)
    observer.start()

    # Print startup message
    print("blq: Watching for changes...")
    print(f"  Commands: {', '.join(commands)}")
    if include_patterns:
        print(f"  Include: {', '.join(include_patterns)}")
    if exclude_patterns:
        excl_str = ", ".join(exclude_patterns[:3])
        if len(exclude_patterns) > 3:
            excl_str += "..."
        print(f"  Exclude: {excl_str}")
    print(f"  Debounce: {debounce_ms}ms")
    print(f"  Session: {session.session_id}")
    print()
    print("Press Ctrl+C to stop.")
    print()

    # Run once on startup if --once flag
    if getattr(args, "once", False):
        # Run synchronously instead of through the controller
        controller._execute_run({"<startup>"})
        observer.stop()
        observer.join()
        return

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n\nStopping watch...")
        controller.stop()
        observer.stop()

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()

    # Print final summary
    print()
    print(f"Session {session.session_id} complete:")
    print(f"  Total runs: {session.run_count}")
    print(f"  Failed runs: {session.failed_count}")
