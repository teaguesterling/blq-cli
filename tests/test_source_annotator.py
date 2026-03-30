"""Tests for source context annotator."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from blq.ext.annotator import Annotation, RunContext
from blq_sandbox.source_annotator import (
    Definition,
    SourceContextAnnotator,
    find_enclosing_definition,
)


class TestFindEnclosingDefinition:
    def test_finds_python_function(self, tmp_path: Path):
        src = tmp_path / "foo.py"
        src.write_text(
            "import os\n"
            "\n"
            "def authenticate(user, password):\n"
            "    if not user:\n"
            "        raise ValueError('no user')\n"
            "    return check(user, password)\n"
        )
        defn = find_enclosing_definition(src, 5)  # line 5 is inside authenticate
        assert defn is not None
        assert defn.kind == "function"
        assert defn.name == "authenticate"
        assert "def authenticate" in defn.signature

    def test_finds_python_class(self, tmp_path: Path):
        src = tmp_path / "bar.py"
        src.write_text(
            "class MyService:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "    def run(self):\n"
            "        pass\n"
        )
        defn = find_enclosing_definition(src, 3)  # inside __init__
        assert defn is not None
        # Should find __init__ (innermost) or MyService
        assert defn.name in ("__init__", "MyService")

    def test_finds_python_method(self, tmp_path: Path):
        src = tmp_path / "baz.py"
        src.write_text(
            "class Foo:\n"
            "    def bar(self, x):\n"
            "        return x + 1\n"
        )
        defn = find_enclosing_definition(src, 3)  # inside bar
        assert defn is not None
        assert defn.name == "bar"

    def test_finds_c_function(self, tmp_path: Path):
        src = tmp_path / "main.c"
        src.write_text(
            "#include <stdio.h>\n"
            "\n"
            "int main(int argc, char **argv) {\n"
            "    printf(\"hello\\n\");\n"
            "    return 0;\n"
            "}\n"
        )
        defn = find_enclosing_definition(src, 4)  # inside main
        assert defn is not None
        assert defn.name == "main"

    def test_returns_none_for_top_level(self, tmp_path: Path):
        src = tmp_path / "top.py"
        src.write_text(
            "import os\n"
            "x = 1\n"
            "y = 2\n"
        )
        defn = find_enclosing_definition(src, 2)
        assert defn is None

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        defn = find_enclosing_definition(tmp_path / "nonexistent.py", 1)
        assert defn is None

    def test_handles_empty_file(self, tmp_path: Path):
        src = tmp_path / "empty.py"
        src.write_text("")
        defn = find_enclosing_definition(src, 1)
        assert defn is None

    def test_line_beyond_file(self, tmp_path: Path):
        src = tmp_path / "short.py"
        src.write_text("x = 1\n")
        defn = find_enclosing_definition(src, 100)
        assert defn is None


class TestSourceContextAnnotator:
    @pytest.fixture
    def project(self, tmp_path: Path):
        """Create a project with source files and a DB."""
        # Create source file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "auth.py").write_text(
            "class AuthService:\n"
            "    def login(self, user, pwd):\n"
            "        if not user:\n"
            "            raise ValueError('missing user')\n"
            "        return True\n"
        )
        return tmp_path

    @pytest.fixture
    def ctx(self, project: Path):
        conn = duckdb.connect()
        conn.execute("""
            CREATE TABLE invocations (
                id VARCHAR, source_name VARCHAR, cmd VARCHAR,
                cwd VARCHAR, extension_data JSON, timestamp TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE events (
                id VARCHAR, invocation_id VARCHAR, event_index INTEGER,
                severity VARCHAR, ref_file VARCHAR, ref_line INTEGER,
                ref_column INTEGER, message VARCHAR, code VARCHAR,
                fingerprint VARCHAR, metadata JSON
            )
        """)
        conn.execute("""
            CREATE TABLE outcomes (
                attempt_id VARCHAR, exit_code INTEGER, duration_ms BIGINT
            )
        """)
        conn.execute("""
            INSERT INTO invocations VALUES (
                'inv-1', 'test', 'pytest', ?, NULL, '2026-03-29 12:00:00'
            )
        """, [str(project)])
        conn.execute("""
            INSERT INTO events VALUES
                ('evt-1', 'inv-1', 0, 'error', 'src/auth.py', 4, NULL,
                 'ValueError: missing user', 'E001', 'fp1', NULL),
                ('evt-2', 'inv-1', 1, 'warning', 'src/auth.py', 2, NULL,
                 'unused import', 'W001', 'fp2', NULL)
        """)
        conn.execute("INSERT INTO outcomes VALUES ('inv-1', 1, 5000)")
        return RunContext(conn=conn, invocation_id="inv-1", source_root=project)

    def test_should_annotate_with_errors(self, ctx):
        annotator = SourceContextAnnotator()
        assert annotator.should_annotate(ctx) is True

    def test_annotates_error_events(self, ctx):
        annotator = SourceContextAnnotator()
        annotator.annotate(ctx)
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        assert result[0] is not None
        meta = json.loads(result[0])
        assert "annotations" in meta
        ann = meta["annotations"][0]
        assert ann["annotator"] == "source_context"
        assert ann["type"] == "source"
        assert ann["display"] == "inline"
        assert "login" in ann["data"]["name"] or "AuthService" in ann["data"]["name"]

    def test_skips_warnings(self, ctx):
        annotator = SourceContextAnnotator()
        annotator.annotate(ctx)
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-2'"
        ).fetchone()
        # Warning should NOT be annotated
        assert result[0] is None

    def test_eager_by_default(self):
        annotator = SourceContextAnnotator()
        assert annotator.eager is True
