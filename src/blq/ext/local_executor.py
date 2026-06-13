"""Default local subprocess executor."""

from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import FrameType

from blq.ext import CommandSpec, ExecutionResult

logger = logging.getLogger("blq-ext")


class LocalExecutor:
    """Execute commands as local subprocesses.

    Handles subprocess lifecycle: creation, output streaming,
    timeout management, signal handling, and cleanup.
    """

    name = "local"

    def __init__(self, quiet: bool = False, live_output_path: Path | None = None):
        """
        Args:
            quiet: If True, don't echo output to stdout.
            live_output_path: Path to write live output to (for live inspection).
        """
        self._quiet = quiet
        self._live_output_path = live_output_path

    def execute(self, spec: CommandSpec) -> ExecutionResult:
        """Execute the command as a local subprocess."""
        started_at = datetime.now()
        output_lines: list[str] = []
        exit_code = 0
        timed_out = False
        process_pid: int | None = None
        process: subprocess.Popen[str] | None = None
        sig: int | None = None

        live_file = None
        if self._live_output_path:
            self._live_output_path.parent.mkdir(parents=True, exist_ok=True)
            live_file = open(self._live_output_path, "w")  # noqa: SIM115

        # Signal handler to clean up subprocess
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        def _cleanup(signum: int, frame: FrameType | None) -> None:
            nonlocal sig
            sig = signum
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            # Re-raise the signal
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            else:
                sys.exit(128 + signum)

        try:
            signal.signal(signal.SIGINT, _cleanup)
            signal.signal(signal.SIGTERM, _cleanup)

            run_env = {**os.environ, **spec.env} if spec.env else None
            process = subprocess.Popen(
                spec.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                cwd=str(spec.cwd),
                env=run_env,
            )
            process_pid = process.pid

            assert process.stdout is not None

            if spec.timeout is None:
                # Simple synchronous read
                for line in process.stdout:
                    output_lines.append(line)
                    if live_file:
                        live_file.write(line)
                        live_file.flush()
                    if not self._quiet:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                exit_code = process.wait()
            else:
                # Timeout-aware read with threading
                output_queue: queue.Queue[str | None] = queue.Queue()

                def _reader() -> None:
                    try:
                        assert process.stdout is not None
                        for line in process.stdout:
                            output_queue.put(line)
                    finally:
                        output_queue.put(None)

                reader_thread = threading.Thread(target=_reader, daemon=True)
                reader_thread.start()

                deadline = time.monotonic() + spec.timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break

                    try:
                        queued = output_queue.get(timeout=min(remaining, 0.5))
                        if queued is None:
                            break
                        output_lines.append(queued)
                        if live_file:
                            live_file.write(queued)
                            live_file.flush()
                        if not self._quiet:
                            sys.stdout.write(queued)
                            sys.stdout.flush()
                    except queue.Empty:
                        if process.poll() is not None:
                            # Process finished — drain remaining output
                            while True:
                                try:
                                    drain_line = output_queue.get_nowait()
                                    if drain_line is None:
                                        break
                                    output_lines.append(drain_line)
                                    if live_file:
                                        live_file.write(drain_line)
                                        live_file.flush()
                                    if not self._quiet:
                                        sys.stdout.write(drain_line)
                                        sys.stdout.flush()
                                except queue.Empty:
                                    break
                            break

                if timed_out:
                    # Kill entire process group (handles shell=True properly)
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        process.kill()  # Fallback
                    if not self._quiet:
                        sys.stdout.write(f"\n[TIMEOUT after {spec.timeout}s]\n")
                        sys.stdout.flush()
                    if live_file:
                        live_file.write(f"\n[TIMEOUT after {spec.timeout}s]\n")
                    reader_thread.join(timeout=1.0)
                    # Drain remaining output after timeout
                    while True:
                        try:
                            timeout_line = output_queue.get_nowait()
                            if timeout_line is None:
                                break
                            output_lines.append(timeout_line)
                            if live_file:
                                live_file.write(timeout_line)
                        except queue.Empty:
                            break
                    exit_code = -1
                else:
                    exit_code = process.wait()
                    reader_thread.join(timeout=1.0)

        finally:
            if live_file:
                live_file.close()
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)
            # Ensure subprocess is terminated if still running
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

        completed_at = datetime.now()
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)

        return ExecutionResult(
            exit_code=exit_code,
            output="".join(output_lines),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            signal=sig,
            timeout=timed_out,
            pid=process_pid,
        )
