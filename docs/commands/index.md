# Commands Reference

## Running & Capturing

| Command | Alias | Description |
|---------|-------|-------------|
| `run <cmd>` | `r` | Run registered command, capture output |
| `exec <cmd>` | `x` | Run ad-hoc command |
| `import <file>` | | Import existing log file |
| `capture` | | Capture from stdin |

## Viewing Results

| Command | Alias | Description |
|---------|-------|-------------|
| `errors` | | Recent errors |
| `warnings` | | Recent warnings |
| `events` | `e` | Events with filtering |
| `inspect <ref>` | `i` | Event details with source context |
| `info <ref>` | `I` | Run details |
| `status` | | Status summary |
| `history` | `h` | Run history |
| `output <ref>` | `o` | View captured output |

## Querying

| Command | Alias | Description |
|---------|-------|-------------|
| `query [file]` | `q` | Query with SQL WHERE |
| `filter [file]` | `f` | Filter with simple syntax |
| `sql <query>` | | Full SQL access |
| `shell` | | Interactive DuckDB shell |

## Command Registry

| Command | Description |
|---------|-------------|
| `commands list` | List registered commands |
| `commands register <name> <cmd>` | Register command |
| `commands unregister <name>` | Remove command |

## CI Integration

| Command | Description |
|---------|-------------|
| `ci check` | Compare against baseline |
| `ci comment` | Post PR comment |
| `report` | Generate markdown report |
| `watch` | Watch files, auto-run |

## Maintenance

| Command | Description |
|---------|-------------|
| `init` | Initialize .lq directory |
| `clean` | Database cleanup |
| `migrate` | Storage migration |

## MCP Server

| Command | Description |
|---------|-------------|
| `mcp install` | Create .mcp.json |
| `mcp serve` | Start MCP server |

## Hooks

| Command | Description |
|---------|-------------|
| `hooks install` | Install git/Claude hooks |
| `hooks remove` | Remove hooks |
| `hooks status` | Show hook status |

## Sandbox

| Command | Description |
|---------|-------------|
| `sandbox list` | Show sandbox specs for all commands |
| `sandbox inspect <cmd>` | Show spec + grade for a command |
| `sandbox suggest <cmd>` | Suggest spec from observed metrics |
| `sandbox profile <cmd>` | Profile with strace to discover access patterns |
| `sandbox tighten <cmd>` | Auto-narrow spec from observed data |

## Utilities

| Command | Description |
|---------|-------------|
| `completions <shell>` | Generate shell completions |
| `formats` | List available log formats |
| `config` | View/edit user config |

---

## Quick Reference

```bash
# Run registered commands
blq run build
blq run test

# View errors
blq errors
blq inspect build:3:1

# View run history and output
blq history
blq output test:5

# Query
blq filter severity=error
blq query -s ref_file,message -f "severity='error'"
blq sql "SELECT * FROM blq_errors(10)"

# Register commands
blq commands register build "make -j8"
blq commands register test "pytest -v"
```
