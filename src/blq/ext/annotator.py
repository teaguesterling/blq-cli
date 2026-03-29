"""Annotator system for enriching events with additional context.

Provides:
- Annotation: typed data attached to events
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
