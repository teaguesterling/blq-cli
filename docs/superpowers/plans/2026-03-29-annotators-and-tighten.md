# Annotator System & Spec Tightening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a plugin system for enriching run events with annotations (e.g., source context for error locations), plus `blq sandbox tighten` to auto-narrow sandbox specs from observed data.

**Architecture:** Annotators are Python plugins discovered via entry points. After events are stored to BIRD, annotators receive a `RunContext` proxy (lazy DB-backed access to events, output, metadata) and write enrichments back to the events `metadata` JSON column. Each annotation carries a `display` hint (`inline`, `detail`, `hidden`) for rendering. Spec tightening is a separate command that queries profiling + monitoring data and updates `commands.toml`.

**Tech Stack:** Python 3.12+, DuckDB, entry points discovery, existing `blq.ext` infrastructure

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/blq/ext/annotator.py` | Create | `Annotator` protocol, `RunContext` proxy, `Annotation` dataclass, discovery + dispatch |
| `src/blq/ext/pipeline.py` | Modify | Call annotators after store in the pipeline |
| `src/blq/commands/execution.py` | Modify | Run eager annotators in Window 2 after events are written |
| `src/blq_sandbox/tighten.py` | Create | Spec tightening logic (query data, compute tighter spec, update TOML) |
| `src/blq/commands/sandbox_cmd.py` | Modify | Add `cmd_sandbox_tighten()` handler |
| `src/blq/cli.py` | Modify | Add `sandbox tighten` subparser |
| `tests/test_annotator.py` | Create | Unit tests for RunContext, Annotation, and annotator dispatch |
| `tests/test_sandbox_tighten.py` | Create | Tests for spec tightening logic |

---

### Task 1: Annotation Data Model

**Files:**
- Create: `src/blq/ext/annotator.py`
- Create: `tests/test_annotator.py`

Define the `Annotation` dataclass, `RunContext` proxy, and `Annotator` protocol.

- [ ] **Step 1: Write failing tests for Annotation dataclass**

```python
# tests/test_annotator.py
"""Tests for the annotator system."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from blq.ext.annotator import Annotation


class TestAnnotation:
    def test_to_dict(self):
        a = Annotation(
            annotator="source_context",
            type="source",
            display="inline",
            data={"function": "def foo():", "file": "src/foo.py", "lines": [10, 20]},
        )
        d = a.to_dict()
        assert d["annotator"] == "source_context"
        assert d["type"] == "source"
        assert d["display"] == "inline"
        assert d["data"]["function"] == "def foo():"

    def test_display_values(self):
        for display in ("inline", "detail", "hidden"):
            a = Annotation(annotator="test", type="test", display=display, data={})
            assert a.display == display

    def test_invalid_display_raises(self):
        with pytest.raises(ValueError):
            Annotation(annotator="test", type="test", display="invalid", data={})

    def test_from_dict(self):
        d = {
            "annotator": "git_blame",
            "type": "provenance",
            "display": "detail",
            "data": {"author": "alice@example.com"},
        }
        a = Annotation.from_dict(d)
        assert a.annotator == "git_blame"
        assert a.data["author"] == "alice@example.com"

    def test_to_json_roundtrip(self):
        a = Annotation(
            annotator="source_context",
            type="source",
            display="inline",
            data={"function": "def foo():"},
        )
        raw = json.dumps(a.to_dict())
        restored = Annotation.from_dict(json.loads(raw))
        assert restored.annotator == a.annotator
        assert restored.data == a.data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_annotator.py::TestAnnotation -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq.ext.annotator'`

- [ ] **Step 3: Implement Annotation dataclass**

```python
# src/blq/ext/annotator.py
"""Annotator system for enriching run events.

Annotators are Python plugins that add structured metadata to events
after they're stored in BIRD. Each annotation carries a display hint
for rendering (inline, detail, hidden).

Annotations are stored in the events.metadata JSON column under
an "annotations" key.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("blq-ext")

DISPLAY_VALUES = ("inline", "detail", "hidden")


