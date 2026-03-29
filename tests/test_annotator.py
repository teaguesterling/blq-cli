"""Tests for the annotator system: Annotation dataclass and RunContext proxy."""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from blq.ext.annotator import Annotation, RunContext


class TestAnnotation:
    def test_to_dict_roundtrip(self):
        ann = Annotation(annotator="mypy", type="diagnostic", display="inline", data={"key": "val"})
        d = ann.to_dict()
        restored = Annotation.from_dict(d)
        assert restored == ann

    def test_display_inline_valid(self):
        Annotation(annotator="a", type="t", display="inline", data={})

    def test_display_detail_valid(self):
        Annotation(annotator="a", type="t", display="detail", data={})

    def test_display_hidden_valid(self):
        Annotation(annotator="a", type="t", display="hidden", data={})

    def test_display_invalid_raises(self):
        with pytest.raises(ValueError, match="display"):
            Annotation(annotator="a", type="t", display="invalid", data={})

    def test_from_dict(self):
        d = {"annotator": "src", "type": "source", "display": "detail", "data": {"line": 42}}
        ann = Annotation.from_dict(d)
        assert ann.annotator == "src"
        assert ann.data == {"line": 42}

    def test_json_roundtrip(self):
        ann = Annotation(annotator="p", type="provenance", display="hidden", data={"sha": "abc123"})
        s = json.dumps(ann.to_dict())
        restored = Annotation.from_dict(json.loads(s))
        assert restored == ann


# ---------------------------------------------------------------------------
# TestRunContext — helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx_db():
    """In-memory DuckDB with minimal schema and test data."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE invocations (
            id VARCHAR,
            source_name VARCHAR,
            cmd VARCHAR,
            cwd VARCHAR,
            extension_data JSON,
            timestamp TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE events (
            id VARCHAR,
            invocation_id VARCHAR,
            event_index INTEGER,
            severity VARCHAR,
            ref_file VARCHAR,
            ref_line INTEGER,
            ref_column INTEGER,
            message VARCHAR,
            code VARCHAR,
            fingerprint VARCHAR,
            metadata JSON
        )
    """)
    conn.execute("""
        CREATE TABLE outcomes (
            attempt_id VARCHAR,
            exit_code INTEGER,
            duration_ms BIGINT
        )
    """)

    # Insert test data
    conn.execute("""
        INSERT INTO invocations VALUES (
            'inv-1', 'test', 'pytest tests/', '/proj',
            '{"mypy": {"strict": true}}',
            '2026-03-28 10:00:00'
        )
    """)
    conn.execute("""
        INSERT INTO events VALUES
            ('evt-1', 'inv-1', 0, 'error', 'src/foo.py', 10, 1, 'undefined var', 'E001', 'fp1', '{}'),
            ('evt-2', 'inv-1', 1, 'warning', 'src/bar.py', 20, 5, 'unused import', 'W001', 'fp2', '{}')
    """)
    conn.execute("""
        INSERT INTO outcomes VALUES ('inv-1', 1, 5432)
    """)

    yield conn
    conn.close()


@pytest.fixture
def run_ctx(ctx_db, tmp_path):
    return RunContext(conn=ctx_db, invocation_id="inv-1", source_root=tmp_path)


# ---------------------------------------------------------------------------
# TestRunContext
# ---------------------------------------------------------------------------

class TestRunContext:
    def test_events_returns_list(self, run_ctx):
        events = run_ctx.events
        assert isinstance(events, list)
        assert len(events) == 2

    def test_events_ordered_by_index(self, run_ctx):
        events = run_ctx.events
        assert events[0]["severity"] == "error"
        assert events[1]["severity"] == "warning"

    def test_metadata_returns_dict(self, run_ctx):
        meta = run_ctx.metadata
        assert isinstance(meta, dict)
        assert meta["source_name"] == "test"
        assert meta["cmd"] == "pytest tests/"

    def test_extension_data(self, run_ctx):
        ext = run_ctx.extension_data
        assert ext["mypy"]["strict"] is True

    def test_source_root(self, run_ctx, tmp_path):
        assert run_ctx.source_root == tmp_path

    def test_conn_accessible(self, run_ctx, ctx_db):
        assert run_ctx.conn is ctx_db

    def test_exit_code(self, run_ctx):
        assert run_ctx.exit_code == 1

    def test_duration_ms(self, run_ctx):
        assert run_ctx.duration_ms == 5432

    def test_add_annotation_stores_in_db(self, run_ctx, ctx_db):
        ann = Annotation(annotator="src", type="source", display="inline", data={"context": "hello"})
        run_ctx.add_annotation("evt-1", ann)

        # Read directly from DB
        row = ctx_db.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(row[0])
        assert len(meta["annotations"]) == 1
        assert meta["annotations"][0]["annotator"] == "src"

    def test_add_multiple_annotations_append(self, run_ctx, ctx_db):
        a1 = Annotation(annotator="src", type="source", display="inline", data={"n": 1})
        a2 = Annotation(annotator="lint", type="diagnostic", display="detail", data={"n": 2})
        run_ctx.add_annotation("evt-1", a1)
        run_ctx.add_annotation("evt-1", a2)

        row = ctx_db.execute(
            "SELECT metadata FROM events WHERE id = 'evt-1'"
        ).fetchone()
        meta = json.loads(row[0])
        assert len(meta["annotations"]) == 2
        assert meta["annotations"][0]["data"]["n"] == 1
        assert meta["annotations"][1]["data"]["n"] == 2

    def test_add_annotation_invalidates_events_cache(self, run_ctx):
        # Access events to populate cache
        _ = run_ctx.events
        ann = Annotation(annotator="x", type="t", display="hidden", data={})
        run_ctx.add_annotation("evt-2", ann)
        # Re-access should reflect the new annotation
        events = run_ctx.events
        evt2 = [e for e in events if e["id"] == "evt-2"][0]
        meta = json.loads(evt2["metadata"]) if isinstance(evt2["metadata"], str) else evt2["metadata"]
        assert len(meta["annotations"]) == 1
