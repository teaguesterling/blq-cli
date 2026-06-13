"""In-memory runtime configuration for the blq MCP server.

Distinct from `.bird/config.toml` (persistent user config) and the DuckDB
storage at `.bird/blq.duckdb` (run history). This module holds *session*
state that the MCP `config()` tool reads and writes — wiped on server
restart, no disk persistence.

Env vars at launch seed the initial values (e.g. BLQ_ACTIVE_ROOT set in
.mcp.json env block). Resetting reverts to those env-seeded values, not
hardcoded defaults.

**Scope.** Only runtime knobs live here. Persistent state stays in the DB
(retention, capture buffer, registered commands) — those are not session
state and don't belong in a tool that gets wiped on restart.

Shape mirrors jetsam.config.runtime and squackit.runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any

_ENV_PREFIX = "BLQ_"

_VALID_LOG_LEVELS = {"debug", "info", "warn", "warning", "error"}


@dataclass
class BlqRuntimeConfig:
    """Session-scoped runtime knobs for the blq MCP server.

    Common keys (suite-wide convention, also on jetsam + squackit):
        active_root: fallback when locating the `.bird/` workspace. None
            means walk up from process cwd (today's behavior).
        log_level: debug | info | warn | error.

    Blq-specific:
        default_lines_window: default for `run(lines=...)` when the caller
            omits it. Format matches the existing `lines` arg syntax
            (e.g. "+20-" for last 20). Empty string means "no inline
            output" (today's default).
        default_history_limit: default for `history(limit=...)` when the
            caller omits it.
    """

    active_root: str | None = None
    log_level: str = "info"
    default_lines_window: str = ""
    default_history_limit: int = 20

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> BlqRuntimeConfig:
        """Build a config seeded from environment variables.

        Variables read (each optional):
            BLQ_ACTIVE_ROOT
            BLQ_LOG_LEVEL
            BLQ_DEFAULT_LINES_WINDOW
            BLQ_DEFAULT_HISTORY_LIMIT

        Invalid values fall back to the dataclass default.
        """
        e = env if env is not None else os.environ
        cfg = cls()

        if v := e.get(f"{_ENV_PREFIX}ACTIVE_ROOT"):
            cfg.active_root = v
        if v := e.get(f"{_ENV_PREFIX}LOG_LEVEL"):
            if v.lower() in _VALID_LOG_LEVELS:
                cfg.log_level = v.lower()
        # default_lines_window: any string is acceptable (validated by callers)
        if (v := e.get(f"{_ENV_PREFIX}DEFAULT_LINES_WINDOW")) is not None:
            cfg.default_lines_window = v
        if v := e.get(f"{_ENV_PREFIX}DEFAULT_HISTORY_LIMIT"):
            try:
                cfg.default_history_limit = max(1, int(v))
            except ValueError:
                pass

        return cfg

    def to_dict(self) -> dict[str, Any]:
        """Flat dict for the MCP `config()` response."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


# Module-level singleton, seeded from env on import.
_runtime: BlqRuntimeConfig = BlqRuntimeConfig.from_env()
_seed: BlqRuntimeConfig = replace(_runtime)


def get_runtime() -> BlqRuntimeConfig:
    """Return the current runtime config."""
    return _runtime


def update_runtime(values: dict[str, Any]) -> BlqRuntimeConfig:
    """Merge values into the runtime config. Validates atomically.

    Raises ValueError on unknown keys or invalid values, leaving the runtime
    config unchanged.
    """
    global _runtime
    valid_keys = {f.name for f in fields(BlqRuntimeConfig)}
    unknown = set(values) - valid_keys
    if unknown:
        raise ValueError(
            f"unknown config key(s): {sorted(unknown)}. Valid keys: {sorted(valid_keys)}"
        )

    candidate = replace(_runtime)
    for key, value in values.items():
        _validate_one(key, value)
        setattr(candidate, key, value)

    _runtime = candidate
    return _runtime


def reset_runtime() -> BlqRuntimeConfig:
    """Reset to the env-seeded values captured at module import time."""
    global _runtime
    _runtime = replace(_seed)
    return _runtime


def resolve_storage_root() -> str | None:
    """Return the directory to search for .bird/ from, or None for cwd-walk.

    When active_root is set, callers should pass `Path(active_root)` to
    BlqStorage's search so the workspace resolves consistently regardless
    of the server's process cwd.
    """
    return _runtime.active_root


def _validate_one(key: str, value: Any) -> None:
    """Per-key value validation."""
    if key == "log_level":
        if not isinstance(value, str) or value.lower() not in _VALID_LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(_VALID_LOG_LEVELS)}, got {value!r}")
    elif key == "default_history_limit":
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"default_history_limit must be a positive int, got {value!r}")
    elif key == "default_lines_window":
        if not isinstance(value, str):
            raise ValueError(f"default_lines_window must be str, got {type(value).__name__}")
    elif key == "active_root":
        if value is not None and not isinstance(value, str):
            raise ValueError(f"active_root must be str or None, got {type(value).__name__}")
