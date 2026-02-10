"""
blq commands module.

This module provides modular command implementations for the blq CLI.
"""

from blq.commands.ci_cmd import cmd_ci_check, cmd_ci_comment, cmd_ci_generate
from blq.commands.clean_cmd import cmd_clean
from blq.commands.events import cmd_context, cmd_event, cmd_inspect
from blq.commands.execution import cmd_capture, cmd_exec, cmd_import, cmd_run
from blq.commands.hooks_cmd import (
    cmd_hooks_add,
    cmd_hooks_generate,
    cmd_hooks_install,
    cmd_hooks_list,
    cmd_hooks_remove,
    cmd_hooks_run,
    cmd_hooks_status,
    cmd_hooks_uninstall,
)
from blq.commands.init_cmd import cmd_init
from blq.commands.management import (
    cmd_completions,
    cmd_errors,
    cmd_events,
    cmd_formats,
    cmd_history,
    cmd_info,
    cmd_last,
    cmd_prune,
    cmd_status,
    cmd_summary,
    cmd_warnings,
)
from blq.commands.mcp_cmd import cmd_mcp_install, cmd_mcp_serve
from blq.commands.migrate import cmd_migrate
from blq.commands.query_cmd import cmd_filter, cmd_query, cmd_shell, cmd_sql
from blq.commands.registry import cmd_commands, cmd_register, cmd_suggest, cmd_unregister
from blq.commands.report_cmd import cmd_report
from blq.commands.sync_cmd import cmd_sync
from blq.commands.watch_cmd import cmd_watch

__all__ = [
    # Init
    "cmd_init",
    # Clean
    "cmd_clean",
    # Migration
    "cmd_migrate",
    # CI
    "cmd_ci_check",
    "cmd_ci_comment",
    "cmd_ci_generate",
    # Report
    "cmd_report",
    # Execution
    "cmd_run",
    "cmd_exec",
    "cmd_import",
    "cmd_capture",
    # Hooks
    "cmd_hooks_generate",
    "cmd_hooks_install",
    "cmd_hooks_uninstall",
    "cmd_hooks_remove",
    "cmd_hooks_status",
    "cmd_hooks_run",
    "cmd_hooks_add",
    "cmd_hooks_list",
    # Watch
    "cmd_watch",
    # Query
    "cmd_query",
    "cmd_filter",
    "cmd_sql",
    "cmd_shell",
    # Management
    "cmd_status",
    "cmd_last",
    "cmd_info",
    "cmd_events",
    "cmd_errors",
    "cmd_warnings",
    "cmd_summary",
    "cmd_history",
    "cmd_prune",
    "cmd_formats",
    "cmd_completions",
    # Events
    "cmd_event",
    "cmd_context",
    "cmd_inspect",
    # Registry
    "cmd_commands",
    "cmd_register",
    "cmd_suggest",
    "cmd_unregister",
    # Sync
    "cmd_sync",
    # MCP
    "cmd_mcp_install",
    "cmd_mcp_serve",
]
