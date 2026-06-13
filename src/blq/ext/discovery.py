"""Extension discovery via Python entry points."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from blq.ext import Extension

logger = logging.getLogger("blq-ext")

DEFAULT_ORDER = ["env", "sandbox", "platform"]


def load_extensions() -> dict[str, Extension]:
    """Discover installed extensions via entry points."""
    extensions: dict[str, Extension] = {}
    for ep in entry_points(group="blq.extensions"):
        try:
            ext_factory = ep.load()
            ext = ext_factory()
            extensions[ext.config_key] = ext
        except Exception as e:
            logger.warning(f"Failed to load extension {ep.name}: {e}")
    return extensions


def order_extensions(
    extensions: dict[str, Extension],
    order: list[str] | None = None,
) -> list[Extension]:
    """Order extensions by priority. Unlisted extensions go last."""
    priority = order or DEFAULT_ORDER

    def sort_key(ext: Extension) -> tuple[int, str]:
        try:
            idx = priority.index(ext.config_key)
        except ValueError:
            idx = len(priority)
        return (idx, ext.config_key)

    return sorted(extensions.values(), key=sort_key)
