"""
Management commands for blq CLI.

Handles status, errors, warnings, summary, history, and prune operations.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta

import duckdb

from blq.commands.core import (
    LOGS_DIR,
    ensure_initialized,
    get_store_for_args,
)


def cmd_status(args: argparse.Namespace) -> None:
    """Show status of all sources."""
    try:
        store = get_store_for_args(args)
        conn = store.connection

        if args.verbose:
            result = conn.execute("FROM lq_status_verbose()").fetchdf()
        else:
            result = conn.execute("FROM lq_status()").fetchdf()
        print(result.to_string(index=False))
    except duckdb.Error:
        # Fallback if macros aren't working
        store = get_store_for_args(args)
        result = store.events().limit(10).df()
        print(result.to_string(index=False))


def cmd_errors(args: argparse.Namespace) -> None:
    """Show recent errors."""
    try:
        store = get_store_for_args(args)
        query = store.errors()

        # Filter by source if specified
        if args.source:
            query = query.filter(source_name=args.source)

        # Order by run_id desc, event_id
        query = query.order_by("run_id", desc=True).limit(args.limit)

        # Select columns based on compact mode
        if args.compact:
            query = query.select("run_id", "event_id", "file_path", "line_number", "message")

        result = query.df()

        if args.json:
            print(result.to_json(orient="records"))
        else:
            print(result.to_string(index=False))
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_warnings(args: argparse.Namespace) -> None:
    """Show recent warnings."""
    try:
        store = get_store_for_args(args)
        result = store.warnings().order_by("run_id", desc=True).limit(args.limit).df()
        print(result.to_string(index=False))
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_summary(args: argparse.Namespace) -> None:
    """Show aggregate summary."""
    try:
        store = get_store_for_args(args)
        conn = store.connection

        if args.latest:
            result = conn.execute("FROM lq_summary_latest()").fetchdf()
        else:
            result = conn.execute("FROM lq_summary()").fetchdf()
        print(result.to_string(index=False))
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_history(args: argparse.Namespace) -> None:
    """Show run history."""
    try:
        store = get_store_for_args(args)
        result = store.runs().head(args.limit)
        print(result.to_string(index=False))
    except duckdb.Error as e:
        print(f"Error: {e}", file=sys.stderr)


def cmd_prune(args: argparse.Namespace) -> None:
    """Remove old log files."""
    lq_dir = ensure_initialized()
    logs_dir = lq_dir / LOGS_DIR

    cutoff = datetime.now() - timedelta(days=args.older_than)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    removed = 0
    for date_dir in logs_dir.glob("date=*"):
        date_str = date_dir.name.replace("date=", "")
        if date_str < cutoff_str:
            if args.dry_run:
                print(f"Would remove: {date_dir}")
            else:
                shutil.rmtree(date_dir)
                print(f"Removed: {date_dir}")
            removed += 1

    if removed == 0:
        print(f"No logs older than {args.older_than} days")
    elif args.dry_run:
        print(f"\nDry run: would remove {removed} date partitions")


def cmd_formats(args: argparse.Namespace) -> None:
    """List available log formats."""
    conn = duckdb.connect(":memory:")

    # Try to load duck_hunt
    try:
        conn.execute("LOAD duck_hunt")
    except duckdb.Error:
        print("duck_hunt extension not available.", file=sys.stderr)
        print("\nBuilt-in formats (fallback parser):", file=sys.stderr)
        print("  auto    - Automatic detection of common formats")
        print("  generic - Generic file:line:col: message pattern")
        sys.exit(1)

    # Get formats from duck_hunt
    try:
        result = conn.execute("SELECT * FROM duck_hunt_formats()").fetchall()
    except duckdb.Error as e:
        print(f"Error querying formats: {e}", file=sys.stderr)
        sys.exit(1)

    # Group by category
    categories: dict[str, list[tuple]] = {}
    for row in result:
        name, desc, category, *_ = row
        if category not in categories:
            categories[category] = []
        categories[category].append((name, desc))

    # Display order
    category_order = [
        "meta",
        "build_system",
        "test_framework",
        "linting_tool",
        "python_tool",
        "security_tool",
        "ci_system",
        "infrastructure_tool",
        "debugging_tool",
        "structured_log",
        "system_log",
        "web_access",
        "cloud_audit",
    ]

    # Nice category names
    category_names = {
        "meta": "Meta",
        "build_system": "Build Systems",
        "test_framework": "Test Frameworks",
        "linting_tool": "Linting Tools",
        "python_tool": "Python Tools",
        "security_tool": "Security Tools",
        "ci_system": "CI/CD Systems",
        "infrastructure_tool": "Infrastructure",
        "debugging_tool": "Debugging",
        "structured_log": "Structured Logs",
        "system_log": "System Logs",
        "web_access": "Web Access Logs",
        "cloud_audit": "Cloud Audit Logs",
    }

    print(f"Available log formats ({len(result)} total):\n")

    for cat in category_order:
        if cat not in categories:
            continue
        formats = categories[cat]
        cat_name = category_names.get(cat, cat)
        print(f"  {cat_name}:")
        for name, desc in sorted(formats):
            print(f"    {name:24} {desc}")
        print()

    # Any remaining categories
    for cat, formats in categories.items():
        if cat not in category_order:
            print(f"  {cat}:")
            for name, desc in sorted(formats):
                print(f"    {name:24} {desc}")
            print()


def cmd_completions(args: argparse.Namespace) -> None:
    """Generate shell completion scripts."""
    shell = args.shell

    if shell == "bash":
        print(_bash_completion())
    elif shell == "zsh":
        print(_zsh_completion())
    elif shell == "fish":
        print(_fish_completion())
    else:
        print(f"Unsupported shell: {shell}", file=sys.stderr)
        print("Supported shells: bash, zsh, fish", file=sys.stderr)
        sys.exit(1)


def _bash_completion() -> str:
    """Generate bash completion script."""
    return """# blq bash completion
