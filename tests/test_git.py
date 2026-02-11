"""Tests for blq.git module."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blq.git import (
    BlameInfo,
    CommitInfo,
    GitContext,
    GitFileContext,
    SubprocessProvider,
    capture_git_info,
    find_git_dir,
    find_git_root,
    get_blame,
    get_context,
    get_file_context,
    get_file_history,
    is_git_repo,
)


class TestGitContext:
    """Tests for GitContext dataclass."""

    def test_default_values(self):
        """Test default values are None."""
        ctx = GitContext()
        assert ctx.commit is None
        assert ctx.branch is None
        assert ctx.dirty is None
        assert ctx.author is None
        assert ctx.commit_time is None
        assert ctx.message is None
        assert ctx.files_changed is None
        assert ctx.remote_url is None
        assert ctx.repo_root is None

    def test_to_dict(self):
        """Test to_dict serialization."""
        ctx = GitContext(
            commit="abc123",
            branch="main",
            dirty=False,
            author="Test User",
            commit_time=datetime(2024, 1, 15, 10, 30, 0),
            message="Test commit",
            files_changed=["file1.py", "file2.py"],
            remote_url="https://github.com/test/repo.git",
            repo_root=Path("/path/to/repo"),
        )
        d = ctx.to_dict()

        assert d["commit"] == "abc123"
        assert d["branch"] == "main"
        assert d["dirty"] is False
        assert d["author"] == "Test User"
        assert d["commit_time"] == "2024-01-15T10:30:00"
        assert d["message"] == "Test commit"
        assert d["files_changed"] == ["file1.py", "file2.py"]
        assert d["remote_url"] == "https://github.com/test/repo.git"
        assert d["repo_root"] == "/path/to/repo"

    def test_to_dict_with_none_values(self):
        """Test to_dict with None values."""
        ctx = GitContext()
        d = ctx.to_dict()

        assert d["commit"] is None
        assert d["commit_time"] is None
        assert d["repo_root"] is None


class TestGitFileContext:
    """Tests for GitFileContext dataclass."""

    def test_default_values(self):
        """Test default values."""
        ctx = GitFileContext(path="test.py")
        assert ctx.path == "test.py"
        assert ctx.line is None
        assert ctx.last_author is None
        assert ctx.recent_commits == []

    def test_to_dict(self):
        """Test to_dict serialization."""
        ctx = GitFileContext(
            path="test.py",
            line=42,
            last_author="Alice",
            last_commit="abc123",
            last_modified=datetime(2024, 1, 15, 10, 30, 0),
            recent_commits=[
                CommitInfo(
                    hash="abc123456789",
                    short_hash="abc1234",
                    author="Alice",
                    time=datetime(2024, 1, 15, 10, 30, 0),
                    message="Fix bug",
                )
            ],
        )
        d = ctx.to_dict()

        assert d["path"] == "test.py"
        assert d["line"] == 42
        assert d["last_author"] == "Alice"
        assert len(d["recent_commits"]) == 1
        assert d["recent_commits"][0]["hash"] == "abc1234"


class TestSubprocessProvider:
    """Tests for SubprocessProvider."""

    def test_run_git_success(self):
        """Test successful git command execution."""
        provider = SubprocessProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="abc123\n",
            )
            result = provider._run_git("rev-parse", "HEAD")
            assert result == "abc123"

    def test_run_git_failure(self):
        """Test failed git command returns None."""
        provider = SubprocessProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
            )
            result = provider._run_git("rev-parse", "HEAD")
            assert result is None

    def test_run_git_timeout(self):
        """Test timeout returns None."""
        provider = SubprocessProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("git", 5)
            result = provider._run_git("rev-parse", "HEAD")
            assert result is None

    def test_run_git_file_not_found(self):
        """Test git not found returns None."""
        provider = SubprocessProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = provider._run_git("rev-parse", "HEAD")
            assert result is None

    def test_get_context_basic(self):
        """Test basic context capture."""
        provider = SubprocessProvider()
        with patch.object(provider, "_run_git") as mock_git:
            mock_git.side_effect = [
                "abc123def456",  # commit
                "main",  # branch
                "",  # status (clean)
                "/path/to/repo",  # show-toplevel
            ]
            ctx = provider.get_context()

            assert ctx.commit == "abc123def456"
            assert ctx.branch == "main"
            assert ctx.dirty is False
            assert ctx.repo_root == Path("/path/to/repo")

    def test_get_context_dirty(self):
        """Test dirty detection."""
        provider = SubprocessProvider()
        with patch.object(provider, "_run_git") as mock_git:
            mock_git.side_effect = [
                "abc123",  # commit
                "main",  # branch
                " M file.py\n",  # status (dirty)
                "/path/to/repo",  # show-toplevel
            ]
            ctx = provider.get_context()

            assert ctx.dirty is True

    def test_get_context_extended(self):
        """Test extended context capture."""
        provider = SubprocessProvider()
        with patch.object(provider, "_run_git") as mock_git:
            mock_git.side_effect = [
                "abc123",  # commit
                "main",  # branch
                "",  # status
                "/path/to/repo",  # show-toplevel
                "Alice|1705318200|Test commit",  # log
                "file1.py\nfile2.py",  # diff-tree
                "https://github.com/test/repo.git",  # remote
            ]
            ctx = provider.get_context(extended=True)

            assert ctx.author == "Alice"
            assert ctx.message == "Test commit"
            assert ctx.files_changed == ["file1.py", "file2.py"]
            assert ctx.remote_url == "https://github.com/test/repo.git"

    def test_get_blame(self):
        """Test blame parsing."""
        provider = SubprocessProvider()
        blame_output = """abc123def456 42 42 1
