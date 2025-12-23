"""Tests for report generation commands."""

import argparse
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from blq.commands.report_cmd import (
    ReportData,
    _collect_report_data,
    _find_baseline_run,
    _format_location,
    _generate_markdown_report,
    cmd_report,
)


class TestReportData:
    """Tests for ReportData dataclass."""

    def test_default_values(self):
        """ReportData has sensible defaults."""
        data = ReportData()
        assert data.run_id is None
        assert data.total_errors == 0
        assert data.total_warnings == 0
        assert data.errors_by_file == []
        assert data.top_errors == []


class TestFormatLocation:
    """Tests for _format_location helper."""

    def test_with_file_and_line(self):
        """Format with file path and line number."""
        error = {"file_path": "src/main.py", "line_number": 42}
        assert _format_location(error) == "src/main.py:42"

    def test_with_file_only(self):
        """Format with file path only."""
        error = {"file_path": "src/main.py", "line_number": None}
        assert _format_location(error) == "src/main.py"

    def test_without_file(self):
        """Format without file path."""
        error = {"file_path": None, "line_number": 42}
        assert _format_location(error) == "?"


class TestFindBaselineRun:
    """Tests for _find_baseline_run function."""

    def test_find_by_run_id(self, initialized_project):
        """Find baseline by numeric run ID."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        mock_runs = pd.DataFrame({
            "run_id": [1, 2, 3],
            "git_branch": ["main", "feature", "main"],
        })
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, "2")
            assert result == 2

    def test_find_by_branch(self, initialized_project):
        """Find baseline by branch name."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        mock_runs = pd.DataFrame({
            "run_id": [1, 2, 3],
            "git_branch": ["main", "feature", "main"],
        })
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, "feature")
            assert result == 2

    def test_no_baseline_returns_none(self, initialized_project):
        """None baseline returns None."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        result = _find_baseline_run(store, None)
        assert result is None


class TestGenerateMarkdownReport:
    """Tests for _generate_markdown_report function."""

    def test_basic_report(self):
        """Generate basic report with no errors."""
        data = ReportData(
            run_id=1,
            source_name="test",
            total_errors=0,
            total_warnings=0,
        )
        result = _generate_markdown_report(data)

        assert "# Build Report: test" in result
        assert ":white_check_mark: **PASSED**" in result
        assert "| Run ID | #1 |" in result
        assert "| Errors | 0 |" in result

    def test_report_with_errors(self):
        """Generate report with errors."""
        data = ReportData(
            run_id=1,
            source_name="build",
            total_errors=5,
            total_warnings=2,
            errors_by_file=[
                {"file_path": "src/main.py", "count": 3},
                {"file_path": "src/utils.py", "count": 2},
            ],
            top_errors=[
                {
                    "file_path": "src/main.py",
                    "line_number": 10,
                    "message": "Undefined variable",
                    "error_code": "E001",
                },
            ],
        )
        result = _generate_markdown_report(data)

        assert ":x: **FAILED** (5 errors)" in result
        assert "## Errors by File" in result
        assert "| `src/main.py` | 3 |" in result
        assert "## Error Details" in result
        assert "[E001]" in result
        assert "Undefined variable" in result

    def test_report_with_baseline(self):
        """Generate report with baseline comparison."""
        data = ReportData(
            run_id=2,
            source_name="test",
            total_errors=3,
            total_warnings=1,
            baseline_run_id=1,
            baseline_errors=5,
            baseline_warnings=2,
            new_errors=[
                {"file_path": "src/new.py", "line_number": 1, "message": "New error"},
            ],
            fixed_errors=[
                {"file_path": "src/fixed.py", "line_number": 1, "message": "Fixed"},
                {"file_path": "src/fixed2.py", "line_number": 2, "message": "Fixed 2"},
            ],
        )
        result = _generate_markdown_report(data)

        assert "## Comparison vs Baseline" in result
        assert "Comparing against run #1" in result
        assert "| Errors | 5 | 3 | -2 |" in result
        assert "### New Errors (1)" in result
        assert "Fixed Errors (2)" in result

    def test_summary_only(self):
        """Summary-only mode excludes details."""
        data = ReportData(
            run_id=1,
            total_errors=5,
            top_errors=[
                {"file_path": "a.py", "line_number": 1, "message": "Error"},
            ],
        )
        result = _generate_markdown_report(data, include_details=False)

        assert "## Summary" in result
        assert "## Error Details" not in result

    def test_include_warnings(self):
        """Include warnings when requested."""
        data = ReportData(
            run_id=1,
            total_errors=0,
            total_warnings=2,
            warnings_by_file=[
                {"file_path": "src/main.py", "count": 2},
            ],
            top_warnings=[
                {"file_path": "src/main.py", "line_number": 5, "message": "Warning"},
            ],
        )
        result = _generate_markdown_report(data, include_warnings=True)

        assert "## Warnings by File" in result
        assert "Warning Details" in result


class TestCollectReportData:
    """Tests for _collect_report_data function."""

    def test_collect_from_latest_run(self, initialized_project):
        """Collect data from latest run."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        # Mock runs and events
        mock_runs = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_errors = pd.DataFrame({
            "file_path": ["a.py", "a.py", "b.py"],
            "line_number": [1, 2, 3],
            "message": ["err1", "err2", "err3"],
            "fingerprint": ["fp1", "fp2", "fp3"],
        })

        mock_warnings = pd.DataFrame({
            "file_path": ["c.py"],
            "line_number": [1],
            "message": ["warn1"],
        })

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query

        # Return different dataframes for errors vs warnings
        def mock_df_side_effect():
            if mock_query.filter.call_args[1].get("severity") == "error":
                return mock_errors
            return mock_warnings

        mock_query.df.side_effect = [mock_errors, mock_warnings]

        with patch.object(store, "runs", return_value=mock_runs):
            with patch.object(store, "run", return_value=mock_query):
                data = _collect_report_data(store)

        assert data.run_id == 1
        assert data.source_name == "test"
        assert data.total_errors == 3
        assert data.total_warnings == 1


