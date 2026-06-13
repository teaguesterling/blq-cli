"""Sandbox engine protocol and discovery."""
from __future__ import annotations

import logging
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

from blq.ext import Collector
from blq_sandbox.spec import SandboxSpec

logger = logging.getLogger("blq-sandbox")


class SandboxEngine(Protocol):
    """A sandbox enforcement backend."""

    name: str
    capabilities: set[str]

    def wrap(
        self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str
    ) -> str: ...

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None: ...


class LogEngine:
    """Declaration-only engine. No enforcement, just logging."""

    name = "log"
    capabilities: set[str] = set()

    def wrap(
        self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str
    ) -> str:
        return command

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        return None


def load_engines() -> dict[str, SandboxEngine]:
    """Discover installed sandbox engines via entry points."""
    engines: dict[str, SandboxEngine] = {"log": LogEngine()}
    for ep in entry_points(group="blq.sandbox.engines"):
        try:
            engine_factory = ep.load()
            engine = engine_factory()
            engines[engine.name] = engine
        except Exception as e:
            logger.warning(f"Failed to load sandbox engine {ep.name}: {e}")
    return engines


def select_engines(
    spec: SandboxSpec,
    available: dict[str, SandboxEngine],
    preferred: list[str] | None = None,
) -> list[SandboxEngine]:
    """Select engines to cover the spec's non-default dimensions."""
    needed = spec.active_dimensions()
    if not needed:
        return [available["log"]]

    candidates = available
    if preferred:
        candidates = {k: v for k, v in available.items() if k in preferred}
        if not candidates:
            logger.warning(
                f"No preferred engines ({preferred}) are installed. "
                f"Falling back to all available engines."
            )
            candidates = available

    selected: list[SandboxEngine] = []
    covered: set[str] = set()
    for name, engine in candidates.items():
        if name == "log":
            continue
        relevant = engine.capabilities & needed
        if relevant - covered:
            selected.append(engine)
            covered |= relevant

    uncovered = needed - covered
    if uncovered:
        logger.warning(
            f"Sandbox dimensions not enforced (no capable engine installed): "
            f"{', '.join(sorted(uncovered))}"
        )

    return selected if selected else [available["log"]]
