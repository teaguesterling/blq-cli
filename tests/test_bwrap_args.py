"""Tests for bwrap argument builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from blq_sandbox.spec import SandboxSpec
from blq_sandbox_bwrap.args import build_bwrap_args


WORKSPACE = Path("/tmp/blq-test-workspace/attempt-abc123")
ATTEMPT_ID = "attempt-abc123"


# =============================================================================
# Helpers
# =============================================================================


def _spec(**kwargs: object) -> SandboxSpec:
    """Build a SandboxSpec with given overrides (defaults are unrestricted)."""
    return SandboxSpec(**kwargs)  # type: ignore[arg-type]


def _args(**kwargs: object) -> list[str]:
    """Build bwrap args from a spec with given overrides."""
    return build_bwrap_args(_spec(**kwargs), WORKSPACE, ATTEMPT_ID)


# =============================================================================
# TestBaseArgs
# =============================================================================


class TestBaseArgs:
    def test_returns_list_of_strings(self) -> None:
        args = _args()
        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)

    def test_die_with_parent(self) -> None:
        args = _args()
        assert "--die-with-parent" in args

    def test_new_session(self) -> None:
        args = _args()
        assert "--new-session" in args

    def test_dev_mount(self) -> None:
        args = _args()
        assert "--dev" in args
        idx = args.index("--dev")
        assert args[idx + 1] == "/dev"

    def test_proc_mount(self) -> None:
        args = _args()
        assert "--proc" in args
        idx = args.index("--proc")
        assert args[idx + 1] == "/proc"


# =============================================================================
# TestNetworkIsolation
# =============================================================================


class TestNetworkIsolation:
    def test_none_adds_unshare_net(self) -> None:
        args = _args(network="none")
        assert "--unshare-net" in args

    def test_localhost_adds_unshare_net(self) -> None:
        args = _args(network="localhost")
        assert "--unshare-net" in args

    def test_unrestricted_no_unshare_net(self) -> None:
        args = _args(network="unrestricted")
        assert "--unshare-net" not in args

    def test_allowed_hosts_no_unshare_net(self) -> None:
        # allowed_hosts is not yet enforced at the bwrap level; treat like unrestricted
        args = _args(network="allowed_hosts")
        assert "--unshare-net" not in args


# =============================================================================
# TestFilesystemIsolation
# =============================================================================


class TestFilesystemIsolation:
    def test_readonly_uses_ro_bind_root(self) -> None:
        args = _args(filesystem="readonly")
        assert "--ro-bind" in args
        idx = args.index("--ro-bind")
        assert args[idx + 1] == "/"
        assert args[idx + 2] == "/"

    def test_readonly_no_writable_bind(self) -> None:
        args = _args(filesystem="readonly")
        assert "--bind" not in args

    def test_workspace_only_has_ro_bind_root(self) -> None:
        args = _args(filesystem="workspace_only")
        assert "--ro-bind" in args
        idx = args.index("--ro-bind")
        assert args[idx + 1] == "/"
        assert args[idx + 2] == "/"

    def test_workspace_only_adds_writable_workspace_bind(self) -> None:
        args = _args(filesystem="workspace_only")
        assert "--bind" in args
        idx = args.index("--bind")
        ws = str(WORKSPACE)
        assert args[idx + 1] == ws
        assert args[idx + 2] == ws

    def test_scoped_write_has_ro_bind_root(self) -> None:
        args = _args(filesystem="scoped_write")
        assert "--ro-bind" in args
        idx = args.index("--ro-bind")
        assert args[idx + 1] == "/"
        assert args[idx + 2] == "/"

    def test_scoped_write_adds_writable_workspace_bind(self) -> None:
        args = _args(filesystem="scoped_write")
        assert "--bind" in args
        idx = args.index("--bind")
        ws = str(WORKSPACE)
        assert args[idx + 1] == ws
        assert args[idx + 2] == ws

    def test_unrestricted_uses_bind_root(self) -> None:
        args = _args(filesystem="unrestricted")
        assert "--bind" in args
        idx = args.index("--bind")
        assert args[idx + 1] == "/"
        assert args[idx + 2] == "/"

    def test_unrestricted_no_ro_bind(self) -> None:
        args = _args(filesystem="unrestricted")
        assert "--ro-bind" not in args

    def test_ro_bind_comes_before_bind_for_workspace_only(self) -> None:
        """ro-bind / / must appear before --bind <workspace> to ensure correct layering."""
        args = _args(filesystem="workspace_only")
        ro_idx = args.index("--ro-bind")
        bind_idx = args.index("--bind")
        assert ro_idx < bind_idx


# =============================================================================
# TestPidIsolation
# =============================================================================


class TestPidIsolation:
    def test_isolated_adds_unshare_pid(self) -> None:
        args = _args(processes="isolated")
        assert "--unshare-pid" in args

    def test_visible_no_unshare_pid(self) -> None:
        args = _args(processes="visible")
        assert "--unshare-pid" not in args


# =============================================================================
# TestTmpfs
# =============================================================================


class TestTmpfs:
    def test_tmpfs_mounts_tmp_with_size(self) -> None:
        args = _args(tmpfs=256 * 1024 * 1024)  # 256m
        # expect: --size <bytes> --tmpfs /tmp
        assert "--size" in args
        size_idx = args.index("--size")
        assert args[size_idx + 1] == str(256 * 1024 * 1024)
        assert args[size_idx + 2] == "--tmpfs"
        assert args[size_idx + 3] == "/tmp"

    def test_no_tmpfs_when_not_specified(self) -> None:
        args = _args()
        assert "--tmpfs" not in args
        assert "--size" not in args

    def test_tmpfs_none_omitted(self) -> None:
        spec = SandboxSpec(tmpfs=None)
        args = build_bwrap_args(spec, WORKSPACE, ATTEMPT_ID)
        assert "--tmpfs" not in args


# =============================================================================
# TestPathsHidden
# =============================================================================


class TestPathsHidden:
    def test_hidden_path_gets_tmpfs_overlay(self) -> None:
        args = _args(paths_hidden=["/home/user/.ssh"])
        assert "--tmpfs" in args
        idx = args.index("--tmpfs")
        assert args[idx + 1] == "/home/user/.ssh"

    def test_multiple_hidden_paths(self) -> None:
        hidden = ["/home/user/.ssh", "/home/user/.gnupg"]
        args = _args(paths_hidden=hidden)
        # Find all --tmpfs occurrences
        tmpfs_paths = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
        for path in hidden:
            assert path in tmpfs_paths

    def test_no_hidden_paths_no_tmpfs(self) -> None:
        # Only check that no --tmpfs is present when neither tmpfs nor paths_hidden set
        args = _args()
        assert "--tmpfs" not in args


# =============================================================================
# TestChdir
# =============================================================================


class TestChdir:
    def test_chdir_to_workspace(self) -> None:
        args = _args()
        assert "--chdir" in args
        idx = args.index("--chdir")
        assert args[idx + 1] == str(WORKSPACE)

    def test_chdir_uses_path_object_workspace(self) -> None:
        ws = Path("/var/tmp/my-project/run-xyz")
        spec = SandboxSpec()
        args = build_bwrap_args(spec, ws, "run-xyz")
        idx = args.index("--chdir")
        assert args[idx + 1] == "/var/tmp/my-project/run-xyz"
