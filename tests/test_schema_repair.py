"""Regression tests for schema self-heal.

An old migration renamed `sandbox` -> `extension_data`, but RENAME COLUMN is
blocked by dependencies on `attempts`/`invocations`, so it failed silently and
the schema version advanced past it — leaving DBs stuck without `extension_data`
and crashing `write_attempt`. `_ensure_schema` now self-heals (ADD + copy),
version-independently, without losing the old `sandbox` data.
"""

from pathlib import Path

import duckdb

import blq.bird as _bird_mod
from blq.bird import BirdStore

SCHEMA = (Path(_bird_mod.__file__).parent / "bird_schema.sql").read_text()


def _apply(conn, sql_text):
    for stmt in BirdStore._split_sql_statements(sql_text):
        try:
            conn.execute(stmt)
        except duckdb.Error as e:
            if "already exists" not in str(e).lower():
                pass


def _cols(conn, table):
    return {
        r[0]
        for r in conn.execute(
            f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'"
        ).fetchall()
    }


def _build_stuck_db(path):
    """A DB from the era when the column was `sandbox` (tables + views all on
    `sandbox`), version 3.0.0 — exactly what a silently-failed 2.4 RENAME left."""
    c = duckdb.connect(str(path))
    _apply(c, SCHEMA.replace("extension_data", "sandbox"))
    c.execute(
        "INSERT INTO attempts (session_id, cwd, cmd, client_id, sandbox) "
        "VALUES ('s1', '/tmp', 'build', 'blq-test', '{\"network\":\"none\"}'::JSON)"
    )
    c.execute("UPDATE blq_metadata SET value='3.0.0' WHERE key='schema_version'")
    c.close()


def test_stuck_db_self_heals_and_preserves_data(tmp_path):
    bird = tmp_path / ".bird"
    bird.mkdir()
    db = bird / "blq.duckdb"
    _build_stuck_db(db)

    c = duckdb.connect(str(db))
    assert "sandbox" in _cols(c, "attempts")
    assert "extension_data" not in _cols(c, "attempts")
    assert BirdStore._needs_repair(c, "3.0.0") is True

    BirdStore._ensure_schema(c, bird)

    # extension_data added on both tables, sandbox data copied + wrapped
    assert "extension_data" in _cols(c, "attempts")
    assert "extension_data" in _cols(c, "invocations")
    val = c.execute("SELECT extension_data FROM attempts WHERE session_id='s1'").fetchone()[0]
    assert "network" in str(val) and "sandbox" in str(val)

    # views recreated, and the write that used to crash now succeeds
    nviews = c.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchone()[0]
    assert nviews >= 4
    c.execute(
        "INSERT INTO attempts (session_id, cwd, cmd, client_id, extension_data) "
        "VALUES ('s2', '/tmp', 'x', 'blq-test', '{\"a\":1}'::JSON)"
    )

    # converges: a repaired DB is a fast no-op on the next open
    assert BirdStore._needs_repair(c, "3.0.0") is False
    c.close()


def test_fresh_db_is_healthy_and_no_op(tmp_path):
    bird = tmp_path / ".bird"
    bird.mkdir()
    c = duckdb.connect(str(bird / "blq.duckdb"))
    BirdStore._ensure_schema(c, bird)  # fresh init
    assert "extension_data" in _cols(c, "attempts")
    assert BirdStore._needs_repair(c, "3.0.0") is False
    # re-open is a no-op and stays healthy
    BirdStore._ensure_schema(c, bird)
    assert "extension_data" in _cols(c, "attempts")
    c.close()
