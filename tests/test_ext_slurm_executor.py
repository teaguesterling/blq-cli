"""Tests for SlurmExecutor, using fake srun/scancel/systemd-run on PATH."""

from __future__ import annotations

import shlex
import stat
import subprocess
from pathlib import Path

import pytest

from blq.ext import CommandSpec
from blq.ext.local_executor import LocalExecutor
from blq.ext.slurm_executor import SlurmExecutor, srun_command
from blq.ext.slurm_policy import Route, SlurmPolicy, SlurmResources

TRIGGER = (
    "srun: error: Unable to allocate resources: "
    "Unable to contact slurm controller (connect failure)"
)
STATUS = "srun: error: node1: task 0: Exited with exit code 1"
STATUS2 = "srun: error: node1: task 1: Exited with exit code 2"
RES = SlurmResources(partition="batch", cpus=4, mem="8G")
SLURM_ROUTE = Route("slurm", "inside roots", RES)

# Fake srun: log the argument line, then act on FAKE_SRUN_MODE.
FAKE_SRUN = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG/srun.log"
if [ -n "$FAKE_SRUN_READS_STDIN" ]; then  # real srun forwards stdin unless told not to
  case " $* " in *" --input=none "*) ;; *) cat > /dev/null;; esac
fi
case "${FAKE_SRUN_MODE:-pass}" in
  killed) kill -9 $$;;
  contact) echo "$FAKE_TRIGGER" >&2; exit 1;;
  hangcontact) echo "$FAKE_TRIGGER" >&2; sleep 30; exit 0;;
  oom)
    echo "slurmstepd-node1: error: Detected 1 oom_kill event in StepId=53.0. \
Some of the step tasks have been OOM Killed." >&2
    echo "srun: error: node1: task 0: Out Of Memory" >&2
    exit 1;;
  config) echo "srun: error: Requested node configuration is not available" >&2; exit 1;;
  hang) sleep 30; exit 0;;
  midstatus) echo first; echo "$FAKE_STATUS" >&2; echo second; exit 1;;
  twostatus) echo "$FAKE_STATUS" >&2; echo first; echo "$FAKE_STATUS2" >&2; echo second; exit 1;;
  statushang) echo first; echo "$FAKE_STATUS" >&2; echo second; sleep 30; exit 0;;
esac
for a in "$@"; do
  case "$a" in --chdir=*) cd "${a#--chdir=}";; esac
done
while [ "$1" != "/bin/sh" ]; do shift; done
exec "$@"
"""
FAKE_SCANCEL = '#!/bin/bash\necho "$*" >> "$FAKE_LOG/scancel.log"\n'
FAKE_SYSTEMD_RUN = r"""#!/bin/bash
case " $* " in
  *" /bin/sh "*) ;;
  *) echo "$*" >> "$FAKE_LOG/systemd-run-probe.log"
     if [ -n "$FAKE_SYSTEMD_BROKEN" ]; then
       echo "Failed to connect to bus: No medium found" >&2; exit 1
     fi
     exit 0;;
esac
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
    monkeypatch.setenv("FAKE_TRIGGER", TRIGGER)
    monkeypatch.setenv("FAKE_STATUS", STATUS)
    monkeypatch.setenv("FAKE_STATUS2", STATUS2)
    monkeypatch.delenv("FAKE_SRUN_MODE", raising=False)
    monkeypatch.delenv("FAKE_SYSTEMD_BROKEN", raising=False)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
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


class TestKilledSrun:
    def test_killed_srun_calls_scancel(self, tmp_path, fakes, monkeypatch):
        # The job can outlive a killed srun, so the executor must cancel it by name.
        monkeypatch.setenv("FAKE_SRUN_MODE", "killed")
        result = _run(tmp_path)
        assert result.exit_code < 0
        assert _lines(fakes, "scancel.log") == ["-n blq-att-001"]

    def test_normal_failure_does_not_call_scancel(self, tmp_path, fakes):
        result = _run(tmp_path, "exit 137")
        assert result.exit_code == 137
        assert _lines(fakes, "scancel.log") == []


