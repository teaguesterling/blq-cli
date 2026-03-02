# Maintenance Commands

blq provides commands for managing log storage and cleaning up data.

## clean - Database Cleanup

The `blq clean` command provides several modes for database maintenance.

```bash
blq clean data                    # Clear run data, keep config
blq clean prune --days 30         # Remove data older than 30 days
blq clean prune --max-runs 50     # Keep at most 50 runs per source
blq clean prune --max-size 500    # Keep total output under 500 MB
blq clean schema                  # Recreate database schema
blq clean full                    # Full reinitialization
```

### Modes

| Mode | Description |
|------|-------------|
| `data` | Clear all run data (invocations, events, outputs). Config and commands preserved. |
| `prune` | Remove data by age, run count, or size. Includes orphaned blob cleanup. |
| `schema` | Recreate database schema. All data lost, config files preserved. |
| `full` | Delete and recreate entire .lq directory. Everything reset. |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--confirm` | `-y` | Confirm destructive operation (required) |
| `--days N` | | For prune: remove data older than N days |
| `--max-runs N` | | For prune: keep at most N runs per source |
| `--max-size N` | | For prune: keep total output under N MB |
| `--dry-run` | | For prune: preview what would be removed |

At least one of `--days`, `--max-runs`, or `--max-size` is required for prune mode.

### Examples

```bash
# Clear all run data but keep commands
$ blq clean data --confirm
Cleared all run data. Config and commands preserved.

# Remove data older than 30 days
$ blq clean prune --days 30 --confirm
Removed 15 invocations and 243 events. Freed 12 blobs (4.2 MB).

# Keep at most 50 runs per source
$ blq clean prune --max-runs 50 --confirm
Pruned 23 invocations (over max_runs limit).

# Keep total output under 500 MB
$ blq clean prune --max-size 500 --confirm
Pruned 8 invocations (over size limit).

# Combine multiple limits
$ blq clean prune --days 30 --max-runs 100 --confirm
Removed 15 invocations by age, 5 by run count. Freed 8 blobs (2.1 MB).

# Preview what prune would remove
$ blq clean prune --days 7 --dry-run
Found 42 invocations and 1,203 events older than 7 days.
Dry run - no changes made.

# Recreate database schema
$ blq clean schema --confirm
Recreated database schema. Config files preserved.

# Full reinitialization
$ blq clean full --confirm
Fully reinitialized .lq directory.
```

## Automatic Pruning

blq can automatically prune old data during `run` and `exec` commands. Configure via user config:

```bash
blq config set storage.auto_prune true
blq config set storage.max_runs 500          # Keep at most 500 runs per source
blq config set storage.max_size_mb 1000      # Keep total output under 1 GB
blq config set storage.prune_interval_minutes 60  # Check at most every 60 minutes
blq config set storage.prune_days 30         # Remove data older than 30 days
```

Autoprune uses a time-based trigger (checks `.lq/.last_prune` timestamp) rather than running on every command. When triggered, it applies limits in order: age, then run count, then size.

Project-level overrides can be set in `.lq/config.toml`:

```toml
[storage]
prune_days = 7        # Override user's 30-day default for this project
max_runs = 100        # Tighter limit for this project
```

## Storage Structure

blq uses BIRD storage (DuckDB tables with content-addressed blob storage):

```
.lq/
├── blq.duckdb           # DuckDB database (tables, views, macros)
├── blobs/
│   └── content/         # Content-addressed blob storage
│       ├── ab/
│       │   └── abc123...def.bin
│       └── cd/
│           └── cde456...ghi.bin
├── config.toml          # Project configuration
└── commands.toml        # Registered commands
```

### Database Tables

| Table | Description |
|-------|-------------|
| `sessions` | Invoker sessions (shell, CLI, MCP) |
| `invocations` | Command executions with metadata |
| `outputs` | Captured stdout/stderr (references blobs) |
| `events` | Parsed diagnostics (errors, warnings) |
| `blob_registry` | Content-addressed blob tracking |

## Use Cases

### Regular Cleanup

Add to cron for automatic cleanup:
```bash
# Weekly cleanup of data older than 30 days
0 0 * * 0 cd /path/to/project && blq clean prune --days 30 --confirm
```

### Before Releases

Clean up old data before packaging:
```bash
blq clean prune --days 7 --confirm
```

### CI Environment

Keep CI storage lean:
```yaml
# .github/workflows/ci.yml
- name: Cleanup old logs
  run: blq clean prune --days 1 --confirm
```

### Development Reset

Start fresh during development:
```bash
blq clean data --confirm
```

## Blob Cleanup

The `prune` mode automatically cleans up orphaned blobs - content-addressed files that are no longer referenced by any output record. This happens after deleting old invocations.

Blob cleanup:
1. Finds blobs in `blob_registry` not referenced by `outputs`
2. Deletes the blob files from disk
3. Removes entries from `blob_registry`
4. Cleans up empty subdirectories

## Best Practices

1. **Use --confirm**: All destructive operations require explicit confirmation
2. **Preview with --dry-run**: Check what prune would remove before executing
3. **Automate cleanup**: Use cron or CI to prevent unbounded growth
4. **Balance retention**: Keep enough history for analysis, but not indefinitely

## Manual Database Access

For advanced operations, access the database directly:

```bash
# Open DuckDB shell
duckdb .lq/blq.duckdb

# View storage stats
duckdb .lq/blq.duckdb "SELECT COUNT(*) FROM invocations"

# Query using blq macros
duckdb .lq/blq.duckdb "SELECT * FROM blq_status()"
```
