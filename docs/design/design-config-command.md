# Design: `blq config` Command and Hooks Configuration

## Overview

The `blq config` command provides a CLI interface for managing user-level configuration stored in `~/.config/blq/config.toml`. This document covers:

1. Command interface and semantics
2. Configuration precedence rules
3. Hooks section for controlling hook behavior
4. Integration with existing commands

## User Config File

**Location**: `~/.config/blq/config.toml` (or `$XDG_CONFIG_HOME/blq/config.toml`)

**Purpose**: Store global preferences that apply across all blq projects. Project-specific settings go in `.lq/config.toml`.

### Current Sections

```toml
[init]
auto_mcp = true           # Create .mcp.json on init
auto_gitignore = true     # Add .lq/ to .gitignore
default_storage = "bird"  # Default storage mode
auto_detect = false       # Auto-detect commands on init

[register]
auto_init = true          # Auto-init on register if not initialized

[output]
default_format = "table"  # table, json, markdown
default_limit = 20        # Default limit for history, errors, etc.

[run]
show_summary = false      # Always show summary after runs
keep_raw = false          # Always keep raw output

[mcp]
safe_mode = false         # MCP server safe mode

[storage]
auto_prune = false        # Enable automatic pruning
prune_days = 30           # Auto-prune logs older than N days

[defaults]
extra_capture_env = []    # Additional env vars to capture
```

### New `[hooks]` Section

```toml
[hooks]
auto_claude_code = false  # Auto-install Claude Code hooks with mcp install
```

## Command Interface

### Basic Usage

```bash
blq config                    # Show all non-default settings
blq config --all              # Show all settings with defaults
blq config --path             # Show config file path
blq config --edit             # Open config in $EDITOR
```

### Get/Set/Unset

```bash
blq config get <key>          # Get a specific value
blq config set <key> <value>  # Set a value
blq config unset <key>        # Remove setting (revert to default)
```

### Key Format

Keys use dot notation matching the TOML structure:

```bash
blq config get init.auto_mcp           # [init] section, auto_mcp key
blq config set hooks.auto_claude_code true
blq config set output.default_limit 50
```

## Semantics

### Display Behavior

**`blq config`** (no arguments):
- Shows only settings that differ from defaults
- If config file doesn't exist or all values are defaults, shows "Using defaults"
- Output format: `key = value` (TOML-like)

```
$ blq config
init.auto_mcp = true
register.auto_init = true
hooks.auto_claude_code = true
```

**`blq config --all`**:
- Shows all settings including defaults
- Marks default values with a comment

```
$ blq config --all
# [init]
init.auto_mcp = true
init.auto_gitignore = true  # (default)
init.default_storage = "bird"  # (default)
init.auto_detect = false  # (default)

# [register]
register.auto_init = true

# [hooks]
hooks.auto_claude_code = false  # (default)
...
```

### Get Behavior

**`blq config get <key>`**:
- Prints the value only (for scripting)
- Exit 0 if key exists, exit 1 if key doesn't exist
- Returns default value if not explicitly set

```bash
$ blq config get init.auto_mcp
true

$ blq config get nonexistent.key
Error: Unknown config key 'nonexistent.key'
$ echo $?
1
```

### Set Behavior

**`blq config set <key> <value>`**:
- Creates config file and parent directories if needed
- Validates key exists in schema
- Validates value type matches expected type
- Only writes non-default values to keep file minimal

**Type Coercion**:
| Expected Type | Accepted Values |
|--------------|-----------------|
| bool | `true`, `false`, `yes`, `no`, `1`, `0` |
| int | Integer strings: `20`, `100` |
| string | Any string value |
| list[str] | Comma-separated: `VAR1,VAR2,VAR3` |

```bash
$ blq config set init.auto_mcp true
Set init.auto_mcp = true

$ blq config set output.default_limit 50
Set output.default_limit = 50

$ blq config set defaults.extra_capture_env "MY_VAR,OTHER_VAR"
Set defaults.extra_capture_env = ["MY_VAR", "OTHER_VAR"]
```

