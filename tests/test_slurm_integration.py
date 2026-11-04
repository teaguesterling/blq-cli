"""End-to-end tests: `blq run` and `blq exec` take the Slurm route when configured.

The tests drive the real CLI in a subprocess, with a real project, a temp HOME that
holds the policy file, and fake `srun` and `scontrol` on PATH. The fakes write a log,
so a test can prove that a tool was called, or was never called.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

import duckdb
import pytest

from blq.commands.core import _load_commands_impl, _save_commands_impl

# The status line is what srun 23.11 printed when measured on a real cluster.
FAKE_SRUN = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG/srun.log"
for a in "$@"; do
  case "$a" in --chdir=*) cd "${a#--chdir=}";; esac
done
while [ "$1" != "/bin/sh" ]; do shift; done
if [ -n "$FAKE_SRUN_STATUS_AFTER" ]; then
  "$@" | awk -v n="$FAKE_SRUN_STATUS_AFTER" \
    '{print} NR==n{print "srun: error: fakenode: task 0: Exited with exit code 1"}'
  exit "${PIPESTATUS[0]}"
fi
"$@"
rc=$?
if [ "$rc" -ne 0 ] && [ -z "$FAKE_SRUN_NO_STATUS" ]; then
  echo "srun: error: fakenode: task 0: Exited with exit code $rc" >&2
fi
exit "$rc"
"""
FAKE_SCONTROL = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG/scontrol.log"
if [ -n "$FAKE_SCONTROL_DOWN" ]; then
  echo "slurm_ping error: Unable to contact slurm controller" >&2
  exit 1
fi
echo "Slurmctld(primary) at fakenode is UP"
"""
FAKE_SCANCEL = '#!/bin/bash\necho "$*" >> "$FAKE_LOG/scancel.log"\n'

PYTEST_FAILURE_LOG = """\
============================= test session starts ==============================
platform linux -- Python 3.12.0, pytest-8.0.0, pluggy-1.4.0
rootdir: /tmp/x
collected 2 items

tests/test_a.py .F                                                       [100%]

=================================== FAILURES ===================================
__________________________________ test_boom ___________________________________

    def test_boom():
>       assert 1 == 2
E       assert 1 == 2

