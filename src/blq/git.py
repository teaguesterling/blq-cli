"""Unified git integration for blq.

This module provides a consistent API for git operations, with support for:
- Basic git context capture (commit, branch, dirty status)
- Extended context via duck_tails DuckDB extension (when available)
- File-level git context (blame, history, diff) for event enrichment

Usage:
    from blq.git import get_context, get_file_context, find_git_root

    # Get current git state
    ctx = get_context()
    print(f"On branch {ctx.branch} at {ctx.commit}")

    # Get git context for a specific file/line
    file_ctx = get_file_context("src/main.py", line=42)
    print(f"Last modified by {file_ctx.last_author}")
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import duckdb

__all__ = [
    "GitContext",
    "GitFileContext",
    "CommitInfo",
    "BlameInfo",
    "get_context",
    "get_file_context",
    "get_blame",
    "get_file_history",
    "find_git_root",
    "is_git_repo",
]


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class GitContext:
    """Git repository state at a point in time.

    Basic fields (commit, branch, dirty) are always populated when in a git repo.
    Extended fields (author, commit_time, message, files_changed) are populated
    when duck_tails is available or via additional subprocess calls.
    """

    # Basic info (always captured)
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None

    # Extended info (with duck_tails or extra subprocess calls)
    author: str | None = None
    commit_time: datetime | None = None
    message: str | None = None
    files_changed: list[str] | None = None

    # Repository info
    remote_url: str | None = None
    repo_root: Path | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for storage/serialization."""
        return {
            "commit": self.commit,
            "branch": self.branch,
            "dirty": self.dirty,
            "author": self.author,
            "commit_time": self.commit_time.isoformat() if self.commit_time else None,
            "message": self.message,
            "files_changed": self.files_changed,
            "remote_url": self.remote_url,
            "repo_root": str(self.repo_root) if self.repo_root else None,
        }


@dataclass
class CommitInfo:
    """Summary of a git commit."""

    hash: str
    short_hash: str
    author: str
    time: datetime
    message: str
    files_changed: list[str] | None = None


@dataclass
class BlameInfo:
    """Blame information for a specific line."""

    commit: str
    author: str
    time: datetime
    line_number: int
    line_content: str


