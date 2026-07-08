"""Security regression tests: MCP safe-mode containment, path confinement, SQLi.

These are adversarial tests for the fixes tracked in GHSA-2g6f-xm6v-cwf5 / issue #45:

1. Safe mode must *contain* an untrusted caller: the ``run`` and ``query`` tools
   (raw SQL + command execution) must be refused when safe mode is on.
2. Source-context resolution must stay confined under ``source_root`` -- an
   absolute or ``..`` ``ref_file`` (which is populated from parsed log content)
   must not read files off-tree.
3. The query builders (serve.py tag clauses + services/query.py) must be
   parameterized so a value containing a quote / boolean-injection cannot alter
   results.

All fixtures are local temp files; nothing here touches the network.
"""

from __future__ import annotations

import pytest

from blq import serve

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def blq_with_data(initialized_project, sample_build_script):
    """An initialized project with a run's worth of parsed events.

    Mirrors the ``mcp_server`` fixture in test_mcp_server.py but returns nothing
    special -- tests use ``_get_storage()`` against the current cwd.
    """
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "blq", "exec", "--quiet", str(sample_build_script)],
        capture_output=True,
    )
    return initialized_project


@pytest.fixture
def safe_mode():
    """Enable MCP safe mode for the duration of a test, then restore.

    ``serve._disabled_tools`` is module-global state; leaking it would poison
    later tests that call query/run, so we reset it in teardown.
    """
    saved = serve._disabled_tools
    serve._disabled_tools = None
    serve._init_disabled_tools(safe_mode=True)
    try:
        yield
    finally:
        serve._disabled_tools = saved


# ---------------------------------------------------------------------------
# 1. Safe-mode containment
# ---------------------------------------------------------------------------


class TestSafeModeContainment:
    """Safe mode must refuse the raw-SQL and command-execution tools."""

    def test_query_is_disabled_in_safe_mode(self, safe_mode):
        """A safe-mode caller cannot run raw SQL (which could read_text/COPY).

        The canary here is conceptual: on base ``query`` has no gate, so a
        ``read_text('/etc/passwd')`` would execute. We assert the tool itself
        is refused (PermissionError) -- containment, not a per-payload block.
        """
        with pytest.raises(PermissionError):
            serve.query(sql="SELECT read_text('/etc/passwd')")

    def test_run_is_disabled_in_safe_mode(self, safe_mode, tmp_path):
        """A safe-mode caller cannot execute a registered command.

        On base ``run`` forwards caller args into a shell; we assert the tool is
        refused before any subprocess is spawned (the marker file must not appear).
        """
        marker = tmp_path / "run_marker"
        assert not marker.exists()
        with pytest.raises(PermissionError):
            serve.run(command="anything", extra=["&&", f"touch {marker}"])
        assert not marker.exists(), "safe-mode run executed despite being disabled"

    def test_safe_mode_disables_run_and_query(self, safe_mode):
        """The disabled set explicitly includes the new tools."""
        disabled = serve._load_disabled_tools()
        assert "run" in disabled
        assert "query" in disabled
        # Existing containment preserved.
        assert "exec" in disabled
        assert "clean" in disabled

    def test_query_still_works_without_safe_mode(self):
        """Sanity: query is NOT gated when safe mode is off."""
        saved = serve._disabled_tools
        serve._disabled_tools = None
        serve._init_disabled_tools(safe_mode=False)
        try:
            # Should not raise PermissionError (may return an error dict if no
            # repo, but that is a different, non-security failure mode).
            result = serve.query(sql="SELECT 1 AS x")
            assert isinstance(result, dict)
        finally:
            serve._disabled_tools = saved


# ---------------------------------------------------------------------------
# 2. Source-context path confinement
# ---------------------------------------------------------------------------


