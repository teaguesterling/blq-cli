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
        error = {"ref_file": "src/main.py", "ref_line": 42}
        assert _format_location(error) == "src/main.py:42"

    def test_with_file_only(self):
        """Format with file path only."""
        error = {"ref_file": "src/main.py", "ref_line": None}
        assert _format_location(error) == "src/main.py"

    def test_without_file(self):
        """Format without file path."""
        error = {"ref_file": None, "ref_line": 42}
        assert _format_location(error) == "?"


class TestFindBaselineRun:
    """Tests for _find_baseline_run function."""

    def test_find_by_run_id(self, initialized_project):
        """Find baseline by numeric run ID."""
        from blq.storage import BlqStorage

        store = BlqStorage.open()

        mock_runs_df = pd.DataFrame({
            "run_id": [1, 2, 3],
            "git_branch": ["main", "feature", "main"],
        })
        mock_rel = MagicMock()
        mock_rel.df.return_value = mock_runs_df
        with patch.object(store, "runs", return_value=mock_rel):
            result = _find_baseline_run(store, "2")
            assert result == 2

    def test_find_by_branch(self, initialized_project):
        """Find baseline by branch name."""
        from blq.storage import BlqStorage

        store = BlqStorage.open()

        mock_runs_df = pd.DataFrame({
            "run_id": [1, 2, 3],
            "git_branch": ["main", "feature", "main"],
        })
        mock_rel = MagicMock()
        mock_rel.df.return_value = mock_runs_df
        with patch.object(store, "runs", return_value=mock_rel):
            result = _find_baseline_run(store, "feature")
            assert result == 2

    def test_no_baseline_returns_none(self, initialized_project):
        """None baseline returns None."""
        from blq.storage import BlqStorage

        store = BlqStorage.open()

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
                {"ref_file": "src/main.py", "count": 3},
                {"ref_file": "src/utils.py", "count": 2},
            ],
            top_errors=[
                {
                    "ref_file": "src/main.py",
                    "ref_line": 10,
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
                {"ref_file": "src/new.py", "ref_line": 1, "message": "New error"},
            ],
            fixed_errors=[
                {"ref_file": "src/fixed.py", "ref_line": 1, "message": "Fixed"},
                {"ref_file": "src/fixed2.py", "ref_line": 2, "message": "Fixed 2"},
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
                {"ref_file": "a.py", "ref_line": 1, "message": "Error"},
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
                {"ref_file": "src/main.py", "count": 2},
            ],
            top_warnings=[
                {"ref_file": "src/main.py", "ref_line": 5, "message": "Warning"},
            ],
        )
        result = _generate_markdown_report(data, include_warnings=True)

        assert "## Warnings by File" in result
        assert "Warning Details" in result


class TestCollectReportData:
    """Tests for _collect_report_data function."""

    def test_collect_from_latest_run(self, initialized_project):
        """Collect data from latest run."""
        from blq.storage import BlqStorage

        store = BlqStorage.open()

        # Mock runs and events
        mock_runs_df = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_errors_df = pd.DataFrame({
            "ref_file": ["a.py", "a.py", "b.py"],
            "ref_line": [1, 2, 3],
            "message": ["err1", "err2", "err3"],
            "fingerprint": ["fp1", "fp2", "fp3"],
        })

        mock_warnings_df = pd.DataFrame({
            "ref_file": ["c.py"],
            "ref_line": [1],
            "message": ["warn1"],
        })

        # Create mock relations for runs(), errors(), warnings()
        mock_runs_rel = MagicMock()
        mock_runs_rel.df.return_value = mock_runs_df

        mock_errors_rel = MagicMock()
        mock_errors_rel.df.return_value = mock_errors_df

        mock_warnings_rel = MagicMock()
        mock_warnings_rel.df.return_value = mock_warnings_df

        with patch.object(store, "runs", return_value=mock_runs_rel):
            with patch.object(store, "errors", return_value=mock_errors_rel):
                with patch.object(store, "warnings", return_value=mock_warnings_rel):
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

        mock_runs_df = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_empty_df = pd.DataFrame(columns=["ref_file", "ref_line", "message"])

        # Create mock relations
        mock_runs_rel = MagicMock()
        mock_runs_rel.df.return_value = mock_runs_df

        mock_errors_rel = MagicMock()
        mock_errors_rel.df.return_value = mock_empty_df

        mock_warnings_rel = MagicMock()
        mock_warnings_rel.df.return_value = mock_empty_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_get_store:
            store = MagicMock()
            store.runs.return_value = mock_runs_rel
            store.errors.return_value = mock_errors_rel
            store.warnings.return_value = mock_warnings_rel
            mock_get_store.return_value = store

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

        mock_runs_df = pd.DataFrame(columns=["run_id"])

        mock_runs_rel = MagicMock()
        mock_runs_rel.df.return_value = mock_runs_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_get_store:
            store = MagicMock()
            store.runs.return_value = mock_runs_rel
            mock_get_store.return_value = store

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

        mock_runs_df = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_empty_df = pd.DataFrame(columns=["ref_file", "ref_line", "message"])

        # Create mock relations
        mock_runs_rel = MagicMock()
        mock_runs_rel.df.return_value = mock_runs_df

        mock_errors_rel = MagicMock()
        mock_errors_rel.df.return_value = mock_empty_df

        mock_warnings_rel = MagicMock()
        mock_warnings_rel.df.return_value = mock_empty_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_get_store:
            store = MagicMock()
            store.runs.return_value = mock_runs_rel
            store.errors.return_value = mock_errors_rel
            store.warnings.return_value = mock_warnings_rel
            mock_get_store.return_value = store

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

        mock_runs_df = pd.DataFrame({
            "run_id": [1],
            "source_name": ["test"],
            "started_at": [None],
            "completed_at": [None],
            "exit_code": [0],
            "git_branch": ["main"],
            "git_commit": ["abc123"],
        })

        mock_empty_df = pd.DataFrame(columns=["ref_file", "ref_line", "message"])

        # Create mock relations
        mock_runs_rel = MagicMock()
        mock_runs_rel.df.return_value = mock_runs_df

        mock_errors_rel = MagicMock()
        mock_errors_rel.df.return_value = mock_empty_df

        mock_warnings_rel = MagicMock()
        mock_warnings_rel.df.return_value = mock_empty_df

        with patch("blq.commands.report_cmd.get_store_for_args") as mock_get_store:
            store = MagicMock()
            store.runs.return_value = mock_runs_rel
            store.errors.return_value = mock_errors_rel
            store.warnings.return_value = mock_warnings_rel
            mock_get_store.return_value = store

            cmd_report(args)

        captured = capsys.readouterr()
        assert "not found" in captured.err
