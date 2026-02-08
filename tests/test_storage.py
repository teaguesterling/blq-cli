"""Tests for BlqStorage - the unified storage abstraction."""

import os
from pathlib import Path

import duckdb
import pytest

from blq.storage import BlqStorage


class TestBlqStorageOpen:
    """Tests for opening BlqStorage."""

    def test_open_finds_lq_dir(self, initialized_project):
        """Open finds .lq in current directory."""
        storage = BlqStorage.open()
        assert storage.path.name == ".lq"
        assert storage.path.exists()
        storage.close()

    def test_open_explicit_path(self, initialized_project):
        """Open with explicit path."""
        storage = BlqStorage.open(Path(".lq"))
        assert storage.path.exists()
        storage.close()

    def test_open_not_found(self, temp_dir):
        """Open raises when .lq not found."""
        original = os.getcwd()
        try:
            os.chdir(temp_dir)
            with pytest.raises(FileNotFoundError):
                BlqStorage.open()
        finally:
            os.chdir(original)

    def test_context_manager(self, initialized_project):
        """Can use as context manager."""
        with BlqStorage.open() as storage:
            assert storage.path.exists()


class TestBlqStorageHasData:
    """Tests for data existence checks."""

    def test_has_data_empty(self, initialized_project):
        """has_data returns False when no runs."""
        with BlqStorage.open() as storage:
            assert storage.has_data() is False
            assert storage.has_runs() is False

    def test_has_data_with_run(self, initialized_project):
        """has_data returns True after writing a run."""
        with BlqStorage.open() as storage:
            storage.write_run({
                "command": "echo test",
                "source_name": "test",
                "source_type": "exec",
                "exit_code": 0,
            })
            assert storage.has_data() is True
            assert storage.has_runs() is True

    def test_has_events_empty(self, initialized_project):
        """has_events returns False when no events."""
        with BlqStorage.open() as storage:
            assert storage.has_events() is False

    def test_has_events_with_events(self, initialized_project):
        """has_events returns True after writing events."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {
                    "command": "make",
                    "source_name": "build",
                    "source_type": "run",
                    "exit_code": 1,
                },
                events=[
                    {"severity": "error", "message": "undefined reference"},
                ],
            )
            assert storage.has_events() is True


class TestBlqStorageRuns:
    """Tests for run queries."""

    def test_runs_returns_relation(self, initialized_project):
        """runs() returns a DuckDB relation."""
        with BlqStorage.open() as storage:
            result = storage.runs()
            assert isinstance(result, duckdb.DuckDBPyRelation)

    def test_runs_empty(self, initialized_project):
        """runs() returns empty relation when no data."""
        with BlqStorage.open() as storage:
            df = storage.runs().df()
            assert len(df) == 0

    def test_runs_with_data(self, initialized_project):
        """runs() returns runs after writing."""
        with BlqStorage.open() as storage:
            storage.write_run({
                "command": "pytest",
                "source_name": "test",
                "source_type": "run",
                "exit_code": 0,
            })
            df = storage.runs().df()
            assert len(df) == 1
            assert df.iloc[0]["source_name"] == "test"

    def test_runs_limit(self, initialized_project):
        """runs() respects limit parameter."""
        with BlqStorage.open() as storage:
            for i in range(5):
                storage.write_run({
                    "command": f"cmd{i}",
                    "source_name": f"run{i}",
                    "source_type": "exec",
                    "exit_code": 0,
                })
            df = storage.runs(limit=3).df()
            assert len(df) == 3

    def test_run_by_id(self, initialized_project):
        """run() returns specific run."""
        with BlqStorage.open() as storage:
            storage.write_run({
                "command": "make",
                "source_name": "build",
                "source_type": "run",
                "exit_code": 0,
            })
            df = storage.run(1).df()
            assert len(df) == 1
            assert df.iloc[0]["source_name"] == "build"

    def test_run_not_found(self, initialized_project):
        """run() returns empty relation for nonexistent ID."""
        with BlqStorage.open() as storage:
            df = storage.run(999).df()
            assert len(df) == 0

    def test_latest_run_id(self, initialized_project):
        """latest_run_id() returns the most recent run ID."""
        with BlqStorage.open() as storage:
            assert storage.latest_run_id() is None

            storage.write_run({
                "command": "cmd1",
                "source_name": "run1",
                "source_type": "exec",
                "exit_code": 0,
            })
            assert storage.latest_run_id() == 1

            storage.write_run({
                "command": "cmd2",
                "source_name": "run2",
                "source_type": "exec",
                "exit_code": 0,
            })
            assert storage.latest_run_id() == 2


class TestBlqStorageEvents:
    """Tests for event queries."""

    def test_events_returns_relation(self, initialized_project):
        """events() returns a DuckDB relation."""
        with BlqStorage.open() as storage:
            result = storage.events()
            assert isinstance(result, duckdb.DuckDBPyRelation)

    def test_events_empty(self, initialized_project):
        """events() returns empty when no data."""
        with BlqStorage.open() as storage:
            df = storage.events().df()
            assert len(df) == 0

    def test_events_with_data(self, initialized_project):
        """events() returns events after writing."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {
                    "command": "make",
                    "source_name": "build",
                    "source_type": "run",
                    "exit_code": 1,
                },
                events=[
                    {"severity": "error", "message": "error 1"},
                    {"severity": "warning", "message": "warning 1"},
                ],
            )
            df = storage.events().df()
            assert len(df) == 2

    def test_events_filter_by_severity(self, initialized_project):
        """events() can filter by severity."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {
                    "command": "make",
                    "source_name": "build",
                    "source_type": "run",
                    "exit_code": 1,
                },
                events=[
                    {"severity": "error", "message": "error 1"},
                    {"severity": "warning", "message": "warning 1"},
                    {"severity": "error", "message": "error 2"},
                ],
            )
            errors = storage.events(severity="error").df()
            assert len(errors) == 2
            assert all(errors["severity"] == "error")

    def test_events_filter_by_run(self, initialized_project):
        """events() can filter by run_id."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {"command": "cmd1", "source_name": "r1", "source_type": "exec", "exit_code": 0},
                events=[{"severity": "error", "message": "e1"}],
            )
            storage.write_run(
                {"command": "cmd2", "source_name": "r2", "source_type": "exec", "exit_code": 0},
                events=[{"severity": "error", "message": "e2"}],
            )
            df = storage.events(run_id=1).df()
            assert len(df) == 1
            assert df.iloc[0]["message"] == "e1"

    def test_errors_convenience(self, initialized_project):
        """errors() returns only error events."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {"command": "make", "source_name": "build", "source_type": "run", "exit_code": 1},
                events=[
                    {"severity": "error", "message": "error"},
                    {"severity": "warning", "message": "warning"},
                ],
            )
            df = storage.errors().df()
            assert len(df) == 1
            assert df.iloc[0]["severity"] == "error"

    def test_warnings_convenience(self, initialized_project):
        """warnings() returns only warning events."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {"command": "make", "source_name": "build", "source_type": "run", "exit_code": 1},
                events=[
                    {"severity": "error", "message": "error"},
                    {"severity": "warning", "message": "warning"},
                ],
            )
            df = storage.warnings().df()
            assert len(df) == 1
            assert df.iloc[0]["severity"] == "warning"