@dataclass
class GitFileContext:
    """Git context for a specific file location.

    Used for event enrichment in `blq inspect --git`.
    """

    path: str
    line: int | None = None

    # Blame info (who last touched this line)
    last_author: str | None = None
    last_commit: str | None = None
    last_modified: datetime | None = None

    # Recent history for this file
    recent_commits: list[CommitInfo] = field(default_factory=list)

    # Changes since a reference point
    changed_since_ref: str | None = None  # reference commit
    diff_summary: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for output."""
        return {
            "path": self.path,
            "line": self.line,
            "last_author": self.last_author,
            "last_commit": self.last_commit,
            "last_modified": (
                self.last_modified.isoformat() if self.last_modified else None
            ),
            "recent_commits": [
                {
                    "hash": c.short_hash,
                    "author": c.author,
                    "time": c.time.isoformat(),
                    "message": c.message,
                }
                for c in self.recent_commits
            ],
            "changed_since_ref": self.changed_since_ref,
            "diff_summary": self.diff_summary,
        }


# =============================================================================
# Provider Protocol
# =============================================================================


class GitProvider(Protocol):
    """Protocol for git data providers."""

    def get_context(self, extended: bool = False) -> GitContext:
        """Get current git repository context."""
        ...

    def get_file_context(
        self,
        path: str,
        line: int | None = None,
        history_limit: int = 5,
    ) -> GitFileContext:
        """Get git context for a specific file."""
        ...

    def get_blame(self, path: str, line: int) -> BlameInfo | None:
        """Get blame info for a specific line."""
        ...

    def get_file_history(self, path: str, limit: int = 5) -> list[CommitInfo]:
        """Get recent commits touching a file."""
        ...


# =============================================================================
# Subprocess Provider (fallback, always available)
# =============================================================================


class SubprocessProvider:
    """Git provider using subprocess calls to git CLI.

    This is the fallback provider when duck_tails is not available.
    """

    def __init__(self, cwd: Path | None = None, timeout: float = 5.0):
        """Initialize subprocess provider.

        Args:
            cwd: Working directory for git commands. Defaults to current directory.
            timeout: Timeout for git commands in seconds.
        """
        self.cwd = cwd or Path.cwd()
        self.timeout = timeout

    def _run_git(self, *args: str) -> str | None:
        """Run a git command and return stdout, or None on failure."""
        try:
            result = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    def get_context(self, extended: bool = False) -> GitContext:
        """Get current git repository context."""
        ctx = GitContext()

        # Basic info
        ctx.commit = self._run_git("rev-parse", "HEAD")
        ctx.branch = self._run_git("rev-parse", "--abbrev-ref", "HEAD")

        # Dirty status
        status = self._run_git("status", "--porcelain")
        if status is not None:
            ctx.dirty = len(status) > 0

        # Repository root
        root = self._run_git("rev-parse", "--show-toplevel")
        if root:
            ctx.repo_root = Path(root)

        if extended and ctx.commit:
            # Extended info requires additional git calls
            # Format: author|timestamp|message
            log_output = self._run_git(
                "log",
                "-1",
                "--format=%an|%at|%s",
                "HEAD",
            )
            if log_output:
                parts = log_output.split("|", 2)
                if len(parts) >= 3:
                    ctx.author = parts[0]
                    try:
                        ctx.commit_time = datetime.fromtimestamp(float(parts[1]))
                    except (ValueError, OSError):
                        pass
                    ctx.message = parts[2]

            # Files changed in HEAD commit
            files_output = self._run_git(
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "HEAD",
            )
            if files_output:
                ctx.files_changed = files_output.splitlines()

            # Remote URL
            ctx.remote_url = self._run_git("remote", "get-url", "origin")

        return ctx

    def get_file_context(
        self,
        path: str,
        line: int | None = None,
        history_limit: int = 5,
    ) -> GitFileContext:
        """Get git context for a specific file."""
        ctx = GitFileContext(path=path, line=line)

        # Get blame info for the specific line
        if line is not None:
            blame = self.get_blame(path, line)
            if blame:
                ctx.last_author = blame.author
                ctx.last_commit = blame.commit
                ctx.last_modified = blame.time

        # Get recent commits for this file
        ctx.recent_commits = self.get_file_history(path, limit=history_limit)

        return ctx

    def get_blame(self, path: str, line: int) -> BlameInfo | None:
        """Get blame info for a specific line."""
        # git blame -L <line>,<line> --porcelain <path>
        output = self._run_git(
            "blame",
            "-L",
            f"{line},{line}",
            "--porcelain",
            path,
        )
        if not output:
            return None

        # Parse porcelain blame output
        lines = output.splitlines()
        if not lines:
            return None

        # First line: <sha> <orig_line> <final_line> <count>
        first_line = lines[0].split()
        if not first_line:
            return None

        commit = first_line[0]
        author = ""
        author_time = None
        line_content = ""

        for blame_line in lines[1:]:
            if blame_line.startswith("author "):
                author = blame_line[7:]
            elif blame_line.startswith("author-time "):
                try:
                    author_time = datetime.fromtimestamp(int(blame_line[12:]))
                except (ValueError, OSError):
                    pass
            elif blame_line.startswith("\t"):
                line_content = blame_line[1:]

        if not author_time:
            author_time = datetime.now()

        return BlameInfo(
            commit=commit[:8],
            author=author,
            time=author_time,
            line_number=line,
            line_content=line_content,
        )

    def get_file_history(self, path: str, limit: int = 5) -> list[CommitInfo]:
        """Get recent commits touching a file."""
        # git log --format="%H|%h|%an|%at|%s" -n <limit> -- <path>
        output = self._run_git(
            "log",
            "--format=%H|%h|%an|%at|%s",
            f"-n{limit}",
            "--",
            path,
        )
        if not output:
            return []

        commits = []
        for line in output.splitlines():
            parts = line.split("|", 4)
            if len(parts) >= 5:
                try:
                    commit_time = datetime.fromtimestamp(float(parts[3]))
                except (ValueError, OSError):
                    commit_time = datetime.now()

                commits.append(
                    CommitInfo(
                        hash=parts[0],
                        short_hash=parts[1],
                        author=parts[2],
                        time=commit_time,
                        message=parts[4],
                    )
                )

        return commits


# =============================================================================
# DuckTails Provider (optional, when extension is loaded)
# =============================================================================


class DuckTailsProvider:
    """Git provider using duck_tails DuckDB extension.

    Provides SQL-native git queries with better performance for bulk operations.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, cwd: Path | None = None):
        """Initialize duck_tails provider.

        Args:
            conn: DuckDB connection with duck_tails loaded.
            cwd: Working directory. Defaults to current directory.
        """
        self.conn = conn
        self.cwd = cwd or Path.cwd()
        self._subprocess = SubprocessProvider(cwd=self.cwd)

    def get_context(self, extended: bool = False) -> GitContext:
        """Get current git repository context using duck_tails."""
        ctx = GitContext()

        try:
            # Get HEAD commit info
            result = self.conn.execute("""
                SELECT
                    commit_hash,
                    author,
                    commit_time,
                    message
                FROM git_log()
                LIMIT 1
            """).fetchone()

            if result:
                ctx.commit = result[0]
                ctx.author = result[1]
                ctx.commit_time = result[2]
                ctx.message = result[3]

            # Get current branch
            branch_result = self.conn.execute("""
                SELECT name
                FROM git_branches()
                WHERE is_head = true
            """).fetchone()

            if branch_result:
                ctx.branch = branch_result[0]

        except Exception:
            # Fall back to subprocess for basic info
            basic = self._subprocess.get_context(extended=False)
            ctx.commit = basic.commit
            ctx.branch = basic.branch

        # Dirty status - duck_tails may not expose this, use subprocess
        ctx.dirty = self._subprocess.get_context().dirty

        # Repository root
        root = self._subprocess._run_git("rev-parse", "--show-toplevel")
        if root:
            ctx.repo_root = Path(root)

        if extended and ctx.commit:
            try:
                # Files changed in HEAD
                files_result = self.conn.execute("""
                    SELECT files_changed
                    FROM git_log()
                    LIMIT 1
                """).fetchone()

                if files_result and files_result[0]:
                    ctx.files_changed = list(files_result[0])
            except Exception:
                pass

            # Remote URL via subprocess
            ctx.remote_url = self._subprocess._run_git("remote", "get-url", "origin")

        return ctx

    def get_file_context(
        self,
        path: str,
        line: int | None = None,
        history_limit: int = 5,
    ) -> GitFileContext:
        """Get git context for a specific file using duck_tails."""
        ctx = GitFileContext(path=path, line=line)

        # Blame - fall back to subprocess (duck_tails may not have blame)
        if line is not None:
            blame = self._subprocess.get_blame(path, line)
            if blame:
                ctx.last_author = blame.author
                ctx.last_commit = blame.commit
                ctx.last_modified = blame.time

        # Recent commits - try duck_tails first
        try:
            result = self.conn.execute(
                """
                SELECT
                    commit_hash,
                    substr(commit_hash, 1, 7) as short_hash,
                    author,
                    commit_time,
                    message
                FROM git_log()
                WHERE list_contains(files_changed, ?)
                ORDER BY commit_time DESC
                LIMIT ?
            """,
                [path, history_limit],
            ).fetchall()

            for row in result:
                ctx.recent_commits.append(
                    CommitInfo(
                        hash=row[0],
                        short_hash=row[1],
                        author=row[2],
                        time=row[3],
                        message=row[4],
                    )
                )
        except Exception:
            # Fall back to subprocess
            ctx.recent_commits = self._subprocess.get_file_history(path, history_limit)

        return ctx

    def get_blame(self, path: str, line: int) -> BlameInfo | None:
        """Get blame info - delegates to subprocess."""
        return self._subprocess.get_blame(path, line)

    def get_file_history(self, path: str, limit: int = 5) -> list[CommitInfo]:
        """Get recent commits touching a file."""
        try:
            result = self.conn.execute(
                """
                SELECT
                    commit_hash,
                    substr(commit_hash, 1, 7) as short_hash,
                    author,
                    commit_time,
                    message
                FROM git_log()
                WHERE list_contains(files_changed, ?)
                ORDER BY commit_time DESC
                LIMIT ?
            """,
                [path, limit],
            ).fetchall()

            return [
                CommitInfo(
                    hash=row[0],
                    short_hash=row[1],
                    author=row[2],
                    time=row[3],
                    message=row[4],
                )
                for row in result
            ]
        except Exception:
            return self._subprocess.get_file_history(path, limit)


