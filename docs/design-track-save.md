# Design: Transparent Command Tracking (`blq record`)

## Overview

Enable blq to capture command executions that happen outside of `blq run`, making blq useful as a passive observer rather than requiring explicit invocation. This is particularly valuable for AI agent workflows where commands run via the Bash tool.

The `blq record` command provides low-level access to blq's attempts/outcomes tracking system, designed primarily for integration with Claude Code hooks.

## Goals

1. **Transparent tracking**: Capture commands without changing how they're invoked
2. **Duration tracking**: Accurate timing via pre/post hooks
3. **Event extraction**: Parse output for errors/warnings even from ad-hoc commands
4. **Pattern detection**: Build intelligence from accumulated command history
5. **Efficiency feedback**: Show users/agents the benefit of using `blq run` directly

## Non-Goals

- Replacing `blq run` (that remains the optimal path)
- Automatic command interception (explicit hook setup required)
- General-purpose "save" functionality (this is specifically for invocation tracking)

## Architecture

The `blq record` command integrates with Claude Code's PreToolUse/PostToolUse hooks for the Bash tool:

```
PreToolUse (Bash)
    └── blq record attempt --command "$COMMAND" --json
    └── Returns attempt_id, stored in /tmp/blq-attempt-{tool_use_id}

[Bash command executes normally]

PostToolUse (Bash)
    └── Retrieve attempt_id from temp file
    └── echo "$OUTPUT" | blq record outcome --attempt=$ID --exit=$CODE [--parse]
    └── blq returns analysis + suggestions
    └── Pass to Claude via additionalContext
```

This builds on the attempts/outcomes schema (see `docs/design-live-inspection.md`):
- `blq record attempt` → writes to `attempts` table via `BirdStore.write_attempt()`
- `blq record outcome` → writes to `outcomes` table via `BirdStore.write_outcome()`

## Implementation Phases

### Phase 1: Simple "Did You Know?" Hook ✅ (Implemented)

A Claude Code PostToolUse hook that checks if a Bash command matches a registered blq command and suggests using the MCP tool instead.

**Command**: `blq commands suggest <command> [--json]`

**Hook**: Installed via `blq mcp install`, registered in `.claude/settings.json`

**Behavior**: After any Bash command, checks for matching registered command and outputs:
```json
{
  "hookSpecificOutput": {
    "additionalContext": "Tip: Use blq MCP tool run(command=\"test\") instead. ..."
  }
}
```

---

### Phase 2: `blq record attempt` - Pre-execution Registration

Register that a command is about to execute. Creates an attempt record and returns an ID for tracking.

#### Command Interface

```bash
# Start tracking, returns attempt ID
ATTEMPT_ID=$(blq record attempt --command "pytest tests/ -v" --json | jq -r '.attempt_id')

# With additional metadata
blq record attempt \
    --command "pytest tests/ -v" \
    --tag test \
    --format pytest_text \
    --json
```

#### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--command CMD` | Command string (required) | - |
| `--tag NAME` | Tag for grouping | from command |
| `--format FMT` | Expected parser format | auto-detect |
| `--cwd PATH` | Working directory | current |
| `--json` | Output JSON (required for hooks) | - |

#### Output (JSON)

```json
{
  "attempt_id": "att_01ABC123...",
  "command": "pytest tests/ -v",
  "tag": "test",
  "started_at": "2024-01-15T10:30:00Z"
}
```

#### Implementation

Calls `BirdStore.write_attempt()` with an `AttemptRecord`. The attempt appears with `status='pending'` until an outcome is recorded.

---

### Phase 3: `blq record outcome` - Post-execution Capture

Record the outcome of a command execution, optionally parsing output for events.

#### Command Interface

```bash
# Complete an attempt with outcome
echo "$OUTPUT" | blq record outcome \
    --attempt $ATTEMPT_ID \
    --exit 0

# With event extraction
echo "$OUTPUT" | blq record outcome \
    --attempt $ATTEMPT_ID \
    --exit 1 \
    --parse \
    --format pytest_text

# Standalone (no prior attempt - creates both records)
echo "$OUTPUT" | blq record outcome \
    --command "pytest tests/ -v" \
    --exit 1 \
    --parse

# From files
blq record outcome \
    --attempt $ATTEMPT_ID \
    --exit 0 \
    --stdout build.stdout \
    --stderr build.stderr
```

#### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--attempt ID` | Link to prior attempt | - |
| `--command CMD` | Command string (if no attempt) | - |
| `--exit CODE` | Exit code | 0 |
| `--parse` | Extract events from output | false |
| `--format FMT` | Parser format for events | auto-detect |
| `--tag NAME` | Tag (if no attempt) | from command |
| `--stdout FILE` | Stdout file | - |
| `--stderr FILE` | Stderr file | - |
| `--output FILE` | Combined output file | - |
| `--json` | Output JSON | - |

If no file flags, reads from stdin.

#### Output (JSON)

```json
{
  "recorded": true,
  "attempt_id": "att_01ABC123...",
  "run_id": 42,
  "exit_code": 1,
  "duration_ms": 15230,
  "output_bytes": 12847,
  "events": {
    "total": 15,
    "errors": 3,
    "warnings": 12
  },
  "suggestion": {
    "type": "register",
    "reason": "Command run 4 times, 12KB avg output",
    "command": "blq commands register test \"pytest tests/ -v\"",
    "mcp_tool": "register_command(name=\"test\", cmd=\"pytest tests/ -v\")"
  }
}
```

#### Implementation

1. If `--attempt` provided:
   - Calls `BirdStore.write_outcome()` with duration calculated from attempt start
   - Links to existing attempt record
2. If `--command` provided (no attempt):
   - Creates both attempt and outcome records (for standalone use)
3. If `--parse`:
   - Writes output to blob storage
   - Parses output using duck_hunt (format auto-detected or specified)
   - Writes events to `events` table
4. Analyzes patterns and returns suggestions

---

### Phase 4: Full Claude Code Hook Integration

Pre and Post hooks that provide complete visibility into Bash commands:

```bash
# .claude/hooks/blq-record-pre.sh (PreToolUse for Bash)
#!/bin/bash
set -e
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
TOOL_USE_ID=$(echo "$INPUT" | jq -r '.tool_use_id')

[[ -z "$COMMAND" ]] && exit 0
command -v blq >/dev/null 2>&1 || exit 0
[[ ! -d .lq ]] && exit 0

# Record attempt
RESULT=$(blq record attempt --command "$COMMAND" --json 2>/dev/null || true)
if [[ -n "$RESULT" ]]; then
    ATTEMPT_ID=$(echo "$RESULT" | jq -r '.attempt_id')
    echo "$ATTEMPT_ID" > "/tmp/blq-attempt-$TOOL_USE_ID"
fi
exit 0
```

```bash
# .claude/hooks/blq-record-post.sh (PostToolUse for Bash)
#!/bin/bash
set -e
INPUT=$(cat)
TOOL_USE_ID=$(echo "$INPUT" | jq -r '.tool_use_id')
OUTPUT=$(echo "$INPUT" | jq -r '.tool_response.output // ""')
EXIT_CODE=$(echo "$INPUT" | jq -r '.tool_response.exit_code // 0')

ATTEMPT_FILE="/tmp/blq-attempt-$TOOL_USE_ID"
[[ ! -f "$ATTEMPT_FILE" ]] && exit 0

ATTEMPT_ID=$(cat "$ATTEMPT_FILE")
rm -f "$ATTEMPT_FILE"

# Record outcome with parsing
RESULT=$(echo "$OUTPUT" | blq record outcome \
    --attempt "$ATTEMPT_ID" \
    --exit "$EXIT_CODE" \
    --parse \
    --json 2>/dev/null || true)

# Return suggestion if present
if [[ -n "$RESULT" ]]; then
    SUGGESTION=$(echo "$RESULT" | jq -r '.suggestion.reason // empty')
    if [[ -n "$SUGGESTION" ]]; then
        MCP_TOOL=$(echo "$RESULT" | jq -r '.suggestion.mcp_tool // empty')
        jq -n --arg sug "$SUGGESTION" --arg mcp "$MCP_TOOL" '{
            hookSpecificOutput: {
                hookEventName: "PostToolUse",
                additionalContext: "blq: \($sug). Use: \($mcp)"
            }
        }'
    fi
fi
exit 0
```

