# ci - CI Integration Commands

Commands for continuous integration and pull request workflows.

## Synopsis

```bash
blq ci check [OPTIONS]
blq ci comment [OPTIONS]
```

## Description

The `ci` command provides subcommands for CI/CD pipeline integration:

- **check** - Compare errors against a baseline and exit with appropriate code
- **comment** - Post error summaries as GitHub PR comments

## ci check

Compare current errors against a baseline run and exit 0 (pass) or 1 (fail).

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--baseline REF` | `-b` | Baseline to compare against (run ID, branch, or commit) |
| `--fail-on-any` | | Fail if any errors exist (no baseline comparison) |
| `--json` | `-j` | Output as JSON |

### Baseline Resolution

The baseline is resolved in this order:

1. **Run ID** - If numeric, use as run ID directly
2. **Commit SHA** - If looks like a commit, find run with matching `git_commit`
3. **Branch name** - Find latest run on that branch
4. **Auto-detect** - If no baseline specified, try `main` then `master`

### Examples

```bash
# Compare against auto-detected baseline (main/master)
blq ci check

# Compare against specific branch
blq ci check --baseline main

# Compare against specific run
blq ci check --baseline 42

# Compare against commit
blq ci check --baseline abc123

# Fail if any errors (zero tolerance)
blq ci check --fail-on-any

# Get JSON output for parsing
blq ci check --json
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No new errors (or no errors with `--fail-on-any`) |
| 1 | New errors found |

### JSON Output

```json
{
  "baseline_run_id": 5,
  "current_run_id": 8,
  "baseline_errors": 3,
  "current_errors": 5,
  "new_errors": 2,
  "fixed_errors": 0,
  "status": "FAIL"
}
```

## ci comment

Post an error summary as a GitHub PR comment.

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--update` | `-u` | Update existing blq comment instead of creating new |
| `--diff` | `-d` | Include diff vs baseline in comment |
| `--baseline REF` | `-b` | Baseline for diff (run ID, branch, or commit) |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | GitHub token with PR comment permissions |
| `GITHUB_REPOSITORY` | Repository in `owner/repo` format (auto-detected in Actions) |
| `GITHUB_EVENT_PATH` | Path to event JSON (auto-detected in Actions) |

### Examples

```bash
# Post a new comment
blq ci comment

# Update existing blq comment (avoids comment spam)
blq ci comment --update

# Include diff against main branch
blq ci comment --diff --baseline main

# Full CI workflow
blq ci comment --update --diff --baseline main
```

### Comment Format

The comment includes:

- Status badge (pass/fail)
- Error count summary
- New errors (if using `--diff`)
- Fixed errors (if using `--diff`)
- Link to run details

Example comment:

```markdown
## blq Build Results

**Status:** FAIL | **Errors:** 5 (+2 new)

### New Errors

| File | Line | Message |
|------|------|---------|
| src/main.c | 15 | undefined variable 'foo' |
| src/util.c | 42 | incompatible types |

### Fixed Errors

- src/old.c:10 - removed unused variable
```

## GitHub Actions Example

```yaml
name: CI
on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install blq-cli

      - name: Initialize blq
        run: blq init --detect --yes

      - name: Run tests
        run: blq run test

      - name: Check for regressions
        run: blq ci check --baseline main

      - name: Post PR comment
        if: github.event_name == 'pull_request'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: blq ci comment --update --diff --baseline main
```

## See Also

- [run](run.md) - Run registered commands
- [report](report.md) - Generate markdown reports
- [status](status.md) - View run status
