"""Annotator system for enriching events with additional context.

Provides:
- Annotation: typed data attached to events
- RunContext: lazy, DB-backed access to a stored run
- Annotator protocol + dispatch: plugin discovery and execution
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

import duckdb

logger = logging.getLogger("blq-ext")

VALID_DISPLAYS = ("inline", "detail", "hidden")


@dataclass
class Annotation:
    """Typed annotation attached to an event."""

    annotator: str
    type: str
    display: str
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if self.display not in VALID_DISPLAYS:
            raise ValueError(
                f"display must be one of {VALID_DISPLAYS}, got {self.display!r}"
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


class RunContext:
    """Lazy, DB-backed access to a stored run."""

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        invocation_id: str,
        source_root: Path,
    ) -> None:
        self._conn = conn
        self._invocation_id = invocation_id
        self._source_root = source_root
        self._events: list[dict[str, Any]] | None = None
        self._metadata: dict[str, Any] | None = None
        self._exit_code: int | None = None
        self._duration_ms: int | None = None
        self._outcome_loaded = False

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    @property
    def invocation_id(self) -> str:
        return self._invocation_id

    @property
    def source_root(self) -> Path:
        return self._source_root

    @property
    def events(self) -> list[dict[str, Any]]:
        if self._events is None:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE invocation_id = ? ORDER BY event_index",
                [self._invocation_id],
            ).fetchall()
            columns = [
                desc[0]
                for desc in self._conn.execute(
                    "SELECT * FROM events LIMIT 0"
                ).description
            ]
            self._events = [dict(zip(columns, row)) for row in rows]
        return self._events

    @property
    def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            row = self._conn.execute(
                "SELECT source_name, cmd, cwd, extension_data, timestamp "
                "FROM invocations WHERE id = ?",
                [self._invocation_id],
            ).fetchone()
            if row is None:
                raise ValueError(f"No invocation found: {self._invocation_id}")
            ext_data = row[3]
            if isinstance(ext_data, str):
                ext_data = json.loads(ext_data)
            self._metadata = {
                "source_name": row[0],
                "cmd": row[1],
                "cwd": row[2],
                "extension_data": ext_data,
                "timestamp": row[4],
            }
        return self._metadata

    @property
    def extension_data(self) -> dict[str, Any]:
        return self.metadata["extension_data"]

    def _load_outcome(self) -> None:
        if not self._outcome_loaded:
            row = self._conn.execute(
                "SELECT exit_code, duration_ms FROM outcomes WHERE attempt_id = ?",
                [self._invocation_id],
            ).fetchone()
            if row is not None:
                self._exit_code = row[0]
                self._duration_ms = row[1]
            self._outcome_loaded = True

    @property
    def exit_code(self) -> int | None:
        self._load_outcome()
        return self._exit_code

    @property
    def duration_ms(self) -> int | None:
        self._load_outcome()
        return self._duration_ms

    def add_annotation(self, event_id: str, annotation: Annotation) -> None:
        """Append an annotation to an event's metadata JSON."""
        row = self._conn.execute(
            "SELECT metadata FROM events WHERE id = ?",
            [event_id],
        ).fetchone()
        if row is None:
            raise ValueError(f"No event found: {event_id}")

        meta = row[0]
        if isinstance(meta, str):
            meta = json.loads(meta)
        if meta is None:
            meta = {}

        annotations = meta.get("annotations", [])
        annotations.append(annotation.to_dict())
        meta["annotations"] = annotations

        self._conn.execute(
            "UPDATE events SET metadata = ? WHERE id = ?",
            [json.dumps(meta), event_id],
        )

        # Invalidate cached events
        self._events = None


# ---------------------------------------------------------------------------
# Component 3: Annotator protocol + dispatch
# ---------------------------------------------------------------------------


class Annotator(Protocol):
    """Protocol for annotator plugins."""

    name: str
    eager: bool

    def should_annotate(self, context: RunContext) -> bool: ...
    def annotate(self, context: RunContext) -> None: ...


def load_annotators() -> list[Annotator]:
    """Discover annotators via entry_points(group='blq.annotators')."""
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
    """Run matching annotators against a run context.

    When eager_only is True, only annotators with eager=True are executed.
    Failures are logged but do not prevent other annotators from running.
    """
    for annotator in annotators:
        if eager_only and not annotator.eager:
            continue
        try:
            if annotator.should_annotate(context):
                annotator.annotate(context)
        except Exception as e:
            logger.warning(
                f"Annotator {annotator.name} failed: {e}",
                exc_info=True,
            )
