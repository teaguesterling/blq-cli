# Integration Guide

This guide covers integrating blq with CI pipelines, editors, and other tools.

## CI/CD Integration

blq provides dedicated CI commands for regression detection and PR feedback.

### Basic CI Workflow

```yaml
# GitHub Actions
name: CI
on: [push, pull_request]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup
        run: |
          pip install blq-cli
          blq init --detect --yes

      - name: Build
        run: blq run build

      - name: Test
        run: blq run test

      - name: Check for regressions
        run: blq ci check --baseline main

      - name: Post PR comment
        if: github.event_name == 'pull_request'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: blq ci comment --update --diff --baseline main
```

### blq ci check

Compare errors against a baseline and exit with appropriate code.

```bash
blq ci check                      # Auto-detect baseline (main/master)
blq ci check --baseline main      # Compare against main branch
blq ci check --baseline 42        # Compare against run ID
blq ci check --fail-on-any        # Fail if any errors (zero tolerance)
```

Exit codes:
- `0` — No new errors
- `1` — New errors found

### blq ci comment

Post error summaries as GitHub PR comments.

```bash
blq ci comment                              # Post new comment
blq ci comment --update                     # Update existing comment
blq ci comment --diff --baseline main       # Include diff vs baseline
```

Requires `GITHUB_TOKEN` with PR comment permissions.

### GitLab CI

```yaml
build:
  script:
    - pip install blq-cli
    - blq init --detect --yes
    - blq run build
    - blq run test
    - blq ci check --baseline main
```

### Jenkins

```groovy
pipeline {
    stages {
        stage('Build') {
            steps {
                sh 'pip install blq-cli'
                sh 'blq init --detect --yes'
                sh 'blq run build'
                sh 'blq ci check --baseline main'
            }
        }
    }
}
```

---

## AI Agent Integration

blq provides an MCP server for AI agents. See the [MCP Guide](mcp.md) for full documentation.

Quick setup:

```bash
blq mcp install    # Creates .mcp.json
```

Agents can then use tools like `run`, `events`, `inspect`, and `diff` to work with structured build results instead of parsing raw output.

---

## Claude Code Integration

blq can automatically capture commands run by Claude Code.

### Install Hooks

```bash
blq hooks install claude-code
```

This registers a pre-command hook that suggests using `blq run` for registered commands.

### Auto-install via Config

Add to `~/.config/blq/config.toml`:

```toml
[hooks]
auto_claude_code = true
```

---

## Shell Completions

Enable tab completion:

```bash
# Bash (add to ~/.bashrc)
eval "$(blq completions bash)"

# Zsh (add to ~/.zshrc)
eval "$(blq completions zsh)"

# Fish
blq completions fish > ~/.config/fish/completions/blq.fish
```

---

## Programmatic Access

### Python API

```python
from blq import LogStore

store = LogStore.open()

# Query errors
errors = store.errors().filter(ref_file="%main%").df()

# Get run history
runs = store.runs().limit(10).df()

# Aggregations
by_file = store.errors().group_by("ref_file").count()
```

See [Python API Guide](python-api.md) for full documentation.

### Direct SQL

```bash
# Query the database directly
duckdb .lq/blq.duckdb "SELECT * FROM blq_errors(10)"

# Via blq
blq sql "SELECT ref_file, COUNT(*) FROM blq_load_events() WHERE severity='error' GROUP BY 1"
```

### Data Export

```bash
# Export to CSV
blq sql "COPY (SELECT * FROM blq_load_events()) TO 'events.csv' (HEADER)"

# Export to JSON
blq sql "COPY (SELECT * FROM blq_load_events()) TO 'events.json'"
```

---

## Report Generation

Generate markdown reports for documentation or PR comments:

```bash
blq report                        # Summary of recent runs
blq report --baseline main        # Include diff vs baseline
blq report --format markdown      # Explicit markdown output
```