author Alice
author-mail <alice@example.com>
author-time 1705318200
author-tz +0000
committer Alice
committer-mail <alice@example.com>
committer-time 1705318200
committer-tz +0000
summary Test commit
filename test.py
\t    some code here"""

        with patch.object(provider, "_run_git") as mock_git:
            mock_git.return_value = blame_output
            blame = provider.get_blame("test.py", 42)

            assert blame is not None
            assert blame.author == "Alice"
            assert blame.commit == "abc123de"
            assert blame.line_number == 42
            assert blame.line_content == "    some code here"

    def test_get_blame_not_found(self):
        """Test blame returns None when file not found."""
        provider = SubprocessProvider()
        with patch.object(provider, "_run_git") as mock_git:
            mock_git.return_value = None
            blame = provider.get_blame("nonexistent.py", 42)

            assert blame is None

    def test_get_file_history(self):
        """Test file history parsing."""
        provider = SubprocessProvider()
        log_output = """abc123|abc1234|Alice|1705318200|First commit
def456|def4567|Bob|1705318100|Second commit"""

        with patch.object(provider, "_run_git") as mock_git:
            mock_git.return_value = log_output
            history = provider.get_file_history("test.py", limit=5)

            assert len(history) == 2
            assert history[0].hash == "abc123"
            assert history[0].short_hash == "abc1234"
            assert history[0].author == "Alice"
            assert history[0].message == "First commit"
            assert history[1].author == "Bob"

    def test_get_file_history_empty(self):
        """Test empty file history."""
        provider = SubprocessProvider()
        with patch.object(provider, "_run_git") as mock_git:
            mock_git.return_value = None
            history = provider.get_file_history("test.py")

            assert history == []


class TestPublicAPI:
    """Tests for public API functions."""

    def test_get_context_uses_provider(self):
        """Test get_context delegates to provider."""
        with patch("blq.git.SubprocessProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            mock_instance.get_context.return_value = GitContext(commit="abc123")

            ctx = get_context()

            assert ctx.commit == "abc123"
            mock_instance.get_context.assert_called_once_with(extended=False)

    def test_get_file_context_uses_provider(self):
        """Test get_file_context delegates to provider."""
        with patch("blq.git.SubprocessProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            mock_instance.get_file_context.return_value = GitFileContext(
                path="test.py"
            )

            ctx = get_file_context("test.py", line=42)

            assert ctx.path == "test.py"
            mock_instance.get_file_context.assert_called_once_with("test.py", 42, 5)

    def test_get_blame_uses_provider(self):
        """Test get_blame delegates to provider."""
        with patch("blq.git.SubprocessProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            mock_instance.get_blame.return_value = BlameInfo(
                commit="abc123",
                author="Alice",
                time=datetime.now(),
                line_number=42,
                line_content="code",
            )

            blame = get_blame("test.py", 42)

            assert blame is not None
            assert blame.author == "Alice"

    def test_get_file_history_uses_provider(self):
        """Test get_file_history delegates to provider."""
        with patch("blq.git.SubprocessProvider") as MockProvider:
            mock_instance = MockProvider.return_value
            mock_instance.get_file_history.return_value = [
                CommitInfo(
                    hash="abc123",
                    short_hash="abc1234",
                    author="Alice",
                    time=datetime.now(),
                    message="Test",
                )
            ]

            history = get_file_history("test.py", limit=10)

            assert len(history) == 1
            mock_instance.get_file_history.assert_called_once_with("test.py", 10)


class TestFindGitRoot:
    """Tests for find_git_root function."""

    def test_find_git_root_in_git_repo(self, tmp_path: Path):
        """Test finding git root in a git repository."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        result = find_git_root(tmp_path)
        assert result == tmp_path

    def test_find_git_root_in_subdirectory(self, tmp_path: Path):
        """Test finding git root from a subdirectory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        subdir = tmp_path / "src" / "subdir"
        subdir.mkdir(parents=True)

        result = find_git_root(subdir)
        assert result == tmp_path

    def test_find_git_root_not_in_repo(self, tmp_path: Path):
        """Test returns None when not in a git repo."""
        result = find_git_root(tmp_path)
        assert result is None


class TestFindGitDir:
    """Tests for find_git_dir function."""

    def test_find_git_dir_in_git_repo(self, tmp_path: Path):
        """Test finding .git directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        result = find_git_dir(tmp_path)
        assert result == git_dir

    def test_find_git_dir_not_in_repo(self, tmp_path: Path):
        """Test returns None when not in a git repo."""
        result = find_git_dir(tmp_path)
        assert result is None


