"""Sandbox spec tightening from observed resource metrics.

Given a current SandboxSpec and observed resource usage data, compute a
tighter spec that keeps the same non-resource dimensions (network, filesystem,
processes) while narrowing memory, cpu, and timeout bounds based on real data.

Only tightens (lowers) bounds — never loosens an existing limit.
"""

from __future__ import annotations

import copy

from blq_sandbox.spec import SandboxSpec

# Headroom multipliers
_MEMORY_HEADROOM = 2
_CPU_HEADROOM = 2
_TIMEOUT_HEADROOM = 3

# Minimums (to avoid impossible constraints)
_MIN_MEMORY_BYTES = 64 * 1024**2  # 64m
_MIN_CPU_SECONDS = 5              # 5s
_MIN_TIMEOUT_SECONDS = 10         # 10s


def compute_tighter_spec(current: SandboxSpec, observed: dict) -> SandboxSpec:
    """Compute a tighter sandbox spec from observed resource metrics.

    Rules:
    - Only tightens (lowers) bounds, never loosens.
    - Adds bounds for unlimited dimensions when data is available.
    - Non-resource dimensions (network, filesystem, processes) preserved unchanged.
    - Headroom: 2x memory, 2x CPU, 3x timeout.
    - Minimums: 64m memory, 5s CPU, 10s timeout.

    Args:
        current: The current SandboxSpec to tighten.
        observed: Dict with optional keys:
            - max_memory_bytes (int): Peak memory in bytes.
            - max_cpu_usec (int): Peak CPU usage in microseconds.
            - max_duration_ms (int): Max wall-clock duration in milliseconds.

    Returns:
        A new SandboxSpec that is equal to or tighter than current.
    """
    tighter = copy.copy(current)

    # --- memory ---
    max_memory_bytes = observed.get("max_memory_bytes")
    if max_memory_bytes is not None:
        suggested_memory = max(
            int(max_memory_bytes * _MEMORY_HEADROOM),
            _MIN_MEMORY_BYTES,
        )
        if current.memory is None:
            # No limit set yet — add one
            tighter.memory = suggested_memory
        elif suggested_memory < current.memory:
            # Suggested is tighter — use it
            tighter.memory = suggested_memory
        # else: suggested >= current → keep current (don't loosen)

    # --- cpu ---
    max_cpu_usec = observed.get("max_cpu_usec")
    if max_cpu_usec is not None:
        suggested_cpu = max(
            int(max_cpu_usec / 1_000_000 * _CPU_HEADROOM),
            _MIN_CPU_SECONDS,
        )
        if current.cpu is None:
            tighter.cpu = suggested_cpu
        elif suggested_cpu < current.cpu:
            tighter.cpu = suggested_cpu
        # else: keep current

    # --- timeout ---
    max_duration_ms = observed.get("max_duration_ms")
    if max_duration_ms is not None:
        suggested_timeout = max(
            int(max_duration_ms / 1000 * _TIMEOUT_HEADROOM),
            _MIN_TIMEOUT_SECONDS,
        )
        if current.timeout is None:
            tighter.timeout = suggested_timeout
        elif suggested_timeout < current.timeout:
            tighter.timeout = suggested_timeout
        # else: keep current

    return tighter