# =============================================================================
# Provider Selection
# =============================================================================


def _get_provider(
    conn: duckdb.DuckDBPyConnection | None = None,
    cwd: Path | None = None,
) -> GitProvider:
    """Get the best available git provider.

    Prefers duck_tails if:
    1. A DuckDB connection is provided
    2. duck_tails extension is available and loaded

    Falls back to subprocess otherwise.
    """
    if conn is not None:
        try:
            # Check if duck_tails is loaded by running a simple query
            conn.execute("SELECT * FROM git_log() LIMIT 0")
            return DuckTailsProvider(conn, cwd=cwd)
        except Exception:
            pass

    return SubprocessProvider(cwd=cwd)


# =============================================================================
# Public API
# =============================================================================


def get_context(
    conn: duckdb.DuckDBPyConnection | None = None,
    extended: bool = False,
    cwd: Path | None = None,
) -> GitContext:
    """Get current git repository context.

    Args:
        conn: Optional DuckDB connection for duck_tails support.
        extended: If True, capture additional info (author, message, files_changed).
        cwd: Working directory. Defaults to current directory.

    Returns:
        GitContext with repository state. Fields are None if not in a git repo.
    """
    provider = _get_provider(conn, cwd)
    return provider.get_context(extended=extended)


def get_file_context(
    path: str,
    line: int | None = None,
    history_limit: int = 5,
    conn: duckdb.DuckDBPyConnection | None = None,
    cwd: Path | None = None,
) -> GitFileContext:
    """Get git context for a specific file location.

    Args:
        path: Path to the file (relative to repo root).
        line: Optional line number for blame info.
        history_limit: Maximum number of recent commits to include.
        conn: Optional DuckDB connection for duck_tails support.
        cwd: Working directory. Defaults to current directory.

    Returns:
        GitFileContext with blame and history info.
    """
    provider = _get_provider(conn, cwd)
    return provider.get_file_context(path, line, history_limit)


