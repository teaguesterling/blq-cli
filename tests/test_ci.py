"""Tests for CI integration commands."""

import argparse
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from blq.commands.ci_cmd import (
    DiffResult,
    _compute_diff,
    _find_baseline_run,
    _format_json_output,
    _format_location,
    _format_pr_comment,
    _get_github_context,
    cmd_ci_check,
    cmd_ci_comment,
)
from blq.github import GitHubClient


class TestDiffResult:
    """Tests for DiffResult dataclass."""

    def test_has_new_errors_true(self):
        """has_new_errors returns True when there are new errors."""
        diff = DiffResult(
            baseline_run_id=1,
            current_run_id=2,
            baseline_errors=5,
            current_errors=6,
            fixed=[],
            new_errors=[{"fingerprint": "abc123"}],
        )
        assert diff.has_new_errors is True

    def test_has_new_errors_false(self):
        """has_new_errors returns False when no new errors."""
        diff = DiffResult(
            baseline_run_id=1,
            current_run_id=2,
            baseline_errors=5,
            current_errors=4,
            fixed=[{"fingerprint": "abc123"}],
            new_errors=[],
        )
        assert diff.has_new_errors is False

    def test_delta(self):
        """delta returns difference in error counts."""
        diff = DiffResult(
            baseline_run_id=1,
            current_run_id=2,
            baseline_errors=5,
            current_errors=3,
            fixed=[],
            new_errors=[],
        )
        assert diff.delta == -2


class TestFindBaselineRun:
    """Tests for _find_baseline_run function."""

    def test_find_by_run_id(self, initialized_project):
        """Find baseline by numeric run ID."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        # Create a mock runs DataFrame
        import pandas as pd

        mock_runs = pd.DataFrame(
            {
                "run_id": [1, 2, 3],
                "git_branch": ["main", "feature", "main"],
                "git_commit": [None, None, None],
            }
        )
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, "2")
            assert result == 2

    def test_find_by_branch(self, initialized_project):
        """Find baseline by branch name."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        import pandas as pd

        mock_runs = pd.DataFrame(
            {
                "run_id": [1, 2, 3],
                "git_branch": ["main", "feature", "main"],
                "git_commit": [None, None, None],
            }
        )
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, "feature")
            assert result == 2

    def test_find_by_commit_sha(self, initialized_project):
        """Find baseline by commit SHA prefix."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        import pandas as pd

        mock_runs = pd.DataFrame(
            {
                "run_id": [1, 2, 3],
                "git_branch": ["main", "feature", "main"],
                "git_commit": ["abc123def456789", "def456abc123789", "789xyz123"],
            }
        )
        with patch.object(store, "runs", return_value=mock_runs):
            # Use 7+ char prefix to match regex
            result = _find_baseline_run(store, "abc123d")
            assert result == 1

    def test_default_to_main(self, initialized_project):
        """Default to main branch when no baseline specified."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        import pandas as pd

        mock_runs = pd.DataFrame(
            {
                "run_id": [1, 2, 3],
                "git_branch": ["main", "feature", "develop"],
                "git_commit": [None, None, None],
            }
        )
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, None)
            assert result == 1

    def test_fallback_to_master(self, initialized_project):
        """Fall back to master branch if main not found."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        import pandas as pd

        mock_runs = pd.DataFrame(
            {
                "run_id": [1, 2, 3],
                "git_branch": ["master", "feature", "develop"],
                "git_commit": [None, None, None],
            }
        )
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, None)
            assert result == 1

    def test_no_runs_returns_none(self, initialized_project):
        """Return None when no runs exist."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        import pandas as pd

        mock_runs = pd.DataFrame(columns=["run_id", "git_branch", "git_commit"])
        with patch.object(store, "runs", return_value=mock_runs):
            result = _find_baseline_run(store, "main")
            assert result is None


