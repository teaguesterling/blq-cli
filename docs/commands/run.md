# run - Execute Registered Commands

Run a registered command and capture its output.

## Synopsis

```bash
blq run [OPTIONS] <registered-name>
blq r [OPTIONS] <registered-name>
```

## Description

The `run` command executes a registered command by name, captures its output, parses it for errors and warnings, and stores the events in `.lq/logs/`.

For ad-hoc shell commands, use `blq exec` instead.

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--name NAME` | `-n` | Source name (default: command name) |
| `--format FORMAT` | `-f` | Parse format hint (default: auto) |
| `--keep-raw` | `-r` | Keep raw output file in `.lq/raw/` |
| `--json` | `-j` | Output structured JSON result |
| `--markdown` | `-m` | Output markdown summary |
| `--quiet` | `-q` | Suppress streaming output |
| `--summary` | `-s` | Show brief summary (errors/warnings count) |
| `--verbose` | `-v` | Show all blq status messages |
| `--include-warnings` | `-w` | Include warnings in structured output |
| `--error-limit N` | | Max errors/warnings in output (default: 20) |
| `--capture` | `-C` | Force log capture (override command config) |
| `--no-capture` | `-N` | Skip log capture, just run command |
| `--register` | | Register and run an unregistered command |

## Examples

### Running Registered Commands

```bash
# First, register your commands
blq register build "make -j8"
blq register test "pytest -v"

# Then run by name
blq run build
blq run test
blq r build  # Short alias
```

### Register and Run

If a command isn't registered yet, use `--register` to register and run in one step:

```bash
blq run --register "make -j8"
# Equivalent to:
#   blq register make-j8 "make -j8"
#   blq run make-j8
```

### Named Run

```bash
blq run --name "nightly build" build
```

### Keep Raw Log

```bash
blq run --keep-raw build
# Creates .lq/raw/001_build_103000.log
```

### Structured Output

For CI/CD or agent integration:

```bash
# JSON output
blq run --json build

# Markdown summary
blq run --markdown build

# Quiet mode (no streaming, just result)
blq run --quiet --json build
```

### Include Warnings

By default, structured output only includes errors. To include warnings:

```bash
blq run --json --include-warnings build
```

### Limit Output

```bash
blq run --json --error-limit 5 build
```

## Verbosity Control

By default, `blq run` shows only the command's output. Use verbosity flags to control additional output:

### Default (quiet blq output)
```bash
blq run build
# Shows only: command output + streaming stdout/stderr
```

### Summary Mode
```bash
blq run --summary build
# Shows: command output + brief summary at end
# Output: ✓ build completed (0 errors, 2 warnings)
```

### Verbose Mode
```bash
blq run --verbose build
# Shows: command output + all blq status messages
# Output includes: parsing progress, storage info, timing
```

## Capture Control

Commands can be configured to skip log capture (see [register](registry.md)). At runtime, you can override this:

### Force Capture
```bash
# Run a no-capture command with capture enabled
blq run --capture format
```

### Skip Capture
```bash
# Run quickly without parsing/storing
blq run --no-capture build
```

### When to Skip Capture

Use `--no-capture` when:
- Speed is critical and you don't need error tracking
- Running formatters, cleaners, or other non-diagnostic commands
- Testing a command before full integration

## Structured Output Format

With `--json`, the output includes:

```json
{
  "run_id": 1,
  "command": "make -j8",
  "status": "FAIL",
  "exit_code": 2,
  "started_at": "2024-01-15T10:30:00",
  "completed_at": "2024-01-15T10:30:12",
  "duration_sec": 12.345,
  "summary": {
    "total_events": 5,
    "errors": 2,
    "warnings": 3
  },
  "errors": [
    {
      "ref": "1:1",
      "severity": "error",
      "ref_file": "src/main.c",
      "ref_line": 15,
      "ref_column": 5,
      "message": "undefined variable 'foo'"
    }
  ]
}
```

With `--include-warnings`, a `warnings` array is also included.

### Event References

Each error/warning has a `ref` field (e.g., `1:1`) that can be used to get more details:

```bash
blq event 1:1
blq context 1:1
```

## Exit Code

`blq run` exits with the same exit code as the command it ran. This preserves the fail/pass semantics for CI/CD pipelines.

## See Also

- [exec](exec.md) - Execute ad-hoc shell commands
- [registry](registry.md) - Register reusable commands
- [capture](capture.md) - Import log files or capture from stdin
- [errors](errors.md) - View errors and event details
- [status](status.md) - Check run status and history
