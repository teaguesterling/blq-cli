"""`import` and `capture` must write where the query layer reads.

Both commands called `write_run_parquet` unconditionally, while `exec`/`run`
branch on `config.use_bird` and write to the BIRD tables. Since BIRD is the
default storage mode, an import on a default project produced a valid parquet
under `.bird/logs/date=*/source=import/` that nothing could read:

    $ blq import report.json --format pytest_json
    Imported 2 events (2 errors, 0 warnings)
    Saved to .bird/logs/date=2026-08-17/source=import/001_report.parquet
    $ blq events
    (no data)

Success reported, destination printed, exit 0, well-formed file — and no
observable outcome. The only signal available to a caller is a query they have
no reason to run as a check.

That mattered most for the richest source in the pipeline: a `pytest_json`
import carries the whole failure block (assertion, source line, and the
`+ where None = normalize(...)` line naming the function under test) where a
default text capture of the same run carries a 7-character fragment.

Covers both commands in both storage modes — parquet mode must keep writing
parquet, which is the behaviour these tests are protecting on that side.
"""

import argparse

import pytest

from blq.commands.execution import cmd_capture, cmd_import


PYTEST_TEXT_LOG = """\
============================= test session starts ==============================
collected 1 item

tests/test_priority.py F                                                 [100%]

=================================== FAILURES ===================================
____________________ test_unknown_name_falls_back_to_default ___________________

    def test_unknown_name_falls_back_to_default():
>       assert normalize(5) == 5
E       assert None == 5
E        +  where None = normalize(5)

tests/test_priority.py:5: AssertionError
=========================== short test summary info ============================
FAILED tests/test_priority.py::test_unknown_name_falls_back_to_default - assert None == 5
1 failed in 0.01s
"""


def _import_args(path, fmt="pytest_text", name=None):
    args = argparse.Namespace()
    args.file = str(path)
    args.format = fmt
    args.name = name
    return args


def _capture_args(fmt="pytest_text", name=None):
    args = argparse.Namespace()
    args.format = fmt
    args.name = name
    return args


def _event_count(project):
    """Rows visible through the query layer callers actually use."""
    from blq.commands.core import get_connection

    conn = get_connection(project / ".bird")
    return conn.execute("SELECT count(*) FROM blq_load_events()").fetchone()[0]


@pytest.fixture
def log_file(tmp_path):
    p = tmp_path / "pytest-run.log"
    p.write_text(PYTEST_TEXT_LOG)
    return p


class TestImportIsQueryable:

    def test_imported_events_are_visible_in_bird_mode(
        self, initialized_project, log_file, capsys
    ):
        cmd_import(_import_args(log_file))
        capsys.readouterr()
        assert _event_count(initialized_project) > 0, (
            "import reported success and wrote a file, but the query layer "
            "sees nothing"
        )

    def test_imported_events_are_visible_in_parquet_mode(
        self, initialized_project_parquet, log_file, capsys
    ):
        cmd_import(_import_args(log_file))
        capsys.readouterr()
        assert _event_count(initialized_project_parquet) > 0

    def test_the_error_message_survives_the_round_trip(
        self, initialized_project, log_file, capsys
    ):
        """The point of importing is the diagnostic text, so assert on it.

        The fixture carries an UNtruncated summary line — what pytest emits
        when it is not abbreviating for an 80-column terminal it cannot see.
        The parser reads that line, so an abbreviated one would make this
        assert on text pytest had already discarded rather than on anything
        blq did.
        """
        from blq.commands.core import get_connection

        cmd_import(_import_args(log_file))
        capsys.readouterr()
        conn = get_connection(initialized_project / ".bird")
        rows = conn.execute(
            "SELECT message FROM blq_load_events() WHERE severity = 'error'"
        ).fetchall()
        assert rows, "no error events queryable after import"
        assert any("assert" in (r[0] or "").lower() for r in rows), rows


class TestCaptureIsQueryable:
    """`capture` shares the same unconditional parquet write as `import`."""

    def test_captured_events_are_visible_in_bird_mode(
        self, initialized_project, monkeypatch, capsys
    ):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(PYTEST_TEXT_LOG))
        cmd_capture(_capture_args())
        capsys.readouterr()
        assert _event_count(initialized_project) > 0

    def test_captured_events_are_visible_in_parquet_mode(
        self, initialized_project_parquet, monkeypatch, capsys
    ):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(PYTEST_TEXT_LOG))
        cmd_capture(_capture_args())
        capsys.readouterr()
        assert _event_count(initialized_project_parquet) > 0


# ── Captured children get a usable terminal width (#49) ──────────────


class TestCaptureEnvironment:
    """Tools that format for a terminal self-truncate when captured.

    pytest consults COLUMNS and falls back to 80 when there is no TTY — which
    is always, under a capturing runner. `pytest -q` abbreviates its
    short-summary line on that basis, so the parsed message loses everything
    past roughly 80 columns minus the length of the test name:

        73-char test name  ->  7 characters of message survive ('asse...')
        35-char test name  -> 45 characters survive, nothing is lost

    A failure's diagnosability therefore depended on how long its test was
    named, which is not a dependency anyone would guess. Presenting a wide
    COLUMNS to the child fixes the whole class at the point of capture,
    rather than each parser trying to recover text the tool already discarded.
    """

    def test_columns_is_set_wide_by_default(self):
        from blq.ext import capture_env

        env = capture_env({"PATH": "/usr/bin"})
        assert int(env["COLUMNS"]) >= 200

    def test_an_explicit_columns_is_respected(self):
        """A caller who set COLUMNS meant it — including a narrow one."""
        from blq.ext import capture_env

        env = capture_env({"PATH": "/usr/bin", "COLUMNS": "40"})
        assert env["COLUMNS"] == "40"

    def test_the_rest_of_the_environment_survives(self):
        from blq.ext import capture_env

        env = capture_env({"PATH": "/usr/bin", "MY_VAR": "keep me"})
        assert env["PATH"] == "/usr/bin"
        assert env["MY_VAR"] == "keep me"

    def test_the_caller_environment_is_not_mutated(self):
        from blq.ext import capture_env

        original = {"PATH": "/usr/bin"}
        capture_env(original)
        assert "COLUMNS" not in original