class TestBlqStorageWrite:
    """Tests for write operations."""

    def test_write_run_returns_id(self, initialized_project):
        """write_run returns the run ID."""
        with BlqStorage.open() as storage:
            run_id = storage.write_run({
                "command": "echo test",
                "source_name": "test",
                "source_type": "exec",
                "exit_code": 0,
            })
            assert run_id is not None
            assert isinstance(run_id, str)  # UUID

    def test_write_run_with_events(self, initialized_project):
        """write_run stores events."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {
                    "command": "make",
                    "source_name": "build",
                    "source_type": "run",
                    "exit_code": 1,
                },
                events=[
                    {"severity": "error", "ref_file": "main.c", "ref_line": 10, "message": "error"},
                ],
            )
            df = storage.events().df()
            assert len(df) == 1
            assert df.iloc[0]["ref_file"] == "main.c"
            assert df.iloc[0]["ref_line"] == 10

    def test_write_run_with_output(self, initialized_project):
        """write_run stores output."""
        with BlqStorage.open() as storage:
            storage.write_run(
                {
                    "command": "echo hello",
                    "source_name": "test",
                    "source_type": "exec",
                    "exit_code": 0,
                },
                output=b"hello\n",
            )
            # Verify output was stored
            result = storage.sql("SELECT * FROM outputs").fetchone()
            assert result is not None

    def test_get_next_run_number(self, initialized_project):
        """get_next_run_number returns sequential numbers."""
        with BlqStorage.open() as storage:
            assert storage.get_next_run_number() == 1
            storage.write_run({
                "command": "cmd1",
                "source_name": "run1",
                "source_type": "exec",
                "exit_code": 0,
            })
            assert storage.get_next_run_number() == 2


class TestBlqStorageSQL:
    """Tests for raw SQL queries."""

    def test_sql_returns_relation(self, initialized_project):
        """sql() returns a DuckDB relation."""
        with BlqStorage.open() as storage:
            result = storage.sql("SELECT 1 AS x")
            assert isinstance(result, duckdb.DuckDBPyRelation)
            assert result.fetchone()[0] == 1

    def test_sql_can_query_tables(self, initialized_project):
        """sql() can query blq tables."""
        with BlqStorage.open() as storage:
            storage.write_run({
                "command": "test",
                "source_name": "test",
                "source_type": "exec",
                "exit_code": 0,
            })
            result = storage.sql("SELECT COUNT(*) FROM invocations").fetchone()
            assert result[0] == 1


class TestBlqStorageStatus:
    """Tests for status queries."""

    def test_status_returns_relation(self, initialized_project):
        """status() returns a DuckDB relation."""
        with BlqStorage.open() as storage:
            result = storage.status()
            assert isinstance(result, duckdb.DuckDBPyRelation)

    def test_source_status_returns_relation(self, initialized_project):
        """source_status() returns a DuckDB relation."""
        with BlqStorage.open() as storage:
            result = storage.source_status()
            assert isinstance(result, duckdb.DuckDBPyRelation)
