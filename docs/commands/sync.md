# Sync Command

The `blq sync` command synchronizes project logs to a central location for cross-project querying.

## Basic Usage

```bash
# Sync to default location (~/.lq/projects/)
blq sync

# Sync to custom location
blq sync ~/my-logs/

# Show what would be synced (dry run)
blq sync --dry-run

# Check current sync status
blq sync --status
```

## Sync Modes

### Soft Sync (Default)

Creates a symlink to your project's `.lq/logs` directory:

```bash
blq sync              # Creates symlink
blq sync --soft       # Explicit soft sync
```

This is fast and always up-to-date since it just links to the original files.

### Hard Sync

Copies files instead of symlinking (not yet implemented):

```bash
blq sync --hard       # Copy files
```

Use hard sync when you need actual copies (e.g., for S3 upload).

## Directory Structure

Synced projects use Hive-style partitioning:

```
~/.lq/projects/
  hostname=snape/
    namespace=github__teaguesterling/
      project=lq/
        date=2025-01-15/
          source=run/
            001_build_143022.parquet
```

The hierarchy is **hostname first**, which optimizes for:
- "What's on this machine" queries
- Local development workflows

## Querying Synced Projects

Use the `-g`/`--global` flag to query across all synced projects:

```bash
# Errors across all projects
blq -g errors

# SQL with partition columns
blq -g sql "SELECT hostname, namespace, project, COUNT(*)
           FROM lq_events WHERE severity='error'
           GROUP BY ALL"

# History across all machines
blq -g history
```

## Options

| Option | Description |
|--------|-------------|
| `--soft`, `-s` | Create symlink (default) |
| `--hard`, `-H` | Copy files instead |
| `--force`, `-f` | Replace existing sync target |
| `--dry-run`, `-n` | Show what would be done |
| `--status` | Show current sync status |
| `--verbose`, `-v` | Verbose output |

## Project Identification

Projects are identified by namespace and project name from your git remote:

| Git Remote | Namespace |
|------------|-----------|
| `github.com/owner/repo` | `github__owner` |
| `gitlab.com/org/repo` | `gitlab__org` |
| No git (filesystem) | `local__path__to__dir` |

Configure manually in `.lq/config.yaml`:

```yaml
project:
  namespace: github__teaguesterling
  project: lq
```

## Examples

```bash
# Initial sync
blq sync
# Output: Synced (soft): ~/.lq/projects/hostname=snape/... -> /path/to/.lq/logs

# Check status
blq sync --status
# Output:
#   snape: github__teaguesterling/lq
#     Mode: symlink (ok)
#     Target: /path/to/project/.lq/logs

# Re-sync (detects already synced)
blq sync
# Output: Already synced: ...

# Force re-sync
blq sync --force
```
