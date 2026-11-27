"""The query tool reads the workspace database, and the engine refuses everything else."""

import threading

import duckdb
import pytest

from blq import serve

CANARY = "outside-the-database"

# class-name: (statement, what the refusal names). {out} is a directory outside the
# workspace, {bird} is the workspace directory.
STATEMENTS = {
    "write-create": ("CREATE TABLE made (x INT)", "CREATE"),
    "write-create-temp": ("CREATE TEMP TABLE made (x INT)", "CREATE"),
    "write-create-macro": ("CREATE MACRO made() AS 1", "CREATE"),
    "write-insert": ("INSERT INTO victim VALUES (2)", "INSERT"),
    "write-update": ("UPDATE victim SET x = 2", "UPDATE"),
    "write-delete": ("DELETE FROM victim", "DELETE"),
    "write-truncate": ("TRUNCATE victim", "DELETE"),
    "write-merge": (
        "MERGE INTO victim USING victim AS other ON victim.x = other.x WHEN MATCHED THEN DELETE",
        "MERGE_INTO",
    ),
    "write-drop": ("DROP TABLE victim", "DROP"),
    "write-alter": ("ALTER TABLE victim ADD COLUMN y INT", "ALTER"),
    "write-comment": ("COMMENT ON TABLE victim IS 'changed'", "ALTER"),
    "write-vacuum": ("VACUUM", "VACUUM"),
    "write-checkpoint": ("CHECKPOINT", "CALL"),
    "write-pivot": ("PIVOT victim ON x USING count(*)", "CREATE"),
    "write-after-select": ("SELECT 1; DELETE FROM victim", "DELETE"),
    "attach": ("ATTACH '{out}/other.duckdb' AS other", "ATTACH"),
    "detach": ("DETACH DATABASE IF EXISTS other", "DETACH"),
    "copy-out": ("COPY victim TO '{out}/victim.csv'", "COPY"),
    "copy-workspace": ("COPY victim TO '{bird}/victim.csv'", "COPY"),
    "copy-in": ("COPY victim FROM '{out}/more.csv'", "COPY"),
    "export": ("EXPORT DATABASE '{out}/dump'", "EXPORT"),
    "load": ("LOAD httpfs", "LOAD"),
    "install": ("INSTALL httpfs", "LOAD"),
    "set": ("SET threads = 1", "SET"),
    "set-use": ("USE blq", "SET"),
    "call": ("CALL pragma_version()", "CALL"),
    "transaction": ("BEGIN TRANSACTION", "TRANSACTION"),
    "prepare": ("PREPARE p AS DELETE FROM victim", "PREPARE"),
    "execute": ("EXECUTE p", "EXECUTE"),
    "explain": ("EXPLAIN ANALYZE DELETE FROM victim", "EXPLAIN"),
}

# Statements that DuckDB calls SELECT. Only the engine can refuse these.
SELECTS = {
    "read-text": "SELECT content FROM read_text('{out}/canary.txt')",
    "read-csv": "SELECT * FROM read_csv('{out}/canary.txt', header=false)",
    "read-glob": "SELECT * FROM glob('{out}/*')",
    "read-workspace-file": "SELECT content FROM read_text('{bird}/canary.txt')",
    "write-sequence": "SELECT nextval('victim_seq')",
}

# One statement per class, run on the engine with no statement check in front of it.
ENGINE = {
    "write": "DELETE FROM victim",
    "write-sequence": "SELECT nextval('victim_seq')",
    "attach": "ATTACH '{out}/other.duckdb' AS other",
    "copy": "COPY victim TO '{out}/victim.csv'",
    "copy-workspace": "COPY victim TO '{bird}/victim.csv'",
    "read-outside": "SELECT content FROM read_text('{out}/canary.txt')",
    "load": "LOAD httpfs",
    "set": "SET enable_external_access = true",
}

READS = [
    "SELECT x FROM victim",
    "WITH v AS (SELECT x FROM victim) SELECT x FROM v",
    "FROM victim",
    "SELECT x FROM victim LIMIT 1",
]