class TestIsGitRepo:
    """Tests for is_git_repo function."""

    def test_is_git_repo_true(self, tmp_path: Path):
        """Test returns True in git repo."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        assert is_git_repo(tmp_path) is True

    def test_is_git_repo_false(self, tmp_path: Path):
        """Test returns False outside git repo."""
        assert is_git_repo(tmp_path) is False


class TestBackwardCompatibility:
    """Tests for backward compatibility functions."""

    def test_capture_git_info(self):
        """Test capture_git_info returns GitInfo."""
        with patch("blq.git.get_context") as mock_get_context:
            mock_get_context.return_value = GitContext(
                commit="abc123",
                branch="main",
                dirty=False,
            )

            info = capture_git_info()

            assert info.commit == "abc123"
            assert info.branch == "main"
            assert info.dirty is False

    def test_capture_git_info_with_none_values(self):
        """Test capture_git_info handles None values."""
        with patch("blq.git.get_context") as mock_get_context:
            mock_get_context.return_value = GitContext()

            info = capture_git_info()

            assert info.commit is None
            assert info.branch is None
            assert info.dirty is None


class TestIntegration:
    """Integration tests that use real git commands."""

    def test_get_context_in_real_repo(self):
        """Test get_context in the actual blq repository."""
        # This test runs in the blq repo itself
        ctx = get_context()

        # We should be able to get basic info
        # (may be None if not in a git repo during CI, but shouldn't error)
        if ctx.commit:
            assert len(ctx.commit) == 40  # Full SHA
        if ctx.branch:
            assert isinstance(ctx.branch, str)

    def test_is_git_repo_in_real_repo(self):
        """Test is_git_repo in the actual blq repository."""
        # This should be True since tests run in the blq repo
        result = is_git_repo()
        # This could be False in some CI environments, so just check it doesn't error
        assert isinstance(result, bool)

    def test_find_git_root_in_real_repo(self):
        """Test find_git_root in the actual blq repository."""
        root = find_git_root()

        if root is not None:
            # Should find a .git directory
            assert (root / ".git").exists() or (root / ".git").is_file()