class TestCmdReport:
    """Tests for cmd_report command."""

    def test_report_outputs_markdown(self, initialized_project, capsys):
        """Report command outputs markdown."""
        args = argparse.Namespace(
            run=None,
            baseline=None,
            output=None,
            warnings=False,
            summary_only=False,
            error_limit=20,
            file_limit=10,
            global_=False,
            database=None,
        )

        mock_runs = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_df = pd.DataFrame(columns=["file_path", "line_number", "message"])

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.return_value = mock_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            cmd_report(args)

        captured = capsys.readouterr()
        assert "# Build Report" in captured.out
        assert "## Summary" in captured.out

    def test_report_no_runs_error(self, initialized_project, capsys):
        """Error when no runs found."""
        args = argparse.Namespace(
            run=None,
            baseline=None,
            output=None,
            warnings=False,
            summary_only=False,
            error_limit=20,
            file_limit=10,
            global_=False,
            database=None,
        )

        mock_runs = pd.DataFrame(columns=["run_id"])

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            mock_store.return_value = store

            with pytest.raises(SystemExit) as exc_info:
                cmd_report(args)

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "No runs found" in captured.err

    def test_report_to_file(self, initialized_project, tmp_path):
        """Report can be written to file."""
        output_file = tmp_path / "report.md"

        args = argparse.Namespace(
            run=None,
            baseline=None,
            output=str(output_file),
            warnings=False,
            summary_only=False,
            error_limit=20,
            file_limit=10,
            global_=False,
            database=None,
        )

        mock_runs = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_df = pd.DataFrame(columns=["file_path", "line_number", "message"])

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.return_value = mock_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            cmd_report(args)

        assert output_file.exists()
        content = output_file.read_text()
        assert "# Build Report" in content

    def test_report_with_baseline_warning(self, initialized_project, capsys):
        """Warning when baseline not found."""
        args = argparse.Namespace(
            run=None,
            baseline="nonexistent",
            output=None,
            warnings=False,
            summary_only=False,
            error_limit=20,
            file_limit=10,
            global_=False,
            database=None,
        )

        mock_runs = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_df = pd.DataFrame(columns=["file_path", "line_number", "message"])

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.return_value = mock_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            cmd_report(args)

        captured = capsys.readouterr()
        assert "not found" in captured.err