class TestComputeDiff:
    """Tests for _compute_diff function."""

    def test_diff_with_new_errors(self, initialized_project):
        """Compute diff showing new errors."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        # Mock the run queries
        import pandas as pd

        baseline_df = pd.DataFrame(
            [{"fingerprint": "fp1", "file_path": "a.py", "line_number": 1, "message": "err1"}]
        )
        current_df = pd.DataFrame(
            [
                {"fingerprint": "fp1", "file_path": "a.py", "line_number": 1, "message": "err1"},
                {"fingerprint": "fp2", "file_path": "b.py", "line_number": 2, "message": "err2"},
            ]
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.side_effect = [baseline_df, current_df]

        with patch.object(store, "run", return_value=mock_query):
            diff = _compute_diff(store, 1, 2)

        assert diff.baseline_errors == 1
        assert diff.current_errors == 2
        assert len(diff.new_errors) == 1
        assert diff.new_errors[0]["fingerprint"] == "fp2"
        assert len(diff.fixed) == 0

    def test_diff_with_fixed_errors(self, initialized_project):
        """Compute diff showing fixed errors."""
        from blq.commands.core import BlqConfig
        from blq.query import LogStore

        config = BlqConfig.find()
        store = LogStore(config.lq_dir)

        import pandas as pd

        baseline_df = pd.DataFrame(
            [
                {"fingerprint": "fp1", "file_path": "a.py", "line_number": 1, "message": "err1"},
                {"fingerprint": "fp2", "file_path": "b.py", "line_number": 2, "message": "err2"},
            ]
        )
        current_df = pd.DataFrame(
            [{"fingerprint": "fp1", "file_path": "a.py", "line_number": 1, "message": "err1"}]
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.side_effect = [baseline_df, current_df]

        with patch.object(store, "run", return_value=mock_query):
            diff = _compute_diff(store, 1, 2)

        assert diff.baseline_errors == 2
        assert diff.current_errors == 1
        assert len(diff.fixed) == 1
        assert diff.fixed[0]["fingerprint"] == "fp2"
        assert len(diff.new_errors) == 0


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


class TestFormatPRComment:
    """Tests for _format_pr_comment function."""

    def test_format_with_new_errors(self):
        """Format comment with new errors."""
        diff = DiffResult(
            baseline_run_id=1,
            current_run_id=2,
            baseline_errors=5,
            current_errors=6,
            fixed=[],
            new_errors=[{"file_path": "a.py", "line_number": 10, "message": "Error message"}],
        )
        result = _format_pr_comment(diff)
        assert "## Build Log Analysis" in result
        assert "### New Errors" in result
        assert "`a.py:10`" in result
        assert "Error message" in result
        assert ":x: New errors introduced" in result

    def test_format_with_fixed_errors(self):
        """Format comment with fixed errors."""
        diff = DiffResult(
            baseline_run_id=1,
            current_run_id=2,
            baseline_errors=5,
            current_errors=4,
            fixed=[{"file_path": "b.py", "line_number": 20, "message": "Fixed error"}],
            new_errors=[],
        )
        result = _format_pr_comment(diff, include_fixed=True)
        assert "<details>" in result
        assert "Fixed Errors (1)" in result
        assert ":white_check_mark:" in result

    def test_format_no_baseline(self):
        """Format comment without baseline."""
        diff = DiffResult(
            baseline_run_id=None,
            current_run_id=2,
            baseline_errors=0,
            current_errors=3,
            fixed=[],
            new_errors=[],
        )
        result = _format_pr_comment(diff)
        assert "Current run" in result
        assert "Baseline run" not in result


class TestFormatJsonOutput:
    """Tests for _format_json_output function."""

    def test_json_output_structure(self):
        """JSON output has correct structure."""
        diff = DiffResult(
            baseline_run_id=1,
            current_run_id=2,
            baseline_errors=5,
            current_errors=6,
            fixed=[
                {"file_path": "a.py", "line_number": 1, "message": "fixed", "fingerprint": "fp1"}
            ],
            new_errors=[
                {"file_path": "b.py", "line_number": 2, "message": "new", "fingerprint": "fp2"}
            ],
        )
        result = json.loads(_format_json_output(diff))

        assert result["baseline_run_id"] == 1
        assert result["current_run_id"] == 2
        assert result["baseline_errors"] == 5
        assert result["current_errors"] == 6
        assert result["fixed_count"] == 1
        assert result["new_count"] == 1
        assert result["has_new_errors"] is True
        assert result["delta"] == 1
        assert len(result["new_errors"]) == 1
        assert len(result["fixed"]) == 1


class TestGetGithubContext:
    """Tests for _get_github_context function."""

    def test_extracts_pr_from_ref(self):
        """Extract PR number from GITHUB_REF."""
        with patch.dict(
            os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_REF": "refs/pull/123/merge"}
        ):
            repo, pr = _get_github_context()
            assert repo == "owner/repo"
            assert pr == 123

    def test_extracts_pr_from_env_var(self):
        """Extract PR number from GITHUB_PR_NUMBER."""
        with patch.dict(
            os.environ,
            {
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_PR_NUMBER": "456",
            },
            clear=True,
        ):
            repo, pr = _get_github_context()
            assert repo == "owner/repo"
            assert pr == 456

    def test_no_repo_returns_none(self):
        """Return None when GITHUB_REPOSITORY not set."""
        with patch.dict(os.environ, {}, clear=True):
            repo, pr = _get_github_context()
            assert repo is None
            assert pr is None


class TestGitHubClient:
    """Tests for GitHubClient class."""

    def test_create_comment(self):
        """Create comment makes correct API call."""
        client = GitHubClient("test-token")

        mock_response = {"id": 12345}
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = client.create_comment("owner/repo", 123, "Test body")

            mock_req.assert_called_once_with(
                "POST", "/repos/owner/repo/issues/123/comments", {"body": "Test body"}
            )
            assert result == 12345

    def test_update_comment(self):
        """Update comment makes correct API call."""
        client = GitHubClient("test-token")

        with patch.object(client, "_request", return_value=None) as mock_req:
            client.update_comment("owner/repo", 12345, "Updated body")

            mock_req.assert_called_once_with(
                "PATCH", "/repos/owner/repo/issues/comments/12345", {"body": "Updated body"}
            )

    def test_find_comment(self):
        """Find comment searches for marker."""
        client = GitHubClient("test-token")

        mock_comments = [
            {"id": 1, "body": "unrelated comment"},
            {"id": 2, "body": "<!-- blq-ci-comment -->\nBuild report"},
            {"id": 3, "body": "another comment"},
        ]
        with patch.object(client, "_request", return_value=mock_comments):
            result = client.find_comment("owner/repo", 123, "<!-- blq-ci-comment -->")

            assert result == 2

    def test_find_comment_not_found(self):
        """Find comment returns None when not found."""
        client = GitHubClient("test-token")

        mock_comments = [{"id": 1, "body": "unrelated comment"}]
        with patch.object(client, "_request", return_value=mock_comments):
            result = client.find_comment("owner/repo", 123, "<!-- blq-ci-comment -->")

            assert result is None


class TestCmdCiCheck:
    """Tests for cmd_ci_check command."""

    def test_check_exits_0_no_new_errors(self, initialized_project, capsys):
        """Exit 0 when no new errors."""
        args = argparse.Namespace(
            baseline=None,
            fail_on_any=False,
            json=False,
            global_=False,
            database=None,
        )

        import pandas as pd

        mock_runs = pd.DataFrame(
            {"run_id": [1, 2], "git_branch": ["main", "feature"], "git_commit": [None, None]}
        )
        mock_df = pd.DataFrame([{"fingerprint": "fp1"}])

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.return_value = mock_df

        with patch("blq.commands.ci_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            with pytest.raises(SystemExit) as exc_info:
                cmd_ci_check(args)

            assert exc_info.value.code == 0

    def test_check_exits_1_with_new_errors(self, initialized_project, capsys):
        """Exit 1 when new errors found."""
        args = argparse.Namespace(
            baseline=None,
            fail_on_any=False,
            json=False,
            global_=False,
            database=None,
        )

        import pandas as pd

        mock_runs = pd.DataFrame(
            {"run_id": [1, 2], "git_branch": ["main", "feature"], "git_commit": [None, None]}
        )
        baseline_df = pd.DataFrame([{"fingerprint": "fp1"}])
        current_df = pd.DataFrame(
            [
                {"fingerprint": "fp1"},
                {"fingerprint": "fp2", "file_path": "a.py", "line_number": 1, "message": "new"},
            ]
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.side_effect = [baseline_df, current_df]

        with patch("blq.commands.ci_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            with pytest.raises(SystemExit) as exc_info:
                cmd_ci_check(args)

            assert exc_info.value.code == 1

    def test_check_fail_on_any(self, initialized_project):
        """--fail-on-any checks absolute error count."""
        args = argparse.Namespace(
            baseline=None,
            fail_on_any=True,
            json=False,
            global_=False,
            database=None,
        )

        import pandas as pd

        mock_runs = pd.DataFrame({"run_id": [1], "git_branch": ["main"], "git_commit": [None]})

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 5

        with patch("blq.commands.ci_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            with pytest.raises(SystemExit) as exc_info:
                cmd_ci_check(args)

            # Should fail because there are errors
            assert exc_info.value.code == 1

    def test_check_json_output(self, initialized_project, capsys):
        """--json outputs JSON format."""
        args = argparse.Namespace(
            baseline="1",
            fail_on_any=False,
            json=True,
            global_=False,
            database=None,
        )

        import pandas as pd

        mock_runs = pd.DataFrame(
            {"run_id": [1, 2], "git_branch": ["main", "feature"], "git_commit": [None, None]}
        )
        mock_df = pd.DataFrame([{"fingerprint": "fp1"}])

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.return_value = mock_df

        with patch("blq.commands.ci_cmd.get_store_for_args") as mock_store:
            store = MagicMock()
            store.runs.return_value = mock_runs
            store.run.return_value = mock_query
            mock_store.return_value = store

            with pytest.raises(SystemExit):
                cmd_ci_check(args)

            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert "baseline_run_id" in result
            assert "current_run_id" in result


class TestCmdCiComment:
    """Tests for cmd_ci_comment command."""

    def test_comment_requires_token(self, initialized_project, capsys):
        """Error when GITHUB_TOKEN not set."""
        args = argparse.Namespace(
            update=False,
            diff=False,
            baseline=None,
            global_=False,
            database=None,
        )

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                cmd_ci_comment(args)

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "GITHUB_TOKEN" in captured.err

    def test_comment_requires_repo(self, initialized_project, capsys):
        """Error when GITHUB_REPOSITORY not set."""
        args = argparse.Namespace(
            update=False,
            diff=False,
            baseline=None,
            global_=False,
            database=None,
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                cmd_ci_comment(args)

            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "GITHUB_REPOSITORY" in captured.err

    def test_comment_creates_pr_comment(self, initialized_project, capsys):
        """Creates PR comment with correct content."""
        args = argparse.Namespace(
            update=False,
            diff=False,
            baseline=None,
            global_=False,
            database=None,
        )

        import pandas as pd

        mock_runs = pd.DataFrame({"run_id": [1], "git_branch": ["main"], "git_commit": [None]})
        mock_df = pd.DataFrame(
            [{"fingerprint": "fp1", "file_path": "a.py", "line_number": 1, "message": "test"}]
        )

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.df.return_value = mock_df

        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_REF": "refs/pull/123/merge",
            },
        ):
            with patch("blq.commands.ci_cmd.get_store_for_args") as mock_store:
                store = MagicMock()
                store.runs.return_value = mock_runs
                store.run.return_value = mock_query
                mock_store.return_value = store

                # Patch GitHubClient where it's imported
                with patch("blq.github.GitHubClient") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_client_cls.return_value = mock_client
                    mock_client.create_comment.return_value = 12345

                    cmd_ci_comment(args)

                    mock_client.create_comment.assert_called_once()
                    call_args = mock_client.create_comment.call_args
                    assert call_args[0][0] == "owner/repo"
                    assert call_args[0][1] == 123
                    assert "Build Log Analysis" in call_args[0][2]

        captured = capsys.readouterr()
        assert "Created comment" in captured.out
