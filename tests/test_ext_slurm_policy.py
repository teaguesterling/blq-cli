"""Tests for the Slurm routing policy."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

import pytest

from blq.ext.slurm_policy import (
    Route,
    SlurmPolicy,
    SlurmResources,
    decide_route,
    load_policy,
    queue_problem,
)

DEFAULTS = SlurmResources("batch", 4, "8G")


def up() -> str | None:
    return None


def boom() -> str | None:
    raise AssertionError("ping must not be called")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "work"
    (r / "proj").mkdir(parents=True)
    return r


@pytest.fixture
def policy(root: Path) -> SlurmPolicy:
    return SlurmPolicy(roots=(root.resolve(),), defaults=DEFAULTS, local_memory_max=None)


def test_no_config_means_local_without_warning(tmp_path):
    route = decide_route(None, tmp_path, None, {}, ping=boom)
    assert (route.kind, route.warn) == ("local", False)


def test_command_can_opt_out(policy, root):
    route = decide_route(policy, root / "proj", False, {}, ping=up)
    assert route.kind == "local"


def test_env_can_opt_out(policy, root):
    route = decide_route(policy, root / "proj", None, {"BLQ_SLURM": "0"}, ping=up)
    assert route.kind == "local"


def test_inside_a_job_stays_local(policy, root):
    route = decide_route(policy, root / "proj", None, {"SLURM_JOB_ID": "5"}, ping=boom)
    assert route.kind == "local" and not route.warn


def test_tree_outside_roots_stays_local(policy, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    route = decide_route(policy, outside, None, {}, ping=up)
    assert route.kind == "local" and not route.warn


def test_inside_root_goes_to_slurm(policy, root):
    route = decide_route(policy, root / "proj", None, {}, ping=up)
    assert route.kind == "slurm" and not route.warn
    assert route.resources == DEFAULTS


def test_symlink_into_root_counts_as_inside(policy, root, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(root / "proj")
    route = decide_route(policy, link, None, {}, ping=up)
    assert route.kind == "slurm"


def test_sibling_prefix_is_not_inside(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "bc").mkdir()
    pol = SlurmPolicy(roots=((tmp_path / "b").resolve(),), defaults=DEFAULTS, local_memory_max=None)
    route = decide_route(pol, tmp_path / "bc", None, {}, ping=up)
    assert route.kind == "local"


def test_queue_down_warns_and_stays_local(policy, root):
    route = decide_route(policy, root / "proj", None, {}, ping=lambda: "no controller")
    assert route.kind == "local" and route.warn and "no controller" in route.reason


def test_outside_roots_never_pings(policy, tmp_path):
    decide_route(policy, tmp_path, None, {}, ping=boom)


def test_command_table_overrides_defaults(policy, root):
    route = decide_route(policy, root / "proj", {"cpus": 8, "mem": "16G"}, {}, ping=up)
    assert route.resources == SlurmResources("batch", 8, "16G")


def test_command_true_uses_defaults(policy, root):
    route = decide_route(policy, root / "proj", True, {}, ping=up)
    assert route.kind == "slurm" and route.resources == DEFAULTS


def test_route_is_a_dataclass_with_reason(policy, root):
    route = decide_route(policy, root / "proj", None, {}, ping=up)
    assert isinstance(route, Route) and isinstance(route.reason, str) and route.reason


# --- load_policy ---


def test_missing_file_returns_none(tmp_path):
    assert load_policy(tmp_path / "nope.toml") is None


def test_disabled_file_returns_none(tmp_path):
    f = tmp_path / "slurm.toml"
    f.write_text('enabled = false\nroots = ["/x"]\n')
    assert load_policy(f) is None


def test_bad_file_warns_and_returns_none(tmp_path, caplog):
    f = tmp_path / "slurm.toml"
    f.write_text("this is = = not toml [")
    with caplog.at_level(logging.WARNING):
        assert load_policy(f) is None
    assert any("slurm" in r.message.lower() for r in caplog.records)


def test_file_without_enabled_key_is_enabled(tmp_path):
    f = tmp_path / "slurm.toml"
    f.write_text(f'roots = ["{tmp_path}"]\n')
    pol = load_policy(f)
    assert pol is not None
    assert pol.roots == (tmp_path.resolve(),)
    assert pol.defaults == SlurmResources("batch", 4, "8G")
    assert pol.local_memory_max is None


def test_file_values_are_read(tmp_path):
    f = tmp_path / "slurm.toml"
    f.write_text(
        f'roots = ["{tmp_path}"]\npartition = "ci"\ncpus = 2\nmem = "1G"\nlocal_memory_max = "2G"\n'
    )
    pol = load_policy(f)
    assert pol.defaults == SlurmResources("ci", 2, "1G")
    assert pol.local_memory_max == "2G"


def test_tilde_in_roots_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "slurm.toml"
    f.write_text('roots = ["~/code"]\n')
    pol = load_policy(f)
    assert pol.roots == ((tmp_path / "code").resolve(),)


def test_wrong_type_warns_and_returns_none(tmp_path, caplog):
    f = tmp_path / "slurm.toml"
    f.write_text('roots = "notalist"\ncpus = "many"\n')
    with caplog.at_level(logging.WARNING):
        assert load_policy(f) is None
    assert caplog.records


# --- queue_problem ---


def _fake_scontrol(tmp_path: Path, monkeypatch, body: str) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "scontrol"
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))


def test_queue_problem_none_when_up(tmp_path, monkeypatch):
    _fake_scontrol(tmp_path, monkeypatch, "echo 'Slurmctld(primary) at host is UP'")
    assert queue_problem() is None


def test_queue_problem_missing_scontrol(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    reason = queue_problem()
    assert reason and "scontrol" in reason


def test_queue_problem_timeout(tmp_path, monkeypatch):
    _fake_scontrol(tmp_path, monkeypatch, "exec sleep 5")
    reason = queue_problem(timeout=0.3)
    assert reason and "time" in reason.lower()


def test_queue_problem_nonzero_exit(tmp_path, monkeypatch):
    _fake_scontrol(tmp_path, monkeypatch, "echo 'is UP'; exit 1")
    assert queue_problem()


def test_queue_problem_output_lacks_is_up(tmp_path, monkeypatch):
    _fake_scontrol(tmp_path, monkeypatch, "echo 'Slurmctld(primary) at host is DOWN'")
    assert queue_problem()


def test_queue_problem_unexecutable_scontrol_never_raises(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "scontrol").write_text("#!/nonexistent/interp\n")
    (bindir / "scontrol").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert queue_problem()
