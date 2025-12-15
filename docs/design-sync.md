# Sync Feature Design

## Overview

The sync feature allows aggregating lq logs from multiple machines/projects to a central store for cross-project analysis and historical tracking.

## Project Identification

Projects are identified by `namespace` and `project` stored in `.lq/config.yaml`:

```yaml
project:
  namespace: github__teaguesterling   # provider__owner format
  project: lq
```

### Detection at `blq init`

1. **Git remote** (preferred): Parse `git remote get-url origin`
   - `git@github.com:owner/repo.git` → namespace=github__owner, project=repo
   - `https://gitlab.com/org/repo` → namespace=gitlab__org, project=repo

2. **Filesystem fallback**: Tokenize parent path
   - `/home/teague/Projects/myapp` → namespace=local__home__teague__Projects, project=myapp

### Provider Detection

| Host | Namespace Prefix |
|------|------------------|
| github.com | `github__` |
| gitlab.com | `gitlab__` |
| bitbucket.org | `bitbucket__` |
| codeberg.org | `codeberg__` |
| Self-hosted | `hostname_com__` (sanitized) |
| No git | `local__` |

## Directory Hierarchy

**Hierarchy order: hostname → namespace → project → date → source**

This ordering optimizes for:
- "What's on this machine" queries (most common local use case)
- Date-based consolidation can be done as a separate step

### Local Central Store

```
~/.lq/projects/
  hostname=snape/
    namespace=github__teaguesterling/
      project=lq/
        date=2025-01-15/
          source=run/
            001_build_143022.parquet
```

### S3 / Cloud Storage

```
s3://bucket/lq/
  hostname=snape/
    namespace=github__teaguesterling/
      project=lq/
        date=2025-01-15/
          source=run/
            001_build_143022.parquet
```

## CLI Interface

### Sync Commands (Implemented)

```bash
# Soft sync (symlink) - default
blq sync                              # Symlink to ~/.lq/projects/
blq sync ~/custom/path/               # Custom destination

# Options
blq sync --dry-run                    # Show what would be done
blq sync --status                     # Show current sync state
blq sync --force                      # Replace existing sync target
blq sync --hard                       # Copy files (not yet implemented)
```

### Cross-Project Querying

```bash
# Query local project (default)
blq errors                            # ./.lq/logs/

# Query global store
blq errors -g                         # ~/.lq/projects/
blq errors --global

# Query custom root (S3, remote, etc.)
blq errors -d s3://bucket/lq/
blq errors --database ~/other/path/
```

Global flags work with: `errors`, `warnings`, `query`, `filter`, `status`, `history`, `sql`

## Configuration

```yaml
# .lq/config.yaml
project:
  namespace: github__teaguesterling
  project: lq

sync:
  destination: ~/.lq/projects/       # or s3://bucket/lq/
  auto: false                        # auto-sync after each run?
  include_raw: false                 # sync .lq/raw/ as well?
```

## Implementation Phases

### Phase 1: Project Detection ✅
- [x] Detect namespace/project from git remote
- [x] Include provider prefix in namespace
- [x] Fallback to filesystem path tokenization
- [x] Store in config.yaml at init

### Phase 2: Local Sync ✅
- [x] `blq sync` command
- [x] Symlink mode (soft sync) for local destinations
- [x] `--dry-run`, `--status`, `--force` flags
- [x] Hostname-first hierarchy
- [ ] Copy mode (hard sync) with incremental sync
- [ ] Track synced files to avoid re-copying

### Phase 3: Cross-Project Querying
- [ ] `-g`/`--global` flag for global store queries
- [ ] `-d`/`--database` flag for custom roots
- [ ] Hive partition columns available in queries (hostname, namespace, project)

### Phase 4: S3 Sync
- [ ] S3 destination support via DuckDB httpfs
- [ ] Credentials from environment (AWS_* vars)
- [ ] Incremental sync (check existing partitions)

## Query Examples

Cross-machine queries with global flag:

```bash
# All errors across all synced projects
blq errors -g

# Filter by project
blq query -g -f "namespace='github__teaguesterling' AND project='lq'"

# SQL across all projects
blq sql -g "SELECT hostname, namespace, project, COUNT(*) as errors
           FROM lq_events WHERE severity='error'
           GROUP BY ALL ORDER BY errors DESC"
```

SQL examples:

```sql
-- All errors across all machines for this project
SELECT hostname, COUNT(*) as errors
FROM read_parquet('~/.lq/projects/**/*.parquet', hive_partitioning=true)
WHERE severity = 'error'
GROUP BY hostname;

-- Compare build times across CI runners
SELECT hostname, AVG(duration_sec) as avg_duration
FROM read_parquet('~/.lq/projects/**/*.parquet', hive_partitioning=true)
WHERE source_name = 'build'
GROUP BY hostname;

-- Errors by project
SELECT namespace, project, COUNT(*) as errors
FROM read_parquet('~/.lq/projects/**/*.parquet', hive_partitioning=true)
WHERE severity = 'error'
GROUP BY namespace, project
ORDER BY errors DESC;
```

---

# Compaction Feature Design

## Overview

Over time, logs accumulate many small parquet files. Compaction consolidates these for better query performance and storage efficiency.

## Use Cases

1. **Merge files** - Combine many small parquet files into fewer larger ones
2. **Compress** - Apply better compression to older data
3. **Summarize** - Aggregate old data, keeping counts but dropping raw messages (lossy)
4. **Prune** - Delete old data entirely (already implemented via `blq prune`)

## CLI Interface

```bash
# Compact all partitions (merge small files)
blq compact

# Only compact data older than 30 days
blq compact --older-than 30d

# Use better compression
blq compact --compression zstd

# Summarize old data (lossy - keeps aggregates only)
blq compact --summarize --older-than 90d

# Dry run
blq compact --dry-run
```

## Compaction vs Prune

| Command | Action | Data Loss |
|---------|--------|-----------|
| `blq prune` | Delete old partitions | Yes |
| `blq compact` | Merge files, recompress | No |
| `blq compact --summarize` | Aggregate old data | Yes (detail) |

## Implementation Notes

- Use DuckDB's `COPY ... TO ... (FORMAT PARQUET)` for rewriting
- Preserve hive partitioning structure
- Track which files have been compacted to avoid re-processing
- Consider minimum file size threshold (don't compact already-large files)

## Future Considerations

- Automatic compaction in background
- Retention policies in config
- Different compression levels by age

---

## Security Considerations

- S3 credentials via environment variables (AWS_ACCESS_KEY_ID, etc.)
- No credentials stored in config.yaml
- Consider signed URLs for temporary access
- Bucket policies for multi-tenant setups