# Add to ~/.bashrc or ~/.bash_completion:
#   eval "$(blq completions bash)"
# Or save to a file:
#   blq completions bash > /etc/bash_completion.d/blq

_blq_completions() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Main commands
    commands="init run r exec e import capture status errors warnings"
    commands="$commands summary history sql shell prune formats event"
    commands="$commands context commands register unregister sync"
    commands="$commands query q filter f serve completions"

    # Complete commands
    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${commands}" -- "${cur}") )
        return 0
    fi

    # Command-specific completions
    case "${COMP_WORDS[1]}" in
        run|r)
            # Complete registered command names
            if [[ -f .lq/commands.yaml ]]; then
                local registered=$(grep -E "^[a-zA-Z]" .lq/commands.yaml 2>/dev/null | cut -d: -f1)
                COMPREPLY=( $(compgen -W "${registered}" -- "${cur}") )
            fi
            ;;
        exec|e)
            # Complete files and common options
            if [[ "${cur}" == -* ]]; then
                local opts="--name --format --keep-raw --json --markdown"
                opts="$opts --quiet --summary --verbose --include-warnings"
                opts="$opts --error-limit --no-capture"
                COMPREPLY=( $(compgen -W "$opts" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -f -- "${cur}") )
            fi
            ;;
        import)
            # Complete log files
            COMPREPLY=( $(compgen -f -X "!*.log" -- "${cur}") )
            COMPREPLY+=( $(compgen -f -X "!*.txt" -- "${cur}") )
            COMPREPLY+=( $(compgen -d -- "${cur}") )
            ;;
        query|q|filter|f)
            # Complete log files and options
            if [[ "${cur}" == -* ]]; then
                local opts="--select --filter --order --limit --json --csv --markdown"
                COMPREPLY=( $(compgen -W "$opts" -- "${cur}") )
            else
                COMPREPLY=( $(compgen -f -- "${cur}") )
            fi
            ;;
        event|context)
            # No specific completions for refs
            ;;
        errors|warnings)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=( $(compgen -W "--source --limit --compact --json" -- "${cur}") )
            fi
            ;;
        register)
            if [[ "${cur}" == -* ]]; then
                local opts="--description --timeout --format --no-capture --force"
                COMPREPLY=( $(compgen -W "$opts" -- "${cur}") )
            fi
            ;;
        completions)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "${cur}") )
            ;;
        *)
            # Default to file completion
            COMPREPLY=( $(compgen -f -- "${cur}") )
            ;;
    esac
}

complete -F _blq_completions blq
"""


def _zsh_completion() -> str:
    """Generate zsh completion script."""
    return """#compdef blq
# blq zsh completion
# Add to ~/.zshrc:
#   eval "$(blq completions zsh)"
# Or save to a file in your fpath:
#   blq completions zsh > ~/.zsh/completions/_blq

