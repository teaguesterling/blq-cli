# blq Integration with Patterns for Toolcraft

*Reference prompt for integrating blq with the design patterns from the Ma experimental program.*

## Before doing anything

Read these files in the judgementalmonad.com repo:

1. `blog/patterns/05-sandbox-specifications.md` — **Primary.** Sandbox specs as a blq feature. Declarative bounds, effects ceiling, monitoring-to-enforcement workflow, queryable grade.
2. `blog/patterns/08-the-coach.md` — blq provides the build/test event stream the Coach observes. Integration architecture section shows how blq events feed coaching suggestions.
3. `blog/patterns/07-the-mode-controller.md` — The Mode Controller watches blq's event stream for failure patterns. Mode transitions can trigger different blq configurations.
4. `blog/patterns/01-the-quartermaster.md` — The Quartermaster selects which blq commands are available per mode. Kit manifests reference blq's run_tests equivalent.

Also read:
5. `~/Projects/lq/main/docs/design/design-sandbox-specs.md` — **The full design doc.** Implementation details for sandbox specs in blq, including bwrap/cgroup enforcement, effects_ceiling computation, Phase 0-3 workflow.
6. `experiments/pilot-findings.md` — Sections 8-11 for the experimental evidence behind these patterns.
7. `drafts/the-experiment-that-proved-us-wrong.md` — The narrative that motivates the patterns.

## What blq should gain

### 1. Sandbox Specifications (primary deliverable)

Every registered command gets an optional sandbox spec:

```toml
[commands.test]
cmd = "python -m pytest tests/"

[commands.test.sandbox]
network = "none"
filesystem = "readonly"
timeout = "60s"
memory = "512m"
```

The spec is:
- **Declarative** — TOML configuration, not code
- **Enforceable** — bwrap wraps the command when enforcement is on
- **Queryable** — DuckDB can query the spec alongside run results
- **Gradable** — `effects_ceiling` computes the Ma grade from the spec

Implementation phases:
- Phase 0: Monitor (log resource usage, no enforcement)
- Phase 1: Declare (spec exists, violations warned not blocked)
- Phase 2: Enforce (bwrap wraps the command)
- Phase 3: Tighten (narrow bounds based on observed usage)

The full design is in `design-sandbox-specs.md`.

### 2. Coach event stream (secondary)

blq already captures structured events (errors, warnings, timing). The Coach pattern needs:

```sql
-- Recent test failures for the Coach to analyze
SELECT event_type, message, file, line, severity
FROM blq_events
WHERE run_id = (SELECT max(run_id) FROM blq_runs)
AND event_type = 'error';

-- Edit-without-test detection
SELECT count(*) as edits_since_last_test
FROM recent_tool_calls(20)
WHERE tool_name LIKE 'file_edit%'
AND sequence_number > (
    SELECT max(sequence_number) FROM recent_tool_calls(20)
    WHERE tool_name = 'run_tests'
);
```

blq doesn't need to implement the Coach — it needs to expose the data the Coach queries. That's already mostly there via `blq events` and `blq errors`. The gap: a `blq query` interface that the Coach hook can call.

### 3. Mode-aware command configuration (tertiary)

Different modes may want different blq configurations:

```toml
# Debug mode: run tests with verbose output
[modes.debug.commands.test]
cmd = "python -m pytest tests/ -v --tb=long"
sandbox = "test"

# Implementation mode: run tests with short output
[modes.implement.commands.test]
cmd = "python -m pytest tests/ --tb=short -q"
sandbox = "test"

# Review mode: no test running (read-only)
[modes.review.commands.test]
enabled = false
```

This is future work — it depends on the Mode Controller pattern being implemented. But the schema should be designed to support it.

## Key experimental findings relevant to blq

1. **run_tests as a separate tool added overhead** — wrapping pytest in a structured tool cost +36% vs running it through bash. The wrapper should be thin and the output should be diagnostic-quality, not just formatted.

2. **The agent runs pytest through bash for exactly one reason** — to see the output. blq's structured event capture is the alternative that could be *better* than bash if the output is diagnostic-quality (grouped failures, expected vs actual, root cause hints).

3. **Sandbox specs change the grade** — adding `--die-with-parent` to bwrap dropped our experiment's bash from level 7 to level 4. One flag. The sandbox spec makes these flags explicit and auditable.

4. **The monitoring-before-enforcing workflow matches blq's philosophy** — blq already captures output before you query it. Sandbox specs follow the same pattern: observe before constraining.

## Priority

1. **Sandbox spec schema** — add to command registry, log alongside runs
2. **effects_ceiling computation** — auto-compute grade from spec
3. **bwrap enforcement** — wrap commands when enforcement is enabled
4. **Coach-compatible queries** — ensure blq's event stream is queryable by external hooks
5. **Mode-aware configuration** — future, depends on Mode Controller

## Connection to the ratchet

blq is already a ratchet tool — it captures build output and structures it. Sandbox specs extend the ratchet to the execution environment: observe resource usage → declare bounds → enforce bounds → tighten over time. Each step is a ratchet turn that drops the grade and improves characterizability.
