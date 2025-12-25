# report - Generate Markdown Reports

Generate markdown reports summarizing build/test results.

## Synopsis

```bash
blq report [OPTIONS]
```

## Description

The `report` command generates a markdown report of build/test results. Reports include error summaries, file breakdowns, and optional baseline comparisons.

## Options

| Option | Short | Description |
|--------|-------|-------------|
| `--run ID` | `-r` | Run ID to report on (default: latest) |
| `--baseline REF` | `-b` | Baseline for comparison (run ID or branch name) |
| `--output FILE` | `-o` | Output file (default: stdout) |
| `--warnings` | `-w` | Include warning details |
| `--summary-only` | `-s` | Summary only, no individual error details |
| `--error-limit N` | `-n` | Max errors to include (default: 20) |
| `--file-limit N` | `-f` | Max files in breakdown (default: 10) |

## Examples

### Basic Report

```bash
# Report on latest run
blq report

# Report on specific run
blq report --run 5
```

### Save to File

```bash
# Save report to file
blq report --output build-report.md

# Save with comparison
blq report --baseline main --output pr-report.md
```

### Comparison Report

```bash
# Compare against main branch
blq report --baseline main

# Compare against specific run
blq report --baseline 42
```

### Customize Output

```bash
# Include warnings
blq report --warnings

# Summary only (no error details)
blq report --summary-only

# Limit errors shown
blq report --error-limit 10

# Limit files in breakdown
blq report --file-limit 5
```

## Report Format

### Standard Report

```markdown
# Build Report

**Run ID:** 8 | **Status:** FAIL | **Duration:** 12.3s

## Summary

| Metric | Count |
|--------|-------|
| Total Events | 15 |
| Errors | 5 |
| Warnings | 10 |

## Errors by File

| File | Errors |
|------|--------|
| src/main.c | 3 |
| src/util.c | 2 |

## Top Errors

| Location | Message |
|----------|---------|
| src/main.c:15 | undefined variable 'foo' |
| src/main.c:22 | incompatible types |
| src/main.c:45 | missing semicolon |
| src/util.c:10 | unused variable 'x' |
| src/util.c:33 | implicit declaration |
```

### Comparison Report

When using `--baseline`, the report includes:

```markdown
# Build Report

**Run ID:** 8 | **Status:** FAIL | **Duration:** 12.3s
**Baseline:** Run 5 (main)

## Summary

| Metric | Current | Baseline | Delta |
|--------|---------|----------|-------|
| Errors | 5 | 3 | +2 |
| Warnings | 10 | 8 | +2 |

## New Errors

| Location | Message |
|----------|---------|
| src/new.c:10 | undefined function |
| src/main.c:50 | type mismatch |

## Fixed Errors

| Location | Message |
|----------|---------|
| src/old.c:15 | removed unused import |
```

## Use Cases

### PR Description

Generate a report for PR descriptions:

```bash
blq report --baseline main --summary-only > pr-summary.md
```

### CI Artifacts

Save detailed reports as CI artifacts:

```bash
blq report --baseline main --output reports/build-${{ github.run_number }}.md
```

### Email/Slack Notifications

Generate concise summaries for notifications:

```bash
blq report --summary-only --error-limit 5
```

## Exit Code

`blq report` always exits with code 0, even if there are errors in the run. Use `blq ci check` for exit code based on error status.

## See Also

- [ci](ci.md) - CI integration commands
- [errors](errors.md) - View error details
- [status](status.md) - View run status
- [run](run.md) - Run commands with `--markdown` output
