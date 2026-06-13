"""Shared service layer for blq.

Services contain pure business logic called by both CLI and MCP.
Each function takes a BlqStorage instance and returns structured data.
No argparse, no MCP, no output formatting.
"""

from __future__ import annotations

from blq.services.execution import run_result_to_concise
from blq.services.inspect import (
    get_fingerprint_history,
    get_git_context,
    get_log_context,
    get_source_context,
)
from blq.services.query import query_diff, query_events, query_history, query_status
from blq.services.refs import ParsedRef, parse_ref, resolve_run_ref

__all__ = [
    "ParsedRef",
    "parse_ref",
    "resolve_run_ref",
    "query_status",
    "query_history",
    "query_events",
    "query_diff",
    "get_source_context",
    "get_log_context",
    "get_git_context",
    "get_fingerprint_history",
    "run_result_to_concise",
]
