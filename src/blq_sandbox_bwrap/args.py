"""Bwrap argument builder.

Pure function: translates a SandboxSpec into a list of bwrap CLI arguments.
No side effects, no subprocess calls, fully testable without bwrap installed.
"""

from __future__ import annotations

from pathlib import Path

from blq_sandbox.spec import SandboxSpec


def build_bwrap_args(spec: SandboxSpec, workspace: Path, attempt_id: str) -> list[str]:
    """Translate a SandboxSpec into a list of bwrap CLI arguments.

    Args:
        spec:        The sandbox specification declaring execution bounds.
        workspace:   The writable workspace directory for this attempt.
        attempt_id:  The attempt identifier (unused in args, reserved for future use).

    Returns:
        A list of string arguments suitable for passing to bwrap.
    """
    args: list[str] = []

    # --- Safety flags (always present) ---
    args += ["--die-with-parent", "--new-session"]

    # --- Filesystem isolation ---
    if spec.filesystem == "unrestricted":
        # Full writable root
        args += ["--bind", "/", "/"]
    else:
        # Read-only root first, then optionally overlay writable workspace
        args += ["--ro-bind", "/", "/"]
        if spec.filesystem in ("workspace_only", "scoped_write"):
            ws = str(workspace)
            args += ["--bind", ws, ws]

    # --- Virtual filesystems (always present) ---
    args += ["--dev", "/dev"]
    args += ["--proc", "/proc"]

    # --- Network isolation ---
    if spec.network in ("none", "localhost"):
        args += ["--unshare-net"]

    # --- PID namespace isolation ---
    if spec.processes == "isolated":
        args += ["--unshare-pid"]

    # --- Tmpfs on /tmp ---
    if spec.tmpfs is not None:
        args += ["--size", str(spec.tmpfs), "--tmpfs", "/tmp"]

    # --- Hidden paths (tmpfs overlay hides content) ---
    for path in spec.paths_hidden:
        args += ["--tmpfs", path]

    # --- Working directory ---
    args += ["--chdir", str(workspace)]

    return args