class TestStatusLineMovesToEnd:
    """srun prints its task status line anywhere, and a split record loses its event."""

    def test_status_line_moves_to_the_end_of_output_and_live_file(
        self, tmp_path, fakes, monkeypatch
    ):
        monkeypatch.setenv("FAKE_SRUN_MODE", "midstatus")
        live = tmp_path / "live.out"
        result = _run(tmp_path, live=live)
        expected = ["first", "second", STATUS]
        assert result.output.splitlines() == expected
        assert live.read_text().splitlines() == expected
        assert result.exit_code == 1

    def test_several_status_lines_keep_their_order(self, tmp_path, fakes, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "twostatus")
        result = _run(tmp_path)
        assert result.output.splitlines() == ["first", "second", STATUS, STATUS2]

    def test_timeout_marker_survives_in_the_live_file(self, tmp_path, fakes, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "statushang")
        live = tmp_path / "live.out"
        result = _run(tmp_path, live=live, timeout=1)
        assert result.timeout
        text = live.read_text()
        assert "[TIMEOUT after 1s]" in text
        assert text.splitlines()[-1] == STATUS
        assert text.count(STATUS) == 1
        assert result.output.splitlines()[-1] == STATUS

    def test_local_route_leaves_a_matching_line_in_place(self, tmp_path, fakes):
        command = f"echo first; echo '{STATUS}'; echo second"
        live = tmp_path / "live.out"
        result = _run(tmp_path, command, route=Route("local", "outside"), live=live)
        expected = ["first", STATUS, "second"]
        assert result.output.splitlines() == expected
        assert live.read_text().splitlines() == expected

    def test_other_srun_errors_stay_in_place(self, tmp_path, fakes):
        command = "echo first; echo 'srun: error: something else'; echo second"
        result = _run(tmp_path, command)
        assert result.output.splitlines() == ["first", "srun: error: something else", "second"]


class TestSrunCommand:
    def test_srun_does_not_read_stdin(self, tmp_path):
        assert "--input=none" in shlex.split(srun_command("true", tmp_path, RES, "x", None))

    def test_while_read_loop_keeps_all_items(self, tmp_path, fakes, monkeypatch):
        # Without --input=none, the first srun takes the rest of the loop's input.
        monkeypatch.setenv("FAKE_SRUN_READS_STDIN", "1")
        wrapped = srun_command("echo got", tmp_path, RES, "x", None)
        script = f"while read item; do {wrapped}; done <<EOF\n1\n2\n3\n4\nEOF\n"
        proc = subprocess.run(["/bin/bash", "-c", script], capture_output=True, text=True)
        assert proc.stdout.splitlines() == ["got"] * 4, proc.stderr

    def test_inner_shell_merges_stderr_before_the_command(self, tmp_path):
        # srun reorders the two streams, so the task must write both to one stream.
        argv = shlex.split(srun_command("echo 'a b' >&2", tmp_path, RES, "x", None))
        assert argv[-3:] == ["/bin/sh", "-c", "exec 2>&1; echo 'a b' >&2"]

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
        assert result.exit_code == 1 and "Out Of Memory" in result.output

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

    def test_trigger_line_in_a_started_commands_output_is_not_rerun(self, fakes, tmp_path, capfd):
        cmd = f'echo "{TRIGGER}"; echo other; exit 1'
        result = _run(tmp_path, cmd)
        assert len(_lines(fakes, "srun.log")) == 1
        assert result.exit_code == 1 and "running locally" not in result.output
        assert "running locally" not in capfd.readouterr().err

    def test_timed_out_run_with_trigger_line_is_not_rerun(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_SRUN_MODE", "hangcontact")
        result = _run(tmp_path, "true", timeout=1)
        assert result.timeout is True
        assert len(_lines(fakes, "srun.log")) == 1
        assert "running locally" not in result.output

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

    def test_warning_reaches_stderr_before_the_command_runs(
        self, fakes, tmp_path, capfd, monkeypatch
    ):
        seen = []
        orig = LocalExecutor.execute

        def spy(self, spec):
            seen.append(capfd.readouterr().err)
            return orig(self, spec)

        monkeypatch.setattr(LocalExecutor, "execute", spy)
        route = Route("local", "down", warn=True)
        _run(tmp_path, "echo hello", route=route)
        assert seen == ["[blq] Slurm unavailable, running locally: down\n"]

    def test_live_file_keeps_timeout_marker_after_warning(self, fakes, tmp_path):
        route = Route("local", "down", warn=True)
        live = tmp_path / "live" / "out.log"
        result = _run(tmp_path, "echo first; sleep 30", route=route, live=live, timeout=1)
        text = live.read_text()
        assert result.timeout is True
        assert text.startswith("[blq] Slurm unavailable, running locally: down\nfirst\n")
        assert "[TIMEOUT after 1s]" in text

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
        assert "--user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 /bin/sh -c" in line

    def test_memory_cap_skipped_inside_a_slurm_job(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("SLURM_JOB_ID", "123")
        policy = SlurmPolicy((), RES, local_memory_max="2G")
        result = _run(tmp_path, "echo hello", route=Route("local", "x"), policy=policy)
        assert result.output == "hello\n"
        assert _lines(fakes, "systemd-run.log") == []

    def test_unusable_systemd_run_runs_unwrapped(self, fakes, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_SYSTEMD_BROKEN", "1")
        policy = SlurmPolicy((), RES, local_memory_max="2G")
        result = _run(tmp_path, "echo hello", route=Route("local", "x"), policy=policy)
        assert (result.exit_code, result.output) == (0, "hello\n")
        assert _lines(fakes, "systemd-run.log") == []
        assert "--user --scope -q true" in _lines(fakes, "systemd-run-probe.log")[0]

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
