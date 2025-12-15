# lq filter

Filter log files with simple, grep-like syntax.

**Alias:** `blq f`

## Synopsis

```bash
blq filter [OPTIONS] [EXPRESSION...] [FILE...]
blq f [OPTIONS] [EXPRESSION...] [FILE...]
```

## Description

The `filter` command provides a simple, grep-like interface for filtering log events. Unlike `query` which uses SQL syntax, `filter` uses intuitive `key=value` expressions.

## Options

| Option | Description |
|--------|-------------|
| `-v, --invert` | Invert match (show non-matching rows) |
| `-c, --count` | Only print count of matches |
| `-i, --ignore-case` | Case insensitive matching |
| `-n, --limit N` | Maximum rows to return |
| `--json, -j` | Output as JSON |
| `--csv` | Output as CSV |
| `--markdown, --md` | Output as Markdown table |

## Filter Expressions

### Exact Match (`=`)

```bash
blq f severity=error build.log
blq f file_path=src/main.c build.log
```

### Multiple Values (`=v1,v2`)

Matches if the field equals any of the values (OR):

```bash
blq f severity=error,warning build.log
```

Equivalent SQL: `severity IN ('error', 'warning')`

### Contains (`~`)

Pattern matching with ILIKE (case insensitive):

```bash
blq f file_path~main build.log
blq f message~undefined build.log
```

Equivalent SQL: `file_path ILIKE '%main%'`

### Not Equal (`!=`)

```bash
blq f severity!=info build.log
```

### Multiple Expressions

Multiple expressions are combined with AND:

```bash
blq f severity=error file_path~main build.log
```

Equivalent SQL: `severity = 'error' AND file_path ILIKE '%main%'`

## Examples

### Filter Errors

```bash
blq f severity=error build.log
```

### Filter Errors and Warnings

```bash
blq f severity=error,warning build.log
```

### Filter by File

```bash
blq f file_path~utils build.log
blq f file_path~.c build.log     # All C files
```

### Exclude Info Messages

```bash
blq f severity!=info build.log
```

### Invert Match

Show everything except errors (like `grep -v`):

```bash
blq f -v severity=error build.log
```

### Count Matches

```bash
blq f -c severity=error build.log
# Output: 5
```

### Case Insensitive

```bash
blq f -i message~error build.log
```

### Combine Options

```bash
blq f -c severity=error,warning file_path~main build.log
```

### Query Stored Events

Without a file, queries stored events:

```bash
blq f severity=error
blq f -c severity=warning
```

### Output Formats

```bash
blq f severity=error --json build.log
blq f severity=error --csv build.log
```

## Comparison with query

| Task | filter | query |
|------|--------|-------|
| Errors only | `blq f severity=error` | `blq q -f "severity='error'"` |
| Contains | `blq f file_path~main` | `blq q -f "file_path LIKE '%main%'"` |
| Multiple values | `blq f severity=error,warning` | `blq q -f "severity IN ('error','warning')"` |
| Select columns | Not supported | `blq q -s file_path,message` |
| Complex SQL | Not supported | `blq q -f "line_number > 100"` |

Use `filter` for quick, simple filtering. Use `query` when you need column selection, complex conditions, or ordering.

## See Also

- [query](query.md) - SQL-based querying
- [errors](errors.md) - Quick error viewing
