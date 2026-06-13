"""Tests for sandbox spec tightening."""

from __future__ import annotations

from blq_sandbox.spec import SandboxSpec
from blq_sandbox.tighten import compute_tighter_spec


class TestComputeTighterSpec:
    def test_tightens_memory_from_observed(self):
        current = SandboxSpec(network="none", filesystem="readonly", memory=1024**3)
        observed = {"max_memory_bytes": 200 * 1024**2}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.memory < current.memory
        assert tighter.memory >= 200 * 1024**2 * 2

    def test_does_not_loosen_memory(self):
        current = SandboxSpec(network="none", filesystem="readonly", memory=100 * 1024**2)
        observed = {"max_memory_bytes": 200 * 1024**2}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.memory == current.memory

    def test_tightens_timeout(self):
        current = SandboxSpec(network="none", filesystem="readonly", timeout=300)
        observed = {"max_duration_ms": 10000}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.timeout < current.timeout
        assert tighter.timeout >= 30

    def test_tightens_cpu(self):
        current = SandboxSpec(network="none", filesystem="readonly", cpu=120)
        observed = {"max_cpu_usec": 5_000_000}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.cpu < current.cpu
        assert tighter.cpu >= 10

    def test_preserves_non_resource_dimensions(self):
        current = SandboxSpec(network="none", filesystem="workspace_only", processes="isolated")
        observed = {}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.network == "none"
        assert tighter.filesystem == "workspace_only"
        assert tighter.processes == "isolated"

    def test_no_change_when_no_data(self):
        current = SandboxSpec(network="none", filesystem="readonly")
        tighter = compute_tighter_spec(current, {})
        assert tighter == current

    def test_adds_memory_when_missing(self):
        current = SandboxSpec(network="none", filesystem="readonly")
        observed = {"max_memory_bytes": 100 * 1024**2}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.memory is not None

    def test_respects_minimum_memory(self):
        current = SandboxSpec(network="none", filesystem="readonly", memory=1024**3)
        observed = {"max_memory_bytes": 1024}  # tiny
        tighter = compute_tighter_spec(current, observed)
        assert tighter.memory >= 64 * 1024**2

    def test_respects_minimum_timeout(self):
        current = SandboxSpec(network="none", filesystem="readonly", timeout=300)
        observed = {"max_duration_ms": 100}  # 0.1s
        tighter = compute_tighter_spec(current, observed)
        assert tighter.timeout >= 10

    def test_respects_minimum_cpu(self):
        current = SandboxSpec(network="none", filesystem="readonly", cpu=120)
        observed = {"max_cpu_usec": 100_000}  # 0.1s
        tighter = compute_tighter_spec(current, observed)
        assert tighter.cpu >= 5
