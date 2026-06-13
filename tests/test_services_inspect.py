"""Tests for inspect service."""

from __future__ import annotations

from pathlib import Path

from blq.services.inspect import get_source_context


class TestGetSourceContext:
    def test_returns_context_for_existing_file(self, tmp_path: Path):
        src = tmp_path / "foo.py"
        src.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = get_source_context(
            ref_file="foo.py", ref_line=3, source_root=tmp_path, context_lines=1
        )
        assert result is not None
        assert "line3" in result

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        result = get_source_context(ref_file="nonexistent.py", ref_line=1, source_root=tmp_path)
        assert result is None

    def test_returns_none_without_ref_line(self, tmp_path: Path):
        (tmp_path / "foo.py").write_text("line1\n")
        result = get_source_context(ref_file="foo.py", ref_line=None, source_root=tmp_path)
        assert result is None

    def test_returns_none_without_ref_file(self, tmp_path: Path):
        result = get_source_context(ref_file=None, ref_line=1, source_root=tmp_path)
        assert result is None


class TestGetGitContext:
    def test_returns_none_without_ref_file(self, tmp_path: Path):
        from blq.services.inspect import get_git_context

        result = get_git_context(ref_file=None, ref_line=1, source_root=tmp_path)
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        from blq.services.inspect import get_git_context

        result = get_git_context(ref_file="nonexistent.py", ref_line=1, source_root=tmp_path)
        assert result is None


class TestGetLogContext:
    def test_returns_none_without_line_numbers(self):
        from blq.services.inspect import get_log_context

        result = get_log_context(storage=None, run_id=1, log_line_start=None, log_line_end=None)
        assert result is None


class TestGetFingerprintHistory:
    def test_returns_none_without_fingerprint(self):
        from blq.services.inspect import get_fingerprint_history

        result = get_fingerprint_history(storage=None, fingerprint=None)
        assert result is None