**Validation Errors**:
```bash
$ blq config set init.auto_mcp maybe
Error: Invalid boolean value 'maybe' for init.auto_mcp
Valid values: true, false, yes, no, 1, 0

$ blq config set output.default_limit abc
Error: Invalid integer value 'abc' for output.default_limit

$ blq config set fake.key value
Error: Unknown config key 'fake.key'
Available keys: init.auto_mcp, init.auto_gitignore, ...
```

### Unset Behavior

**`blq config unset <key>`**:
- Removes the key from config file (reverts to default)
- If section becomes empty, removes the section
- If file becomes empty, deletes the file

```bash
$ blq config unset init.auto_mcp
Unset init.auto_mcp (default: true)
```

### Edit Behavior

**`blq config --edit`**:
- Opens config file in `$EDITOR` (or `$VISUAL`, or fallback to `vi`)
- Creates file with commented template if it doesn't exist
- Validates TOML syntax after editing (warning only, don't reject)

## Hooks Configuration

### `hooks.auto_claude_code`

**Default**: `false`

**Behavior**: When `true`, `blq mcp install` automatically installs Claude Code hooks (equivalent to `blq mcp install --hooks`).

**Precedence**:
1. CLI flag `--hooks` explicitly enables hooks
2. CLI flag `--no-hooks` explicitly disables hooks
3. If neither flag: use `hooks.auto_claude_code` config value
4. If not in config: default to `false`

```bash
# With hooks.auto_claude_code = true in config:
blq mcp install              # Installs hooks (from config)
blq mcp install --no-hooks   # Skips hooks (CLI overrides)

# With hooks.auto_claude_code = false in config:
blq mcp install              # Skips hooks (from config)
blq mcp install --hooks      # Installs hooks (CLI overrides)
```

### Future Hook Settings

The `[hooks]` section can be extended for other hook behaviors:

```toml
[hooks]
auto_claude_code = true       # Auto-install Claude Code hooks
auto_git_hooks = false        # Auto-install git hooks on init

# Claude Code hook behavior
suggest_threshold = 0.8       # Similarity threshold for suggestions (future)
suggest_templates = true      # Include template command suggestions
```

## Precedence Rules

Configuration values are resolved in this order (highest priority first):

1. **CLI flags** (`--hooks`, `--no-capture`, etc.)
2. **Environment variables** (`BLQ_*` - if we add these)
3. **Project config** (`.lq/config.toml` - for project-specific overrides)
4. **User config** (`~/.config/blq/config.toml`)
5. **Built-in defaults** (hardcoded in `UserConfig` class)

### Example: MCP Safe Mode

```bash
# Precedence for MCP safe mode:
blq mcp serve --safe-mode          # 1. CLI flag wins
BLQ_MCP_SAFE_MODE=true blq mcp serve  # 2. Env var (if supported)
# .lq/config.toml [mcp] safe_mode = true  # 3. Project config
# ~/.config/blq/config.toml [mcp] safe_mode = true  # 4. User config
# Default: false                    # 5. Built-in default
```

## Implementation Notes

### Config Schema

Define a schema for validation:

```python
CONFIG_SCHEMA = {
    "init.auto_mcp": {"type": "bool", "default": None},  # None = mcp_available()
    "init.auto_gitignore": {"type": "bool", "default": True},
    "init.default_storage": {"type": "str", "default": "bird"},
    "init.auto_detect": {"type": "bool", "default": False},
    "register.auto_init": {"type": "bool", "default": False},
    "output.default_format": {"type": "str", "default": "table"},
    "output.default_limit": {"type": "int", "default": 20},
    "run.show_summary": {"type": "bool", "default": False},
    "run.keep_raw": {"type": "bool", "default": False},
    "mcp.safe_mode": {"type": "bool", "default": False},
    "storage.auto_prune": {"type": "bool", "default": False},
    "storage.prune_days": {"type": "int", "default": 30},
    "hooks.auto_claude_code": {"type": "bool", "default": False},
    "defaults.extra_capture_env": {"type": "list[str]", "default": []},
}
```

### Minimal File Writing

When saving config, only write non-default values:

```python
# Good: Only non-defaults written
[init]
auto_mcp = true

[hooks]
auto_claude_code = true

# Bad: All values written (verbose, hard to see customizations)
[init]
auto_mcp = true
auto_gitignore = true
default_storage = "bird"
...
```

### JSON Output Mode

Support `--json` for scripting:

```bash
$ blq config --json
{
  "init.auto_mcp": true,
  "register.auto_init": true,
  "hooks.auto_claude_code": true
}

$ blq config get init.auto_mcp --json
{"init.auto_mcp": true}
```

## CLI Changes

### Add `--no-hooks` to `mcp install`

```bash
blq mcp install [--hooks] [--no-hooks] [--force]
```

The `--hooks` and `--no-hooks` flags are mutually exclusive. If neither is specified, behavior follows `hooks.auto_claude_code` config.

### Add `config` Subcommand

```
blq config                      Show non-default settings
blq config --all                Show all settings with defaults
blq config --path               Show config file path
blq config --edit               Open in $EDITOR
blq config --json               Output as JSON
blq config get <key>            Get specific value
blq config set <key> <value>    Set a value
blq config unset <key>          Remove setting (revert to default)
```

## Error Handling

### Missing Config File

- `blq config`: Shows "Using defaults" or similar
- `blq config get <key>`: Returns default value
- `blq config set <key> <value>`: Creates file

### Invalid TOML

- `blq config`: Shows error with line number, suggests `--edit`
- `blq config set`: Refuses to modify, suggests fixing manually
- After `--edit`: Warns if invalid, but doesn't reject

### Permission Errors

- Clear error message with path
- Suggest checking permissions or using sudo (if appropriate)

## Examples

### Workflow: Enable Auto-Hooks

```bash
# Check current setting
$ blq config get hooks.auto_claude_code
false

# Enable auto-hooks
$ blq config set hooks.auto_claude_code true
Set hooks.auto_claude_code = true

# Now mcp install includes hooks by default
$ blq mcp install
Configured blq MCP server in .mcp.json
Created .claude/hooks/blq-suggest.sh
...
```

### Workflow: Scripting

```bash
# Check if auto_mcp is enabled
if [ "$(blq config get init.auto_mcp)" = "true" ]; then
    echo "MCP auto-creation enabled"
fi

# Batch configuration
blq config set init.auto_mcp true
blq config set register.auto_init true
blq config set hooks.auto_claude_code true
```

### Workflow: View All Settings

```bash
$ blq config --all
# User config: /home/user/.config/blq/config.toml

# [init]
init.auto_mcp = true
init.auto_gitignore = true  # (default)
init.default_storage = "bird"  # (default)
init.auto_detect = false  # (default)

# [register]
register.auto_init = true

# [output]
output.default_format = "table"  # (default)
output.default_limit = 20  # (default)

# [run]
run.show_summary = false  # (default)
run.keep_raw = false  # (default)

# [mcp]
mcp.safe_mode = false  # (default)

# [storage]
storage.auto_prune = false  # (default)
storage.prune_days = 30  # (default)

# [hooks]
hooks.auto_claude_code = true

# [defaults]
defaults.extra_capture_env = []  # (default)
```

## Testing

1. **Config loading**: Verify defaults, file parsing, type coercion
2. **Get command**: Default values, explicit values, missing keys
3. **Set command**: Type validation, file creation, minimal writing
4. **Unset command**: Key removal, section cleanup, file deletion
5. **Hooks integration**: `--hooks`/`--no-hooks` vs config precedence
6. **Edge cases**: Empty file, invalid TOML, permission errors

## Migration

No migration needed - this is a new command. Existing `user_config.py` already handles the file format and loading.
