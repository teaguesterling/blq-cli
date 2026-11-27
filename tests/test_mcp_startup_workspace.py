"""An agent's first exec must work, reads must not write, and nothing hangs forever."""

import subprocess

import pytest

from blq import serve
from blq.user_config import UserConfig


def _git(path, *args):
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


@pytest.fixture
def short_default(monkeypatch):
    original = UserConfig.load

    def load(*args, **kwargs):
        config = original(*args, **kwargs)
        config.mcp_timeout = 1
        return config

    monkeypatch.setattr(UserConfig, "load", load)


def test_exec_in_fresh_repo_creates_self_ignoring_workspace_at_toplevel(repo):
    sub = repo / "pkg"
    sub.mkdir()

    result = serve._exec_impl("echo hello", root=str(sub))

    assert result["status"] == "OK", result
    assert result["workspace_created"] is True
    assert result["run_ref"]
    assert (repo / ".bird" / ".gitignore").read_text() == "*\n"
    assert not (sub / ".bird").exists()
    assert _git(repo, "status", "--porcelain", "--untracked-files=all") == ""


@pytest.mark.parametrize("call", ["exec", "run"])
def test_outside_git_is_a_structured_no_workspace(tmp_path, call):
    impl = serve._exec_impl if call == "exec" else serve._run_impl

    result = impl("echo hello", root=str(tmp_path))

    assert result["error"] == "no_workspace", result
    assert "cwd=" in result["hint"]
    assert not (tmp_path / ".bird").exists()


def test_unknown_run_in_new_workspace_names_exec(repo):
    result = serve._run_impl("nope", root=str(repo))

    assert "exec" in result["error"], result
    assert "run tool with exec mode" not in result["error"]


def test_register_creates_the_workspace(repo):
    result = serve._register_command_impl("hi", "echo hi", root=str(repo))

    assert result["success"], result
    assert (repo / ".bird" / ".gitignore").read_text() == "*\n"


def test_read_leaves_legacy_lq_in_place(tmp_path):
    (tmp_path / ".lq").mkdir()

    config = serve._find_config(root=str(tmp_path))

    assert config is not None and config.lq_dir == tmp_path / ".lq"
    assert not (tmp_path / ".bird").exists()


def test_write_never_creates_bird_beside_lq(repo):
    (repo / ".lq").mkdir()

    serve._exec_impl("echo hello", root=str(repo))

    assert not ((repo / ".bird").exists() and (repo / ".lq").exists())


@pytest.mark.parametrize("call", ["exec", "run"])
def test_default_timeout_returns_structured_result(repo, short_default, call):
    (repo / ".bird").mkdir()
    (repo / ".bird" / "commands.toml").write_text('[commands.slow]\ncmd = "sleep 5"\n')

    if call == "exec":
        result = serve._exec_impl("sleep 6", root=str(repo))
    else:
        result = serve._run_impl("slow", root=str(repo))

    assert result["status"] == "TIMEOUT", result


def test_command_timeout_beats_the_default(repo, short_default):
    (repo / ".bird").mkdir()
    (repo / ".bird" / "commands.toml").write_text(
        '[commands.slow]\ncmd = "sleep 2"\ntimeout = 30\n'
    )

    assert serve._run_impl("slow", root=str(repo))["status"] == "OK"
