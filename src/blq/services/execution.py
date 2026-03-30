"""Execution services for blq.

Provides shared business logic for shaping RunResult JSON into the concise
format returned to MCP callers and CLI consumers.
No argparse, no MCP, no output formatting.
"""

from __future__ import annotations

from typing import Any


def run_result_to_concise(full_result: dict[str, Any], source_name: str) -> dict[str, Any]:
    """Convert a full RunResult JSON dict into the concise MCP response format.

    The ``full_result`` is the dict produced by ``RunResult.to_json()`` (already
    parsed from JSON).  ``source_name`` is used to build ``run_ref`` when the
    result does not contain one (e.g. the caller knows the registered command
    name but the result only has a numeric run_id).

    Always-present keys in the returned dict:
        run_ref       – "<source_name>:<run_id>" or None when run_id is absent
        cmd           – the command string from full_result["command"]
        status        – from full_result or derived from exit_code
        exit_code     – int
        duration_sec  – float rounded to 1 decimal place
        summary       – dict from full_result
        output_stats  – {"lines": int, "bytes": int}

    Conditionally-present keys (only when the value is non-empty/non-None):
        status_reason – str
        errors        – first 10 items
        warnings      – first 5 items
        infos         – first 5 items
    """
    run_id = full_result.get("run_id")
    exit_code = full_result.get("exit_code", 0)
    status = full_result.get("status", "FAIL" if exit_code != 0 else "OK")
    output_stats_raw = full_result.get("output_stats", {})

    concise: dict[str, Any] = {
        "run_ref": f"{source_name}:{run_id}" if run_id is not None else None,
        "cmd": full_result.get("command"),
        "status": status,
        "exit_code": exit_code,
        "duration_sec": round(full_result.get("duration_sec", 0), 1),
        "summary": full_result.get("summary", {}),
        "output_stats": {
            "lines": output_stats_raw.get("lines", 0),
            "bytes": output_stats_raw.get("bytes", 0),
        },
    }

    # Include status_reason when present and non-None
    status_reason = full_result.get("status_reason")
    if status_reason:
        concise["status_reason"] = status_reason

    # Include errors (capped at 10, summary has total count)
    errors = full_result.get("errors", [])
    if errors:
        concise["errors"] = errors[:10]

    # Include warnings (top 5)
    warnings = full_result.get("warnings", [])
    if warnings:
        concise["warnings"] = warnings[:5]

    # Include info/summary events (top 5)
    infos = full_result.get("infos", [])
    if infos:
        concise["infos"] = infos[:5]

    return concise