class TestSourceContextConfinement:
    """read_source_context / get_source_context must not escape source_root."""

    def _make_canary(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        (root / "in_tree.py").write_text("print('inside')\n")
        secret_dir = tmp_path / "outside"
        secret_dir.mkdir()
        canary = secret_dir / "canary.txt"
        canary.write_text("CANARY_SECRET_TOKEN\n")
        return root, canary

    def test_absolute_ref_file_is_refused(self, tmp_path):
        """An absolute ref_file must not be read (pathlib discards the root)."""
        import blq.output as output_mod

        root, canary = self._make_canary(tmp_path)
        # Path(root) / "/abs/canary" == Path("/abs/canary") -- root is discarded.
        result = output_mod.read_source_context(str(canary), 1, ref_root=str(root), context=2)
        assert result is None or "CANARY_SECRET_TOKEN" not in result

    def test_dotdot_ref_file_is_refused(self, tmp_path):
        """A ../ traversal out of source_root must not be read."""
        import blq.output as output_mod

        root, canary = self._make_canary(tmp_path)
        rel = f"../outside/{canary.name}"
        result = output_mod.read_source_context(rel, 1, ref_root=str(root), context=2)
        assert result is None or "CANARY_SECRET_TOKEN" not in result

    def test_in_tree_file_still_reads(self, tmp_path):
        """Legitimate in-tree relative paths still resolve (no over-blocking)."""
        import blq.output as output_mod

        root, _ = self._make_canary(tmp_path)
        result = output_mod.read_source_context("in_tree.py", 1, ref_root=str(root), context=2)
        assert result is not None
        assert "inside" in result

    def test_service_get_source_context_confined(self, tmp_path):
        """The service wrapper (used by inspect) is confined too."""
        from pathlib import Path

        from blq.services.inspect import get_source_context

        root, canary = self._make_canary(tmp_path)
        result = get_source_context(
            ref_file=str(canary),
            ref_line=1,
            source_root=Path(root),
            context_lines=2,
        )
        assert result is None or "CANARY_SECRET_TOKEN" not in result

    def test_git_context_absolute_ref_file_confined(self, tmp_path):
        """get_git_context must not run git ops on an off-root absolute path."""
        from pathlib import Path

        from blq.services.inspect import get_git_context

        root, canary = self._make_canary(tmp_path)
        result = get_git_context(
            ref_file=str(canary),
            ref_line=1,
            source_root=Path(root),
        )
        assert result is None


# ---------------------------------------------------------------------------
# 3. SQL injection in the query builders
# ---------------------------------------------------------------------------


class TestQueryBuilderInjection:
    """Values flowing into query builders must be parameterized."""

    def test_source_filter_injection_does_not_alter_results(self, blq_with_data):
        """services/query.py: a boolean-injection in `source` must not match all rows."""
        from blq.serve import _get_storage
        from blq.services.query import query_events

        storage = _get_storage()
        baseline = query_events(storage, all_runs=True, limit=100)
        assert baseline["total_count"] > 0, "fixture should have produced events"

        injected = query_events(storage, source="nope' OR '1'='1", all_runs=True, limit=100)
        # Parameterized: the literal source matches nothing -> 0 rows.
        # On the f-string path the OR makes it match every row.
        assert injected["total_count"] == 0, "SQL injection via source filter altered results"

    def test_tag_clause_injection_does_not_bypass_tag(self, blq_with_data):
        """serve.py tag where-clause: injection must not bypass the tag match."""
        from blq.serve import _event_impl, _get_storage
        from blq.services.query import query_events

        storage = _get_storage()
        events = query_events(storage, all_runs=True, limit=1)["events"]
        assert events, "fixture should have produced events"
        ev = events[0]
        serial = ev["run_serial"]
        eid = ev["event_id"]

        # A bogus tag combined with a boolean injection: on the f-string path
        # `tag = 'zzz' OR '1'='1' AND run_serial = S AND event_id = E` matches
        # the real event regardless of tag. Parameterized, the literal tag
        # (which no event carries) matches nothing.
        bogus_ref = f"zzz' OR '1'='1:{serial}:{eid}"
        result = _event_impl(bogus_ref)
        assert result is None, "tag-clause SQL injection bypassed the tag filter"

    def test_single_quote_value_does_not_error(self, blq_with_data):
        """A plain single-quote in a value must be handled cleanly, not crash."""
        from blq.serve import _get_storage
        from blq.services.query import query_events

        storage = _get_storage()
        # Should return a well-formed (empty) result, never raise.
        result = query_events(storage, source="a'b", all_runs=True, limit=10)
        assert result["total_count"] == 0
