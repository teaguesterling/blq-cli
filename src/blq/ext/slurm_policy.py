"""Routing policy: decide whether a command runs through Slurm or locally.

Pure decision logic. The Slurm executor that acts on the decision lives elsewhere.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from blq.config_format import load_toml

logger = logging.getLogger("blq-ext")

DEFAULT_POLICY_PATH = Path("~/.config/blq/slurm.toml")
_OFF_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class SlurmResources:
    partition: str
    cpus: int
    mem: str


@dataclass(frozen=True)
class SlurmPolicy:
    roots: tuple[Path, ...]
    defaults: SlurmResources
    local_memory_max: str | None


@dataclass(frozen=True)
class Route:
    kind: Literal["slurm", "local"]
    reason: str
    resources: SlurmResources | None = None
    warn: bool = False


def load_policy(path: Path | None = None) -> SlurmPolicy | None:
    """Load the policy file. Return None if absent, disabled, or invalid."""
    path = (path or DEFAULT_POLICY_PATH).expanduser()
    if not path.exists():
        return None
    try:
        data = load_toml(path)
        if data.get("enabled", True) is False:
            return None
        roots = data.get("roots", [])
        if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
            raise ValueError("roots must be a list of strings")
        partition = data.get("partition", "batch")
        cpus = data.get("cpus", 4)
        mem = data.get("mem", "8G")
        local_max = data.get("local_memory_max")
        if not isinstance(partition, str) or not isinstance(mem, str):
            raise ValueError("partition and mem must be strings")
        if isinstance(cpus, bool) or not isinstance(cpus, int):
            raise ValueError("cpus must be an integer")
        if local_max is not None and not isinstance(local_max, str):
            raise ValueError("local_memory_max must be a string")
    except Exception as e:  # bad TOML or bad values: warn and fall back to local
        logger.warning("Ignoring Slurm policy %s: %s", path, e)
        return None
    return SlurmPolicy(
        roots=tuple(Path(r).expanduser().resolve() for r in roots),
        defaults=SlurmResources(partition, cpus, mem),
        local_memory_max=local_max,
    )


def queue_problem(timeout: float = 5.0) -> str | None:
    """Return None if `scontrol ping` reports the controller is up, else the reason."""
    try:
        proc = subprocess.run(["scontrol", "ping"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "scontrol not found"
    except subprocess.TimeoutExpired:
        return f"scontrol ping timed out after {timeout:g}s"
    except OSError as e:
        return f"scontrol could not run: {e}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"scontrol ping failed (exit {proc.returncode}): {detail}".rstrip(": ")
    if "is UP" not in proc.stdout:
        return f"controller not up: {proc.stdout.strip() or 'no output'}"
    return None


def _inside(cwd: Path, roots: tuple[Path, ...]) -> bool:
    resolved = cwd.resolve()
    return any(resolved.is_relative_to(root) for root in roots)


def decide_route(
    policy: SlurmPolicy | None,
    cwd: Path,
    command_override: bool | dict[str, Any] | None,
    env: Mapping[str, str],
    ping: Callable[[], str | None] = queue_problem,
) -> Route:
    """Decide where a command runs. `ping` is called last, only if all else says Slurm."""
    if policy is None:
        return Route("local", "no Slurm policy")
    if command_override is False:
        return Route("local", "command opted out of Slurm")
    if env.get("BLQ_SLURM", "").strip().lower() in _OFF_VALUES:
        return Route("local", "BLQ_SLURM disables Slurm")
    if env.get("SLURM_JOB_ID"):
        return Route("local", "already inside a Slurm job")
    if not _inside(cwd, policy.roots):
        return Route("local", "directory is outside the Slurm roots")
    problem = ping()
    if problem:
        return Route("local", f"Slurm queue unavailable: {problem}", warn=True)
    resources = policy.defaults
    if isinstance(command_override, dict):
        fields = {k: v for k, v in command_override.items() if k in ("partition", "cpus", "mem")}
        resources = replace(resources, **fields)
    return Route("slurm", "directory is inside the Slurm roots", resources)
