"""blq-sandbox-bwrap: bubblewrap enforcement engine for sandbox specs."""

from __future__ import annotations

from pathlib import Path

from blq.ext import Collector
from blq_sandbox.spec import SandboxSpec
from blq_sandbox_bwrap.args import build_bwrap_args


class BwrapEngine:
    """Bubblewrap (bwrap) sandbox enforcement engine.

    Translates SandboxSpec dimensions into bwrap namespace isolation:
    - network: --unshare-net
    - filesystem: --ro-bind / --bind mount strategy
    - processes: --unshare-pid
    - tmpfs: --tmpfs with --size
    - paths_hidden: --tmpfs overlays

    Does NOT handle: memory, cpu (those are cgroup limits — use systemd engine).
    """

    name = "bwrap"
    capabilities = {"network", "filesystem", "processes", "tmpfs", "paths_hidden", "paths_readable"}

    def wrap(self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str) -> str:
        args = build_bwrap_args(spec, workspace, attempt_id)
        parts = ["bwrap"] + args + ["--", command]
        return " ".join(parts)

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        return None
