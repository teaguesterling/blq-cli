"""Run one query on a read-only DuckDB engine, in a process of its own.

DuckDB allows one configuration per database file in a process. The server holds
read-write handles, so its locked-down read-only connection lives in a child
process. The engine settings enforce read-only. The statement type check in front
of them only gives a refusal a clear name.

The child reads a JSON request on stdin and writes a JSON result on stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
from pydantic_core import to_json

from blq.bird import BirdStore, retry_on_lock

# In this order: nothing can change after the last one.
LOCKDOWN = (
    "SET autoload_known_extensions = false",
    "SET autoinstall_known_extensions = false",
    "SET allow_community_extensions = false",
    "SET temp_directory = ''",
    # No directory is allowed: an allowed directory is writable as well as readable.
    "SET enable_external_access = false",
    "SET lock_configuration = true",
)

# About 8 s in total, so that a query outlasts an ordinary write.
LOCK_RETRIES = 8


def _lock_down(conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    for statement in LOCKDOWN:
        conn.execute(statement)
    return conn


def open_engine(lq_dir: Path) -> duckdb.DuckDBPyConnection:
    """Open the workspace read-only and locked down.

    Call this only in a process that holds no other handle on the database.
    """
    db_path = lq_dir / "blq.duckdb"
    if not db_path.exists():
        # An empty database in memory
        return _lock_down(BirdStore.open(lq_dir, read_only=True).connection)
    BirdStore.open(lq_dir, read_only=True).close()  # upgrades an old schema first
    return _lock_down(duckdb.connect(str(db_path), read_only=True))


def _refusal(sql: str) -> str | None:
    """Name the first statement that is not a SELECT."""
    # Parsing can read files for some statements, so the parser is locked down too.
    conn = _lock_down(duckdb.connect())
    try:
        for statement in conn.extract_statements(sql):
            if statement.type != duckdb.StatementType.SELECT:
                return f"query is read-only: {statement.type.name} refused"
        return None
    finally:
        conn.close()


def _run(lq_dir: Path, sql: str, limit: int) -> dict[str, Any]:
    try:
        refusal = _refusal(sql)
        if refusal:
            return {"error": refusal}
        conn = retry_on_lock(lambda: open_engine(lq_dir), max_retries=LOCK_RETRIES)
        # Add LIMIT if not present (basic safety)
        if "LIMIT" not in sql.upper():
            sql = f"SELECT * FROM ({sql}) LIMIT {limit}"
        result = conn.sql(sql)
        columns = result.columns
        return {"columns": columns, "rows": [list(row) for row in result.fetchall()]}
    except duckdb.PermissionException as e:
        return {"error": f"query is read-only: external access refused ({e})"}
    except duckdb.Error as e:
        if "could not set lock" in str(e).lower():
            return {"error": "another process is writing to the database; try the query again"}
        if "read-only" in str(e).lower():
            return {"error": f"query is read-only: write refused ({e})"}
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def run_query(lq_dir: Path, sql: str, limit: int) -> dict[str, Any]:
    """Run sql in a child process.

    Returns:
        {"columns": ..., "rows": ...} or {"error": ...}
    """
    request = {"lq_dir": str(lq_dir.resolve()), "sql": sql, "limit": limit}
    child = subprocess.run(
        # Not the caller's cwd: -m would import modules from it.
        [sys.executable, "-m", "blq.query_worker"],
        input=json.dumps(request).encode(),
        capture_output=True,
        cwd=Path(sys.executable).anchor,
    )
    if child.returncode != 0:
        return {"error": f"query failed: the query engine stopped (exit {child.returncode})"}
    result: dict[str, Any] = json.loads(child.stdout)
    return result


def main() -> None:
    request = json.load(sys.stdin)
    # Keep the result channel private: anything else printed here goes to stderr.
    channel = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    result = _run(Path(request["lq_dir"]), request["sql"], request["limit"])
    # The same value forms that the MCP layer produces
    channel.write(to_json(result, bytes_mode="base64", fallback=str))
    channel.close()


if __name__ == "__main__":
    main()
