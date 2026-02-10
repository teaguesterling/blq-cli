# Maintenance Commands

blq provides commands for managing log storage and cleaning up data.

## clean - Database Cleanup

The `blq clean` command provides several modes for database maintenance.

```bash
blq clean data                    # Clear run data, keep config
blq clean prune --days 30         # Remove data older than 30 days
blq clean schema                  # Recreate database schema
blq clean full                    # Full reinitialization
```

### Modes

| Mode | Description |
|------|-------------|
| `data` | Clear all run data (invocations, events, outputs). Config and commands preserved. |
| `prune` | Remove data older than N days. Includes orphaned blob cleanup. |
| `schema` | Recreate database schema. All data lost, config files preserved. |
| `full` | Delete and recreate entire .lq directory. Everything reset. |

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--confirm` | `-y` | Confirm destructive operation (required) |
| `--days N` | | For prune: days to keep (required for prune mode) |
| `--dry-run` | | For prune: preview what would be removed |

### Examples

```bash
# Clear all run data but keep commands
$ blq clean data --confirm
Cleared all run data. Config and commands preserved.

# Remove data older than 30 days
$ blq clean prune --days 30 --confirm
Removed 15 invocations and 243 events. Freed 12 blobs (4.2 MB).

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
├── config.yaml          # Project configuration
└── commands.yaml        # Registered commands
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