_blq() {
    local -a commands
    commands=(
        'init:Initialize .lq directory'
        'run:Run registered command (alias: r)'
        'r:Run registered command'
        'exec:Execute ad-hoc command (alias: e)'
        'e:Execute ad-hoc command'
        'import:Import existing log file'
        'capture:Capture from stdin'
        'status:Show status of all sources'
        'errors:Show recent errors'
        'warnings:Show recent warnings'
        'summary:Aggregate summary'
        'history:Show run history'
        'sql:Run arbitrary SQL'
        'shell:Interactive SQL shell'
        'prune:Remove old logs'
        'formats:List available log formats'
        'event:Show event details by reference'
        'context:Show context lines around event'
        'commands:List registered commands'
        'register:Register a command'
        'unregister:Remove a registered command'
        'sync:Sync logs to central location'
        'query:Query log files or stored events (alias: q)'
        'q:Query log files or stored events'
        'filter:Filter with simple syntax (alias: f)'
        'f:Filter with simple syntax'
        'serve:Start MCP server'
        'completions:Generate shell completions'
    )

    _arguments -C \\
        '-V[Show version]' \\
        '--version[Show version]' \\
        '-F[Log format]:format:' \\
        '--log-format[Log format]:format:' \\
        '-g[Query global store]' \\
        '--global[Query global store]' \\
        '-d[Database path]:path:_files' \\
        '--database[Database path]:path:_files' \\
        '1:command:->command' \\
        '*::args:->args'

    case "$state" in
        command)
            _describe -t commands 'blq command' commands
            ;;
        args)
            case "${words[1]}" in
                run|r)
                    # Complete registered commands
                    if [[ -f .lq/commands.yaml ]]; then
                        local -a registered
                        local cmd="grep -E '^[a-zA-Z]' .lq/commands.yaml 2>/dev/null"
                        registered=(${(f)"$($cmd | cut -d: -f1)"})
                        _describe -t registered 'registered command' registered
                    fi
                    ;;
                exec|e)
                    _arguments \\
                        '-n[Source name]:name:' \\
                        '--name[Source name]:name:' \\
                        '-f[Parse format]:format:' \\
                        '--format[Parse format]:format:' \\
                        '-r[Keep raw output]' \\
                        '--keep-raw[Keep raw output]' \\
                        '-j[JSON output]' \\
                        '--json[JSON output]' \\
                        '-m[Markdown output]' \\
                        '--markdown[Markdown output]' \\
                        '-q[Quiet mode]' \\
                        '--quiet[Quiet mode]' \\
                        '-s[Show summary]' \\
                        '--summary[Show summary]' \\
                        '-v[Verbose mode]' \\
                        '--verbose[Verbose mode]' \\
                        '-w[Include warnings]' \\
                        '--include-warnings[Include warnings]' \\
                        '-N[Skip capture]' \\
                        '--no-capture[Skip capture]' \\
                        '*:command:_command_names -e'
                    ;;
                import)
                    _arguments \\
                        '-n[Source name]:name:' \\
                        '--name[Source name]:name:' \\
                        '*:file:_files -g "*.log *.txt"'
                    ;;
                query|q)
                    _arguments \\
                        '-s[Select columns]:columns:' \\
                        '--select[Select columns]:columns:' \\
                        '-f[Filter]:filter:' \\
                        '--filter[Filter]:filter:' \\
                        '-o[Order by]:column:' \\
                        '--order[Order by]:column:' \\
                        '-l[Limit]:number:' \\
                        '--limit[Limit]:number:' \\
                        '-j[JSON output]' \\
                        '--json[JSON output]' \\
                        '-c[CSV output]' \\
                        '--csv[CSV output]' \\
                        '-m[Markdown output]' \\
                        '--markdown[Markdown output]' \\
                        '*:file:_files'
                    ;;
                errors|warnings)
                    _arguments \\
                        '-s[Filter by source]:source:' \\
                        '--source[Filter by source]:source:' \\
                        '-n[Max results]:number:' \\
                        '--limit[Max results]:number:' \\
                        '-c[Compact format]' \\
                        '--compact[Compact format]' \\
                        '-j[JSON output]' \\
                        '--json[JSON output]'
                    ;;
                completions)
                    _arguments '1:shell:(bash zsh fish)'
                    ;;
                *)
                    _files
                    ;;
            esac
            ;;
    esac
}

