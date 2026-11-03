"""Tests for SlurmExecutor, using fake srun/scancel/systemd-run on PATH."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from blq.ext import CommandSpec
from blq.ext.slurm_executor import SlurmExecutor, srun_command
from blq.ext.slurm_policy import Route, SlurmPolicy, SlurmResources

RES = SlurmResources(partition="batch", cpus=4, mem="8G")
SLURM_ROUTE = Route("slurm", "inside roots", RES)

# Fake srun: log the argument line, then act on FAKE_SRUN_MODE.
FAKE_SRUN = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG/srun.log"
case "${FAKE_SRUN_MODE:-pass}" in
  contact) echo "srun: error: Unable to contact slurm controller (connect failure)" >&2; exit 1;;
  oom) echo "slurmstepd: error: Detected 1 oom_kill event in StepId=1.0" >&2; exit 1;;
  config) echo "srun: error: Requested node configuration is not available" >&2; exit 1;;
  hang) sleep 30; exit 0;;
esac
for a in "$@"; do
  case "$a" in --chdir=*) cd "${a#--chdir=}";; esac
done
while [ "$1" != "/bin/sh" ]; do shift; done
exec "$@"
"""
FAKE_SCANCEL = '#!/bin/bash\necho "$*" >> "$FAKE_LOG/scancel.log"\n'
FAKE_SYSTEMD_RUN = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG/systemd-run.log"
while [ "$1" != "/bin/sh" ]; do shift; done
exec "$@"
"""


def _script(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Put fake srun, scancel and systemd-run first on PATH. Returns the log dir."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    log.mkdir()
    _script(bindir / "srun", FAKE_SRUN)
    _script(bindir / "scancel", FAKE_SCANCEL)
    _script(bindir / "systemd-run", FAKE_SYSTEMD_RUN)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_SRUN_MODE", raising=False)
    return log


def _lines(log: Path, name: str) -> list[str]:
    f = log / name
    return f.read_text().splitlines() if f.exists() else []


def _spec(tmp_path, command="echo hello", **overrides) -> CommandSpec:
    defaults = dict(
        command=command,
        original_command=command,
        command_name="test",
        attempt_id="att-001",
        workspace=tmp_path,
        cwd=tmp_path,
        live_dir=tmp_path / "live",
        env={},
    )
    defaults.update(overrides)
    return CommandSpec(**defaults)


def _run(tmp_path, command="echo hello", route=SLURM_ROUTE, policy=None, live=None, **spec_kw):
    ex = SlurmExecutor(route, policy, quiet=True, live_output_path=live)
    return ex.execute(_spec(tmp_path, command, **spec_kw))


class TestSrunCommand:
    def test_srun_args_include_one_task_and_resources(self, tmp_path):
        cmd = srun_command("true", tmp_path, RES, "att-001", None)
        assert "-n 1" in cmd and "-c 4" in cmd and "--mem=8G" in cmd and "-p batch" in cmd
        assert "-J blq-att-001" in cmd
        assert "-Q" not in cmd.split()

    def test_cwd_with_space_is_quoted(self, tmp_path):
        cwd = tmp_path / "a dir"
        cwd.mkdir()
        cmd = srun_command("true", cwd, RES, "x", None)
        assert f"--chdir='{cwd}'" in cmd

    def test_time_limit_only_with_timeout(self, tmp_path):
        assert "-t" not in srun_command("true", tmp_path, RES, "x", None).split()
        assert "-t 3" in srun_command("true", tmp_path, RES, "x", 90)  # ceil(90/60)+1
        assert "-t 2" in srun_command("true", tmp_path, RES, "x", 60)  # exact minute


class TestSlurmRoute:
    def test_quoting_survives_shell_metacharacters(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", "/home/quoted")
        result = _run(tmp_path, 'echo "a b"; echo $HOME')
        assert result.output == "a b\n/home/quoted\n"

    def test_cwd_with_space_runs_there(self, fakes, tmp_path):
        cwd = tmp_path / "a dir"
        cwd.mkdir()
        result = _run(tmp_path, "pwd", cwd=cwd)
        assert result.output.strip() == str(cwd)

    def test_srun_receives_wrapped_command(self, fakes, tmp_path):
        _run(tmp_path, "echo hello")
        (line,) = _lines(fakes, "srun.log")
        assert "-n 1 -c 4 --mem=8G -p batch -J blq-att-001" in line
        assert "/bin/sh -c" in line

    def test_exit_code_and_output_pass_through(self, fakes, tmp_path):
        result = _run(tmp_path, "echo hello; exit 7")
        assert (result.exit_code, "hello" in result.output) == (7, True)

    def test_output_reaches_live_file(self, fakes, tmp_path):
        live = tmp_path / "live" / "out.log"
        _run(tmp_path, "echo hello", live=live)
        assert live.read_text() == "hello\n"

    def test_original_command_is_unchanged(self, fakes, tmp_path):
        spec = _spec(tmp_path, "echo hello")
        SlurmExecutor(SLURM_ROUTE, None, quiet=True).execute(spec)
        assert spec.command == "echo hello" and spec.original_command == "echo hello"

    def test_timeout_cancels_the_job(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "hang")
        result = _run(tmp_path, "true", timeout=1)
        assert result.timeout is True
        assert _lines(fakes, "scancel.log") == ["-n blq-att-001"]

    def test_normal_run_does_not_scancel(self, fakes, tmp_path):
        _run(tmp_path, "true")
        assert _lines(fakes, "scancel.log") == []

    def test_out_of_memory_is_not_rerun(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "oom")
        result = _run(tmp_path, "true")
        assert len(_lines(fakes, "srun.log")) == 1
        assert result.exit_code == 1 and "oom_kill" in result.output

    def test_other_srun_error_is_not_rerun(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "config")
        result = _run(tmp_path, "true")
        assert len(_lines(fakes, "srun.log")) == 1
        assert result.exit_code == 1 and "not available" in result.output

    def test_unable_to_contact_reruns_locally_once_and_warns(
        self, fakes, tmp_path, monkeypatch, capfd
    ):
        monkeypatch.setenv("FAKE_SRUN_MODE", "contact")
        live = tmp_path / "live" / "out.log"
        result = _run(tmp_path, "echo ran-locally", live=live)
        assert len(_lines(fakes, "srun.log")) == 1  # srun tried once, rerun is not srun
        assert result.exit_code == 0
        warning = "[blq] Slurm unavailable, running locally: controller unreachable\n"
        assert result.output == warning + "ran-locally\n"
        assert "Unable to contact" not in result.output
        assert live.read_text() == result.output
        assert warning in capfd.readouterr().err

    def test_failing_local_rerun_is_not_looped(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "contact")
        result = _run(tmp_path, "exit 3")
        assert result.exit_code == 3
        assert len(_lines(fakes, "srun.log")) == 1


class TestLocalRoute:
    def test_warning_is_first_in_output_and_shown_when_quiet(self, fakes, tmp_path, capfd):
        route = Route("local", "Slurm queue unavailable: down", warn=True)
        live = tmp_path / "live" / "out.log"
        result = _run(tmp_path, "echo hello", route=route, live=live)
        line = "[blq] Slurm unavailable, running locally: Slurm queue unavailable: down\n"
        assert result.output == line + "hello\n"
        assert live.read_text() == result.output
        assert line in capfd.readouterr().err
        assert _lines(fakes, "srun.log") == []

    def test_no_warning_without_warn(self, fakes, tmp_path, capfd):
        result = _run(tmp_path, "echo hello", route=Route("local", "no Slurm policy"))
        assert result.output == "hello\n"
        assert capfd.readouterr().err == ""

    def test_local_timeout_never_scancels(self, fakes, tmp_path):
        result = _run(tmp_path, "sleep 30", route=Route("local", "x"), timeout=1)
        assert result.timeout is True and result.exit_code == -1
        assert _lines(fakes, "scancel.log") == []

    def test_local_memory_max_uses_systemd_run(self, fakes, tmp_path):
        policy = SlurmPolicy((), RES, local_memory_max="2G")
        result = _run(tmp_path, "echo hello", route=Route("local", "x"), policy=policy)
        assert result.output == "hello\n"
        (line,) = _lines(fakes, "systemd-run.log")
        assert "--user --scope -q -p MemoryMax=2G /bin/sh -c" in line

    def test_no_memory_max_means_no_systemd_run(self, fakes, tmp_path):
        policy = SlurmPolicy((), RES, local_memory_max=None)
        _run(tmp_path, "echo hello", route=Route("local", "x"), policy=policy)
        assert _lines(fakes, "systemd-run.log") == []

    def test_local_without_systemd_run_still_runs(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        policy = SlurmPolicy((), RES, local_memory_max="2G")
        result = _run(tmp_path, "echo hello", route=Route("local", "x"), policy=policy)
        assert (result.exit_code, result.output) == (0, "hello\n")