STATE = """
    SELECT 'table', table_name || ' ' || coalesce(comment, '') FROM duckdb_tables()
    UNION ALL SELECT 'column', table_name || '.' || column_name FROM duckdb_columns()
        WHERE NOT internal
    UNION ALL SELECT 'macro', function_name FROM duckdb_functions() WHERE NOT internal
    UNION ALL SELECT 'database', database_name FROM duckdb_databases()
    UNION ALL SELECT 'threads', current_setting('threads')::VARCHAR
    UNION ALL SELECT 'victim', x::VARCHAR FROM victim
    UNION ALL SELECT 'sequence', coalesce(last_value::VARCHAR, 'unused') FROM duckdb_sequences()
    ORDER BY ALL
"""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    out = tmp_path / "out"
    (root / ".bird").mkdir(parents=True)
    out.mkdir()
    (out / "more.csv").write_text("x\n2\n")
    (out / "canary.txt").write_text(CANARY)
    (root / ".bird" / "canary.txt").write_text(CANARY)
    storage = serve._get_storage(root=str(root), read_only=False)
    storage.connection.execute("CREATE TABLE victim (x INT)")
    storage.connection.execute("INSERT INTO victim VALUES (1)")
    storage.connection.execute("CREATE SEQUENCE victim_seq")
    storage.close()
    return root


def _fill(sql, repo):
    return sql.format(out=repo.parent / "out", bird=repo / ".bird")


def _state(repo):
    storage = serve._get_storage(root=str(repo), read_only=False)
    try:
        rows = storage.connection.execute(STATE).fetchall()
    finally:
        storage.close()
    outside = sorted(p.name for p in (repo.parent / "out").iterdir())
    return rows, outside, (repo / ".bird" / "victim.csv").exists()


@pytest.fixture(params=[True, False], ids=["with-write-handle", "alone"])
def held(request, repo):
    """Run the test once while this process holds a write handle, and once without."""
    writer = serve._get_storage(root=str(repo), read_only=False) if request.param else None
    yield writer
    if writer is not None:
        writer.close()


# The tool adds a row limit to text that does not name one. The comment stops that,
# so that the statement itself reaches the engine.
@pytest.mark.parametrize("case", STATEMENTS)
def test_query_refuses_a_non_read_statement(repo, held, case):
    sql, kind = STATEMENTS[case]
    before = _state(repo)

    result = serve._query_impl(sql=_fill(sql, repo) + " /* LIMIT */", root=str(repo))

    assert result.get("error") == f"query is read-only: {kind} refused", result
    assert _state(repo) == before


def test_refusal_is_named_without_a_row_limit(repo):
    result = serve._query_impl(sql="DELETE FROM victim", root=str(repo))

    assert result.get("error") == "query is read-only: DELETE refused", result


@pytest.mark.parametrize("case", SELECTS)
def test_query_refuses_a_select_that_leaves_the_database(repo, held, case):
    before = _state(repo)

    result = serve._query_impl(sql=_fill(SELECTS[case], repo), root=str(repo))

    assert result["rows"] == [], result
    assert CANARY not in str(result)
    if held is None:
        assert result["error"].startswith("query is read-only: "), result
    else:
        assert result.get("error"), result
    assert _state(repo) == before


@pytest.mark.parametrize("case", ENGINE)
def test_the_engine_refuses_by_itself(repo, case):
    from blq.query_worker import open_engine

    before = _state(repo)
    conn = open_engine(repo / ".bird")

    with pytest.raises(duckdb.Error):
        conn.execute(_fill(ENGINE[case], repo)).fetchall()

    conn.close()
    assert _state(repo) == before


def test_the_engine_settings_are_locked_down(repo):
    from blq.query_worker import open_engine

    conn = open_engine(repo / ".bird")
    names = [
        "access_mode",
        "enable_external_access",
        "autoload_known_extensions",
        "autoinstall_known_extensions",
        "allow_community_extensions",
        "lock_configuration",
    ]
    values = [conn.execute(f"SELECT current_setting('{n}')").fetchone()[0] for n in names]
    conn.close()

    assert [str(v).lower() for v in values] == ["read_only"] + ["false"] * 4 + ["true"]


@pytest.mark.parametrize("sql", READS)
def test_query_reads(repo, sql):
    result = serve._query_impl(sql=sql, root=str(repo))

    assert result.get("error") is None, result
    assert result["rows"] == [[1]]


def test_query_does_not_use_a_write_handle(repo):
    writer = serve._get_storage(root=str(repo), read_only=False)

    result = serve._query_impl(sql="SELECT x FROM victim", root=str(repo))

    assert "writing" in result.get("error", ""), result
    writer.connection.execute("INSERT INTO victim VALUES (2)")
    writer.close()


def test_query_waits_for_a_write_handle_to_close(repo):
    writer = serve._get_storage(root=str(repo), read_only=False)
    timer = threading.Timer(1.0, writer.close)
    timer.start()

    result = serve._query_impl(sql="SELECT x FROM victim", root=str(repo))

    timer.join()
    assert result.get("rows") == [[1]], result
