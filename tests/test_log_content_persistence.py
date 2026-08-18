"""Tests for #52: duck_hunt's `log_content` is received and must be persisted.

duck_hunt returns `log_content` carrying the whole failure block (default
`content_mode = FULL`). blq's parser already puts it in the event dict, but
neither storage backend had a column for it, so it was dropped at write time —
leaving only `log_line_start`/`log_line_end`, pointers into a log that is not
retained unless `--keep-raw`.

These tests pin the field end to end (exec -> storage -> blq_load_events()) for
both backends, the cap on oversized values, and the migration of BIRD databases
created before the column existed.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

import blq.bird as _bird_mod
from blq.bird import BirdStore, InvocationRecord
from blq.commands.core import (
    LOG_CONTENT_MAX_CHARS,
    PARQUET_SCHEMA_COLUMNS,
    get_connection,
    truncate_log_content,
)

SCHEMA = (Path(_bird_mod.__file__).parent / "bird_schema.sql").read_text()

# A one-assertion pytest failure, verbatim from #52. The `message` duck_hunt
# derives from the short-summary line is 7 chars ("asse..."); log_content is the
# whole FAILURES block, including the `+  where None = normalize(5)` line that
# names the function under test.
PYTEST_FAILURE_LOG = """\
============================= test session starts ==============================
collected 1 item

tests/test_priority.py F                                                 [100%]

=================================== FAILURES ===================================
________________ test_unknown_name_falls_back_to_default _______________________

    def test_unknown_name_falls_back_to_default():
>       assert normalize(5) == 5
E       assert None == 5
E        +  where None = normalize(5)

