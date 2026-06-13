"""Tests for the in-memory runtime config + MCP config() tool."""

from __future__ import annotations

import pytest

from blq import runtime as rt
from blq.runtime import (
    BlqRuntimeConfig,
    get_runtime,
    reset_runtime,
    resolve_storage_root,
    update_runtime,
)


@pytest.fixture(autouse=True)
def _isolate_runtime():
    """Reset the module singleton + seed between tests so order doesn't matter."""
    rt._runtime = BlqRuntimeConfig()
    rt._seed = BlqRuntimeConfig()
    yield
    rt._runtime = BlqRuntimeConfig()
    rt._seed = BlqRuntimeConfig()


class TestFromEnv:
    def test_empty_env_returns_defaults(self):
        cfg = BlqRuntimeConfig.from_env(env={})
        assert cfg.active_root is None
        assert cfg.log_level == "info"
        assert cfg.default_lines_window == ""
        assert cfg.default_history_limit == 20

    def test_active_root_from_env(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_ACTIVE_ROOT": "/tmp/repo"})
        assert cfg.active_root == "/tmp/repo"

    def test_log_level_normalized_lowercase(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_LOG_LEVEL": "DEBUG"})
        assert cfg.log_level == "debug"

    def test_log_level_invalid_falls_back_to_default(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_LOG_LEVEL": "verbose"})
        assert cfg.log_level == "info"

    def test_lines_window_accepts_any_string(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_DEFAULT_LINES_WINDOW": "+30-"})
        assert cfg.default_lines_window == "+30-"

    def test_history_limit_int(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_DEFAULT_HISTORY_LIMIT": "100"})
        assert cfg.default_history_limit == 100

    def test_history_limit_invalid_falls_back(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_DEFAULT_HISTORY_LIMIT": "abc"})
        assert cfg.default_history_limit == 20

    def test_history_limit_zero_clamped_to_one(self):
        cfg = BlqRuntimeConfig.from_env(env={"BLQ_DEFAULT_HISTORY_LIMIT": "0"})
        assert cfg.default_history_limit == 1


class TestUpdateRuntime:
    def test_set_active_root_persists_in_singleton(self):
        update_runtime({"active_root": "/tmp/x"})
        assert get_runtime().active_root == "/tmp/x"

    def test_unknown_key_raises_and_leaves_unchanged(self):
        original = get_runtime().to_dict()
        with pytest.raises(ValueError, match="unknown config key"):
            update_runtime({"bogus": "value"})
        assert get_runtime().to_dict() == original

    def test_invalid_value_raises_and_leaves_unchanged(self):
        original = get_runtime().to_dict()
        with pytest.raises(ValueError, match="log_level"):
            update_runtime({"log_level": "verbose"})
        assert get_runtime().to_dict() == original

    def test_atomic_batch_set_either_all_or_none(self):
        original = get_runtime().to_dict()
        with pytest.raises(ValueError):
            update_runtime({"active_root": "/tmp/y", "log_level": "verbose"})
        # active_root should NOT have been applied even though it's valid
        assert get_runtime().to_dict() == original

    def test_history_limit_rejects_string(self):
        with pytest.raises(ValueError, match="default_history_limit"):
            update_runtime({"default_history_limit": "50"})

    def test_history_limit_rejects_bool(self):
        with pytest.raises(ValueError, match="default_history_limit"):
            update_runtime({"default_history_limit": True})

    def test_history_limit_rejects_zero(self):
        with pytest.raises(ValueError, match="default_history_limit"):
            update_runtime({"default_history_limit": 0})

    def test_lines_window_accepts_empty_string(self):
        update_runtime({"default_lines_window": ""})
        assert get_runtime().default_lines_window == ""


class TestResetRuntime:
    def test_reset_reverts_to_seed(self, monkeypatch):
        monkeypatch.setenv("BLQ_ACTIVE_ROOT", "/tmp/seeded")
        rt._seed = BlqRuntimeConfig.from_env()
        rt._runtime = BlqRuntimeConfig.from_env()
        update_runtime({"active_root": "/tmp/overridden"})
        assert get_runtime().active_root == "/tmp/overridden"
        reset_runtime()
        assert get_runtime().active_root == "/tmp/seeded"

    def test_reset_with_no_env_goes_to_defaults(self):
        update_runtime({"active_root": "/tmp/x", "default_history_limit": 200})
        reset_runtime()
        assert get_runtime().active_root is None
        assert get_runtime().default_history_limit == 20


class TestResolveStorageRoot:
    def test_returns_none_when_unset(self):
        assert resolve_storage_root() is None

    def test_returns_active_root_when_set(self):
        update_runtime({"active_root": "/tmp/somewhere"})
        assert resolve_storage_root() == "/tmp/somewhere"


class TestConfigToolViaMCP:
    """Smoke tests for the registered MCP tool."""

    def test_config_is_registered(self):
        pytest.importorskip("fastmcp")
        import asyncio

        from fastmcp import Client

        from blq.serve import mcp

        async def _list():
            async with Client(mcp) as client:
                tools = await client.list_tools()
                return [t.name for t in tools]

        loop = asyncio.new_event_loop()
        try:
            names = loop.run_until_complete(_list())
        finally:
            loop.close()
        assert "config" in names

    def test_call_returns_current_state(self):
        pytest.importorskip("fastmcp")
        import asyncio

        from fastmcp import Client

        from blq.serve import mcp

        async def _call():
            async with Client(mcp) as client:
                return await client.call_tool("config", {})

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_call())
        finally:
            loop.close()
        # FastMCP wraps the dict; pull data or text out
        data = getattr(result, "data", None) or getattr(result, "structured_content", None)
        if data is None and hasattr(result, "content"):
            data = result.content[0].text
        assert "active_root" in str(data)