def get_blame(
    path: str,
    line: int,
    conn: duckdb.DuckDBPyConnection | None = None,
    cwd: Path | None = None,
) -> BlameInfo | None:
    """Get blame info for a specific line.

    Args:
        path: Path to the file.
        line: Line number.
        conn: Optional DuckDB connection for duck_tails support.
        cwd: Working directory. Defaults to current directory.

    Returns:
        BlameInfo or None if not available.
    """
    provider = _get_provider(conn, cwd)
    return provider.get_blame(path, line)


def get_file_history(
    path: str,
    limit: int = 5,
    conn: duckdb.DuckDBPyConnection | None = None,
    cwd: Path | None = None,
) -> list[CommitInfo]:
    """Get recent commits touching a file.

    Args:
        path: Path to the file.
        limit: Maximum number of commits to return.
        conn: Optional DuckDB connection for duck_tails support.
        cwd: Working directory. Defaults to current directory.

    Returns:
        List of CommitInfo for recent commits.
    """
    provider = _get_provider(conn, cwd)
    return provider.get_file_history(path, limit)


def find_git_root(start: Path | None = None) -> Path | None:
    """Find the git repository root from a starting directory.

    Args:
        start: Starting directory. Defaults to current directory.

    Returns:
        Path to repository root (.git's parent), or None if not in a git repo.
    """
    cwd = start or Path.cwd()
    for p in [cwd, *list(cwd.parents)]:
        git_dir = p / ".git"
        if git_dir.is_dir():
            return p
    return None


def find_git_dir(start: Path | None = None) -> Path | None:
    """Find the .git directory from a starting directory.

    Args:
        start: Starting directory. Defaults to current directory.

    Returns:
        Path to .git directory, or None if not in a git repo.
    """
    cwd = start or Path.cwd()
    for p in [cwd, *list(cwd.parents)]:
        git_dir = p / ".git"
        if git_dir.is_dir():
            return git_dir
    return None


def is_git_repo(path: Path | None = None) -> bool:
    """Check if a directory is inside a git repository.

    Args:
        path: Directory to check. Defaults to current directory.

    Returns:
        True if inside a git repository.
    """
    return find_git_root(path) is not None


# =============================================================================
# Backward Compatibility
# =============================================================================


@dataclass
class GitInfo:
    """Git repository state at time of run.

    Deprecated: Use GitContext instead. This class is provided for backward
    compatibility with existing code that uses capture_git_info().
    """

    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None


def capture_git_info() -> GitInfo:
    """Capture current git repository state.

    Deprecated: Use get_context() instead.

    Returns:
        GitInfo with commit hash, branch name, and dirty status.
        Fields are None if not in a git repo or git not available.
    """
    ctx = get_context()
    return GitInfo(
        commit=ctx.commit,
        branch=ctx.branch,
        dirty=ctx.dirty,
    )
