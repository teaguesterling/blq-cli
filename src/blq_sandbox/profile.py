"""Run a command under strace and return a parsed StraceProfile."""
from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from blq_sandbox.strace_parser import StraceProfile, parse_strace_output

logger = logging.getLogger("blq-sandbox")

# Directories whose contents are considered "system" and excluded from
# paths_readable in the suggested spec.
_SYSTEM_DIRS = frozenset([
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc",
    "/proc",
    "/dev",
    "/sys",
])


def run_profile(
    command: str,
    workspace: Path,
    timeout: int = 300,
) -> StraceProfile | None:
    """Run *command* under strace and return the parsed StraceProfile.

    Parameters
    ----------
    command:
        Shell command string to execute.
    workspace:
        The project workspace directory (used for context; not modified here).
    timeout:
        Maximum seconds to wait for the command.  On timeout we attempt to
        parse whatever strace managed to write before terminating.

    Returns
    -------
    StraceProfile on success, or None if strace is not available or if an
    unexpected error occurs.
    """
    if not shutil.which("strace"):
        logger.warning("strace not found on PATH; cannot profile command")
        return None

    with tempfile.NamedTemporaryFile(
        suffix=".strace", delete=False, mode="w"
    ) as tmp:
        strace_output_path = tmp.name

    try:
        # Split the command string so the target binary is directly execve'd
        # (rather than wrapped in sh -c), giving strace a cleaner executable
        # trace.  shlex.split handles quoted arguments correctly.
        try:
            command_argv = shlex.split(command)
        except ValueError:
            command_argv = ["sh", "-c", command]

        strace_cmd = [
            "strace",
            "-f",
            "-e", "trace=%file,%network,%process",
            "-o", strace_output_path,
            "--",
            *command_argv,
        ]
        try:
            subprocess.run(
                strace_cmd,
                timeout=timeout,
                check=False,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "strace timed out after %d seconds profiling %r; "
                "parsing partial output",
                timeout,
                command,
            )
        # Read whatever strace wrote (may be partial on timeout).
        try:
            output = Path(strace_output_path).read_text(errors="replace")
        except OSError as exc:
            logger.warning("Could not read strace output file: %s", exc)
            return None

        return parse_strace_output(output)

    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected error while profiling command %r: %s", command, exc)
        return None
    finally:
        try:
            Path(strace_output_path).unlink(missing_ok=True)
        except OSError:
            pass


def suggest_spec_from_profile(
    profile: StraceProfile,
    workspace: Path,
) -> dict[str, object]:
    """Derive a sandbox spec dict from a StraceProfile.

    Parameters
    ----------
    profile:
        The profile returned by :func:`run_profile`.
    workspace:
        The project workspace directory.  Writes within this directory (or
        ``/tmp``) are considered acceptable for the ``workspace_only``
        classification.

    Returns
    -------
    A ``dict`` suitable for TOML serialization with keys:

    * ``network`` – ``"none"`` or ``"unrestricted"``
    * ``filesystem`` – ``"readonly"``, ``"workspace_only"``, or
      ``"unrestricted"``
    * ``processes`` – ``"isolated"`` or ``"visible"``
    * ``paths_readable`` – list of non-system directories read by the command
    """
    # ------------------------------------------------------------------
    # network
    # ------------------------------------------------------------------
    network = "none" if not profile.has_network else "unrestricted"

    # ------------------------------------------------------------------
    # filesystem
    # ------------------------------------------------------------------
    if not profile.has_writes:
        filesystem = "readonly"
    else:
        workspace_str = str(workspace.resolve())
        all_writes_local = all(
            p.startswith(workspace_str) or p.startswith("/tmp")
            for p in profile.files_written
        )
        filesystem = "workspace_only" if all_writes_local else "unrestricted"

    # ------------------------------------------------------------------
    # processes
    # ------------------------------------------------------------------
    processes = "isolated" if not profile.has_spawns else "visible"

    # ------------------------------------------------------------------
    # paths_readable  (non-system read directories, excluding workspace)
    # ------------------------------------------------------------------
    workspace_str = str(workspace.resolve())

    def _is_system(path: str) -> bool:
        for sdir in _SYSTEM_DIRS:
            if path == sdir or path.startswith(sdir + "/"):
                return True
        return False

    paths_readable: list[str] = sorted(
        {
            d
            for d in profile.read_directories()
            if d
            and not _is_system(d)
            and d != workspace_str
            and not d.startswith(workspace_str + "/")
        }
    )

    return {
        "network": network,
        "filesystem": filesystem,
        "processes": processes,
        "paths_readable": paths_readable,
    }