@dataclass
class Annotation:
    """A structured enrichment attached to an event."""

    annotator: str  # Plugin name that produced this
    type: str  # Category (source, provenance, diagnostic, etc.)
    display: str  # When to show: inline, detail, hidden
    data: dict[str, Any]  # Annotator-specific payload

    def __post_init__(self) -> None:
        if self.display not in DISPLAY_VALUES:
            raise ValueError(
                f"Invalid display value: {self.display!r}. "
                f"Expected one of: {', '.join(DISPLAY_VALUES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotator": self.annotator,
            "type": self.type,
            "display": self.display,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Annotation:
        return cls(
            annotator=d["annotator"],
            type=d["type"],
            display=d["display"],
            data=d["data"],
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_annotator.py::TestAnnotation -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq/ext/annotator.py tests/test_annotator.py
git commit -m "feat: add Annotation dataclass for event enrichment"
```

---

### Task 2: RunContext Proxy

**Files:**
- Modify: `src/blq/ext/annotator.py`
- Modify: `tests/test_annotator.py`

The RunContext provides lazy, DB-backed access to a stored run's data.

- [ ] **Step 1: Write failing tests for RunContext**

Append to `tests/test_annotator.py`:

```python
import duckdb

from blq.ext.annotator import RunContext


class TestRunContext:
    @pytest.fixture
    def ctx(self, tmp_path: Path):
        """Create a RunContext with a minimal in-memory DB."""
        conn = duckdb.connect()
        # Create minimal schema
        conn.execute("""
            CREATE TABLE invocations (
                id VARCHAR, source_name VARCHAR, cmd VARCHAR,
                cwd VARCHAR, extension_data JSON, timestamp TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE events (
                id VARCHAR, invocation_id VARCHAR, event_index INTEGER,
                severity VARCHAR, ref_file VARCHAR, ref_line INTEGER,
                ref_column INTEGER, message VARCHAR, code VARCHAR,
                fingerprint VARCHAR, metadata JSON
            )
        """)
        conn.execute("""
            CREATE TABLE outcomes (
                attempt_id VARCHAR, exit_code INTEGER, duration_ms BIGINT
            )
        """)
        # Insert test data
        conn.execute("""
            INSERT INTO invocations VALUES (
                'inv-1', 'test', 'pytest tests/', '/project',
                '{"sandbox": {"network": "none"}}', '2026-03-29 12:00:00'
            )
        """)
        conn.execute("""
            INSERT INTO events VALUES
                ('evt-1', 'inv-1', 0, 'error', 'src/foo.py', 42, NULL,
                 'undefined variable x', 'E001', 'fp1', NULL),
                ('evt-2', 'inv-1', 1, 'warning', 'src/bar.py', 10, 5,
                 'unused import', 'W001', 'fp2', NULL)
        """)
        conn.execute("""
            INSERT INTO outcomes VALUES ('inv-1', 1, 5000)
        """)
        return RunContext(
            conn=conn,
            invocation_id="inv-1",
            source_root=tmp_path,
        )

    def test_events_returns_list(self, ctx):
        events = ctx.events
        assert len(events) == 2
        assert events[0]["severity"] == "error"

    def test_metadata_returns_dict(self, ctx):
        meta = ctx.metadata
        assert meta["source_name"] == "test"
        assert meta["cmd"] == "pytest tests/"

    def test_extension_data_returns_dict(self, ctx):
        ext = ctx.extension_data
        assert ext["sandbox"]["network"] == "none"

    def test_source_root(self, ctx, tmp_path):
        assert ctx.source_root == tmp_path

    def test_conn_accessible(self, ctx):
        result = ctx.conn.execute("SELECT 1").fetchone()
        assert result[0] == 1

    def test_exit_code(self, ctx):
        assert ctx.exit_code == 1

    def test_duration_ms(self, ctx):
        assert ctx.duration_ms == 5000

    def test_add_annotation_to_event(self, ctx):
        annotation = Annotation(
            annotator="test_plugin",
            type="test",
            display="inline",
            data={"key": "value"},
        )
        ctx.add_annotation("evt-1", annotation)
        # Verify it's stored in metadata
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(result[0])
        assert "annotations" in meta
        assert meta["annotations"][0]["annotator"] == "test_plugin"

    def test_add_multiple_annotations(self, ctx):
        a1 = Annotation(annotator="p1", type="t1", display="inline", data={"a": 1})
        a2 = Annotation(annotator="p2", type="t2", display="detail", data={"b": 2})
        ctx.add_annotation("evt-1", a1)
        ctx.add_annotation("evt-1", a2)
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(result[0])
        assert len(meta["annotations"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_annotator.py::TestRunContext -v`
Expected: FAIL — `cannot import name 'RunContext' from 'blq.ext.annotator'`

- [ ] **Step 3: Implement RunContext**

Add to `src/blq/ext/annotator.py`:

```python
class RunContext:
    """Lazy, DB-backed proxy into a stored run's data.

    Provides access to events, output, metadata, and extension data
    without requiring callers to write SQL. Annotators use this to
    read run data and write annotations back to the events table.
    """

    def __init__(
        self,
        conn: Any,  # duckdb.DuckDBPyConnection
        invocation_id: str,
        source_root: Path,
    ) -> None:
        self._conn = conn
        self._invocation_id = invocation_id
        self._source_root = source_root
        # Lazy caches
        self._events: list[dict[str, Any]] | None = None
        self._metadata: dict[str, Any] | None = None
        self._outcome: dict[str, Any] | None = None

    @property
    def conn(self) -> Any:
        """Raw DuckDB connection for custom queries."""
        return self._conn

    @property
    def invocation_id(self) -> str:
        return self._invocation_id

    @property
    def source_root(self) -> Path:
        return self._source_root

    @property
    def events(self) -> list[dict[str, Any]]:
        """All events for this run, ordered by event_index."""
        if self._events is None:
            result = self._conn.execute(
                """
                SELECT id, event_index, severity, ref_file, ref_line,
                       ref_column, message, code, fingerprint, metadata
                FROM events
                WHERE invocation_id = ?
                ORDER BY event_index
                """,
                [self._invocation_id],
            ).fetchall()
            columns = [
                "id", "event_index", "severity", "ref_file", "ref_line",
                "ref_column", "message", "code", "fingerprint", "metadata",
            ]
            self._events = [dict(zip(columns, row)) for row in result]
        return self._events

    @property
    def metadata(self) -> dict[str, Any]:
        """Invocation metadata (source_name, cmd, cwd, etc.)."""
        if self._metadata is None:
            result = self._conn.execute(
                """
                SELECT source_name, cmd, cwd, extension_data, timestamp
                FROM invocations
                WHERE id = ?
                """,
                [self._invocation_id],
            ).fetchone()
            if result:
                self._metadata = {
                    "source_name": result[0],
                    "cmd": result[1],
                    "cwd": result[2],
                    "extension_data": (
                        json.loads(result[3]) if result[3] else {}
                    ),
                    "timestamp": result[4],
                }
            else:
                self._metadata = {}
        return self._metadata

    @property
    def extension_data(self) -> dict[str, Any]:
        """Extension data (sandbox spec, grades, metrics)."""
        return self.metadata.get("extension_data", {})

    @property
    def exit_code(self) -> int | None:
        """Exit code from the outcomes table."""
        if self._outcome is None:
            self._load_outcome()
        return self._outcome.get("exit_code")

    @property
    def duration_ms(self) -> int | None:
        """Duration in milliseconds from the outcomes table."""
        if self._outcome is None:
            self._load_outcome()
        return self._outcome.get("duration_ms")

    def _load_outcome(self) -> None:
        result = self._conn.execute(
            "SELECT exit_code, duration_ms FROM outcomes WHERE attempt_id = ?",
            [self._invocation_id],
        ).fetchone()
        if result:
            self._outcome = {"exit_code": result[0], "duration_ms": result[1]}
        else:
            self._outcome = {}

    def add_annotation(self, event_id: str, annotation: Annotation) -> None:
        """Write an annotation to an event's metadata.

        Appends to the "annotations" list in the metadata JSON.
        Creates the list if it doesn't exist.
        """
        # Read current metadata
        result = self._conn.execute(
            "SELECT metadata FROM events WHERE id = ?",
            [event_id],
        ).fetchone()

        if result and result[0]:
            meta = json.loads(result[0]) if isinstance(result[0], str) else result[0]
        else:
            meta = {}

        annotations_list = meta.get("annotations", [])
        annotations_list.append(annotation.to_dict())
        meta["annotations"] = annotations_list

        self._conn.execute(
            "UPDATE events SET metadata = ? WHERE id = ?",
            [json.dumps(meta), event_id],
        )

        # Invalidate events cache
        self._events = None
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_annotator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq/ext/annotator.py tests/test_annotator.py
git commit -m "feat: add RunContext proxy for annotator DB access"
```

---

### Task 3: Annotator Protocol and Discovery

**Files:**
- Modify: `src/blq/ext/annotator.py`
- Modify: `tests/test_annotator.py`

Define the `Annotator` protocol and discovery/dispatch functions.

- [ ] **Step 1: Write failing tests for annotator protocol and dispatch**

Append to `tests/test_annotator.py`:

```python
from blq.ext.annotator import Annotator, load_annotators, run_annotators


class MockAnnotator:
    """A test annotator that adds a fixed annotation to error events."""

    name = "mock"
    eager = True

    def should_annotate(self, context: RunContext) -> bool:
        return any(e["severity"] == "error" for e in context.events)

    def annotate(self, context: RunContext) -> None:
        for event in context.events:
            if event["severity"] == "error":
                context.add_annotation(
                    event["id"],
                    Annotation(
                        annotator=self.name,
                        type="test",
                        display="inline",
                        data={"enriched": True},
                    ),
                )


class DeferredAnnotator:
    """A test annotator that is not eager."""

    name = "deferred"
    eager = False

    def should_annotate(self, context: RunContext) -> bool:
        return True

    def annotate(self, context: RunContext) -> None:
        pass


class TestAnnotatorProtocol:
    def test_mock_satisfies_protocol(self):
        a = MockAnnotator()
        assert a.name == "mock"
        assert a.eager is True

    def test_deferred_annotator(self):
        a = DeferredAnnotator()
        assert a.eager is False


class TestRunAnnotators:
    @pytest.fixture
    def ctx(self, tmp_path: Path):
        """Same fixture as TestRunContext."""
        conn = duckdb.connect()
        conn.execute("""
            CREATE TABLE invocations (
                id VARCHAR, source_name VARCHAR, cmd VARCHAR,
                cwd VARCHAR, extension_data JSON, timestamp TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE events (
                id VARCHAR, invocation_id VARCHAR, event_index INTEGER,
                severity VARCHAR, ref_file VARCHAR, ref_line INTEGER,
                ref_column INTEGER, message VARCHAR, code VARCHAR,
                fingerprint VARCHAR, metadata JSON
            )
        """)
        conn.execute("""
            CREATE TABLE outcomes (
                attempt_id VARCHAR, exit_code INTEGER, duration_ms BIGINT
            )
        """)
        conn.execute("""
            INSERT INTO invocations VALUES (
                'inv-1', 'test', 'pytest', '/project', NULL, '2026-03-29 12:00:00'
            )
        """)
        conn.execute("""
            INSERT INTO events VALUES
                ('evt-1', 'inv-1', 0, 'error', 'src/foo.py', 42, NULL,
                 'undefined variable x', 'E001', 'fp1', NULL),
                ('evt-2', 'inv-1', 1, 'info', NULL, NULL, NULL,
                 'build complete', NULL, NULL, NULL)
        """)
        conn.execute("INSERT INTO outcomes VALUES ('inv-1', 1, 5000)")
        return RunContext(conn=conn, invocation_id="inv-1", source_root=tmp_path)

    def test_run_eager_annotators(self, ctx):
        annotators = [MockAnnotator(), DeferredAnnotator()]
        run_annotators(ctx, annotators, eager_only=True)
        # Mock is eager and should have annotated the error event
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(result[0])
        assert "annotations" in meta
        assert meta["annotations"][0]["data"]["enriched"] is True

    def test_deferred_skipped_when_eager_only(self, ctx):
        annotators = [DeferredAnnotator()]
        run_annotators(ctx, annotators, eager_only=True)
        # No annotations should be added
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        assert result[0] is None

    def test_run_all_annotators(self, ctx):
        annotators = [MockAnnotator(), DeferredAnnotator()]
        run_annotators(ctx, annotators, eager_only=False)
        # Both should have run (deferred runs but doesn't annotate here)
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(result[0])
        assert len(meta["annotations"]) == 1  # Only mock annotates

    def test_annotator_failure_does_not_block(self, ctx):
        class FailingAnnotator:
            name = "failing"
            eager = True

            def should_annotate(self, context):
                return True

            def annotate(self, context):
                raise RuntimeError("annotator crashed")

        annotators = [FailingAnnotator(), MockAnnotator()]
        run_annotators(ctx, annotators, eager_only=True)
        # Mock should still have run despite the failure
        result = ctx.conn.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(result[0])
        assert meta["annotations"][0]["annotator"] == "mock"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_annotator.py::TestAnnotatorProtocol tests/test_annotator.py::TestRunAnnotators -v`
Expected: FAIL — `cannot import name 'load_annotators' from 'blq.ext.annotator'`

- [ ] **Step 3: Implement protocol and dispatch**

Add to `src/blq/ext/annotator.py`:

```python
class Annotator(Protocol):
    """Plugin that enriches stored events with additional context.

    Annotators run after events are written to BIRD. They receive a
    RunContext proxy and write annotations back to event metadata.

    Attributes:
        name: Plugin identifier
        eager: If True, runs during blq run (Window 2). If False,
               runs on demand (blq inspect, blq annotate).
    """

    name: str
    eager: bool

    def should_annotate(self, context: RunContext) -> bool:
        """Return True if this annotator should run for this context."""
        ...

    def annotate(self, context: RunContext) -> None:
        """Enrich events via context.add_annotation()."""
        ...


def load_annotators() -> list[Annotator]:
    """Discover installed annotators via entry points."""
    annotators: list[Annotator] = []
    for ep in entry_points(group="blq.annotators"):
        try:
            factory = ep.load()
            annotator = factory()
            annotators.append(annotator)
        except Exception as e:
            logger.warning(f"Failed to load annotator {ep.name}: {e}")
    return annotators


def run_annotators(
    context: RunContext,
    annotators: list[Annotator],
    eager_only: bool = False,
) -> None:
    """Run annotators against a stored run.

    Args:
        context: RunContext proxy for the run.
        annotators: List of annotator instances.
        eager_only: If True, skip annotators with eager=False.
    """
    for annotator in annotators:
        if eager_only and not annotator.eager:
            continue
        try:
            if annotator.should_annotate(context):
                annotator.annotate(context)
        except Exception as e:
            logger.warning(
                f"Annotator '{annotator.name}' failed: {e}"
            )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_annotator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq/ext/annotator.py tests/test_annotator.py
git commit -m "feat: add Annotator protocol with eager/deferred dispatch"
```

---

### Task 4: Wire Annotators into Execution Pipeline

**Files:**
- Modify: `src/blq/commands/execution.py`

Run eager annotators in Window 2 after events are written to the DB.

- [ ] **Step 1: Add annotator dispatch to `_execute_with_live_output()`**

In `src/blq/commands/execution.py`, find the Window 2 section where events are written (after `store.write_events()`). Inside the `with BirdStore.open_with_retry(lq_dir) as store:` block, after events are written and before the connection closes, add:

```python
            # Run eager annotators on stored events
            from blq.ext.annotator import RunContext, load_annotators, run_annotators

            annotators = load_annotators()
            if annotators:
                run_context = RunContext(
                    conn=store._conn,
                    invocation_id=attempt_id,
                    source_root=config.lq_dir.parent,
                )
                run_annotators(run_context, annotators, eager_only=True)
```

Place this after `store.write_events()` and after `store.update_attempt_extension_data()` but before the live output finalization. This runs inside the existing Window 2 DB lock so we don't need a separate connection.

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/pytest tests/ -q --tb=short -x`
Expected: All pass (annotators list will be empty since none are registered)

- [ ] **Step 3: Commit**

```bash
git add src/blq/commands/execution.py
git commit -m "feat: run eager annotators in Window 2 after events are stored"
```

---

### Task 5: Sandbox Spec Tightening

**Files:**
- Create: `src/blq_sandbox/tighten.py`
- Modify: `src/blq/commands/sandbox_cmd.py`
- Modify: `src/blq/cli.py`
- Create: `tests/test_sandbox_tighten.py`

`blq sandbox tighten <cmd>` computes a tighter spec from observed data and writes it to commands.toml.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_tighten.py
"""Tests for sandbox spec tightening."""
from __future__ import annotations

from pathlib import Path

import pytest

from blq_sandbox.spec import SandboxSpec
from blq_sandbox.tighten import compute_tighter_spec


class TestComputeTighterSpec:
    def test_tightens_memory_from_observed(self):
        current = SandboxSpec(
            network="none", filesystem="readonly",
            memory=1024 * 1024 * 1024,  # 1g
        )
        observed = {"max_memory_bytes": 200 * 1024 * 1024}  # 200m observed
        tighter = compute_tighter_spec(current, observed)
        # Should suggest 400m (2x headroom), tighter than 1g
        assert tighter.memory is not None
        assert tighter.memory < current.memory
        assert tighter.memory >= 200 * 1024 * 1024 * 2  # at least 2x

    def test_does_not_loosen(self):
        current = SandboxSpec(
            network="none", filesystem="readonly",
            memory=100 * 1024 * 1024,  # 100m
        )
        observed = {"max_memory_bytes": 200 * 1024 * 1024}  # 200m > 100m
        tighter = compute_tighter_spec(current, observed)
        # Should NOT loosen — keep 100m or warn, don't set to 400m
        assert tighter.memory == current.memory

    def test_tightens_timeout(self):
        current = SandboxSpec(
            network="none", filesystem="readonly",
            timeout=300,  # 5m
        )
        observed = {"max_duration_ms": 10000}  # 10s observed
        tighter = compute_tighter_spec(current, observed)
        assert tighter.timeout is not None
        assert tighter.timeout < current.timeout
        assert tighter.timeout >= 30  # at least 3x the 10s

    def test_tightens_cpu(self):
        current = SandboxSpec(
            network="none", filesystem="readonly",
            cpu=120,  # 2m
        )
        observed = {"max_cpu_usec": 5_000_000}  # 5s observed
        tighter = compute_tighter_spec(current, observed)
        assert tighter.cpu is not None
        assert tighter.cpu < current.cpu
        assert tighter.cpu >= 10  # at least 2x the 5s

    def test_preserves_non_resource_dimensions(self):
        current = SandboxSpec(
            network="none", filesystem="workspace_only",
            processes="isolated",
        )
        observed = {}
        tighter = compute_tighter_spec(current, observed)
        assert tighter.network == "none"
        assert tighter.filesystem == "workspace_only"
        assert tighter.processes == "isolated"

    def test_no_change_when_no_data(self):
        current = SandboxSpec(network="none", filesystem="readonly")
        observed = {}
        tighter = compute_tighter_spec(current, observed)
        assert tighter == current

    def test_adds_resource_limits_when_missing(self):
        current = SandboxSpec(network="none", filesystem="readonly")
        observed = {"max_memory_bytes": 100 * 1024 * 1024}
        tighter = compute_tighter_spec(current, observed)
        # Should add memory limit (wasn't set before)
        assert tighter.memory is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sandbox_tighten.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq_sandbox.tighten'`

- [ ] **Step 3: Implement tightening logic**

```python
# src/blq_sandbox/tighten.py
"""Sandbox spec tightening — auto-narrow specs from observed data.

The ratchet: observe resource usage → compute tighter bounds → update spec.
Only tightens (never loosens) existing bounds. Adds bounds for dimensions
that were previously unlimited when observation data is available.
"""
from __future__ import annotations

import copy
from typing import Any

from blq_sandbox.spec import SandboxSpec

# Headroom multipliers
MEMORY_HEADROOM = 2.0  # 2x observed peak
CPU_HEADROOM = 2.0  # 2x observed peak
TIMEOUT_HEADROOM = 3.0  # 3x observed max wall time
MIN_TIMEOUT = 10  # Never suggest less than 10s
MIN_CPU = 5  # Never suggest less than 5s
MIN_MEMORY = 64 * 1024 * 1024  # Never suggest less than 64m


def compute_tighter_spec(
    current: SandboxSpec,
    observed: dict[str, Any],
) -> SandboxSpec:
    """Compute a tighter spec from observed resource usage.

    Rules:
    - Only tightens (lowers) bounds, never loosens them
    - Adds bounds for unlimited dimensions when data is available
    - Non-resource dimensions (network, filesystem, processes) are preserved
    - Applies headroom multipliers to observed maximums

    Args:
        current: The current sandbox spec.
        observed: Dict with optional keys:
            max_memory_bytes, max_cpu_usec, max_duration_ms

    Returns:
        A new SandboxSpec (may be identical to current if no tightening possible).
    """
    tighter = copy.copy(current)

    # Memory
    max_memory = observed.get("max_memory_bytes")
    if max_memory is not None:
        suggested = max(int(max_memory * MEMORY_HEADROOM), MIN_MEMORY)
        if current.memory is None:
            tighter.memory = suggested
        elif suggested < current.memory:
            tighter.memory = suggested
        # else: observed is higher than current — don't loosen

    # CPU
    max_cpu_usec = observed.get("max_cpu_usec")
    if max_cpu_usec is not None:
        observed_cpu_s = max_cpu_usec / 1_000_000
        suggested = max(int(observed_cpu_s * CPU_HEADROOM), MIN_CPU)
        if current.cpu is None:
            tighter.cpu = suggested
        elif suggested < current.cpu:
            tighter.cpu = suggested

    # Timeout
    max_duration_ms = observed.get("max_duration_ms")
    if max_duration_ms is not None:
        observed_s = max_duration_ms / 1000
        suggested = max(int(observed_s * TIMEOUT_HEADROOM), MIN_TIMEOUT)
        if current.timeout is None:
            tighter.timeout = suggested
        elif suggested < current.timeout:
            tighter.timeout = suggested

    return tighter
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_sandbox_tighten.py -v`
Expected: All PASS

- [ ] **Step 5: Add CLI command**

Add `cmd_sandbox_tighten()` to `src/blq/commands/sandbox_cmd.py`:

```python
def cmd_sandbox_tighten(args: Any) -> None:
    """Tighten sandbox spec from observed resource metrics."""
    config = BlqConfig.ensure()
    cmd_name = args.command
    dry_run = getattr(args, "dry_run", False)

    if cmd_name not in config.commands:
        print(f"Error: Unknown command '{cmd_name}'", file=sys.stderr)
        sys.exit(1)

    reg_cmd = config.commands[cmd_name]
    sandbox_raw = reg_cmd._extra.get("sandbox")

    if sandbox_raw is None:
        print(f"Command '{cmd_name}' has no sandbox spec. Use 'blq sandbox suggest' first.")
        return

    current = resolve_sandbox(sandbox_raw)
    if current is None:
        print(f"Error: Could not resolve sandbox spec", file=sys.stderr)
        sys.exit(1)

    # Query observed metrics
    from blq.bird import BirdStore

    try:
        with BirdStore.open(config.lq_dir) as store:
            result = store._conn.execute(
                """
                SELECT
                    count(*) as run_count,
                    max(json_extract(extension_data,
                        '$.metrics.memory_peak_bytes')::BIGINT),
                    max(json_extract(extension_data,
                        '$.metrics.cpu_usage_usec')::BIGINT),
                    max(o.duration_ms)
                FROM invocations i
                LEFT JOIN outcomes o ON o.attempt_id = i.id
                WHERE i.source_name = ?
                  AND i.extension_data IS NOT NULL
                """,
                [cmd_name],
            ).fetchone()
    except Exception as e:
        print(f"Error querying metrics: {e}", file=sys.stderr)
        sys.exit(1)

    run_count = result[0] if result else 0
    if run_count < 3:
        print(f"Only {run_count} run(s) found. Need at least 3 for reliable tightening.")
        return

    observed = {}
    if result[1] is not None:
        observed["max_memory_bytes"] = result[1]
    if result[2] is not None:
        observed["max_cpu_usec"] = result[2]
    if result[3] is not None:
        observed["max_duration_ms"] = result[3]

    if not observed:
        print("No resource metrics available. Enable systemd engine for cgroup monitoring.")
        return

    from blq_sandbox.tighten import compute_tighter_spec

    tighter = compute_tighter_spec(current, observed)

    if tighter == current:
        print(f"Spec for '{cmd_name}' is already as tight as observed data allows.")
        return

    # Show diff
    from blq_sandbox.spec import format_duration, format_size

    print(f"Tightening sandbox spec for '{cmd_name}' (from {run_count} runs):")
    print()

    current_d = current.to_dict()
    tighter_d = tighter.to_dict()
    for key in sorted(set(current_d.keys()) | set(tighter_d.keys())):
        old = current_d.get(key, "(none)")
        new = tighter_d.get(key, "(none)")
        if old != new:
            print(f"  {key}: {old} -> {new}")
        else:
            print(f"  {key}: {old}")

    if dry_run:
        print()
        print("(dry run — no changes written)")
        return

    # Write updated spec
    reg_cmd._extra["sandbox"] = tighter.to_dict()
    config.save_commands()
    print()
    print(f"Updated commands.toml")
```

Add to `src/blq/cli.py`, in the sandbox subparser section:

```python
    # sandbox tighten
    p_sandbox_tighten = sandbox_subparsers.add_parser(
        "tighten", help="Tighten sandbox spec from observed data"
    )
    p_sandbox_tighten.add_argument("command", help="Command name")
    p_sandbox_tighten.add_argument(
        "--dry-run", action="store_true",
        help="Show changes without writing to commands.toml",
    )
    p_sandbox_tighten.set_defaults(func=cmd_sandbox_tighten)
```

Add `cmd_sandbox_tighten` to the import from `blq.commands.sandbox_cmd`.

- [ ] **Step 6: Run full test suite**

Run: `.venv/bin/pytest tests/ -q --tb=short -x`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/blq_sandbox/tighten.py src/blq/commands/sandbox_cmd.py src/blq/cli.py tests/test_sandbox_tighten.py
git commit -m "feat: add blq sandbox tighten for auto-narrowing specs"
```

---

### Task 6: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/sandbox.md`

- [ ] **Step 1: Update CLAUDE.md**

Add to the Completed section:
```
- **Annotator plugin system** for enriching run events (RunContext, eager/deferred dispatch)
- **Sandbox spec tightening** (`blq sandbox tighten`) — auto-narrow from observed data
```

- [ ] **Step 2: Update docs/sandbox.md**

Add a "Tightening" section to the Discovery Workflow, after "4. Enforce":

```markdown
### 5. Tighten

After accumulating runs, auto-narrow the spec based on observed resource usage:

\`\`\`bash
blq sandbox tighten test
# Tightening sandbox spec for 'test' (from 15 runs):
#   memory: 512m -> 256m
#   timeout: 1m -> 30s
#   cpu: 30s -> 15s
# Updated commands.toml
\`\`\`

Use `--dry-run` to preview changes without writing:

\`\`\`bash
blq sandbox tighten test --dry-run
\`\`\`

Tightening only reduces bounds — it never loosens them. It applies headroom
(2x memory, 2x CPU, 3x timeout) to observed maximums.
```

Add an "Annotators" section before "Requirements":

```markdown
## Annotators

Annotators are plugins that enrich stored events with additional context.
They run after events are written to the database and add structured
annotations to the `metadata` JSON column.

Each annotation has:
- **type** — what kind of enrichment (source, provenance, diagnostic)
- **display** — when to show it: `inline` (always), `detail` (inspect only), `hidden` (queryable only)
- **data** — annotator-specific payload

Annotators declare whether they're **eager** (run during `blq run`) or
**deferred** (run on demand). Eager annotators execute in Window 2
alongside event storage. Deferred annotators run when explicitly requested.

Annotators are discovered via Python entry points (`blq.annotators` group).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/sandbox.md
git commit -m "docs: add annotator system and spec tightening documentation"
```
