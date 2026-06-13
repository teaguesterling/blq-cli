"""Extension pipeline orchestration."""
from __future__ import annotations

import logging

from blq.ext import CommandSpec, ExecutionResult, Executor, Extension

logger = logging.getLogger("blq-ext")


def run_pipeline(
    spec: CommandSpec,
    extensions: list[Extension],
    executor: Executor,
) -> ExecutionResult:
    """Run the full extension pipeline.

    1. prepare() — forward order, only active extensions
    2. execute() — the executor runs the command
    3. collect() — reverse order of registered collectors
    """
    # 1. Prepare (forward order, only active extensions)
    for ext in extensions:
        if ext.config_key in spec.extension_data:
            spec = ext.prepare(spec)

    # 2. Execute
    result = executor.execute(spec)

    # 3. Collect (reverse order)
    for collector in reversed(spec.collectors):
        try:
            collector.collect(spec, result)
        except Exception as e:
            logger.warning(
                f"Collector {type(collector).__name__} failed: {e}"
            )

    # 4. Store (forward order) — deferred: extensions write their own artifacts
    # to BIRD storage. Not yet implemented; store() is a no-op on all extensions.
    # When implemented, pass an open BirdStore connection:
    # for ext in active_extensions:
    #     ext.store(spec, result, store)

    return result
