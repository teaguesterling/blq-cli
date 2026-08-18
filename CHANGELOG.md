# Changelog

## v1.2.1

### Fixed — 1.2.0 was broken on Python 3.10 and 3.11

The TypedDicts added in 1.2.0 were imported from `typing`. pydantic refuses to
build a schema from `typing.TypedDict` below 3.12 ("Please use
`typing_extensions.TypedDict` instead"), so importing `blq.serve` raised on
every supported Python under 3.12 — which is to say the MCP server did not
start at all there.

`requires-python` is `>=3.10`, so 1.2.0 should not have shipped. It passed
local checks because the development interpreter was 3.14; CI caught it on the
3.10 matrix entry, but the publish workflow triggers on release rather than on
a green CI run, so the package reached PyPI anyway.

Now imported from `typing_extensions` below 3.12, with the backport declared as
a conditional dependency. Verified against a real 3.11 interpreter: 1382 tests
pass, where 1.2.0 could not import.

**If you installed 1.2.0 on Python 3.10 or 3.11, upgrade.** On 3.12+ it was
unaffected.


## v1.2.0

### Added — MCP tools publish the names of the keys they return (#47)

fastmcp derives `outputSchema` from a tool's return annotation, so
`dict[str, Any]` published `{"type": "object", "additionalProperties": true}`
— an object with no key names. A programmatic caller had to guess them, and a
wrong guess produces a program that validates, runs, and answers incorrectly.
Measured across four local models driven through lackpy: **0/24 correct while
17/24 called the right tool**, with `len()` on `events`' dict returning its key
count rather than its event count.

14 tools now return TypedDicts whose keys come from the implementations' own
return statements. Declaring the shapes immediately caught three places where
the code did not match a reasonable reading of it:

- `output.streams` is a list of stream names, not a mapping
- `query.rows` is positional value lists paired with `columns`, not per-row objects
- `ci_generate.available` is the registered command names, not a boolean

No data is dropped — keys a TypedDict does not declare still reach the client
in `structuredContent`.

**One behavioural note for Python callers:** with an `outputSchema` present,
fastmcp's own client deserializes `.data` into a generated model rather than a
dict, so `result.data["key"]` no longer works. Read `structured_content`, which
is the JSON a real MCP client receives over the wire. Consumers speaking
JSON-RPC (Claude Code, lackpy, any stdio client) are unaffected and strictly
better off.


## v1.1.0

Three fixes with one theme: blq reported success while losing the thing it was
asked to capture. Schema moves 3.0.0 -> 3.1.0 and migrates automatically on the
next open; no user action, existing rows are preserved.

### Fixed — `import` and `capture` wrote data no query could read (#50)

`cmd_import` and `cmd_capture` called `write_run_parquet` unconditionally, while
`exec`/`run` branch on `config.use_bird`. BIRD is the default storage mode, so on
a default project an import produced a well-formed parquet under `.bird/logs/`
that the query layer — a view over the BIRD tables — never reads:

    $ blq import report.json --format pytest_json
    Imported 2 events (2 errors, 0 warnings)
    Saved to .bird/logs/date=.../source=import/001_report.parquet
    $ blq events
    (no data)

Success, a destination path, exit 0, a real file, and no observable effect. The
storage branch had been added to the exec path and never to these two. Both now
route through the configured backend; parquet mode is unchanged.

This mattered most for the richest source available: a `pytest_json` import
carries the whole failure block — assertion, source line, and the
`+ where None = normalize(...)` line naming the function under test — where a
default text capture of the same run carried seven characters.

### Fixed — captured children were told the terminal was 80 columns (#49)

Anything that formats for a terminal consults `COLUMNS` and falls back to 80
without a TTY, which is always true under a capturing runner. `pytest -q`
abbreviates its short-summary line on that basis, and blq parses that line, so
the stored message lost everything past roughly 80 columns minus the test name.
How diagnosable a failure was depended on how long someone had named the test:

| test name | message stored |
|---|---|
| 73 characters | 7 — `asse...` |
| 35 characters | 45 — nothing lost |

Captured children now receive `COLUMNS=200` unless the caller set one; an
explicit `COLUMNS=80` still truncates, which is the caller's choice. Applied in
`blq.ext.capture_env`, covering `LocalExecutor` (the path `exec`/`run` take) and
the two direct `Popen` sites, plus the sandbox profiler for consistency.

### Fixed — duck_hunt's `log_content` was received and discarded (#52)

duck_hunt returns the entire failure block in `log_content`. blq queries
`SELECT *` so it received the column, then dropped it at write time because the
event schema had no place for it — keeping only `log_line_start`/`log_line_end`,
pointers into a log not retained unless `--keep-raw` is set. Measured on one
assertion failure: **391 characters available, 16 persisted.**

`events.log_content` now exists in both backends, exposed through
`blq_load_events()`, capped at 32K with the truncation stated in the value
rather than applied silently.

The vestigial `raw_text` column was renamed rather than duplicated: nothing
wrote it, its only reader looked up a key duck_hunt does not emit, and
`docs/design/duck-hunt-v3-migration.md` already recorded `raw_text -> log_content`
as the intended migration. Old parquet files stay readable via
`union_by_name = true`. The MCP `event` tool now returns a populated
`log_content` where it previously returned an always-null `raw_text`.

### Migration

Schema 3.0.0 -> 3.1.0, handled by the existing self-heal on next open. Two
layers, following the `extension_data` precedent: a version-keyed migration that
drops dependent views before the `ALTER`, and a version-independent column
reconcile so a database whose version advanced without the `ALTER` landing is
still repaired. Verified against a database built from the shipped 3.0.0 schema
with prior data: version advances, existing rows survive with NULL, new rows
carry the block.


## v1.0.2

### Bug fixes
- **Self-heal DBs stuck without `extension_data`.** The 2.3->2.4 migration renamed `sandbox` -> `extension_data` via `RENAME COLUMN`, which DuckDB blocks when non-view dependencies (FKs/constraints) exist on `attempts`/`invocations` — even after dropping views. The rename failed silently but the schema version advanced anyway, leaving DBs permanently stuck without `extension_data` and crashing every `write_attempt` ("Table attempts does not have a column extension_data"). `_ensure_schema` now self-heals version-independently: it gates on a missing column, adds `extension_data` via `ADD COLUMN` (never blocked), copies any existing `sandbox` data across (wrapped as `{"sandbox": ...}`, no data loss), and re-applies the idempotent schema to recreate views. Healthy/fresh DBs stay a fast no-op. Repairs on next connect (the CLI heals on the next `blq run`; a running MCP server heals after restart).
