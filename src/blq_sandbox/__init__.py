"""blq-sandbox: Sandbox specification extension for blq."""

from __future__ import annotations

import logging
from typing import Any

from blq.ext import CommandSpec, ExecutionResult
from blq_sandbox.engines import load_engines, select_engines
from blq_sandbox.spec import SandboxSpec, resolve_sandbox

__all__ = ["SandboxExtension", "SandboxSpec", "resolve_sandbox"]

logger = logging.getLogger("blq-sandbox")


class SandboxExtension:
    """Sandbox extension — declares and enforces execution bounds."""

    name = "sandbox"
    config_key = "sandbox"

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        config = spec.extension_data.get("sandbox")
        if not config:
            return spec

        # Resolve spec from config (preset string or dict)
        sandbox_spec = resolve_sandbox(config)
        if sandbox_spec is None:
            return spec

        # Store parsed spec for later use (overwrite raw config with resolved dict)
        spec.extension_data["sandbox"] = sandbox_spec.to_dict()
        spec.extension_data["sandbox_grade_w"] = sandbox_spec.grade_w
        spec.extension_data["sandbox_effects_ceiling"] = sandbox_spec.effects_ceiling

        # Bridge sandbox timeout to CommandSpec if not already set
        if sandbox_spec.timeout is not None and spec.timeout is None:
            spec.timeout = sandbox_spec.timeout

        # Load and select engines
        engines = load_engines()
        preferred = config.get("engines") if isinstance(config, dict) else None
        selected = select_engines(sandbox_spec, engines, preferred)

        # Wrap command through each engine
        for engine in selected:
            spec.command = engine.wrap(spec.command, sandbox_spec, spec.workspace, spec.attempt_id)
            collector = engine.collector(sandbox_spec, spec.attempt_id)
            if collector is not None:
                spec.collectors.append(collector)

        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        try:
            resolve_sandbox(config)
        except (ValueError, TypeError) as e:
            warnings.append(str(e))
        return warnings

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass  # Stubbed this round