tests/test_priority.py:5: AssertionError
=========================== short test summary info ============================
FAILED tests/test_priority.py::test_unknown_name_falls_back_to_default - asse...
============================== 1 failed in 0.01s ===============================
"""

IMPLICATED_SOURCE_LINE = "+  where None = normalize(5)"


def _failing_pytest_script(project: Path) -> list[str]:
    """A command that reproduces the pytest output above on stdout, exit 1.

    Written to a file and `cat`-ed so the block survives shell quoting exactly.
    """
    log_file = project / "pytest_output.txt"
    log_file.write_text(PYTEST_FAILURE_LOG)
    script = project / "run_pytest.sh"
    script.write_text(f'#!/bin/bash\ncat "{log_file}"\nexit 1\n')
    script.chmod(0o755)
    return [str(script)]


def _cols(conn, table):
    return {
        r[0]
        for r in conn.execute(
            f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'"
        ).fetchall()
    }


def _apply(conn, sql_text):
    for stmt in BirdStore._split_sql_statements(sql_text):
        try:
            conn.execute(stmt)
        except duckdb.Error:
            pass


# ---------------------------------------------------------------------------
# End to end: exec -> storage -> blq_load_events()
# ---------------------------------------------------------------------------


class TestEndToEndPersistence:
    def test_bird_exec_persists_log_content(self, initialized_project, run_adhoc_command):
        """BIRD mode: the failure block survives to blq_load_events()."""
        run_adhoc_command(_failing_pytest_script(initialized_project), format="pytest_text")

        conn = get_connection(Path(".bird"))
        row = conn.execute(
            "SELECT message, log_content FROM blq_load_events() WHERE severity = 'error'"
        ).fetchone()

        assert row is not None, "no error event was stored"
        message, log_content = row
        assert log_content, "log_content was dropped at write time"
        assert IMPLICATED_SOURCE_LINE in log_content
        assert "assert None == 5" in log_content
        assert len(log_content) > len(message or "")

    def test_parquet_exec_persists_log_content(
        self, initialized_project_parquet, run_adhoc_command
    ):
        """Legacy parquet mode: same guarantee, same column name."""
        run_adhoc_command(_failing_pytest_script(initialized_project_parquet), format="pytest_text")

        conn = get_connection(Path(".bird"))
        row = conn.execute(
            "SELECT message, log_content FROM blq_load_events() WHERE severity = 'error'"
        ).fetchone()

        assert row is not None, "no error event was stored"
        message, log_content = row
        assert log_content, "log_content was dropped at write time"
        assert IMPLICATED_SOURCE_LINE in log_content
        assert len(log_content) > len(message or "")


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


class TestSchemaShape:
    def test_parquet_schema_has_log_content_and_no_vestigial_raw_text(self):
        """`raw_text` was never populated by anything; log_content replaces it.

        docs/design/duck-hunt-v3-migration.md records this exact rename as the
        intended migration (`raw_text` -> `log_content`).
        """
        assert "log_content" in PARQUET_SCHEMA_COLUMNS
        assert "raw_text" not in PARQUET_SCHEMA_COLUMNS

    def test_bird_events_table_has_log_content(self, tmp_path):
        bird = tmp_path / ".bird"
        bird.mkdir()
        conn = duckdb.connect(str(bird / "blq.duckdb"))
        BirdStore._ensure_schema(conn, bird)
        assert "log_content" in _cols(conn, "events")
        conn.close()


# ---------------------------------------------------------------------------
# Cap: bounded, and visibly truncated rather than silently
# ---------------------------------------------------------------------------


class TestCap:
    def test_short_values_pass_through_unchanged(self):
        assert truncate_log_content(PYTEST_FAILURE_LOG) == PYTEST_FAILURE_LOG
        assert truncate_log_content(None) is None
        assert truncate_log_content("") == ""

    def test_oversized_values_are_capped_and_say_so(self):
        oversized = "x" * (LOG_CONTENT_MAX_CHARS + 5000)
        result = truncate_log_content(oversized)

        assert len(result) < len(oversized)
        assert "truncated" in result
        # The marker must state both the kept and the original size, so a reader
        # can tell how much is missing.
        assert str(len(oversized)) in result
        assert str(LOG_CONTENT_MAX_CHARS) in result
        assert result.startswith("x" * 100)

    def test_bird_write_events_applies_the_cap(self, tmp_path):
        bird = tmp_path / ".bird"
        bird.mkdir()
        with BirdStore.open(bird) as store:
            store.ensure_session("s", "blq-test", "blq", "cli")
            inv = InvocationRecord(
                id="00000000-0000-0000-0000-000000000001",
                session_id="s",
                cmd="pytest",
                cwd="/tmp",
                exit_code=1,
                client_id="blq-test",
                source_name="test",
            )
            store.write_invocation(inv)
            store.write_events(
                inv.id,
                [{"severity": "error", "message": "boom", "log_content": "y" * 999_999}],
                client_id="blq-test",
            )
            stored = store.connection.execute("SELECT log_content FROM events").fetchone()[0]

        assert len(stored) <= LOG_CONTENT_MAX_CHARS + 200  # value + marker
        assert "truncated" in stored


# ---------------------------------------------------------------------------
# Migration of databases created before the column existed
# ---------------------------------------------------------------------------


def _schema_without_log_content() -> str:
    """The schema as it was before log_content existed.

    Rebuilding the old schema is how the pre-existing repair tests simulate an
    older era; DROP COLUMN is not usable here because DuckDB refuses to drop a
    column that an index sits after.
    """
    lines = [
        line
        for line in SCHEMA.splitlines(keepends=True)
        if line.strip() not in ("e.log_content,",) and not line.strip().startswith("log_content ")
    ]
    text = "".join(lines)
    assert "log_content" not in text, "failed to strip log_content from schema"
    return text


def _build_pre_log_content_db(path: Path) -> None:
    """A healthy DB from before log_content existed: schema at 3.0.0, events
    table without the column, and one event row already in it."""
    conn = duckdb.connect(str(path))
    _apply(conn, _schema_without_log_content())
    conn.execute(
        "INSERT INTO invocations (id, session_id, cmd, cwd, client_id, exit_code, source_name) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa', 's1', 'pytest', '/tmp', "
        "'blq-test', 1, 'test')"
    )
    conn.execute(
        "INSERT INTO events (invocation_id, event_index, client_id, severity, message) "
        "VALUES ('00000000-0000-0000-0000-0000000000aa', 0, 'blq-test', 'error', 'old event')"
    )
    conn.execute("UPDATE blq_metadata SET value='3.0.0' WHERE key='schema_version'")
    conn.close()


class TestMigration:
    def test_existing_db_gains_the_column_without_losing_rows(self, tmp_path):
        bird = tmp_path / ".bird"
        bird.mkdir()
        db = bird / "blq.duckdb"
        _build_pre_log_content_db(db)

        conn = duckdb.connect(str(db))
        assert "log_content" not in _cols(conn, "events")
        assert BirdStore._needs_repair(conn, "3.0.0") is True

        BirdStore._ensure_schema(conn, bird)

        assert "log_content" in _cols(conn, "events")
        # Pre-existing rows survive, with NULL for the new column
        row = conn.execute("SELECT message, log_content FROM events").fetchone()
        assert row[0] == "old event"
        assert row[1] is None
        # And the compatibility view exposes the new column
        assert "log_content" in [
            d[0] for d in conn.execute("SELECT * FROM blq_load_events()").description
        ]
        conn.close()

    def test_migrated_db_accepts_writes_with_log_content(self, tmp_path):
        bird = tmp_path / ".bird"
        bird.mkdir()
        _build_pre_log_content_db(bird / "blq.duckdb")

        with BirdStore.open(bird) as store:
            store.ensure_session("s2", "blq-test", "blq", "cli")
            inv = InvocationRecord(
                id="00000000-0000-0000-0000-0000000000bb",
                session_id="s2",
                cmd="pytest",
                cwd="/tmp",
                exit_code=1,
                client_id="blq-test",
                source_name="test",
            )
            store.write_invocation(inv)
            store.write_events(
                inv.id,
                [{"severity": "error", "message": "asse...", "log_content": PYTEST_FAILURE_LOG}],
                client_id="blq-test",
            )
            got = store.connection.execute(
                "SELECT log_content FROM events WHERE message = 'asse...'"
            ).fetchone()[0]
        assert IMPLICATED_SOURCE_LINE in got

    def test_migration_converges(self, tmp_path):
        """A repaired DB reports healthy and re-opening is a no-op."""
        bird = tmp_path / ".bird"
        bird.mkdir()
        db = bird / "blq.duckdb"
        _build_pre_log_content_db(db)

        conn = duckdb.connect(str(db))
        BirdStore._ensure_schema(conn, bird)
        version = conn.execute(
            "SELECT value FROM blq_metadata WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == _bird_mod.SCHEMA_VERSION
        assert BirdStore._needs_repair(conn, version) is False
        BirdStore._ensure_schema(conn, bird)
        assert "log_content" in _cols(conn, "events")
        conn.close()

    def test_column_missing_at_current_version_is_still_healed(self, tmp_path):
        """Version-independent self-heal: a DB whose version already advanced
        past the migration but lacks the column (a silently-failed ALTER) is
        repaired anyway — the failure mode #52's predecessor bug hit."""
        bird = tmp_path / ".bird"
        bird.mkdir()
        db = bird / "blq.duckdb"
        conn = duckdb.connect(str(db))
        _apply(conn, _schema_without_log_content())
        # version left at current — nothing version-based would fire
        conn.execute(
            f"UPDATE blq_metadata SET value='{_bird_mod.SCHEMA_VERSION}' WHERE key='schema_version'"
        )
        assert BirdStore._needs_repair(conn, _bird_mod.SCHEMA_VERSION) is True
        BirdStore._ensure_schema(conn, bird)
        assert "log_content" in _cols(conn, "events")
        conn.close()


@pytest.mark.parametrize("severity", ["error"])
def test_log_content_reaches_mcp_event_response(initialized_project, run_adhoc_command, severity):
    """The MCP `blq_event` response carries the block (it previously exposed a
    `raw_text` key that duck_hunt never populates)."""
    from blq.serve import _event_impl

    run_adhoc_command(_failing_pytest_script(initialized_project), format="pytest_text")

    conn = get_connection(Path(".bird"))
    ref = conn.execute(
        f"SELECT ref FROM blq_load_events() WHERE severity = '{severity}'"
    ).fetchone()[0]

    response = _event_impl(ref)
    assert response is not None
    assert IMPLICATED_SOURCE_LINE in (response.get("log_content") or "")