_blq "$@"
"""


def _fish_completion() -> str:
    """Generate fish completion script."""
    return """# blq fish completion
# Save to ~/.config/fish/completions/blq.fish:
#   blq completions fish > ~/.config/fish/completions/blq.fish

# Disable file completion by default
complete -c blq -f

# Commands
complete -c blq -n "__fish_use_subcommand" -a init -d "Initialize .lq directory"
complete -c blq -n "__fish_use_subcommand" -a run -d "Run registered command"
complete -c blq -n "__fish_use_subcommand" -a r -d "Run registered command (alias)"
complete -c blq -n "__fish_use_subcommand" -a exec -d "Execute ad-hoc command"
complete -c blq -n "__fish_use_subcommand" -a e -d "Execute ad-hoc command (alias)"
complete -c blq -n "__fish_use_subcommand" -a import -d "Import existing log file"
complete -c blq -n "__fish_use_subcommand" -a capture -d "Capture from stdin"
complete -c blq -n "__fish_use_subcommand" -a status -d "Show status of all sources"
complete -c blq -n "__fish_use_subcommand" -a errors -d "Show recent errors"
complete -c blq -n "__fish_use_subcommand" -a warnings -d "Show recent warnings"
complete -c blq -n "__fish_use_subcommand" -a summary -d "Aggregate summary"
complete -c blq -n "__fish_use_subcommand" -a history -d "Show run history"
complete -c blq -n "__fish_use_subcommand" -a sql -d "Run arbitrary SQL"
complete -c blq -n "__fish_use_subcommand" -a shell -d "Interactive SQL shell"
complete -c blq -n "__fish_use_subcommand" -a prune -d "Remove old logs"
complete -c blq -n "__fish_use_subcommand" -a formats -d "List available log formats"
complete -c blq -n "__fish_use_subcommand" -a event -d "Show event details"
complete -c blq -n "__fish_use_subcommand" -a context -d "Show context around event"
complete -c blq -n "__fish_use_subcommand" -a commands -d "List registered commands"
complete -c blq -n "__fish_use_subcommand" -a register -d "Register a command"
complete -c blq -n "__fish_use_subcommand" -a unregister -d "Remove a registered command"
complete -c blq -n "__fish_use_subcommand" -a sync -d "Sync logs to central location"
complete -c blq -n "__fish_use_subcommand" -a query -d "Query log files"
complete -c blq -n "__fish_use_subcommand" -a q -d "Query log files (alias)"
complete -c blq -n "__fish_use_subcommand" -a filter -d "Filter with simple syntax"
complete -c blq -n "__fish_use_subcommand" -a f -d "Filter with simple syntax (alias)"
complete -c blq -n "__fish_use_subcommand" -a serve -d "Start MCP server"
complete -c blq -n "__fish_use_subcommand" -a completions -d "Generate shell completions"

# Global options
complete -c blq -s V -l version -d "Show version"
complete -c blq -s F -l log-format -d "Log format for parsing"
complete -c blq -s g -l global -d "Query global store"
complete -c blq -s d -l database -d "Database path"

# completions subcommand
complete -c blq -n "__fish_seen_subcommand_from completions" -a "bash zsh fish" -d "Shell type"

# exec options
complete -c blq -n "__fish_seen_subcommand_from exec e" -s n -l name -d "Source name"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s f -l format -d "Parse format"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s r -l keep-raw -d "Keep raw output"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s j -l json -d "JSON output"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s q -l quiet -d "Quiet mode"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s s -l summary -d "Show summary"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s v -l verbose -d "Verbose mode"
complete -c blq -n "__fish_seen_subcommand_from exec e" -s N -l no-capture -d "Skip capture"

# errors/warnings options
complete -c blq -n "__fish_seen_subcommand_from errors warnings" \\
    -s s -l source -d "Filter by source"
complete -c blq -n "__fish_seen_subcommand_from errors warnings" -s n -l limit -d "Max results"
complete -c blq -n "__fish_seen_subcommand_from errors warnings" -s c -l compact -d "Compact format"
complete -c blq -n "__fish_seen_subcommand_from errors warnings" -s j -l json -d "JSON output"

# import - complete log files
complete -c blq -n "__fish_seen_subcommand_from import" -F -d "Log file"

# query/filter - complete files
complete -c blq -n "__fish_seen_subcommand_from query q filter f" -F -d "Log file"
"""
