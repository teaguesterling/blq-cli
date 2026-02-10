#!/bin/bash
# Claude Code PostToolUse hook for Bash commands
# Suggests using blq MCP run tool when a matching registered command is found
#
# Installed by: blq mcp install
# Manual install: add to .claude/settings.json:
# {
#   "hooks": {
#     "PostToolUse": [{
#       "matcher": "Bash",
#       "hooks": [{ "type": "command", "command": ".claude/hooks/blq-suggest.sh" }]
#     }]
#   }
# }

set -e

# Read hook input from stdin
INPUT=$(cat)

# Extract the command that was run
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Skip if no command
[[ -z "$COMMAND" ]] && exit 0

# Skip if blq not available
command -v blq >/dev/null 2>&1 || exit 0

# Skip if .lq not initialized (no MCP server to suggest)
[[ ! -d .lq ]] && exit 0

# Skip if MCP not configured (no .mcp.json)
[[ ! -f .mcp.json ]] && exit 0

# Get suggestion from blq (returns nothing if no match)
SUGGESTION=$(blq commands suggest "$COMMAND" --json 2>/dev/null || true)

# If we got a suggestion, output it for Claude to see
if [[ -n "$SUGGESTION" ]]; then
    TIP=$(echo "$SUGGESTION" | jq -r '.tip // empty')
    MCP_TOOL=$(echo "$SUGGESTION" | jq -r '.mcp_tool // empty')

    jq -n --arg tip "$TIP" --arg mcp "$MCP_TOOL" '{
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: "Tip: Use blq MCP tool \($mcp) instead. \($tip)"
        }
    }'
fi

exit 0
