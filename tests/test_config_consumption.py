"""Tests for tools consuming runtime config knobs."""

from __future__ import annotations

import pytest

from blq import runtime as rt
from blq.runtime import BlqRuntimeConfig, update_runtime


@pytest.fixture(autouse=True)
def _isolate_runtime():
    rt._runtime = BlqRuntimeConfig()
    rt._seed = BlqRuntimeConfig()
    yield
    rt._runtime = BlqRuntimeConfig()
    rt._seed = BlqRuntimeConfig()


class TestDefaultLinesWindow:
    """_resolve_command_lines should consult runtime config when neither
    explicit lines nor a command-level lines setting is set."""

    def test_explicit_lines_wins(self):
        from blq.serve import _resolve_command_lines

        update_runtime({"default_lines_window": "+5-"})
        assert _resolve_command_lines("test", "+20-") == "+20-"

    def test_runtime_fallback_when_explicit_none(self):
        from blq.serve import _resolve_command_lines

        update_runtime({"default_lines_window": "+10-"})
        # No command-level config (BlqConfig.find returns None outside a
        # blq workspace); should fall through to the runtime knob.
        assert _resolve_command_lines("nonexistent_cmd", None) == "+10-"

    def test_empty_runtime_treated_as_no_default(self):
        from blq.serve import _resolve_command_lines

        # default_lines_window="" means "no inline output" — same as old behavior
        update_runtime({"default_lines_window": ""})
        assert _resolve_command_lines("nonexistent_cmd", None) is None


class TestDefaultHistoryLimit:
    """history() with limit=None should use runtime's default_history_limit."""

    def test_history_limit_uses_runtime_default(self, monkeypatch):
        """Stub out _history_impl so we can verify the resolved limit."""
        captured = {}

        def fake_impl(limit, source, status):
            captured["limit"] = limit
            return {"runs": []}

        import blq.serve

        monkeypatch.setattr(blq.serve, "_history_impl", fake_impl)

        update_runtime({"default_history_limit": 99})

        from blq.serve import history

        history.fn(limit=None) if hasattr(history, "fn") else history(limit=None)
        assert captured["limit"] == 99

    def test_history_explicit_limit_wins(self, monkeypatch):
        captured = {}

        def fake_impl(limit, source, status):
            captured["limit"] = limit
            return {"runs": []}

        import blq.serve

        monkeypatch.setattr(blq.serve, "_history_impl", fake_impl)

        update_runtime({"default_history_limit": 99})

        from blq.serve import history

        history.fn(limit=5) if hasattr(history, "fn") else history(limit=5)
        assert captured["limit"] == 5
