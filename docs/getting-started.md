# Getting Started

## Installation

### From PyPI

```bash
pip install blq-cli
```

### From Source

```bash
git clone https://github.com/yourusername/lq.git
cd lq
pip install -e .
```

## Initialize Your Project

Run `blq init` in your project directory:

```bash
cd my-project
blq init
```

This creates a `.lq/` directory and installs the `duck_hunt` extension for log parsing.

```
Initialized .lq at /path/to/my-project/.lq
  logs/      - Hive-partitioned parquet files
  raw/       - Raw log files (optional)
  schema.sql - SQL schema and macros
  duck_hunt  - Installed successfully
```

## Your First Query

### Query a Log File Directly

If you have an existing log file:

```bash
blq q build.log
```

Select specific columns:

```bash
blq q -s file_path,line_number,severity,message build.log
```

Filter for errors:

```bash
blq f severity=error build.log
```

### Run and Capture

Run a command and capture its output:

```bash
blq run make -j8
```

This:
1. Runs `make -j8`
2. Parses the output for errors/warnings
3. Stores events in `.lq/logs/`
4. Prints a summary

### View Results

```bash
# Recent errors
blq errors

# All warnings
blq warnings

# Overall status
blq status
```

## Output Formats

### Default Table

```bash
blq q -s file_path,severity,message build.log
```

```
  file_path severity                  message
 src/main.c    error undefined variable 'foo'
src/utils.c    error        missing semicolon
```

### JSON

```bash
blq q --json build.log
```

```json
[
  {"file_path": "src/main.c", "severity": "error", "message": "undefined variable 'foo'"},
  {"file_path": "src/utils.c", "severity": "error", "message": "missing semicolon"}
]
```

### CSV

```bash
blq q --csv build.log
```

### Markdown

```bash
blq q --markdown build.log
```

## Shell Completions

Enable tab completion for your shell:

```bash
# Bash (add to ~/.bashrc)
eval "$(blq completions bash)"

# Zsh (add to ~/.zshrc)
eval "$(blq completions zsh)"

# Fish
blq completions fish > ~/.config/fish/completions/blq.fish
```

## Next Steps

- [Commands Reference](commands/) - Learn all available commands
- [Query Guide](query-guide.md) - Master querying techniques
- [Integration Guide](integration.md) - Use with AI agents