tests/test_a.py:5: AssertionError
=========================== short test summary info ============================
FAILED tests/test_a.py::test_boom - assert 1 == 2
========================= 1 failed, 1 passed in 0.03s ==========================
"""


def _script(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _duck_hunt_loads() -> bool:
    try:
        duckdb.connect(":memory:").execute("LOAD duck_hunt")
    except duckdb.Error:
        return False
    return True


class Env:
    """A blq project, a temp HOME, and the logs of the fake Slurm tools."""

    def __init__(self, project: Path, home: Path, log: Path):
        self.project = project
        self.home = home
        self.log = log

    def calls(self, tool: str) -> list[str]:
        f = self.log / f"{tool}.log"
        return f.read_text().splitlines() if f.exists() else []

    def write_policy(self, text: str | None = None) -> None:
        cfg = self.home / ".config" / "blq"
        cfg.mkdir(parents=True, exist_ok=True)
        if text is None:
            text = f'roots = ["{self.project}"]\npartition = "ci"\ncpus = 2\nmem = "1G"\n'
        (cfg / "slurm.toml").write_text(text)

    def write_commands(self, text: str) -> None:
        (self.project / ".bird" / "commands.toml").write_text(text)

    def blq(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "blq", *args],
            cwd=cwd or self.project,
            capture_output=True,
            text=True,
            timeout=120,
        )


@pytest.fixture
def env(initialized_project, tmp_path, monkeypatch) -> Env:
    # duck_hunt loads from under HOME, so the temp HOME keeps the real extensions.
    # Without them every parse returns no events, and event tests pass on nothing.
    real_duckdb = Path(os.path.expanduser("~/.duckdb"))
    home = tmp_path / "home"
    home.mkdir()
    if real_duckdb.exists():
        (home / ".duckdb").symlink_to(real_duckdb)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    log.mkdir()
    _script(bindir / "srun", FAKE_SRUN)
    _script(bindir / "scontrol", FAKE_SCONTROL)
    _script(bindir / "scancel", FAKE_SCANCEL)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_LOG", str(log))
    # The suite itself runs inside a Slurm job, and that alone keeps the route local.
    for var in (
        "SLURM_JOB_ID",
        "BLQ_SLURM",
        "FAKE_SCONTROL_DOWN",
        "FAKE_SRUN_NO_STATUS",
        "FAKE_SRUN_STATUS_AFTER",
    ):
        monkeypatch.delenv(var, raising=False)
    # The MCP tools and the autouse fixture resolve the root from here.
    monkeypatch.setenv("BLQ_ACTIVE_ROOT", str(initialized_project))
    return Env(Path(initialized_project), home, log)


HELLO = '[commands.hello]\ncmd = "echo hello-marker"\n'


class TestNoConfigChangesNothing:
    """With no policy file, blq must not look at Slurm at all."""

    def _assert_untouched(self, env: Env, proc: subprocess.CompletedProcess[str]) -> None:
        assert proc.returncode == 0, proc.stderr
        assert "hello-marker" in proc.stdout  # positive evidence that the command ran
        assert env.calls("srun") == []
        assert env.calls("scontrol") == []
        assert "Slurm" not in proc.stderr

    def test_no_config_never_calls_srun(self, env):
        env.write_commands(HELLO)
        self._assert_untouched(env, env.blq("run", "hello"))

    def test_exec_never_calls_srun(self, env):
        self._assert_untouched(env, env.blq("exec", "echo hello-marker"))

    def test_no_capture_never_calls_srun(self, env):
        env.write_commands(HELLO)
        self._assert_untouched(env, env.blq("run", "--no-capture", "hello"))

    def test_disabled_policy_never_calls_srun(self, env):
        env.write_policy(f'enabled = false\nroots = ["{env.project}"]\n')
        env.write_commands(HELLO)
        self._assert_untouched(env, env.blq("run", "hello"))


class TestRoutedThroughSrun:
    def test_run_inside_root_goes_through_srun_and_is_captured(self, env):
        env.write_policy()
        env.write_commands(HELLO)
        proc = env.blq("run", "--keep-raw", "hello")
        assert proc.returncode == 0, proc.stderr
        assert "hello-marker" in proc.stdout  # streamed live

        calls = env.calls("srun")
        assert len(calls) == 1
        assert "-c 2" in calls[0] and "--mem=1G" in calls[0] and "-p ci" in calls[0]
        assert f"--chdir={env.project}" in calls[0]

        # The run is queryable, under the command the user asked for, not the wrapper.
        history = env.blq("sql", "SELECT command, source_name, exit_code FROM blq_load_runs()")
        assert "hello" in history.stdout, history.stderr
        assert "echo hello-marker" in history.stdout
        assert "srun" not in history.stdout

        output = env.blq("output", "hello")
        assert "hello-marker" in output.stdout, output.stderr

    def test_exec_inside_root_goes_through_srun(self, env):
        env.write_policy()
        proc = env.blq("exec", "echo hello-marker")
        assert proc.returncode == 0, proc.stderr
        assert "hello-marker" in proc.stdout
        assert len(env.calls("srun")) == 1

    def test_exit_code_comes_through_srun(self, env):
        env.write_policy()
        assert env.blq("exec", "exit 3").returncode == 3
        assert len(env.calls("srun")) == 1

    def test_mcp_tools_get_the_route(self, env):
        from blq.serve import _exec_impl, _run_impl

        env.write_policy()
        env.write_commands(HELLO)
        _run_impl("hello", root=str(env.project))
        assert len(env.calls("srun")) == 1
        _exec_impl("echo other-marker", root=str(env.project))
        assert len(env.calls("srun")) == 2


class TestRouteStaysLocal:
    def _assert_local(self, env: Env, proc: subprocess.CompletedProcess[str]) -> None:
        assert proc.returncode == 0, proc.stderr
        assert "hello-marker" in proc.stdout
        assert env.calls("srun") == []

    def test_registered_slurm_false_stays_local(self, env):
        env.write_policy()
        env.write_commands(HELLO + "slurm = false\n")
        self._assert_local(env, env.blq("run", "hello"))
        assert env.calls("scontrol") == []

    def test_blq_slurm_off_stays_local(self, env, monkeypatch):
        env.write_policy()
        monkeypatch.setenv("BLQ_SLURM", "off")
        self._assert_local(env, env.blq("exec", "echo hello-marker"))

    def test_inside_a_job_stays_local(self, env, monkeypatch):
        env.write_policy()
        monkeypatch.setenv("SLURM_JOB_ID", "12345")
        self._assert_local(env, env.blq("exec", "echo hello-marker"))

    def test_outside_the_roots_stays_local(self, env):
        env.write_policy('roots = ["/nonexistent/elsewhere"]\n')
        self._assert_local(env, env.blq("exec", "echo hello-marker"))
        assert env.calls("scontrol") == []

    def test_bad_slurm_toml_runs_the_command_locally(self, env):
        env.write_policy("this is = = not toml\n")
        proc = env.blq("exec", "echo hello-marker")
        self._assert_local(env, proc)
        assert "Ignoring Slurm policy" in proc.stderr

    def test_queue_down_warns_and_runs_locally(self, env, monkeypatch):
        env.write_policy()
        monkeypatch.setenv("FAKE_SCONTROL_DOWN", "1")
        proc = env.blq("exec", "echo hello-marker")
        self._assert_local(env, proc)
        assert "[blq] Slurm unavailable, running locally:" in proc.stderr
        assert len(env.calls("scontrol")) == 1


class TestRegisteredResources:
    TABLE = (
        '[commands.hello]\ncmd = "echo hello-marker"\n'
        '[commands.hello.slurm]\ncpus = 8\nmem = "16G"\npartition = "batch"\n'
    )

    def test_registered_table_sets_resources_and_round_trips(self, env):
        env.write_policy()
        env.write_commands(self.TABLE)
        assert env.blq("run", "hello").returncode == 0
        calls = env.calls("srun")
        assert len(calls) == 1
        assert "-c 8" in calls[0] and "--mem=16G" in calls[0] and "-p batch" in calls[0]

        lq_dir = env.project / ".bird"
        loaded = _load_commands_impl(lq_dir)
        assert loaded["hello"].slurm == {"cpus": 8, "mem": "16G", "partition": "batch"}
        _save_commands_impl(lq_dir, loaded)
        assert _load_commands_impl(lq_dir)["hello"].slurm == loaded["hello"].slurm
        assert "slurm" not in loaded["hello"]._extra  # would reach extensions and save twice

    def test_slurm_false_survives_a_save(self, env):
        # `blq run -R` saves every command, so a dropped `false` would reroute a command.
        env.write_commands(HELLO + "slurm = false\n")
        lq_dir = env.project / ".bird"
        _save_commands_impl(lq_dir, _load_commands_impl(lq_dir))
        assert _load_commands_impl(lq_dir)["hello"].slurm is False


class TestSlurmValueValidation:
    """A bad `slurm` value is a warning and None. It must never fail command loading."""

    @pytest.mark.parametrize(
        "value",
        [
            '"yes"',
            "1",
            "[1, 2]",
            "{ cpu = 8 }",
            "{ cpus = 0 }",
            "{ cpus = -1 }",
            "{ cpus = true }",
            '{ cpus = "8" }',
            "{ mem = 16 }",
            '{ mem = "" }',
            "{ partition = 1 }",
            '{ partition = "" }',
        ],
    )
    def test_bad_value_warns_and_is_none(self, tmp_path, caplog, value):
        (tmp_path / "commands.toml").write_text(
            f'[commands.bad]\ncmd = "true"\nslurm = {value}\n[commands.good]\ncmd = "true"\n'
        )
        with caplog.at_level(logging.WARNING, logger="blq"):
            commands = _load_commands_impl(tmp_path)
        assert commands["bad"].slurm is None
        assert commands["bad"].cmd == "true"
        assert "slurm" not in commands["bad"]._extra
        assert commands["good"].slurm is None
        assert "bad" in caplog.text and "slurm" in caplog.text

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("true", True),
            ("false", False),
            ("{}", {}),
            ("{ cpus = 8 }", {"cpus": 8}),
            ('{ mem = "16G", partition = "batch" }', {"mem": "16G", "partition": "batch"}),
        ],
    )
    def test_good_value_is_kept_without_a_warning(self, tmp_path, caplog, value, expected):
        (tmp_path / "commands.toml").write_text(f'[commands.c]\ncmd = "true"\nslurm = {value}\n')
        with caplog.at_level(logging.WARNING, logger="blq"):
            commands = _load_commands_impl(tmp_path)
        assert commands["c"].slurm == expected
        assert caplog.text == ""


class TestNoCapture:
    def test_no_capture_command_also_uses_the_route(self, env):
        env.write_policy()
        env.write_commands(HELLO + "capture = false\n")
        proc = env.blq("run", "hello")
        assert proc.returncode == 0, proc.stderr
        assert "hello-marker" in proc.stdout
        calls = env.calls("srun")
        assert len(calls) == 1
        assert "-c 2" in calls[0] and "--mem=1G" in calls[0] and "-J blq-" in calls[0]

    def test_exec_no_capture_uses_the_route(self, env):
        env.write_policy()
        proc = env.blq("exec", "--no-capture", "echo hello-marker")
        assert "hello-marker" in proc.stdout, proc.stderr
        assert len(env.calls("srun")) == 1

    def test_no_capture_honours_slurm_false(self, env):
        env.write_policy()
        env.write_commands(HELLO + "capture = false\nslurm = false\n")
        proc = env.blq("run", "hello")
        assert "hello-marker" in proc.stdout, proc.stderr
        assert env.calls("srun") == []

    def test_no_capture_warns_when_the_queue_is_down(self, env, monkeypatch):
        env.write_policy()
        monkeypatch.setenv("FAKE_SCONTROL_DOWN", "1")
        proc = env.blq("exec", "--no-capture", "echo hello-marker")
        assert "hello-marker" in proc.stdout, proc.stderr
        assert env.calls("srun") == []
        assert "[blq] Slurm unavailable, running locally:" in proc.stderr


class TestSrunStatusLine:
    @pytest.mark.skipif(not _duck_hunt_loads(), reason="duck_hunt is needed to produce events")
    def test_srun_status_line_adds_no_error_event(self, env, monkeypatch):
        (env.project / "fail.log").write_text(PYTEST_FAILURE_LOG)
        env.write_policy()
        command = "cat fail.log; exit 1"

        def run_json() -> dict:
            proc = env.blq("exec", "--json", "--quiet", "--", command)
            assert proc.returncode == 1, proc.stderr
            return json.loads(proc.stdout)

        def fingerprints(result: dict) -> list[str]:
            return sorted(e["fingerprint"] for e in result["errors"])

        with_line = run_json()
        monkeypatch.setenv("FAKE_SRUN_NO_STATUS", "1")
        without_line = run_json()

        assert len(env.calls("srun")) == 2
        assert fingerprints(with_line), "no events: this comparison would prove nothing"
        assert fingerprints(with_line) == fingerprints(without_line)
        # The line stays in the stored output, because only parsing may ignore it.
        assert "Exited with exit code" in env.blq("output", "--grep", "Exited", "+2").stdout
        assert "Exited with exit code" not in env.blq("output", "+1").stdout

    @pytest.mark.skipif(not _duck_hunt_loads(), reason="duck_hunt is needed to produce events")
    def test_status_line_inside_a_record_loses_no_event(self, env, monkeypatch):
        # Real srun prints this line at any position, and duck_hunt loses a record it splits.
        (env.project / "rustc.log").write_text(
            "error[E0308]: mismatched types\n --> src/main.rs:2:5\n"
        )
        env.write_policy()
        command = "cat rustc.log; exit 1"

        def run_json() -> dict:
            proc = env.blq("exec", "--json", "--quiet", "--", command)
            assert proc.returncode == 1, proc.stderr
            return json.loads(proc.stdout)

        monkeypatch.setenv("FAKE_SRUN_NO_STATUS", "1")
        without_line = run_json()
        monkeypatch.delenv("FAKE_SRUN_NO_STATUS")
        monkeypatch.setenv("FAKE_SRUN_STATUS_AFTER", "1")
        with_line = run_json()

        assert without_line["errors"], "no events: this comparison would prove nothing"
        assert len(with_line["errors"]) == len(without_line["errors"])
        # No line is dropped: the stored output still holds the status line, last.
        stored = env.blq("output", "+1").stdout.strip().splitlines()
        assert stored[-1] == "srun: error: fakenode: task 0: Exited with exit code 1"
        assert len(stored) == 3


class TestWatch:
    def test_watch_passes_the_command_override(self):
        from blq.commands.core import RegisteredCommand
        from blq.commands.watch_cmd import WatchController, WatchSession

        config = mock.Mock()
        config.commands = {"c": RegisteredCommand(name="c", cmd="true", slurm=False)}
        session = WatchSession(
            session_id="s",
            commands=["c"],
            include_patterns=[],
            exclude_patterns=[],
            debounce_ms=0,
            started_at=datetime.now(),
        )
        controller = WatchController(session, config, quiet=True)
        with mock.patch("blq.commands.watch_cmd._execute_command") as run:
            run.return_value = mock.Mock(status="OK", duration_sec=0.0)
            controller._execute_run({"a.py"})
        assert run.call_args.kwargs["slurm"] is False
