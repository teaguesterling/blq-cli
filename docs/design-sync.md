# Sync Feature Design

## Overview

The sync feature allows aggregating lq logs from multiple machines/projects to a central store for cross-project analysis and historical tracking.

## Project Identification

Projects are identified by `namespace` and `project` stored in `.lq/config.yaml`:

```yaml
project:
  namespace: teaguesterling
  project: lq
```

### Detection at `lq init`

1. **Git remote** (preferred): Parse `git remote get-url origin`
   - `git@github.com:owner/repo.git` → namespace=owner, project=repo
   - `https://github.com/owner/repo` → namespace=owner, project=repo

2. **Filesystem fallback**: Tokenize parent path
   - `/home/teague/Projects/myapp` → namespace=home__teague__Projects, project=myapp
   - Path separators replaced with `__` for filesystem safety

## Destination Formats

### Local Central Store

```
~/.lq/projects/
  namespace=teaguesterling/
    project=lq/
      hostname=snape/
        date=2025-01-15/
          source=run/
            001_build_143022.parquet
```

Implementation options:
- **Symlink** (simplest): `ln -s /path/to/project/.lq/logs ~/.lq/projects/.../hostname=snape`
- **Copy**: Rsync or file copy for actual data duplication

### S3 / Cloud Storage

```
s3://bucket/lq/
  namespace=teaguesterling/
    project=lq/
      hostname=snape/
        date=2025-01-15/
          source=run/
            001_build_143022.parquet
```

Implementation: Use DuckDB's httpfs extension for direct parquet writes:

```sql
-- Push local data to S3
COPY (
  SELECT * FROM read_parquet('.lq/logs/**/*.parquet', hive_partitioning=true)
)
TO 's3://bucket/lq/namespace=X/project=Y/hostname=Z/'
(FORMAT PARQUET, PARTITION_BY (date, source));
```

## CLI Interface

```bash
# Push to configured destination
lq sync

# Push to explicit destination
lq sync ~/.lq/projects/
lq sync s3://my-bucket/lq/

# Dry run - show what would be synced
lq sync --dry-run

# Pull/query across synced sources
lq query --include-synced "SELECT * FROM lq_errors()"
```

## Configuration

```yaml
# .lq/config.yaml
project:
  namespace: teaguesterling
  project: lq

sync:
  destination: ~/.lq/projects/  # or s3://bucket/lq/
  auto: false                   # auto-sync after each run?
  include_raw: false            # sync .lq/raw/ as well?
```

## Implementation Phases

### Phase 1: Project Detection (Implemented)
- [x] Detect namespace/project from git remote
- [x] Fallback to filesystem path tokenization
- [x] Store in config.yaml at init

### Phase 2: Local Sync
- [ ] `lq sync` command
- [ ] Symlink mode for local destinations
- [ ] Copy mode with incremental sync
- [ ] Track synced files to avoid re-copying

### Phase 3: S3 Sync
- [ ] S3 destination support via DuckDB httpfs
- [ ] Credentials from environment (AWS_* vars)
- [ ] Incremental sync (check existing partitions)

### Phase 4: Query Across Sources
- [ ] `--include-synced` flag for query commands
- [ ] Union query across local + synced data
- [ ] Filter by namespace/project/hostname

## Query Examples

Once sync is implemented, cross-machine queries become possible:

```sql
-- All errors across all machines for this project
SELECT hostname, COUNT(*) as errors
FROM read_parquet('~/.lq/projects/namespace=teaguesterling/project=lq/**/*.parquet')
WHERE severity = 'error'
GROUP BY hostname;

-- Compare build times across CI runners
SELECT hostname, AVG(duration_sec) as avg_duration
FROM read_parquet('s3://bucket/lq/namespace=X/project=Y/**/*.parquet')
WHERE source_name = 'build'
GROUP BY hostname;
```

## Security Considerations

- S3 credentials via environment variables (AWS_ACCESS_KEY_ID, etc.)
- No credentials stored in config.yaml
- Consider signed URLs for temporary access
- Bucket policies for multi-tenant setups