**Registration** (`.claude/settings.json`):
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": ".claude/hooks/blq-record-pre.sh" }]
    }],
    "PostToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "command": ".claude/hooks/blq-record-post.sh" }]
    }]
  }
}
```

#### What This Enables

| Capability | Description |
|------------|-------------|
| **Full visibility** | blq sees ALL Bash commands, not just registered ones |
| **Accurate duration** | Pre records start time, Post calculates duration |
| **Event extraction** | Parse errors/warnings even from ad-hoc commands |
| **Pattern detection** | Track command frequency, output size, failure rates |
| **Smart suggestions** | "Run 4 times, consider registering" |
| **Reduced context** | blq stores full output, agent gets summary |

#### Stale Attempts

Attempts without an outcome within 1 hour are marked as `status='orphaned'` (indicates crashed/interrupted command).

---

### Phase 5: `blq output` Enhancements

Add grep-style filtering to output retrieval:

```bash
blq output 42                          # Full output
blq output 42 --tail 50                # Last 50 lines
blq output 42 --head 50                # First 50 lines
blq output 42 --grep "FAILED"          # Lines matching pattern
blq output 42 --grep "error:" -B 5     # 5 lines before each match
blq output 42 --grep "error:" -A 10    # 10 lines after each match
blq output 42 --grep "FAILED" -C 3     # 3 lines context (before + after)
```

This is valuable for agents investigating failures without loading full output into context.

---

## Data Model

Uses the existing attempts/outcomes schema from BIRD v5 (see `docs/design-live-inspection.md`):

### Attempt Record

Written by `blq record attempt`, maps to `attempts` table:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Attempt identifier |
| `session_id` | VARCHAR | Session grouping |
| `cmd` | VARCHAR | Command string |
| `cwd` | VARCHAR | Working directory |
| `timestamp` | TIMESTAMP | Start time |
| `tag` | VARCHAR | Grouping tag |
| `format_hint` | VARCHAR | Expected parser |
| `git_commit` | VARCHAR | Git context |
| + other metadata fields | | |

### Outcome Record

Written by `blq record outcome`, maps to `outcomes` table:

| Field | Type | Description |
|-------|------|-------------|
| `attempt_id` | UUID | Link to attempt |
| `completed_at` | TIMESTAMP | Completion time |
| `duration_ms` | BIGINT | Duration (calculated) |
| `exit_code` | INTEGER | Exit code (NULL = crashed) |
| `signal` | INTEGER | Signal if killed |
| `timeout` | BOOLEAN | Whether timed out |

### Status Derivation

| Condition | Status |
|-----------|--------|
| No outcome record | `pending` (still running) |
| Outcome with NULL exit_code | `orphaned` (crashed) |
| Outcome with exit_code | `completed` |

---

## Configuration

### User Config (`~/.config/blq/config.toml`)

```toml
[record]
# Auto-detect format from command
auto_format = true

# Show efficiency tip when recording outcome
show_tip = true

# Minimum output size (bytes) to show tip
tip_threshold = 1000

# Suggest registration after N uses of same command
suggest_register_threshold = 3
```

### Project Config (`.lq/config.toml`)

```toml
[record]
# Commands to ignore (regex patterns)
ignore_patterns = [
  "^(cd|ls|cat|head|tail|echo|pwd)\\b",
  "^git (status|log|diff)\\b"
]

# Always record these (even if small output)
always_record = ["make", "npm", "pytest", "cargo"]

# Always parse events for these commands
always_parse = ["pytest", "mypy", "ruff", "cargo"]
```

---

## Hook Installation

### Current: `blq mcp install`

Installs Phase 1 suggest hook:
- Creates `.claude/hooks/blq-suggest.sh`
- Registers in `.claude/settings.json`

### Future: `blq mcp install --record`

Installs full recording hooks (Phase 4):
- Creates `.claude/hooks/blq-record-pre.sh`
- Creates `.claude/hooks/blq-record-post.sh`
- Registers both in `.claude/settings.json`

---

## Migration Path

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | `blq commands suggest` + basic hook | ✅ Implemented |
| Phase 2 | `blq record attempt` command | Planned |
| Phase 3 | `blq record outcome` command | Planned |
| Phase 4 | Full pre/post hook integration | Planned |
| Phase 5 | `blq output` grep/context | Future |

Each phase is independently useful and builds on the previous.

---

## Open Questions

1. **Attempt cleanup**: How long before orphaned attempts are pruned? (Proposed: 1 hour for orphaned, 24 hours for pending)

2. **Default parsing**: Should `--parse` be default when format is auto-detected? Or always explicit?

3. **Output storage**: Always store raw output, or only when `--parse` is used? (Proposed: always store, prune handles cleanup)

4. **MCP integration**: Add `record_attempt()` and `record_outcome()` MCP tools for non-Bash scenarios?

5. **Pattern storage**: Where to store pattern data (command frequency, etc.)? Separate table or derive from attempts?
